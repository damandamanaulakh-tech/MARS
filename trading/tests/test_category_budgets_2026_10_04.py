"""Owner order 2026-10-04: "fix it so all 5 categories get cash", split
picked from the options card: momentum 40%, intraday / short / swing /
positional 15% each.

The 09:20 rotation scans momentum first; a replay of the live machine on a
strong week opened 46 momentum trades and none in the other four. Each
category now spends only its own share of the deployable capital.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from trading.risk import RiskState, can_open
from trading.tests.test_audit_s4_s9_2026_10_02 import _near_target, _scan_engine

MOM = "nifty200_momentum"
BOOK = 5_00_00_000.0


def _pos(ticker, rupees, mode=MOM, tags=None, price=100.0):
    return PaperPosition(
        ticker=ticker, qty=int(rupees // price), entry_price=price,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=price,
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=price * 0.95, target_price=price * 1.2,
        tags=list(tags or []))


def _cfg(tmp_path=None):
    cfg = Config(state_dir=str(tmp_path or "/tmp/sb_cat_budget"), mode=MOM)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.0
    return cfg


def _book(momentum_rupees):
    return {"M.NS": _pos("M.NS", momentum_rupees)}


# ------------------------------------------------------------- the numbers

def test_the_owner_split_is_the_default():
    from trading import category_budget as cb
    cfg = Config()
    assert cb.enabled(cfg)
    assert cb.share(cfg, MOM) == pytest.approx(0.40)
    for m in ("intraday", "short", "swing", "positional"):
        assert cb.share(cfg, m) == pytest.approx(0.15), m
    assert cb.share_total(cfg) == pytest.approx(1.0)
    assert cb.share(cfg, "ocean") is None          # Ocean is exempt


def test_a_position_belongs_to_the_category_it_was_opened_under():
    from trading import category_budget as cb
    assert cb.category_of(_pos("A", 1e5, mode="swing")) == "swing"
    assert cb.category_of(_pos("A", 1e5, mode="")) == MOM   # legacy blank
    assert cb.category_of(_pos("A", 1e5, mode=MOM, tags=["ocean"])) == "ocean"


def test_the_reserve_shrinks_every_share_alike():
    """A correction's 20% reserve must not starve the last category in
    line: shares are of DEPLOYABLE capital, so all five shrink together."""
    from trading import category_budget as cb
    cfg = _cfg()
    cfg.cash_reserve_pct = 0.20
    assert cb.limit(cfg, MOM, BOOK) == pytest.approx(0.40 * 0.80 * BOOK)
    assert cb.limit(cfg, "positional", BOOK) == pytest.approx(0.15 * 0.80 * BOOK)
    total = sum(cb.limit(cfg, c, BOOK) for c in cb.SHARE_FIELDS)
    assert total == pytest.approx(0.80 * BOOK)


# ---------------------------------------------------------------- the gate

def _gate(cfg, positions, category, cash, want=4_00_000.0, price=100.0):
    risk = RiskState(cfg=cfg, starting_capital=BOOK)
    return can_open(risk, cash, BOOK, positions, "NEW.NS", price, cfg,
                    notional_override=want, category=category)


def test_a_full_momentum_share_refuses_a_momentum_entry():
    cfg = _cfg()
    book = _book(2_05_00_000.0)                   # past the Rs 2 crore share
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 2_05_00_000.0)
    assert not ok and why == "category_budget_full"


def test_the_other_categories_still_get_cash():
    """The point of the order: momentum full, swing still buys."""
    cfg = _cfg()
    book = _book(2_05_00_000.0)
    for cat in ("intraday", "short", "swing", "positional"):
        ok, why, qty = _gate(cfg, book, cat, cash=BOOK - 2_05_00_000.0)
        assert ok, (cat, why)
        assert qty == 4_000


def test_the_share_left_caps_the_size():
    cfg = _cfg()
    book = _book(1_98_00_000.0 - 0.0)             # Rs 2 lakh of 2 crore left
    ok, why, qty = _gate(cfg, book, MOM, cash=3_00_00_000.0)
    assert ok
    assert qty * 100.0 == pytest.approx(2_00_000.0)


def test_a_remainder_under_the_slab_is_a_full_share():
    cfg = _cfg()
    assert cfg.sizing_mode == "slab"
    book = _book(1_99_00_000.0)                   # Rs 1 lakh left < 1.5L slab
    ok, why, _ = _gate(cfg, book, MOM, cash=3_00_00_000.0)
    assert not ok and why == "category_budget_full"


def test_ocean_and_the_switch_bypass_the_shares():
    cfg = _cfg()
    book = _book(2_05_00_000.0)
    ok, _, _ = _gate(cfg, book, None, cash=BOOK - 2_05_00_000.0)   # Ocean
    assert ok
    cfg.category_budgets_enabled = False
    ok, _, _ = _gate(cfg, book, MOM, cash=BOOK - 2_05_00_000.0)
    assert ok


def test_ocean_positions_spend_no_category_share():
    cfg = _cfg()
    book = {"OC.NS": _pos("OC.NS", 2_10_00_000.0, mode=MOM, tags=["ocean"])}
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 2_10_00_000.0)
    assert ok, why


# -------------------------------------------------------------- the engine

def _momentum_full_engine(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.positions.clear()
    e.portfolio.positions["HELD.NS"] = _pos("HELD.NS", 2_10_00_000.0)
    e.portfolio.cash = BOOK - 2_10_00_000.0
    return e


def test_the_scan_refuses_momentum_once_its_share_is_spent(tmp_path, monkeypatch):
    e = _momentum_full_engine(tmp_path, monkeypatch)
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") == "category_budget_full"
    mine = e.portfolio.decision_journal[n0:]
    full = [x for x in mine if x.get("action") == "CATEGORY_BUDGET_FULL"]
    assert full and full[0]["category"] == MOM
    assert full[0]["refused"] == ["UPP.NS"]


def _open_swing_gates(e, monkeypatch):
    import trading.scoring as S
    import trading.signals.news as N
    monkeypatch.setattr(N, "fetch_sentiment",
                        lambda t: type("x", (), {"score": 50.0})())
    monkeypatch.setattr(S, "fetch_earnings_growth", lambda t: None)
    monkeypatch.setattr(S, "_analyst_revision_score", lambda t: 50.0)
    monkeypatch.setattr(S, "_short_interest_score", lambda t, c: 50.0)
    e.cfg.mode = "swing"
    e.cfg.apply_mode_preset()
    e.cfg.min_score_to_enter = 0.0
    e.cfg.breakout_threshold_pct = 1.0
    e.cfg.volume_confirm_ratio = 0.0
    e.cfg.candle_confirm_enabled = False


def test_a_swing_pass_still_buys_while_momentum_is_full(tmp_path, monkeypatch):
    """Before the order, cash was one pot and the category that ran first
    could spend all of it; now swing has its own 15%."""
    e = _momentum_full_engine(tmp_path, monkeypatch)
    _open_swing_gates(e, monkeypatch)
    e.scan()
    assert "UPP.NS" in e.portfolio.positions
    pos = e.portfolio.positions["UPP.NS"]
    assert pos.mode == "swing"
    assert pos.qty * pos.entry_price <= 0.15 * BOOK * 1.001


def test_momentum_cannot_spend_past_its_share_in_one_pass(tmp_path, monkeypatch):
    """Rs 1.9 crore spent of the Rs 2 crore share: the pick opens at the
    Rs 10 lakh that is left, never more, whatever its ladder size."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.positions.clear()
    e.portfolio.positions["HELD.NS"] = _pos("HELD.NS", 1_90_00_000.0)
    e.portfolio.cash = BOOK - 1_90_00_000.0
    e.scan()
    from trading import category_budget as cb
    spent = cb.used(e.portfolio.positions.values(), MOM)
    assert spent <= 0.40 * BOOK * 1.001


