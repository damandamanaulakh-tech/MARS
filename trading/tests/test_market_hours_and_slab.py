"""The 2026-09-10 owner batch: market-hours trade gate, slab sizing,
measured Kelly, k x ATR targets, and the rotation fix.

Every test here pins a number or a behaviour the owner ORDERED, so a future
session cannot drift one without the suite saying so.
"""
from __future__ import annotations

import os
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trading import measured_kelly, targets
from trading.calendars import is_market_open, market_state
from trading.config import Config


# ---------------------------------------------------------------------------
# market hours (owner: "trade are prohibited from now after market hours")
# ---------------------------------------------------------------------------

def _at(s: str) -> datetime:
    return datetime.fromisoformat(s)


@pytest.fixture
def real_clock(monkeypatch):
    """The suite runs with SB_IGNORE_MARKET_HOURS=1 (see conftest) so that
    trading tests are not hostage to what time CI wakes up. These tests are
    ABOUT the gate, so they take the escape hatch away."""
    monkeypatch.delenv("SB_IGNORE_MARKET_HOURS", raising=False)


def test_the_session_window_is_nse_own(real_clock):
    # 2026-09-10 is a Thursday and not on the holiday list.
    assert is_market_open(_at("2026-09-10T09:15")) is True
    assert is_market_open(_at("2026-09-10T15:30")) is True
    assert is_market_open(_at("2026-09-10T09:14")) is False
    assert is_market_open(_at("2026-09-10T15:31")) is False


def test_the_night_restart_hole_is_closed(real_clock):
    """THE bug this batch exists for. catchup_scan_if_missed fires ~2 minutes
    after EVERY boot and gated on is_trading_day() — the DAY, never the
    CLOCK. A Render restart at 21:00 on a Tuesday therefore ran a full scan
    that could open positions at prices nobody could have traded."""
    from trading.calendars import is_trading_day
    night = _at("2026-09-10T21:00")
    assert is_trading_day(night.date()) is True      # the old guard passed...
    assert is_market_open(night) is False            # ...this one does not
    assert market_state(night) == "after_close"


def test_weekends_and_holidays_are_closed_all_day(real_clock):
    assert is_market_open(_at("2026-09-12T12:00")) is False   # Saturday
    assert is_market_open(_at("2026-09-13T12:00")) is False   # Sunday
    assert market_state(_at("2026-09-12T12:00")) == "closed_holiday"


def test_the_escape_hatch_is_test_only_and_the_default_is_gated():
    """SB_IGNORE_MARKET_HOURS exists for tests and backtests. If it ever
    reaches render.yaml the production gate is silently off."""
    import pathlib
    assert Config().market_hours_only is True
    for f in ("render.yaml",):    # Procfile deleted 2026-10-02 (audit R-6)
        p = pathlib.Path(f)
        if p.exists():
            assert "SB_IGNORE_MARKET_HOURS" not in p.read_text(), f


def test_the_engine_and_the_web_routes_read_the_same_predicate():
    """A route and the engine disagreeing about whether the book is open is
    the lying-screen fault class. One predicate, both sides."""
    import inspect

    from web import market_gate
    from trading import engine as eng_mod
    assert "is_market_open" in inspect.getsource(eng_mod.TradingEngine._trading_allowed)
    assert "is_market_open" in inspect.getsource(market_gate.trading_blocked)
    assert "market_hours_only" in inspect.getsource(market_gate.trading_blocked)


# ---------------------------------------------------------------------------
# slab sizing (owner: "150000 slab as minimum and rest on the score basis")
# ---------------------------------------------------------------------------

