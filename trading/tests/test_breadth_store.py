"""F3 fix (owner order 2026-08-20: "fix F3") — full-store breadth.

The fault: the A3 breadth gate read the scan's ~50-name rotating bucket,
±7pp of sampling noise against a <=50% line — whether the engine could buy
was decided by WHICH 50 names the rotation served that day. These tests pin
the fix's three promises:

  1. the reduction is honest — thin samples return None, dead names don't
     vote, the reading carries its as-of date;
  2. the gate PREFERS the wide reading while fresh and falls back to the
     bucket when it is stale or missing — degrading to exactly the pre-fix
     behaviour, never to something new;
  3. the reading survives a restart (the risk_day lesson).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from trading.breadth import DEAD_NAME_DAYS, is_fresh, store_breadth
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio


# ---------------------------------------------------------------------------
# 1. The reduction
# ---------------------------------------------------------------------------

def _doc(ticker: str, end: str, closes: list) -> dict:
    return {"ticker": ticker, "end": end,
            "candles": [[end, c, c, c, c, 0.0] for c in closes]}


def _above(t, end="2026-08-14"):
    # rising: last close far above the 200-mean
    return _doc(t, end, list(range(100, 300)))


def _below(t, end="2026-08-14"):
    return _doc(t, end, list(range(300, 100, -1)))


def test_store_breadth_counts_above_and_below():
    docs = [_above(f"A{i}") for i in range(6)] + [_below(f"B{i}")
                                                 for i in range(4)]
    r = store_breadth(docs, dma=200, min_sample=5)
    assert r["sample"] == 10 and r["above"] == 6 and r["pct"] == 0.6
    assert r["asof"] == "2026-08-14" and r["dma"] == 200


def test_thin_sample_returns_none_never_a_guess():
    assert store_breadth([_above("A")], dma=200, min_sample=5) is None


def test_short_history_is_excluded_and_counted():
    docs = [_above("A0"), _above("A1"),
            _doc("SHORT", "2026-08-14", [100.0] * 50)]
    r = store_breadth(docs, dma=200, min_sample=2)
    assert r["sample"] == 2 and r["excluded_short"] == 1


def test_dead_names_do_not_vote_their_old_state_forever():
    """A symbol that stopped updating months ago must not poison the
    reading with its last known regime."""
    old = (date(2026, 8, 14) - timedelta(days=DEAD_NAME_DAYS + 10)).isoformat()
    docs = ([_above(f"A{i}") for i in range(3)]
            + [_below("DEAD", end=old)])
    r = store_breadth(docs, dma=200, min_sample=2)
    assert r["sample"] == 3 and r["excluded_stale"] == 1
    assert r["pct"] == 1.0                      # the dead bear didn't vote


def test_is_fresh_window():
    r = {"pct": 0.6, "asof": "2026-08-10"}
    assert is_fresh(r, date(2026, 8, 15), max_age_days=10)
    assert not is_fresh(r, date(2026, 8, 25), max_age_days=10)
    assert not is_fresh(None, date(2026, 8, 15), 10)
    assert not is_fresh({}, date(2026, 8, 15), 10)
    assert not is_fresh({"pct": None, "asof": "2026-08-10"},
                        date(2026, 8, 15), 10)


# ---------------------------------------------------------------------------
# 2. The gate prefers wide-and-fresh, falls back to the bucket
# ---------------------------------------------------------------------------

def _engine(tmp_path) -> TradingEngine:
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.breadth_gate_enabled = True
    cfg.breadth_min_sample = 2
    eng = TradingEngine(cfg, Portfolio.fresh(cfg))
    return eng


def _bucket_history(frac_above: float, n: int = 10) -> dict:
    """A fake scan bucket where `frac_above` of names are above their 200DMA."""
    out = {}
    idx = pd.bdate_range("2025-01-01", periods=260)
    for i in range(n):
        up = i < int(frac_above * n)
        closes = (list(range(100, 360)) if up
                  else list(range(360, 100, -1)))[:260]
        out[f"T{i}.NS"] = pd.DataFrame({"Close": closes}, index=idx)
    return out


_NOW = datetime(2026, 8, 15, 9, 20)


def test_fresh_wide_reading_overrides_a_panicky_bucket(tmp_path):
    """The F3 scenario inverted: the bucket happens to sample the dead band
    (50%) but the full store says 70% — the gate must NOT block, because
    2,700 names beat 10. (Bucket moved 30% -> 50% for the 2026-09-09 band
    re-cut: 30% is now BELOW the band and would be allowed either way, so
    the old fixture could no longer tell the two sources apart.)"""
    eng = _engine(tmp_path)
    eng.portfolio.breadth_store = {"pct": 0.70, "sample": 2700,
                                   "asof": "2026-08-14"}
    assert eng._breadth_gate_blocks(_bucket_history(0.5), _NOW) is False


def test_fresh_wide_reading_blocks_and_names_its_source(tmp_path):
    eng = _engine(tmp_path)
    eng.portfolio.breadth_store = {"pct": 0.45, "sample": 2700,
                                   "asof": "2026-08-14"}
    assert eng._breadth_gate_blocks(_bucket_history(0.9), _NOW,
                                    n_picks=3) is True
    j = eng.portfolio.decision_journal[-1]
    assert j["action"] == "BREADTH_BLOCKED"
    assert "full store" in j["why"] and "2026-08-14" in j["why"]


def test_stale_reading_falls_back_to_the_bucket(tmp_path):
    """Past max age the wide reading is history, not breadth — the gate
    reads the live bucket exactly as it did before the fix."""
    eng = _engine(tmp_path)
    eng.portfolio.breadth_store = {"pct": 0.70, "sample": 2700,
                                   "asof": "2026-07-01"}      # 45 days old
    assert eng._breadth_gate_blocks(_bucket_history(0.5), _NOW) is True
    assert "bucket" in eng.portfolio.decision_journal[-1]["why"]


def test_no_reading_at_all_falls_back_to_the_bucket(tmp_path):
    eng = _engine(tmp_path)
    assert eng.portfolio.breadth_store == {}
    assert eng._breadth_gate_blocks(_bucket_history(0.5), _NOW) is True
    assert eng._breadth_gate_blocks(_bucket_history(0.9), _NOW) is False
    # and the washed-out end of the bucket is buyable now (re-cut 2026-09-09)
    assert eng._breadth_gate_blocks(_bucket_history(0.2), _NOW) is False


def test_vix_reentry_bypass_still_wins_over_everything(tmp_path):
    """Owner decision 2026-07-27 stands: fear-break entries skip breadth."""
    eng = _engine(tmp_path)
    eng.portfolio.breadth_store = {"pct": 0.10, "sample": 2700,
                                   "asof": "2026-08-14"}
    assert eng._breadth_gate_blocks(_bucket_history(0.1), _NOW,
                                    bypass=True) is False


# ---------------------------------------------------------------------------
# 3. Persistence + the scheduler wiring
# ---------------------------------------------------------------------------

def test_reading_survives_a_save_load_round_trip(tmp_path):
    """The risk_day lesson: state that isn't persisted BOTH ways dies on
    every Render restart."""
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.breadth_store = {"pct": 0.55, "sample": 2600, "asof": "2026-08-14"}
    p.save()
    p2 = Portfolio.load_or_fresh(cfg)
    assert p2.breadth_store == p.breadth_store


def test_scheduler_refresh_stores_and_persists(monkeypatch):
    from web import scheduler, state
    # 1,500 docs: past the raised 1000-name bar (external review fix, PR #68)
    docs = [_above(f"A{i}") for i in range(750)] + \
           [_below(f"B{i}") for i in range(750)]
    monkeypatch.setattr("web.db.history_tails", lambda rows: iter(docs))
    p = state.portfolio()
    old = p.breadth_store
    try:
        r = scheduler.refresh_breadth_store("test")
        assert r["pct"] == 0.5 and r["sample"] == 1500
        assert state.portfolio().breadth_store["pct"] == 0.5
        assert "computed" in state.portfolio().breadth_store
    finally:
        p.breadth_store = old
        p.save()


def test_scheduler_failure_keeps_the_previous_reading(monkeypatch):
    from web import scheduler, state

    def boom(rows):
        raise RuntimeError("mongo exploded")
    monkeypatch.setattr("web.db.history_tails", boom)
    p = state.portfolio()
    old = p.breadth_store
    p.breadth_store = {"pct": 0.61, "sample": 2700, "asof": "2026-08-14"}
    try:
        assert scheduler.refresh_breadth_store("test") == {}
        assert p.breadth_store["pct"] == 0.61     # untouched
    finally:
        p.breadth_store = old
        p.save()
