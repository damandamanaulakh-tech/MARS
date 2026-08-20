"""Audit flaws F1 + F2, fixed on the owner's order 2026-08-20 ("fix F1 and
F2 both").

F1: the Friday weekly OHLCV top-up gated on broker.is_connected() although
Upstox historical candles are a PUBLIC endpoint (verified live twice) — so
the store silently skipped refresh any week the 03:30-dead token wasn't
back. Now: get_daily_candles works tokenless, and the scheduler skips only
when no client OBJECT exists at all.

F2: every backtest network-fetched the ^CNX200 benchmark while
data/nifty200_daily.csv (2004→2026-08-07) sat committed with zero
consumers. Now the committed file is the deterministic base and the network
is asked only for the tail past its last row.
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from trading import data as D
from trading.brokers.upstox import UpstoxClient


# ---------------------------------------------------------------------------
# F1 — the broker fetches history without a token
# ---------------------------------------------------------------------------

class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"candles": [
            ["2026-08-14T00:00:00+05:30", 100.0, 101.0, 99.0, 100.5, 1000, 0],
            ["2026-08-13T00:00:00+05:30", 99.0, 100.0, 98.0, 100.0, 900, 0],
        ]}}


def _client(token=None) -> UpstoxClient:
    c = UpstoxClient(api_key="k", api_secret="s", redirect_uri="r")
    c.access_token = token
    return c


def test_daily_candles_work_with_no_token(monkeypatch):
    """The whole F1 fault: _auth_headers raised on a dead token and the
    except swallowed it into None, so a public endpoint read as down."""
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return _FakeResp()
    monkeypatch.setattr("trading.brokers.upstox.httpx.get", fake_get)
    df = _client(token=None).get_daily_candles("NSE_EQ|X", "2026-08-01",
                                               "2026-08-14")
    assert df is not None and len(df) == 2
    assert "Authorization" not in (seen["headers"] or {})


def test_daily_candles_still_send_auth_when_a_token_exists(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return _FakeResp()
    monkeypatch.setattr("trading.brokers.upstox.httpx.get", fake_get)
    df = _client(token="tok123").get_daily_candles("NSE_EQ|X", "2026-08-01",
                                                   "2026-08-14")
    assert df is not None
    assert seen["headers"]["Authorization"] == "Bearer tok123"


def test_weekly_topup_no_longer_gates_on_the_login_token():
    """Source-pinned: the Friday job must not consult is_connected — the
    endpoint is public. Only a missing client object may skip."""
    from web import scheduler
    src = inspect.getsource(scheduler._weekly_history_topup)
    code_only = "\n".join(ln for ln in src.splitlines()
                          if not ln.strip().startswith("#"))
    assert "is_connected()" not in code_only.replace("db.is_connected()", ""), (
        "the weekly top-up must not gate on the broker token — Upstox "
        "historical candles are public (F1, owner order 2026-08-20)")
    assert "broker is None" in code_only      # the one legitimate skip


def test_paper_lock_untouched_by_the_public_history_path():
    """History is read-only; the order path must still be locked."""
    from pathlib import Path

    from trading.brokers import upstox
    src = Path(upstox.__file__).read_text()
    assert "assert_paper_trading" in src


# ---------------------------------------------------------------------------
# F2 — the N200 benchmark is deterministic from the committed file
# ---------------------------------------------------------------------------

def test_committed_nifty200_loads_and_clips():
    df = D.committed_nifty200("2020-01-01", "2021-01-01")
    assert df is not None and not df.empty
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index[0] >= pd.Timestamp("2020-01-01")
    assert df.index[-1] <= pd.Timestamp("2021-01-01")
    assert (df["Volume"] == 0.0).all()        # source has none; never a guess


def test_window_outside_the_file_returns_none_not_a_guess():
    assert D.committed_nifty200("2030-01-01", "2030-06-01") is None


def _kill_network(monkeypatch):
    monkeypatch.setattr(D, "_HISTORY_BROKER", None)

    def boom(*a, **k):
        raise RuntimeError("network dead in this test")
    monkeypatch.setattr(D.yf, "download", boom)


def test_benchmark_resolves_fully_offline(monkeypatch):
    """The point of F2: a covered window needs no network at all."""
    _kill_network(monkeypatch)
    out = D.fetch_daily_range(["^CNX200"], "2018-01-01", "2024-01-01")
    b = out.get("^CNX200")
    assert b is not None and len(b) > 1000
    assert b.index[-1] <= pd.Timestamp("2024-01-01")


def test_dead_network_truncates_the_tail_instead_of_failing(monkeypatch):
    """Window runs past the file's end: the base survives and the missing
    tail is exactly what the backtest's BENCHMARK TRUNCATED warning covers."""
    _kill_network(monkeypatch)
    out = D.fetch_daily_range(["^CNX200"], "2026-01-01", "2030-01-01")
    b = out.get("^CNX200")
    assert b is not None and not b.empty
    assert b.index[-1] == pd.Timestamp("2026-08-07")   # the file's last row


def test_live_tail_is_merged_beyond_the_file(monkeypatch):
    """With a working broker, only the tail comes from the network and the
    merge keeps one row per date."""
    calls = []

    class _TailBroker:
        def get_daily_candles(self, key, start, end):
            calls.append((start, end))
            idx = pd.to_datetime(["2026-08-08", "2026-08-11"])
            return pd.DataFrame(
                {"Open": [1.0, 1.0], "High": [1.0, 1.0], "Low": [1.0, 1.0],
                 "Close": [14300.0, 14350.0], "Volume": [0.0, 0.0]},
                index=idx).astype("float32")
    monkeypatch.setattr(D, "_HISTORY_BROKER", _TailBroker())
    out = D.fetch_daily_range(["^CNX200"], "2026-07-01", "2026-08-12")
    b = out["^CNX200"]
    assert b.index[-1] == pd.Timestamp("2026-08-11")
    assert not b.index.duplicated().any()
    # the network was asked ONLY for the tail, not the whole window
    assert calls and calls[0][0] >= "2026-08-08"


def test_other_tickers_still_use_the_normal_path(monkeypatch):
    """The special case must not swallow the rest of the request list."""
    _kill_network(monkeypatch)
    out = D.fetch_daily_range(["^CNX200", "TCS.NS"], "2020-01-01",
                              "2021-01-01")
    assert "^CNX200" in out
    assert "TCS.NS" not in out                # network dead, honestly absent