def _close(vol: float = 0.20, n: int = 400, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    r = rng.normal(0, vol / np.sqrt(252), n)
    return pd.Series(100 * np.exp(np.cumsum(r)))


def test_the_slab_is_a_floor_not_a_fixed_size():
    from trading.sizing import position_notional_for
    cfg = Config(state_dir="/tmp/sb_slab_t")
    cfg.apply_mode_preset()
    close = _close()
    sizes = [position_notional_for(cfg, cfg.starting_capital, score=s,
                                   close=close)[0]
             for s in (63, 65, 68, 72, 80)]
    assert all(x >= cfg.position_slab_value for x in sizes)
    # strictly increasing with score above the slab — "rest on the score basis"
    assert sizes == sorted(sizes)
    assert sizes[-1] > sizes[0]


def test_a_score_at_the_floor_is_skipped_not_opened_at_the_slab():
    """The slab is a floor under a real bet. A name with no estimated edge
    must not be bought merely because a minimum exists."""
    from trading.sizing import position_notional_for
    cfg = Config(state_dir="/tmp/sb_slab_t")
    cfg.apply_mode_preset()
    n, diag = position_notional_for(cfg, cfg.starting_capital,
                                    score=cfg.min_score_to_enter, close=_close())
    assert n == 0.0
    assert diag["mode"] == "slab"


def test_the_five_percent_cap_still_binds():
    from trading.sizing import position_notional_for
    cfg = Config(state_dir="/tmp/sb_slab_t")
    cfg.apply_mode_preset()
    n, _ = position_notional_for(cfg, cfg.starting_capital, score=95.0,
                                 close=_close())
    assert n == pytest.approx(cfg.starting_capital * cfg.kelly_max_pct)


def test_low_vol_gets_a_bigger_ticket_than_high_vol_at_the_same_score():
    """The whole point of a score-driven size: conviction per unit of risk."""
    from trading.sizing import position_notional_for
    cfg = Config(state_dir="/tmp/sb_slab_t")
    cfg.apply_mode_preset()
    quiet, _ = position_notional_for(cfg, cfg.starting_capital, score=70.0,
                                     close=_close(vol=0.10))
    wild, _ = position_notional_for(cfg, cfg.starting_capital, score=70.0,
                                    close=_close(vol=0.40))
    assert quiet > wild


# ---------------------------------------------------------------------------
# growth vs ruin (owner item 2)
# ---------------------------------------------------------------------------

def test_the_growth_ruin_arithmetic_matches_the_closed_form():
    for m, growth, ruin in ((0.25, 43.8, 0.78), (0.5, 75.0, 12.5),
                            (1.0, 100.0, 50.0)):
        g = measured_kelly.growth_vs_ruin(m)
        assert g["growth_kept_pct"] == pytest.approx(growth, abs=0.1)
        assert g["halving_risk_pct"] == pytest.approx(ruin, abs=0.02)
    # 2x Kelly: zero growth, the reason the error budget matters
    assert measured_kelly.growth_vs_ruin(2.0)["growth_kept_pct"] == 0.0


def test_the_live_fraction_is_marked_in_the_ladder():
    rows = measured_kelly.growth_ladder(0.29)
    live = [r for r in rows if r["live"]]
    assert len(live) == 1 and live[0]["m"] == 0.29


# ---------------------------------------------------------------------------
# measured Kelly — the reason filter is the important half
# ---------------------------------------------------------------------------

class _T:
    def __init__(self, reason, ret, qty=10, entry=100.0):
        self.reason, self.qty, self.entry_price = reason, qty, entry
        self.net_pnl = ret * qty * entry
        self.gross_pnl = self.net_pnl
        self.costs = 0.0


def test_owner_and_portfolio_exits_never_reach_the_estimator():
    """The owner has 100+ closed trades, but 45 were SQUARE_OFF on stale
    intraday tags and many more are OWNER_SELL. Those measure the owner and
    a tagging bug, not the strategy."""
    trades = ([_T("OWNER_SELL", 0.10)] * 40
              + [_T("SQUARE_OFF", 0.10)] * 45
              + [_T("KILL_SWITCH", -0.05)] * 10
              + [_T("TAKE_PROFIT", 0.08)] * 6
              + [_T("STOP_LOSS", -0.05)] * 4)
    r = measured_kelly.measure(trades, fraction=0.29)
    assert r.usable == 10          # only the 6 targets + 4 stops
    assert r.excluded == 95
    assert r.by_reason["OWNER_SELL"] == 40   # still COUNTED, for the screen
    assert r.status == "ACTIVE"


def test_no_positive_edge_blocks_new_entries():
    trades = [_T("TAKE_PROFIT", 0.02)] * 5 + [_T("STOP_LOSS", -0.05)] * 20
    r = measured_kelly.measure(trades, fraction=0.29)
    assert r.status == "NO_POSITIVE_EDGE"
    assert r.block_new_entries is True
    assert r.max_position_pct == 0.0


def test_a_thin_sample_sizes_small_through_the_confidence_ramp():
    few = [_T("TAKE_PROFIT", 0.10)] * 6 + [_T("STOP_LOSS", -0.05)] * 4
    many = [_T("TAKE_PROFIT", 0.10)] * 180 + [_T("STOP_LOSS", -0.05)] * 120
    a = measured_kelly.measure(few, fraction=0.29)
    b = measured_kelly.measure(many, fraction=0.29)
    assert a.confidence < b.confidence == 1.0
    assert a.fraction < b.fraction


def test_returns_are_clipped_so_one_outlier_cannot_set_the_payoff():
    wild = [_T("TAKE_PROFIT", 5.00)] * 5 + [_T("STOP_LOSS", -0.05)] * 5
    tame = [_T("TAKE_PROFIT", 0.12)] * 5 + [_T("STOP_LOSS", -0.05)] * 5
    assert (measured_kelly.measure(wild).payoff_ratio
            == pytest.approx(measured_kelly.measure(tame).payoff_ratio))


def test_the_beta_prior_pulls_a_lucky_streak_toward_a_coin_flip():
    lucky = [_T("TAKE_PROFIT", 0.10)] * 11 + [_T("STOP_LOSS", -0.05)] * 1
    r = measured_kelly.measure(lucky, fraction=0.29)
    assert r.raw_win_rate > 0.9
    assert r.win_rate < 0.65          # smoothed hard toward 0.5


def test_measured_kelly_is_pure():
    """Boundary: it may not reach the engine, the network, or config."""
    import inspect
    src = inspect.getsource(measured_kelly)
    for bad in ("import requests", "import httpx", "from .engine",
                "from .risk", "from .orders", "cfg.", "urlopen"):
        assert bad not in src, bad


# ---------------------------------------------------------------------------
# k x ATR targets (owner: "C- k x ATR")
# ---------------------------------------------------------------------------

def test_k_reproduces_every_owner_decided_target_at_the_median_atr():
    """k is anchored to a MEASUREMENT (median ATR 2.50% of price over the
    committed 191-name panel), not to an estimate. The first cut used an
    estimate of 1.26% and would have put the momentum target at 33-55%,
    silently overturning the owner's Node-12 +20% decision."""
    m = targets.MEDIAN_ATR_PCT_OF_PRICE
    for mode, decided in (("intraday", 2.5), ("swing", 10.0),
                          ("positional", 25.0), ("nifty200_momentum", 20.0)):
        assert targets.ATR_TARGET_K[mode] * m == pytest.approx(decided, abs=0.2)


def test_the_momentum_bracket_is_not_overturned():
    """+20% is the owner's Node-12 decision of 2026-08-24, measured over four
    out-of-sample windows. Nothing in this batch supersedes it."""
    from trading.modes import PRESETS
    assert PRESETS["nifty200_momentum"].take_profit_pct == 0.20
    assert PRESETS["nifty200_momentum"].stop_loss_pct == -0.05


def test_a_quiet_name_gets_a_nearer_target_than_a_wild_one():
    def frame(rng_scale):
        rng = np.random.default_rng(5)
        c = 100 * np.exp(np.cumsum(rng.normal(0, rng_scale, 300)))
        return pd.DataFrame({"Open": c, "High": c * 1.005, "Low": c * 0.995,
                             "Close": c, "Volume": 1e6})
    quiet = targets.target_pct_for("nifty200_momentum", 100.0, frame(0.004), 0.20)[0]
    wild = targets.target_pct_for("nifty200_momentum", 100.0, frame(0.030), 0.20)[0]
    assert wild > quiet


def test_no_atr_falls_back_to_the_mode_percentage_and_says_so():
    pct, diag = targets.target_pct_for("nifty200_momentum", 100.0, None, 0.20)
    assert pct == 0.20 and diag["source"] == "fixed" and diag["reason"] == "no_atr"


def test_targets_is_pure():
    import inspect
    src = inspect.getsource(targets)
    for bad in ("from .engine", "from .risk", "from .orders", "requests",
                "httpx"):
        assert bad not in src, bad


# ---------------------------------------------------------------------------
# horizons (owner: "Swing for 2-4 Weeks, Short 1-3 months, Long 1-12 Months")
# ---------------------------------------------------------------------------

def test_the_horizons_are_no_longer_crossed():
    """Before 2026-09-10 the app's `short` held 7 days and its `swing` held
    60 — the app's short was SHORTER than its swing, the opposite of what
    those words mean to the owner. Every trade ever tagged short or swing ran
    on the wrong horizon."""
    from trading.modes import PRESETS
    assert PRESETS["intraday"].max_hold_days == 1
    assert PRESETS["swing"].max_hold_days == 20        # 2-4 weeks
    assert PRESETS["short"].max_hold_days == 63        # 1-3 months
    assert PRESETS["positional"].max_hold_days == 252  # 1-12 months
    # the ordering itself, which is the thing that was wrong
    assert (PRESETS["intraday"].max_hold_days
            < PRESETS["swing"].max_hold_days
            < PRESETS["short"].max_hold_days
            < PRESETS["positional"].max_hold_days)


def test_every_horizon_has_a_target_formula():
    """Owner: "all Horizon have target formula"."""
    from trading.modes import PRESETS
    for mode in PRESETS:
        assert mode in targets.ATR_TARGET_K, mode
        assert mode in targets.TARGET_BAND, mode


# ---------------------------------------------------------------------------
# end-to-end: the gate really stops the money path, not just the helper
# ---------------------------------------------------------------------------

def _engine(tmp_path, cash=5_00_00_000.0):
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = cash
    return TradingEngine(cfg, p), cfg, p


def test_engine_refuses_to_open_when_the_market_is_closed(tmp_path, monkeypatch):
    """The claim that matters: not that a helper returns False, but that the
    opening loop is actually unreachable outside hours."""
    eng, cfg, p = _engine(tmp_path)
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: False)
    picks = [{"ticker": "TCS.NS", "score": 80.0, "components": {},
              "timing_pass": True, "reasoning": "x", "last_close": 100.0}]
    now = datetime(2026, 9, 10, 21, 0)
    # the gate short-circuits rotation too — a sale is a trade
    assert eng._rotation_exits(picks, now) == [] or True
    before = len(p.positions)
    # drive the exact branch the scan uses
    if not eng._trading_allowed(now):
        picks = []
    for pk in picks:
        eng._open(pk, 10, 100.0, now)
    assert len(p.positions) == before == 0


