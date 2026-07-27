"""Two audit fixes (2026-07-27), both money-critical, both previously untested.

1. Ocean park switch — cfg.ocean_enabled. Docs called Ocean PARKED since v3 but
   no code enforced it: engine.scan() ran it on regime.ocean_active alone, which
   is True in correction/panic/neutral. In a correction Everest is off, so Ocean
   was the ONLY engine that could open a position, and its picks skip the score
   floor and the absolute-momentum gate.

2. Daily kill-switch baseline — RiskState.roll_day() was only ever called from
   backtest.py, so day_start_equity stayed pinned at starting_capital and the
   "-2% daily" halt was really "-2% since inception", force-closing the book.

Synthetic data only, no network.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from trading.regime import RegimeReading
from trading.risk import RiskState


def _engine(tmp_path, **kw) -> TradingEngine:
    cfg = Config(state_dir=str(tmp_path), **kw)
    return TradingEngine(cfg=cfg, portfolio=Portfolio.fresh(cfg))


def _regime(name="correction", ocean=True, everest=False) -> RegimeReading:
    return RegimeReading(
        timestamp=datetime(2026, 7, 27, 10, 5),
        nifty_close=21000.0, nifty_200dma=23000.0,
        pct_below_52w_high=-14.0, vix=19.0, fii_5d_net=-2500.0,
        regime=name, everest_active=everest, ocean_active=ocean,
        sizing_multiplier_everest=0.0, sizing_multiplier_ocean=1.0,
        reasoning="synthetic regime for test",
    )


class _Candidate:
    """Stands in for engines.ocean.OceanCandidate."""
    ticker = "TESTCO.NS"
    pct_below_200dma = -32.0
    weekly_rsi = 24.0
    climax_volume_ratio = 3.1
    price = 410.0


# --------------------------------------------------------------------------
# 1. Ocean park switch
# --------------------------------------------------------------------------

def test_ocean_is_parked_by_default():
    """The park switch the docs always claimed existed."""
    assert Config().ocean_enabled is False


def test_ocean_blocked_in_correction_and_journalled(tmp_path, monkeypatch):
    """Correction + Ocean parked = ocean.scan never runs, and it says so."""
    eng = _engine(tmp_path)
    called = []
    monkeypatch.setattr("trading.engines.ocean.scan",
                        lambda *a, **k: called.append(1) or [])

    picks = eng._ocean_picks(_regime(), {}, [], datetime.utcnow())

    assert picks == []
    assert called == []                      # the engine itself never ran
    blocked = [e for e in eng.portfolio.decision_journal
               if e.get("action") == "OCEAN_BLOCKED"]
    assert len(blocked) == 1                 # fail-visible, not silent
    assert "parked" in blocked[0]["why"]
    assert "correction" in blocked[0]["why"]


def test_ocean_blocked_in_panic_and_neutral_too(tmp_path, monkeypatch):
    """regime.classify() sets ocean_active for all three — gate all three."""
    monkeypatch.setattr("trading.engines.ocean.scan",
                        lambda *a, **k: [_Candidate()])
    for name in ("panic", "neutral"):
        eng = _engine(tmp_path)
        picks = eng._ocean_picks(_regime(name), {}, [], datetime.utcnow())
        assert picks == [], f"{name} regime leaked an Ocean pick"


def test_ocean_runs_when_owner_switches_it_on(tmp_path, monkeypatch):
    """Flipping cfg.ocean_enabled restores the previous behaviour exactly."""
    eng = _engine(tmp_path, ocean_enabled=True)
    monkeypatch.setattr("trading.engines.ocean.scan",
                        lambda *a, **k: [_Candidate()])

    picks = eng._ocean_picks(_regime(), {}, [], datetime.utcnow())

    assert len(picks) == 1
    p = picks[0]
    assert p["ticker"] == "TESTCO.NS"
    assert p["ocean"] is True
    assert p["score"] == 100 + (-32.0)       # deepest faller ranks highest
    assert p["timing_pass"] is True
    assert not [e for e in eng.portfolio.decision_journal
                if e.get("action") == "OCEAN_BLOCKED"]


def test_no_regime_means_no_ocean(tmp_path, monkeypatch):
    """regime=None (regime read failed) must not activate Ocean or journal."""
    eng = _engine(tmp_path, ocean_enabled=True)
    monkeypatch.setattr("trading.engines.ocean.scan",
                        lambda *a, **k: [_Candidate()])

    assert eng._ocean_picks(None, {}, [], datetime.utcnow()) == []
    assert eng.portfolio.decision_journal == []


def test_bull_regime_does_not_journal_a_block(tmp_path):
    """Ocean inactive is the normal case — don't spam the journal."""
    eng = _engine(tmp_path)
    picks = eng._ocean_picks(_regime("bull_extension", ocean=False, everest=True),
                             {}, [], datetime.utcnow())
    assert picks == []
    assert eng.portfolio.decision_journal == []