# ------------------------------------------------------------- rotation

def test_rotation_never_sells_another_category_for_a_full_share(tmp_path,
                                                                monkeypatch):
    """Momentum full, cash plentiful, a SWING holding near target: selling it
    frees cash momentum still cannot spend. Nothing is sold."""
    e = _momentum_full_engine(tmp_path, monkeypatch)
    # HELD.NS (momentum, 0% to target, no live quote in the fixture) is not
    # rotatable. It used to carry an owner_pick tag here; since the
    # 2026-10-21 owner bucket that tag would move it OUT of momentum's share
    # and the share would no longer be full, so the tag is gone and the
    # "why" line is the one the refused pick writes (CATEGORY_BUDGET_FULL).
    sw = _near_target("SWG.NS")
    sw.mode = "swing"
    e.portfolio.positions["SWG.NS"] = sw
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    assert "SWG.NS" in e.portfolio.positions
    assert "HELD.NS" in e.portfolio.positions
    assert not [t for t in e.portfolio.trades if t.reason == "ROTATION_EXIT"]
    # and it says why — the share, not the cash, was short
    mine = e.portfolio.decision_journal[n0:]
    line = [x for x in mine if x.get("action") == "CATEGORY_BUDGET_FULL"]
    assert line and line[0]["category"] == MOM
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") == "category_budget_full"


