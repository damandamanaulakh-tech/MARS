"""The rotation suggester executes (owner order 2026-09-04).

"skipper exit suggestion is there but not executed — make it happen." The
F6 card on the dashboard said "Suggestion only; nothing is sold
automatically". Now engine._rotation_exits sells a near-target position at
the scan to fund a fresh pick the cash cannot cover.

Pinned here: it fires only when cash is SHORT; only as many rotations as
the shortfall needs, highest progress first; owner picks are exempt; a name
with no live quote is left alone and journaled; the Settings toggle turns
it off; and scan() calls it BEFORE the opening loop so the freed cash is
counted.
"""
from __future__ import annotations

import inspect
from datetime import datetime

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

NOW = datetime(2026, 9, 4, 9, 25)


def _eng(tmp_path, cash: float):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    p = Portfolio.fresh(cfg)
    p.cash = cash
    return TradingEngine(cfg=cfg, portfolio=p)


def _hold(eng, ticker, entry=1000.0, target=1200.0, qty=100, tags=()):
    eng.portfolio.positions[ticker] = PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry, entry_date=NOW,
        mode="nifty200_momentum", sector="IT", peak_price=entry,
        entry_score=70.0, reasoning="fixture", target_price=target,
        tags=list(tags))


def _quotes(monkeypatch, table: dict):
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: table[t] for t in tickers if t in table})


FRESH = [{"ticker": "TCS.NS", "score": 71.0}]


def _acts(p):
    return [e.get("action") for e in p.decision_journal]


def test_near_target_position_is_sold_when_cash_is_short(tmp_path, monkeypatch):
    eng = _eng(tmp_path, cash=50_000)               # < one Rs 1.25L ticket
    _hold(eng, "NEAR.NS")                            # 1000 -> 1200 target
    _quotes(monkeypatch, {"NEAR.NS": 1190.0})        # 95% of the way
    cash0 = eng.portfolio.cash

    rotated = eng._rotation_exits(FRESH, NOW)

    assert rotated == ["NEAR.NS"]
    assert "NEAR.NS" not in eng.portfolio.positions
    assert eng.portfolio.cash > cash0 + 1_00_000      # ~1.19L freed less costs
    trade = eng.portfolio.trades[-1]
    assert trade.ticker == "NEAR.NS" and trade.reason == "ROTATION_EXIT"
    entry = next(e for e in eng.portfolio.decision_journal
                 if e.get("action") == "ROTATION_EXIT")
    assert entry["fund_ticker"] == "TCS.NS" and entry["progress_pct"] == 95.0


def test_nothing_is_sold_when_cash_already_covers_the_picks(tmp_path, monkeypatch):
    """Selling early when the cash is there just gives up the last 10% —
    the +20% target handles the name."""
    eng = _eng(tmp_path, cash=10_00_000)
    _hold(eng, "NEAR.NS")
    _quotes(monkeypatch, {"NEAR.NS": 1190.0})
    assert eng._rotation_exits(FRESH, NOW) == []
    assert "NEAR.NS" in eng.portfolio.positions
    assert "ROTATION_EXIT" not in _acts(eng.portfolio)


def test_only_as_many_rotations_as_the_shortfall_needs(tmp_path, monkeypatch):
    """Three near-target names, one pick short by Rs 75k: sell ONE, the
    highest-progress one, not all three."""
    eng = _eng(tmp_path, cash=50_000)
    _hold(eng, "A.NS"); _hold(eng, "B.NS"); _hold(eng, "C.NS")
    _quotes(monkeypatch, {"A.NS": 1190.0, "B.NS": 1180.0, "C.NS": 1195.0})
    rotated = eng._rotation_exits(FRESH, NOW)
    assert rotated == ["C.NS"]                       # 97.5% beats 95% and 90%
    assert {"A.NS", "B.NS"} <= set(eng.portfolio.positions)


def test_owner_picks_are_never_rotated(tmp_path, monkeypatch):
    """Same rule the score-decay exit follows: the owner's picks are theirs."""
    eng = _eng(tmp_path, cash=50_000)
    _hold(eng, "MINE.NS", tags=("owner_pick",))
    _quotes(monkeypatch, {"MINE.NS": 1190.0})
    assert eng._rotation_exits(FRESH, NOW) == []
    assert "MINE.NS" in eng.portfolio.positions


def test_no_live_quote_means_no_sale_and_a_journal_line(tmp_path, monkeypatch):
    """The 2026-08-04 rule: never an invented fill — and never a silent skip."""
    eng = _eng(tmp_path, cash=50_000)
    _hold(eng, "NEAR.NS")
    _quotes(monkeypatch, {})                          # feed answered nothing
    assert eng._rotation_exits(FRESH, NOW) == []
    assert "NEAR.NS" in eng.portfolio.positions
    held = next(e for e in eng.portfolio.decision_journal
                if e.get("action") == "ROTATION_HELD_NO_QUOTE")
    assert held["tickers"] == ["NEAR.NS"]


def test_settings_toggle_turns_it_off(tmp_path, monkeypatch):
    eng = _eng(tmp_path, cash=50_000)
    eng.cfg.rotation_auto_enabled = False
    _hold(eng, "NEAR.NS")
    _quotes(monkeypatch, {"NEAR.NS": 1190.0})
    assert eng._rotation_exits(FRESH, NOW) == []
    assert "NEAR.NS" in eng.portfolio.positions


def test_a_position_below_the_progress_bar_is_left_alone(tmp_path, monkeypatch):
    eng = _eng(tmp_path, cash=50_000)
    _hold(eng, "HALF.NS")
    _quotes(monkeypatch, {"HALF.NS": 1100.0})         # 50% of the way
    assert eng._rotation_exits(FRESH, NOW) == []


def test_scan_runs_rotation_before_the_opening_loop():
    """Order is the point: the freed cash must exist when can_open counts.
    Since audit S-4 (2026-10-02) rotation also runs AFTER the gate pass, so
    it funds only picks every gate has cleared (behaviour pinned in
    test_audit_s4_s9_2026_10_02.py)."""
    src = "\n".join(ln for ln in
                    inspect.getsource(TradingEngine.scan).splitlines()
                    if not ln.lstrip().startswith("#"))
    rot = src.index("self._rotation_exits(")
    assert src.index("for pick in picks:") < rot < src.index("for pick in cleared:")
    assert rot < src.index("can_open(")