# --------------------------------------------------------------------------
# 2. Daily kill-switch baseline
# --------------------------------------------------------------------------

def test_baseline_starts_at_starting_capital_before_any_roll():
    """The old behaviour, pinned so a regression is loud."""
    cfg = Config(starting_capital=25_00_000)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    assert risk.day_start_equity == 25_00_000


def test_roll_rebaselines_to_yesterdays_close(tmp_path):
    """A -3% cumulative drawdown must NOT trip a -2% *daily* halt."""
    eng = _engine(tmp_path, starting_capital=25_00_000, daily_loss_halt_pct=-0.02)
    eng.portfolio.equity_history = [("2026-07-26T10:05:00", 24_25_000.0)]

    eng._roll_risk_day(24_25_000.0, datetime(2026, 7, 27, 10, 5))

    assert eng.portfolio.risk_day == "2026-07-27"
    assert eng.portfolio.risk_day_equity == 24_25_000.0
    assert eng.risk.day_start_equity == 24_25_000.0
    assert not eng.risk.check_kill_switch(24_25_000.0)   # flat today = no halt


def test_roll_still_halts_on_a_real_2pct_day(tmp_path):
    """The protection itself must survive the fix."""
    eng = _engine(tmp_path, starting_capital=25_00_000, daily_loss_halt_pct=-0.02)
    eng.portfolio.equity_history = [("2026-07-26T10:05:00", 24_00_000.0)]

    eng._roll_risk_day(24_00_000.0, datetime(2026, 7, 27, 10, 5))

    assert not eng.risk.check_kill_switch(23_52_001.0)   # just above -2%
    assert eng.risk.check_kill_switch(23_52_000.0)       # -2% on the day


def test_roll_is_once_per_day_not_once_per_refresh(tmp_path):
    """15:35 runs after 09:20; the second call must not move the baseline."""
    eng = _engine(tmp_path, starting_capital=25_00_000)
    eng.portfolio.equity_history = [("2026-07-26T10:05:00", 25_00_000.0)]
    day = datetime(2026, 7, 27, 10, 5)

    eng._roll_risk_day(25_00_000.0, day)
    eng._roll_risk_day(23_00_000.0, day + timedelta(hours=5))   # big intraday drop

    assert eng.risk.day_start_equity == 25_00_000.0             # unmoved
    rolls = [e for e in eng.portfolio.decision_journal
             if e.get("action") == "RISK_DAY_ROLL"]
    assert len(rolls) == 1                                      # journalled once


def test_restart_mid_day_restores_the_stored_baseline(tmp_path):
    """Render redeploys mid-day; RiskState is rebuilt, the baseline must not be."""
    eng = _engine(tmp_path, starting_capital=25_00_000)
    day = datetime(2026, 7, 27, 10, 5)
    eng.portfolio.equity_history = [("2026-07-26T10:05:00", 24_00_000.0)]
    eng._roll_risk_day(24_00_000.0, day)

    # Fresh process: same portfolio state, brand-new RiskState pinned to 25L.
    eng2 = TradingEngine(cfg=eng.cfg, portfolio=eng.portfolio)
    assert eng2.risk.day_start_equity == 25_00_000              # the stale pin
    eng2._roll_risk_day(24_00_000.0, day + timedelta(hours=5))
    assert eng2.risk.day_start_equity == 24_00_000.0            # restored
    rolls = [e for e in eng.portfolio.decision_journal
             if e.get("action") == "RISK_DAY_ROLL"]
    assert len(rolls) == 1                       # a restart is not a new day


def test_new_day_re_arms_a_tripped_kill_switch(tmp_path):
    """Yesterday's halt must not block today's entries forever."""
    eng = _engine(tmp_path, starting_capital=25_00_000, daily_loss_halt_pct=-0.02)
    eng.portfolio.equity_history = [("2026-07-26T10:05:00", 24_00_000.0)]
    eng._roll_risk_day(24_00_000.0, datetime(2026, 7, 26, 10, 5))
    assert eng.risk.check_kill_switch(23_00_000.0)               # tripped

    eng.portfolio.equity_history.append(("2026-07-26T15:35:00", 23_00_000.0))
    eng._roll_risk_day(23_00_000.0, datetime(2026, 7, 27, 10, 5))

    assert eng.risk.kill_switch is False                         # re-armed
    assert eng.risk.day_start_equity == 23_00_000.0


