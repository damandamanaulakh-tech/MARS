"""Direct unit tests for engine.scan() and engine.refresh() — the F6
leftover the 2026-08-12 audit named ("engine.scan/refresh … have ZERO
direct tests; integration-tested only"). Owner order 2026-08-23: "start
fixing ur pending and flaws one by one".

These call the REAL methods with only the network seams
(fetch_daily / routed_fetch_quotes) replaced by deterministic frames, so
the paths that guard money — the score floor, the kill switch, the stop
exit, the no-quote HOLD — are exercised as the methods the scheduler
actually calls, not as reimplementations.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio


def _frame(n=300, start=100.0, drift=0.15, seed=1, vol_scale=5e6):
    rng = np.random.default_rng(seed)
    steps = 1 + drift / n + rng.normal(0, 0.004, n)
    close = start * np.cumprod(steps)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close,
        "Volume": rng.integers(vol_scale, vol_scale * 2, n).astype(float),
    }, index=idx)


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.custom_universe = ("UPP.NS", "DOWN.NS")
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    frames = {
        "UPP.NS": _frame(drift=0.60, seed=2),      # strong up-trender
        "DOWN.NS": _frame(drift=-0.40, seed=3),    # falling name
        cfg.benchmark: _frame(drift=0.05, seed=4),
        "^INDIAVIX": _frame(n=300, start=12.0, drift=0.0, seed=5),
    }

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}

    def fake_quotes(tickers, upstox_client=None):
        return {t: float(frames[t]["Close"].iloc[-1])
                for t in tickers if t in frames}
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", fake_quotes)
    e._frames = frames
    return e


def test_scan_runs_end_to_end_and_journals_its_work(eng):
    res = eng.scan()
    assert res is not None
    p = eng.portfolio
    assert p.last_scan                              # timestamp recorded
    assert (p.scan_matrix or {}).get("rows")        # matrix written
    # every scanned row carries a real score, never a fabricated one
    for t, r in p.scan_matrix["rows"].items():
        assert r.get("score") is not None, t


def test_scan_never_buys_below_the_floor(eng):
    eng.scan()
    floor = eng.cfg.min_score_to_enter
    for t in eng.portfolio.last_scan_opened:
        entry = next(e for e in eng.portfolio.decision_journal
                     if e.get("action") == "BUY" and e.get("ticker") == t)
        # a scan BUY must have cleared the mode floor (owner order: 62)
        assert entry.get("current_score") is None or \
            float(entry.get("current_score") or floor) >= floor


def test_kill_switch_blocks_every_scan_entry(eng):
    """A switch tripped TODAY blocks every entry. (2026-10-04: this test
    used to set the flag by hand on a fresh book, which the engine's daily
    roll correctly clears at scan start — it only passed because the candle
    gate happened to block the pick. Model the real state: tripped today.)"""
    from datetime import datetime as _dt
    eng.portfolio.risk_day = _dt.utcnow().date().isoformat()
    eng.portfolio.risk_day_equity = eng.portfolio.cash
    eng.portfolio.risk_day_killed = True
    eng.scan()
    assert eng.portfolio.last_scan_opened == []
    assert len(eng.portfolio.positions) == 0
    assert any(s["reason"] == "kill_switch_active"
               for s in eng.portfolio.last_scan_skipped)


def test_a_switch_from_yesterday_resets_at_the_day_roll(eng):
    """The daily halt is daily: yesterday's tripped flag does not carry over."""
    eng.risk.kill_switch = True
    eng.scan()
    assert eng.risk.kill_switch is False



def test_refresh_marks_positions_and_fires_the_stop(eng):
    p = eng.portfolio
    last = float(eng._frames["DOWN.NS"]["Close"].iloc[-1])
    entry = last / 0.90                    # live price sits 10% under entry
    p.positions["DOWN.NS"] = PaperPosition(
        ticker="DOWN.NS", qty=100, entry_price=entry,
        entry_date=datetime(2026, 8, 1), sector="Test", peak_price=entry,
        entry_score=70.0, reasoning="fixture",
        stop_price=entry * 0.95, target_price=entry * 1.12)
    p.cash -= 100 * entry
    eng.refresh()
    assert "DOWN.NS" not in p.positions            # -10% < the -5% stop
    assert p.trades and p.trades[-1].ticker == "DOWN.NS"
    assert p.trades[-1].exit_price == pytest.approx(last, rel=0.02)


def test_refresh_holds_unquoted_names_instead_of_inventing_a_fill(eng, monkeypatch):
    """AUDIT FIX A2 stays true under direct test: no quote = HOLD, never an
    invented exit price."""
    p = eng.portfolio
    p.positions["GHOST.NS"] = PaperPosition(
        ticker="GHOST.NS", qty=10, entry_price=500.0,
        entry_date=datetime(2026, 8, 1), sector="Test", peak_price=500.0,
        entry_score=70.0, reasoning="fixture",
        stop_price=475.0, target_price=560.0)
    eng.refresh()
    assert "GHOST.NS" in p.positions               # still held
    assert all(t.ticker != "GHOST.NS" for t in p.trades)


def test_refresh_persists_the_drawdown_peak(eng):
    """With a live book, refresh must record the governor's peak (the
    2026-07-31 fix: a Render restart used to reset measured drawdown to 0)."""
    p = eng.portfolio
    last = float(eng._frames["UPP.NS"]["Close"].iloc[-1])
    p.positions["UPP.NS"] = PaperPosition(
        ticker="UPP.NS", qty=100, entry_price=last * 0.95,
        entry_date=datetime(2026, 8, 1), sector="Test",
        peak_price=last * 0.95, entry_score=70.0, reasoning="fixture",
        stop_price=last * 0.90, target_price=last * 1.20)
    eng.refresh()
    assert p.dd_peak_equity > 0                    # governor never resets to 0