def test_rotation_may_sell_within_the_category_to_free_its_share(tmp_path,
                                                                 monkeypatch):
    """Momentum full and a MOMENTUM holding near target: selling it frees
    momentum's own share, which is exactly what rotation is for."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.positions.clear()
    old = _near_target("OLD.NS")
    old.qty = 10_000
    e.portfolio.positions["OLD.NS"] = old                       # Rs 10 lakh
    e.portfolio.positions["HELD.NS"] = _pos("HELD.NS", 1_95_00_000.0)
    e.portfolio.cash = BOOK - 1_95_00_000.0 - 10_00_000.0
    e.scan()                     # share Rs 2 Cr, spent 2.05 Cr: 5 lakh over
    rot = [t.ticker for t in e.portfolio.trades if t.reason == "ROTATION_EXIT"]
    assert rot == ["OLD.NS"]
    line = [x for x in e.portfolio.decision_journal
            if x.get("action") == "ROTATION_EXIT"][-1]
    assert line["short_of"] == "category_share"
    assert "UPP.NS" in e.portfolio.positions       # and the pick really bought


def test_rotation_never_sells_when_it_cannot_make_room(tmp_path, monkeypatch):
    """Over the share by more than the near-target holding's cost: selling
    it would still leave no room, so nothing is sold (the S-4 rule)."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.positions.clear()
    old = _near_target("OLD.NS")
    old.qty = 1_000                                             # Rs 1 lakh
    e.portfolio.positions["OLD.NS"] = old
    e.portfolio.positions["HELD.NS"] = _pos("HELD.NS", 2_10_00_000.0)
    e.portfolio.cash = BOOK - 2_10_00_000.0 - 1_00_000.0
    n0 = len(e.portfolio.decision_journal)
    e.scan()
    assert "OLD.NS" in e.portfolio.positions
    assert not [t for t in e.portfolio.trades if t.reason == "ROTATION_EXIT"]
    mine = e.portfolio.decision_journal[n0:]
    assert [x for x in mine if x.get("action") == "ROTATION_BUDGET_FULL"]


