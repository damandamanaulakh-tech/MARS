"""Money-path fixes A6-A11 from the 2026-08-09 audit (owner: "fix A6 to A11
too"). Each test fails if the flaw returns.

A6  a Settings typo in sizing_mode silently re-enabled half_kelly
A7  editing the 8 preset-owned fields was a silent no-op that said "Saved."
A8  the backtest-mode global poisoned live scans running in other threads
A9  deploy/sector caps were measured on frozen starting_capital, not equity
A10 scan entries always filled at yesterday's close, even with a live broker
A11 owner buy paths sized outside the shared sizing entry point
"""

import threading
from datetime import datetime

import pytest

from trading.config import Config
from trading.modes import get_preset


# --------------------------------------------------------- A6: typo fallback

def test_a_sizing_mode_typo_lands_on_fixed_not_kelly():
    from pathlib import Path
    src = Path("web/routes/settings_routes.py").read_text()
    guard = src.split('not in ("fixed", "kelly", "half_kelly")')[1][:200]
    assert 'cfg.sizing_mode = "fixed"' in guard, \
        "a typo re-enables Kelly against the owner's written order"
    assert '= "half_kelly"' not in guard


# --------------------------------------------- A7: preset edits finally stick

def test_owner_override_survives_the_preset_reapplication():
    cfg = Config()
    cfg.mode = "nifty200_momentum"
    cfg.apply_mode_preset()
    assert cfg.stop_loss_pct == get_preset("nifty200_momentum").stop_loss_pct
    cfg.preset_overrides = {"nifty200_momentum": {"stop_loss_pct": -0.08}}
    cfg.apply_mode_preset()          # boot / rotation / settings-save path
    assert cfg.stop_loss_pct == -0.08, "the owner's edit was silently reverted"
    cfg.apply_mode_preset()          # idempotent
    assert cfg.stop_loss_pct == -0.08


def test_override_is_per_mode_and_other_fields_keep_the_preset():
    cfg = Config()
    cfg.mode = "nifty200_momentum"
    cfg.preset_overrides = {"nifty200_momentum": {"take_profit_pct": 0.20}}
    cfg.apply_mode_preset()
    assert cfg.take_profit_pct == 0.20
    assert cfg.stop_loss_pct == get_preset("nifty200_momentum").stop_loss_pct
    cfg.mode = "swing"               # rotation switches mode
    cfg.apply_mode_preset()
    assert cfg.take_profit_pct == get_preset("swing").take_profit_pct, \
        "an override for one mode leaked into another"
    cfg.mode = "nifty200_momentum"   # and back
    cfg.apply_mode_preset()
    assert cfg.take_profit_pct == 0.20


def test_no_overrides_means_pure_preset_behavior():
    cfg = Config()
    cfg.mode = "nifty200_momentum"
    cfg.preset_overrides = {}
    cfg.apply_mode_preset()
    p = get_preset("nifty200_momentum")
    assert cfg.min_score_to_enter == p.min_score_to_enter == 62.0


# --------------------------------------------------- A8: thread-local BT mode

def test_backtest_mode_cannot_poison_another_thread():
    """A web backtest thread must not flip the live scan's snapshot feeders
    to None mid-scan — the flag is per-thread now."""
    from trading import data
    data.set_backtest_mode(True)
    try:
        seen = {}

        def live_scan_thread():
            seen["bt"] = data.is_backtest_mode()

        t = threading.Thread(target=live_scan_thread)
        t.start(); t.join()
        assert data.is_backtest_mode() is True          # this thread: on
        assert seen["bt"] is False, "backtest mode leaked into another thread"
    finally:
        data.set_backtest_mode(False)


# ----------------------------------------------------- A9: caps follow equity

def _positions(total_notional, n=4, price=100.0):
    from trading.paper import PaperPosition
    qty = int(total_notional / n / price)
    return {f"S{i}.NS": PaperPosition(
        ticker=f"S{i}.NS", qty=qty, entry_price=price,
        entry_date=datetime(2026, 7, 1), sector="Unknown", peak_price=price,
        entry_score=70.0, reasoning="t") for i in range(n)}


def test_grown_book_can_deploy_past_starting_capital():
    """Rs 60L equity on a Rs 50L start: the old cap froze deployment at 50L
    forever. Deployed 49.6L, equity 60L, reserve 0% -> must still open."""
    from trading.risk import RiskState, can_open
    cfg = Config(state_dir="/tmp/a9_tests")
    cfg.apply_mode_preset()
    ok, reason, qty = can_open(
        RiskState(cfg=cfg, starting_capital=50_00_000.0),
        cash=10_00_000.0, current_equity=60_00_000.0,
        open_positions=_positions(49_60_000.0, n=8),
        ticker="NEW.NS", fill_price=500.0, cfg=cfg)
    assert ok, f"growth is frozen out: {reason}"


def test_shrunk_book_reserve_tightens_with_equity():
    """In a correction the forced 20% reserve must be 20% of what's LEFT,
    not of the original Rs 50L."""
    from trading.risk import RiskState, can_open
    cfg = Config(state_dir="/tmp/a9_tests")
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.20
    # equity fell to 40L; deployed 33L. Old cap: 50L*0.8=40L -> would buy.
    # New cap: 40L*0.8=32L -> deployed 33L breaches the reserve.
    ok, reason, qty = can_open(
        RiskState(cfg=cfg, starting_capital=50_00_000.0),
        cash=7_00_000.0, current_equity=40_00_000.0,
        open_positions=_positions(33_00_000.0, n=8),
        ticker="NEW.NS", fill_price=500.0, cfg=cfg)
    assert not ok and reason == "reserve_breached", \
        "the crash reserve no longer shrinks with the book"


# ------------------------------------------------ A10: live fills, disclosed

def test_scan_fetches_live_quotes_for_fills_and_journals_the_source():
    from pathlib import Path
    src = Path("trading/engine.py").read_text()
    scan_body = src.split("def scan(")[1].split("def square_off_intraday")[0]
    assert "live_fill_px" in scan_body
    assert '"fill_source"' in src
    assert 'slippage_price(raw_fill, "BUY", cfg)' in scan_body
    # the prior-close-only fill is gone
    assert 'slippage_price(pick["last_close"], "BUY", cfg)' not in src


# ------------------------------------- A11: one sizing entry point everywhere

def test_owner_buy_paths_use_the_shared_sizing_entry_point():
    from pathlib import Path
    for path in ("web/routes/scan_routes.py", "trading/orders.py"):
        src = Path(path).read_text()
        assert "position_notional_for" in src, f"{path} sizes on its own"
        assert "cfg.position_notional(" not in src, \
            f"{path} still bypasses the shared sizing module"


def test_manual_size_is_the_owners_fixed_number_under_every_mode():
    from trading.sizing import position_notional_for
    for mode in ("fixed", "half_kelly", "kelly"):
        cfg = Config(sizing_mode=mode)
        n, diag = position_notional_for(cfg, 50_00_000.0)   # no score/history
        assert n == 125_000.0, (mode, n)