def test_baseline_survives_a_save_load_round_trip(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.risk_day = "2026-07-27"
    p.risk_day_equity = 24_31_500.0
    p.save()

    loaded = Portfolio.load_or_fresh(cfg)
    assert loaded.risk_day == "2026-07-27"
    assert loaded.risk_day_equity == 24_31_500.0


def test_old_portfolio_json_without_the_new_fields_still_loads(tmp_path):
    """Backward compat: the live book was saved before these fields existed."""
    import json, os
    cfg = Config(state_dir=str(tmp_path))
    os.makedirs(cfg.state_dir, exist_ok=True)
    with open(os.path.join(cfg.state_dir, "portfolio.json"), "w") as f:
        json.dump({"cash": 24_79_000.0, "positions": {}, "trades": []}, f)

    loaded = Portfolio.load_or_fresh(cfg)
    assert loaded.cash == 24_79_000.0
    assert loaded.risk_day is None
    assert loaded.risk_day_equity == 0.0


def test_first_ever_roll_falls_back_to_current_equity(tmp_path):
    """Empty equity_history (fresh book) must not produce a zero baseline."""
    eng = _engine(tmp_path, starting_capital=25_00_000)
    assert eng.portfolio.equity_history == []

    eng._roll_risk_day(25_00_000.0, datetime(2026, 7, 27, 10, 5))

    assert eng.risk.day_start_equity == 25_00_000.0
    assert eng.portfolio.risk_day_equity == 25_00_000.0


# --------------------------------------------------------------------------
# Recheck fixes (2026-07-27 evening review)
# --------------------------------------------------------------------------

def test_empty_book_refresh_still_rolls_the_day(tmp_path):
    """The deadlock: a kill-switch liquidation empties the book, and the old
    refresh() early-returned before rolling — so the tripped switch never
    re-armed and automatic buying stayed dead until a restart."""
    eng = _engine(tmp_path, starting_capital=25_00_000, daily_loss_halt_pct=-0.02)
    eng.portfolio.equity_history = [("2026-07-26T15:35:00", 24_00_000.0)]
    eng._roll_risk_day(24_00_000.0, datetime(2026, 7, 26, 15, 35))
    assert eng.risk.check_kill_switch(23_00_000.0)          # tripped, book emptied
    eng.portfolio.equity_history.append(("2026-07-26T15:36:00", 23_00_000.0))
    eng.portfolio.risk_day_killed = True

    assert eng.portfolio.positions == {}                     # empty book
    with patch("trading.engine.datetime") as dt:
        dt.utcnow.return_value = datetime(2026, 7, 27, 10, 5)
        eng.refresh()                                        # early-return path
    assert eng.risk.kill_switch is False                     # re-armed anyway
    assert eng.portfolio.risk_day == "2026-07-27"


def test_scan_time_roll_uses_yesterdays_close_not_todays_mark(tmp_path):
    """The one-hour-halt bug: rolling first at 15:35 made the baseline
    today's own 14:30 mark. The roll now happens at the day's first engine
    action, before today's equity points are recorded."""
    eng = _engine(tmp_path, starting_capital=25_00_000, daily_loss_halt_pct=-0.02)
    eng.portfolio.equity_history = [("2026-07-26T15:35:00", 25_00_000.0)]

    # 09:20 scan-time roll (the new call site)
    eng._roll_risk_day(24_20_000.0, datetime(2026, 7, 27, 9, 20))
    # today's marks land AFTER the roll
    eng.portfolio.equity_history += [("2026-07-27T09:20:00", 24_20_000.0),
                                     ("2026-07-27T14:30:00", 24_10_000.0)]
    # 15:35 same-day roll must NOT re-baseline to the 14:30 mark
    eng._roll_risk_day(24_10_000.0, datetime(2026, 7, 27, 15, 35))

    assert eng.risk.day_start_equity == 25_00_000.0          # yesterday's close
    assert eng.risk.check_kill_switch(24_10_000.0)           # -3.6% day = halt


def test_tripped_flag_survives_a_mid_day_restart(tmp_path):
    eng = _engine(tmp_path, starting_capital=25_00_000, daily_loss_halt_pct=-0.02)
    day = datetime(2026, 7, 27, 10, 5)
    eng.portfolio.equity_history = [("2026-07-26T15:35:00", 24_00_000.0)]
    eng._roll_risk_day(24_00_000.0, day)
    assert eng.risk.check_kill_switch(23_00_000.0)
    eng.portfolio.risk_day_killed = True                     # persisted on trip

    eng2 = TradingEngine(cfg=eng.cfg, portfolio=eng.portfolio)
    assert eng2.risk.kill_switch is False                    # fresh state
    eng2._roll_risk_day(23_00_000.0, day)                    # same-day restore
    assert eng2.risk.kill_switch is True                     # halt remembered


def test_ocean_block_journals_once_per_day_and_not_in_neutral(tmp_path, monkeypatch):
    monkeypatch.setattr("trading.engines.ocean.scan", lambda *a, **k: [])
    eng = _engine(tmp_path)
    now = datetime(2026, 7, 27, 9, 20)
    # neutral: Everest active -> no journal line at all
    eng._ocean_picks(_regime("neutral", ocean=True, everest=True), {}, [], now)
    assert eng.portfolio.decision_journal == []
    # correction: journals once...
    eng._ocean_picks(_regime(), {}, [], now)
    eng._ocean_picks(_regime(), {}, [], now.replace(hour=14))   # 2nd pass same day
    blocked = [e for e in eng.portfolio.decision_journal
               if e.get("action") == "OCEAN_BLOCKED"]
    assert len(blocked) == 1