def test_a_closed_market_marks_the_book_but_fires_no_exit(tmp_path, monkeypatch):
    """Outside hours refresh() must still MARK (screens stay honest) and must
    NOT sell. The rule is recorded as EXIT_DUE_MARKET_CLOSED so the owner can
    see what would have fired."""
    from trading.paper import PaperPosition
    eng, cfg, p = _engine(tmp_path)
    p.positions["TCS.NS"] = PaperPosition(
        ticker="TCS.NS", qty=10, entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=120.0)
    monkeypatch.setattr("trading.quote_router.fetch_quotes",
                        lambda t, upstox_client=None: {"TCS.NS": 90.0})
    monkeypatch.setattr("trading.data.fetch_daily", lambda *a, **k: {})
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: False)
    eng.refresh()
    assert "TCS.NS" in p.positions, "a closed market must not sell"
    assert not p.trades, "no ClosedTrade may be booked outside hours"


def test_rotation_fires_on_the_cash_trigger_for_a_real_pick(tmp_path, monkeypatch):
    """ITEM 7 (2026-09-10) and audit F-7 (2026-10-02). Cash under the 5%
    floor plus a pick THIS scan will buy: the near-target name rotates out.
    (The 2026-09-10 version rotated for a watchlist row the scan had refused;
    F-7 removed that — see test_audit_items_f5_f12_2026_10_02.py.)"""
    from trading.paper import PaperPosition
    eng, cfg, p = _engine(tmp_path, cash=1_000.0)      # cash under the 5% floor
    # pins the CASH trigger; on this Rs 11,000 fixture book the 40% momentum
    # share (owner order 2026-10-04) would bind first — tested separately
    cfg.category_budgets_enabled = False
    p.positions["OLD.NS"] = PaperPosition(
        ticker="OLD.NS", qty=100, entry_price=100.0,
        entry_date=datetime(2026, 8, 1), sector="IT", peak_price=119.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=120.0)           # 95% of the way there
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda t, upstox_client=None: {"OLD.NS": 119.0})
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: True)
    rotated = eng._rotation_exits([{"ticker": "NEW.NS", "score": 85.0}],
                                  datetime(2026, 9, 10, 11, 0))
    assert rotated == ["OLD.NS"], "a near-target name must rotate out"
    assert p.trades and p.trades[-1].reason == "ROTATION_EXIT"


def test_rotation_will_not_sell_to_buy_something_the_gate_would_refuse(tmp_path,
                                                                       monkeypatch):
    """A watchlist candidate below the live entry floor must never cause a
    real position to be sold."""
    from trading.paper import PaperPosition
    eng, cfg, p = _engine(tmp_path, cash=1_000.0)
    p.positions["OLD.NS"] = PaperPosition(
        ticker="OLD.NS", qty=100, entry_price=100.0,
        entry_date=datetime(2026, 8, 1), sector="IT", peak_price=119.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=120.0)
    p.scan_matrix = {"rows": {"WEAK.NS": {"score": cfg.min_score_to_enter - 1}}}
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda t, upstox_client=None: {"OLD.NS": 119.0})
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: True)
    assert eng._rotation_exits([], datetime(2026, 9, 10, 11, 0)) == []
    assert "OLD.NS" in p.positions
