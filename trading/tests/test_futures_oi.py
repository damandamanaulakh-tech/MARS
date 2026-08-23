"""Node 7 (owner order 2026-08-23: "start fixing ur pending and flaws one
by one"): the short_interest composite component stops being a constant.

What these tests pin: the four-quadrant OI-vs-price read, the two honesty
rules (non-F&O names and stale panels read the old neutral 50 — the failure
mode of this fix is the world before it), and that no page still calls the
component a stub.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from trading.signals import futures_oi as F


@pytest.fixture(autouse=True)
def _fake_panel(monkeypatch):
    """A tiny in-memory panel: BUILDUP (OI +20%), COVER (OI -20%), FLAT."""
    days = [f"2026-08-{d:02d}" for d in range(10, 22)]
    def tail(mult):
        base = 1_000_000.0
        rows = [(d, base) for d in days[:-F.WINDOW]]
        rows += [(d, base * mult) for d in days[-F.WINDOW:]]
        return rows
    monkeypatch.setattr(F, "_cache", {
        "BUILDUP": tail(1.20), "COVER": tail(0.80), "FLAT": tail(1.0),
    })
    monkeypatch.setattr(F, "_cache_last", days[-1])
    yield
    F._cache, F._cache_last = None, ""


def _px(direction: float) -> pd.Series:
    n = 30
    base = 100.0
    return pd.Series([base * (1 + direction * i / n) for i in range(n)])


TODAY = date(2026, 8, 22)


def test_short_buildup_scores_low():
    """OI rising while price falls = shorts building = bearish."""
    s = F.score_short_interest("BUILDUP.NS", _px(-0.10), today=TODAY)
    assert s < 40


def test_short_covering_scores_high():
    s = F.score_short_interest("COVER.NS", _px(+0.10), today=TODAY)
    assert s > 60


def test_long_buildup_is_mildly_constructive():
    s = F.score_short_interest("BUILDUP.NS", _px(+0.10), today=TODAY)
    assert 50 < s < 62


def test_long_unwinding_is_mildly_bearish():
    s = F.score_short_interest("COVER.NS", _px(-0.10), today=TODAY)
    assert 38 < s < 50


def test_non_fo_names_read_the_old_neutral():
    """~2,150 of the universe have no futures — absence of a derivatives
    market is not information about shorts."""
    assert F.score_short_interest("NOFNO.NS", _px(-0.10), today=TODAY) == 50.0


def test_a_stale_panel_reads_neutral_never_yesterdays_confidence():
    """The panel refreshes only when a Colab run lands; 16-day-old OI must
    not wear today's confidence."""
    late = date(2026, 9, 15)
    assert F.score_short_interest("BUILDUP.NS", _px(-0.10), today=late) == 50.0


def test_any_failure_returns_the_stub_value(monkeypatch):
    monkeypatch.setattr(F, "_load", lambda: (_ for _ in ()).throw(RuntimeError))
    assert F.score_short_interest("BUILDUP.NS", _px(-0.10), today=TODAY) == 50.0


def test_scoring_wires_the_proxy_and_no_stub_set_remains():
    from trading.config import STUB_COMPONENTS
    assert STUB_COMPONENTS == set()
    src = Path("trading/scoring.py").read_text()
    assert "_short_interest_score(ticker, close)" in src


def test_no_page_still_calls_it_a_stub():
    src = Path("web/routes/scan_routes.py").read_text()
    assert "STUB — waiting" not in src
    assert "futures-OI proxy" in src or "futures_oi" in src


def test_the_real_committed_panel_loads_and_dates_itself():
    F._cache, F._cache_last = None, ""       # drop the fake
    tails = F._load()
    assert len(tails) > 200                  # ~278 F&O symbols
    assert F.panel_asof() >= "2026-08-01"
    F._cache, F._cache_last = None, ""
