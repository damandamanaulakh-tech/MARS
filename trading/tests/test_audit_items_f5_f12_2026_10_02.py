"""Owner order 2026-10-02 ("go for next"), audit items F-5, F-7, F-10,
F-11 and F-12 from docs/AUDIT_2026-10-02.md.

F-5   IFR throttle thresholds were a quantile of their own window (fired at
      a fixed rate, 76% of days on a random walk) and halved the Rs 1.5 lakh
      slab to Rs 75,000.
F-7   Rotation sold a near-target winner to fund a watchlist name the scan
      had refused, and sized the cash need with no price history.
F-10  Two losing closes halted all entries whatever the book size, counting
      owner sells, kill switch and drawdown closes too.
F-11  The weekly universe kept names at Rs 1 crore; the entry gate needs 5.
F-12  A score exactly at the floor got the full slab when no history came
      with it.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trading import ifr
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from trading.risk_v2 import ConsecutiveLossTracker, LiquidityRules
from trading.sizing import position_notional_for


# --------------------------------------------------------------------- F-5

def _panel(names, n=420, vol=0.006, seed=1, crash_from=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    out = {}
    for i, t in enumerate(names):
        r = rng.normal(0.0004, vol, n)
        if crash_from is not None:
            r[crash_from:] = -0.04
        out[t] = pd.DataFrame({"Close": 100 * np.cumprod(1 + r)}, index=idx)
    return out


def test_the_thresholds_are_frozen_measurements_not_a_window_quantile():
    with open(ifr.FROZEN_FILE) as f:
        rec = json.load(f)
    assert ifr.FROZEN["cluster"] == pytest.approx(rec["cluster_threshold_p90"])
    assert ifr.FROZEN["ratio"] == pytest.approx(rec["ratio_threshold_p85"])
    # the reason freezing is honest: the two halves agree
    assert abs(rec["cluster_p90_train"] - rec["cluster_p90_test"]) < 0.01
    assert abs(rec["ratio_p85_train"] - rec["ratio_p85_test"]) < 0.01
    # and on noise the frozen rule is quiet where the old one was not
    assert rec["random_walk_active_old_rolling"] > 0.5
    assert rec["random_walk_active_new_frozen"] == 0.0


def test_the_live_reading_uses_the_frozen_threshold():
    names = sorted(ifr.FROZEN["tickers"])[:60]
    snap = ifr.check_damage_cluster(_panel(names), now=datetime(2025, 8, 1))
    assert snap.threshold_value == pytest.approx(ifr.FROZEN["cluster"])
    assert snap.ratio_threshold == pytest.approx(ifr.FROZEN["ratio"])


def test_a_calm_market_does_not_throttle():
    names = sorted(ifr.FROZEN["tickers"])[:60]
    snap = ifr.check_damage_cluster(_panel(names), now=datetime(2025, 8, 1))
    assert snap.active is False and snap.sizing_multiplier == 1.0


def test_names_outside_the_reference_panel_cannot_move_the_reading():
    """The reading used to move with whatever the scan loaded (49, 443, 1,205
    names). A crash in names outside the reference panel is not read."""
    ref = sorted(ifr.FROZEN["tickers"])[:60]
    others = [f"SMALLCAP{i}.NS" for i in range(300)]
    hist = {**_panel(ref), **_panel(others, seed=2, crash_from=380)}
    snap = ifr.check_damage_cluster(hist, now=datetime(2025, 8, 1))
    assert snap.active is False


def test_a_crash_in_the_reference_panel_still_throttles():
    ref = sorted(ifr.FROZEN["tickers"])[:60]
    snap = ifr.check_damage_cluster(_panel(ref, crash_from=405),
                                    now=datetime(2025, 8, 1))
    assert snap.active is True and snap.sizing_multiplier == ifr.THROTTLE_MULTIPLIER


def test_a_thin_reference_panel_never_throttles():
    ref = sorted(ifr.FROZEN["tickers"])[:ifr.MIN_REFERENCE_NAMES - 1]
    snap = ifr.check_damage_cluster(_panel(ref, crash_from=405),
                                    now=datetime(2025, 8, 1))
    assert snap.active is False
    assert "insufficient reference panel" in snap.reason


def test_a_throttle_never_cuts_below_the_slab():
    slab = 1_50_000.0
    # Rs 1.5 lakh approved at Rs 1,000: half would be Rs 75,000
    assert TradingEngine._throttled_qty(150, 0.5, 1000.0, slab) == 150
    # Rs 6 lakh approved: half is Rs 3 lakh, above the slab, so it stands
    assert TradingEngine._throttled_qty(600, 0.5, 1000.0, slab) == 300
    # Rs 2 lakh approved: half is Rs 1 lakh, so it is lifted to the slab
    assert TradingEngine._throttled_qty(200, 0.5, 1000.0, slab) == 150
    # never more than what the risk check approved
    assert TradingEngine._throttled_qty(100, 0.5, 1000.0, slab) == 50
    # no throttle = no change
    assert TradingEngine._throttled_qty(200, 1.0, 1000.0, slab) == 200


# --------------------------------------------------------------------- F-7

NOW = datetime(2026, 10, 5, 11, 0)


def _eng(tmp_path, cash):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = cash
    eng = TradingEngine(cfg=cfg, portfolio=p)
    eng._trading_allowed = lambda now=None: True
    return eng


def _near_target(eng, qty=1000):
    eng.portfolio.positions["OLD.NS"] = PaperPosition(
        ticker="OLD.NS", qty=qty, entry_price=100.0,
        entry_date=datetime(2026, 8, 1), sector="IT", peak_price=119.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=120.0)


def _quotes(monkeypatch):
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda t, upstox_client=None: {"OLD.NS": 119.0})


def test_rotation_never_sells_for_a_name_this_scan_will_not_buy(tmp_path,
                                                                monkeypatch):
    """Cash is under the 5% floor and the watchlist carries a strong name
    the scan refused (no pick). Before F-7 the winner was sold for it."""
    eng = _eng(tmp_path, cash=1_000.0)
    _near_target(eng)
    eng.portfolio.scan_matrix = {"rows": {"NEW.NS": {"score": 85.0}}}
    _quotes(monkeypatch)
    assert eng._rotation_exits([], NOW) == []
    assert "OLD.NS" in eng.portfolio.positions
    acts = [e.get("action") for e in eng.portfolio.decision_journal]
    assert "ROTATION_NOTHING_TO_FUND" in acts


def _strong_close():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0015, 0.008, 300)
    return pd.Series(100 * np.cumprod(1 + r),
                     index=pd.bdate_range("2025-06-01", periods=300))


def test_the_cash_need_is_sized_with_the_picks_own_history(tmp_path,
                                                           monkeypatch):
    """A strong, calm name gets a slab-Kelly ticket far above the slab.
    Cash covers the slab but not the real ticket: before F-7 rotation read
    every ticket as the slab and did nothing; now it frees the cash."""
    close = _strong_close()
    eng = _eng(tmp_path, cash=0.0)
    _near_target(eng, qty=1_00_000)                # a Rs 1 crore holding
    cfg = eng.cfg
    eq = 1_00_000 * 100.0 + 1_60_000.0
    real, _ = position_notional_for(cfg, eq, score=90.0, close=close)
    slab, _ = position_notional_for(cfg, eq, score=90.0, close=None)
    assert real > slab + 50_000, "fixture must give a ticket above the slab"
    eng.portfolio.cash = slab + 10_000.0          # covers the slab only
    cfg.rotation_cash_trigger_pct = 0.0           # isolate the need path
    _quotes(monkeypatch)
    pick = {"ticker": "NEW.NS", "score": 90.0,
            "history": pd.DataFrame({"Close": close})}
    assert eng._rotation_exits([pick], NOW) == ["OLD.NS"]


# -------------------------------------------------------------------- F-10

def test_owner_and_portfolio_closes_do_not_count_toward_the_halt():
    t = ConsecutiveLossTracker()
    d = date(2026, 10, 5)
    for why in ("OWNER_SELL", "KILL_SWITCH", "DRAWDOWN_LADDER",
                "OWNER_SECTOR_EXIT", "SQUARE_OFF"):
        t.record_close(d, -1000.0, reason=why)
    assert t.losses_today == 0
    assert t.halted(today=d, open_positions=5) is False


def test_strategy_losses_still_halt_a_small_book():
    t = ConsecutiveLossTracker()
    d = date(2026, 10, 5)
    t.record_close(d, -1000.0, reason="STOP_LOSS")
    t.record_close(d, -1000.0, reason="DANGER_EXIT_HALF (ATR Shock)")
    assert t.halted(today=d, open_positions=8) is True


def test_the_limit_grows_with_the_book():
    t = ConsecutiveLossTracker()
    d = date(2026, 10, 5)
    t.record_close(d, -1000.0, reason="STOP_LOSS")
    t.record_close(d, -1000.0, reason="STOP_LOSS")
    # 48 still open + 2 lost = a 50-name day: limit 5
    assert t.halted(today=d, open_positions=48) is False
    assert t.snapshot(open_positions=48)["max_allowed"] == 5
    for _ in range(3):
        t.record_close(d, -1000.0, reason="STOP_LOSS")
    assert t.halted(today=d, open_positions=45) is True


def test_the_engine_passes_reason_and_book_size():
    import inspect
    src = inspect.getsource(TradingEngine)
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "record_close(now.date(), net_pnl, reason=reason)" in code
    assert "open_positions=len(self.portfolio.positions)" in code


# -------------------------------------------------------------------- F-11

def test_the_universe_floor_is_the_entry_gates_own_bar():
    from trading import universe_select as us
    assert us.MIN_TURNOVER == LiquidityRules().min_rupee_volume_5d_cr * 1e7


def test_a_name_the_entry_gate_would_refuse_is_not_scanned():
    from trading.universe_select import select_liquid_universe

    def doc(t, close, vol):
        return {"ticker": t, "candles": [[f"d{i}", close, close, close, close,
                                          vol] for i in range(25)]}
    res = select_liquid_universe([doc("MID.NS", 300.0, 1_00_000),     # 3 Cr
                                  doc("BIG.NS", 500.0, 2_00_000)],    # 10 Cr
                                 target=10)
    assert res["tickers"] == ["BIG.NS"] and res["skipped_illiquid"] == 1


# -------------------------------------------------------------------- F-12

def test_a_score_at_the_floor_is_skipped_even_without_history():
    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    floor = cfg.min_score_to_enter
    rs, diag = position_notional_for(cfg, 5e7, score=floor, close=None)
    assert rs == 0.0 and diag["reason"] == "no_estimated_edge_at_or_below_floor"
    rs, _ = position_notional_for(cfg, 5e7, score=floor + 1, close=None)
    assert rs == cfg.position_slab_value


def test_the_owners_manual_ticket_is_untouched():
    """No score = the owner's own buy: still sized at the slab."""
    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    rs, _ = position_notional_for(cfg, 5e7)
    assert rs == cfg.position_slab_value