def test_rotation_frees_the_share_at_entry_cost_and_stops(tmp_path,
                                                          monkeypatch):
    """Three near-target momentum names; selling the best one frees enough
    of the share (at its ENTRY cost, the share's own measure), so only it
    goes."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.positions.clear()
    for i, t in enumerate(("A.NS", "B.NS", "C.NS")):
        x = _near_target(t)
        x.qty = 30_000                                          # Rs 30 lakh each
        e.portfolio.positions[t] = x
    e.portfolio.positions["HELD.NS"] = _pos("HELD.NS", 1_15_00_000.0)
    e.portfolio.cash = BOOK - 1_15_00_000.0 - 90_00_000.0
    import trading.engine as E
    q = {"A.NS": 119.0, "B.NS": 118.0, "C.NS": 119.5, "UPP.NS": 150.0}
    real = E.routed_fetch_quotes
    monkeypatch.setattr(E, "routed_fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: v for t in tickers
                         for v in [q.get(t) or real([t]).get(t)] if v})
    e.scan()                     # share 2 Cr, spent 2.05 Cr; one sale = 30L
    rot = [t.ticker for t in e.portfolio.trades if t.reason == "ROTATION_EXIT"]
    assert rot == ["C.NS"]                         # 97.5% beats 95% and 90%


# ------------------------------------------------------------- the page

def test_the_slot_card_counts_the_category_share():
    from trading.slots import slot_view
    cfg = _cfg()
    p = Portfolio.fresh(cfg)
    p.positions = _book(1_95_00_000.0)            # Rs 5 lakh of 2 crore left
    p.cash = BOOK - 1_95_00_000.0
    picks = [{"ticker": f"P{i}.NS", "score": 63.0} for i in range(10)]
    v = slot_view(p, cfg, picks=picks, category=MOM)
    assert v.category_left == pytest.approx(5_00_000.0)
    assert v.budget_binds and v.limiting == "category_budget"
    assert v.free == 3                             # 3 x Rs 1.5 lakh slab
    w = slot_view(p, cfg, picks=picks, category="swing")
    assert w.category_left == pytest.approx(75_00_000.0) and w.free == 10


def test_the_backtest_warning_names_the_gap():
    import ast
    import inspect
    from trading import backtest
    src = inspect.getsource(backtest)
    assert "_category_share" in {n.id for n in ast.walk(ast.parse(src))
                                 if isinstance(n, ast.Name)}


# ------------------------------------------- follow-ups from the exit check

def _danger_engine(tmp_path, monkeypatch, pos):
    from trading import danger
    from trading.engine import TradingEngine
    cfg = _cfg(tmp_path)                           # live mode = momentum
    p = Portfolio.fresh(cfg)
    p.positions = {pos.ticker: pos}
    p.danger_alerts_start = "2026-01-01"           # auto mode long armed
    e = TradingEngine(cfg, p)
    tid = next(iter(danger.VERIFIED))
    monkeypatch.setattr(danger, "evaluate_stock", lambda df, ext=None: [tid])
    monkeypatch.setattr(danger, "externals_for", lambda held: {})
    return e


def _hist(n=80):
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range(end="2026-10-01", periods=n)
    c = np.full(n, 100.0)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def test_the_danger_leash_uses_the_positions_own_stop(tmp_path, monkeypatch):
    """A positional holding (-10% stop) must not be tightened to momentum's
    -5% because the app happens to sit in momentum. At 100 the -10% leash
    gives 90, which is not tighter than its stop, so nothing moves."""
    pos = _pos("POS.NS", 10_00_000.0, mode="positional")
    pos.stop_price = 90.0
    e = _danger_engine(tmp_path, monkeypatch, pos)
    e._danger_pass({"POS.NS": 100.0}, {"POS.NS": _hist()},
                   datetime(2026, 10, 1, 6, 0))
    assert e.portfolio.positions["POS.NS"].stop_price == 90.0
    line = [x for x in e.portfolio.decision_journal
            if str(x.get("action", "")).startswith("DANGER_")
            and x.get("ticker") == "POS.NS"][-1]
    assert line["leash_pct"] == 10.0
    assert line["action"] == "DANGER_ALERT"


def test_a_momentum_holding_still_gets_its_own_leash(tmp_path, monkeypatch):
    pos = _pos("MOM.NS", 10_00_000.0, mode=MOM)
    pos.stop_price = 95.0
    e = _danger_engine(tmp_path, monkeypatch, pos)
    e._danger_pass({"MOM.NS": 110.0}, {"MOM.NS": _hist()},
                   datetime(2026, 10, 1, 6, 0))
    assert e.portfolio.positions["MOM.NS"].stop_price == pytest.approx(104.5)


def test_the_hold_line_names_the_positions_own_rules(tmp_path, monkeypatch):
    """The HOLD text printed the LIVE mode's numbers — a positional holding
    read "max 200d" while the app sat in momentum."""
    from trading.engine import TradingEngine
    import trading.engine as E
    cfg = _cfg(tmp_path)
    p = Portfolio.fresh(cfg)
    pos = _pos("POS.NS", 10_00_000.0, mode="positional")
    pos.stop_price, pos.target_price = 90.0, 120.0
    p.positions = {"POS.NS": pos}
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(E, "routed_fetch_quotes",
                        lambda t, upstox_client=None: {"POS.NS": 101.0})
    monkeypatch.setattr(E, "fetch_daily",
                        lambda t, period="2y": {"POS.NS": _hist(300)})
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: True)
    cfg.danger_layer_enabled = False
    n0 = len(p.decision_journal)
    e.refresh()
    hold = [x for x in p.decision_journal[n0:] if x.get("action") == "HOLD"]
    assert hold, [x.get("action") for x in p.decision_journal[n0:]]
    why = hold[-1]["why"]
    assert "max 252d" in why and "(positional)" in why
    assert "₹90.00" in why and "₹120.00" in why


def test_the_owners_manual_states_the_live_shares():
    """APP_BRIEF must say the split the config really runs."""
    from pathlib import Path
    cfg = Config()
    brief = (Path(__file__).resolve().parents[2] / "docs" / "APP_BRIEF.md").read_text()
    assert f"**{cfg.budget_share_momentum * 100:.0f}%**" in brief
    assert f"**{cfg.budget_share_swing * 100:.0f}% each**" in brief
    assert cfg.budget_share_intraday == cfg.budget_share_short \
        == cfg.budget_share_swing == cfg.budget_share_positional
