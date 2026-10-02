"""Owner order 2026-10-02 ("go for next"), audit items 4-7 from
docs/AUDIT_2026-10-02.md:

4. The VIX-crush re-entry took its peak over a fixed 120 days; the v4 study
   took it from the START of the correction. A spike before the correction
   began now no longer counts.
5. check_exit read trail / decay floor / max-hold from the LIVE config
   (momentum), so swing and short trades were held up to 200 days. Each
   position now exits on its own category's rules.
6. The boot retag rewrote every target to the flat preset percentage,
   wiping the per-stock k x ATR target set at entry.
7. Every unmapped name counted as one "Unknown" sector, so at most 12 of
   the ~950 unmapped scan names could be held at once.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.exits import retag_positions_per_mode
from trading.paper import PaperPosition, Portfolio
from trading.regime import correction_episode_start, vix_crush_ok
from trading.risk import RiskState, can_open


# ------------------------------------------------------------------ item 4

def _idx(n):
    return pd.bdate_range("2025-01-01", periods=n)


def test_episode_start_is_the_first_close_of_the_current_run_below_200dma():
    idx = _idx(320)
    close = pd.Series(np.r_[np.linspace(100, 200, 280), np.full(40, 120.0)],
                      index=idx)
    start = correction_episode_start(close)
    assert start == idx[280]


def test_no_episode_when_the_last_close_is_above_its_200dma():
    idx = _idx(300)
    close = pd.Series(np.linspace(100, 200, 300), index=idx)
    assert correction_episode_start(close) is None


def test_a_spike_before_the_correction_no_longer_counts():
    """The fault: VIX spiked to 30 BEFORE the correction, the correction
    began with VIX at 14 and it has since eased to 13. The old 120-day rule
    reads 13 <= 0.75 x 30 and calls the fear crushed. Measured from the
    correction's start, the peak is 14 and nothing has been crushed."""
    idx = _idx(60)
    vix = pd.Series(np.r_[np.full(20, 30.0), np.full(20, 14.0),
                          np.full(20, 13.0)], index=idx)
    start = idx[20]
    assert vix_crush_ok(vix) is True                    # the old reading
    assert vix_crush_ok(vix, since=start) is False      # the tested rule


def test_a_real_spike_inside_the_correction_still_fires():
    idx = _idx(60)
    vix = pd.Series(np.r_[np.full(20, 14.0), np.full(10, 28.0),
                          np.full(30, 18.0)], index=idx)
    assert vix_crush_ok(vix, since=idx[0]) is True      # 18 <= 0.75 x 28


def test_the_engine_passes_the_episode_start():
    src = Path("trading/engine.py").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "correction_episode_start(" in code
    assert re.search(r"vix_crush_ok\([^)]*since=episode_start", code,
                     re.S), "the engine must hand the rule the episode start"


# ------------------------------------------------------------------ item 5

def _frame(n=300, drift=0.30, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + drift / n + rng.normal(0, 0.004, n))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close,
                         "Volume": np.full(n, 5e6)}, index=idx)


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.danger_layer_enabled = False
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    frames = {"AA.NS": _frame(seed=2), cfg.benchmark: _frame(seed=3)}
    monkeypatch.setattr(
        "trading.engine.fetch_daily",
        lambda tickers, period="6mo": {t: frames[t].copy()
                                       for t in ([tickers] if isinstance(tickers, str) else tickers)
                                       if t in frames})
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {
            t: float(frames[t]["Close"].iloc[-1]) for t in tickers if t in frames})
    # a healthy composite, so only max-hold can act on the swing position
    stub = lambda *a, **k: (80.0, {})  # noqa: E731
    monkeypatch.setattr("trading.scoring.compute_score", stub)
    monkeypatch.setattr("trading.engine.compute_score", stub)
    e._frames = frames
    return e


def _hold(eng, mode, days):
    last = float(eng._frames["AA.NS"]["Close"].iloc[-1])
    eng.portfolio.positions["AA.NS"] = PaperPosition(
        ticker="AA.NS", qty=10, entry_price=last,
        entry_date=datetime.utcnow() - timedelta(days=days), sector="Test",
        peak_price=last, entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=last * 0.5, target_price=last * 2.0)
    eng.portfolio.cash -= 10 * last


def test_a_swing_trade_exits_on_swings_20_day_hold(eng):
    """Under the live momentum config the old code held this 25-day swing
    trade (momentum max-hold 200). Swing's own hold is 20 days."""
    from trading.modes import PRESETS
    assert PRESETS["swing"].max_hold_days < 25 < eng.cfg.max_hold_days
    _hold(eng, "swing", 25)
    eng.refresh()
    assert "AA.NS" not in eng.portfolio.positions
    assert eng.portfolio.trades[-1].reason == "MAX_HOLD"


def test_a_momentum_trade_of_the_same_age_is_held(eng):
    _hold(eng, "nifty200_momentum", 25)
    eng.refresh()
    assert "AA.NS" in eng.portfolio.positions


def test_the_exit_config_never_touches_the_shared_config(eng):
    before = (eng.cfg.mode, eng.cfg.max_hold_days, eng.cfg.trailing_stop_pct)
    _hold(eng, "swing", 1)
    c = eng._exit_cfg(eng.portfolio.positions["AA.NS"], eng.cfg)
    assert c is not eng.cfg and c.mode == "swing"
    assert (eng.cfg.mode, eng.cfg.max_hold_days,
            eng.cfg.trailing_stop_pct) == before


def test_owner_per_mode_edits_reach_the_exit_rules(eng):
    eng.cfg.preset_overrides = {"swing": {"max_hold_days": 40}}
    _hold(eng, "swing", 25)
    c = eng._exit_cfg(eng.portfolio.positions["AA.NS"], eng.cfg)
    assert c.max_hold_days == 40


# ------------------------------------------------------------------ item 6

def _pos(mode, target):
    return PaperPosition(
        ticker="X.NS", qty=1, entry_price=100.0,
        entry_date=datetime.utcnow() - timedelta(days=3), sector="Test",
        peak_price=100.0, entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=95.0, target_price=target)


def test_retag_keeps_a_valid_positions_atr_target(tmp_path):
    p = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    p.positions["X.NS"] = _pos("nifty200_momentum", 126.4)   # k x ATR target
    retag_positions_per_mode(p)
    assert p.positions["X.NS"].target_price == pytest.approx(126.4)


def test_retag_still_fixes_a_blank_or_missing_target(tmp_path):
    from trading.modes import PRESETS
    want = round(100 * (1 + PRESETS["nifty200_momentum"].take_profit_pct), 2)
    p = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    p.positions["X.NS"] = _pos("", 126.4)                    # blank tag
    retag_positions_per_mode(p)
    assert p.positions["X.NS"].target_price == pytest.approx(want)
    p.positions["X.NS"] = _pos("nifty200_momentum", 0.0)     # no target
    retag_positions_per_mode(p)
    assert p.positions["X.NS"].target_price == pytest.approx(want)


# ------------------------------------------------------------------ item 7

def _book(cfg, tickers):
    return {t: PaperPosition(
        ticker=t, qty=1, entry_price=100.0, entry_date=datetime.utcnow(),
        sector="x", peak_price=100.0, entry_score=70.0, reasoning="f")
        for t in tickers}


def test_unmapped_names_are_not_capped_as_one_sector():
    cfg = Config()
    n = cfg.max_sector_positions
    held = _book(cfg, [f"UNMAPPED{i}.NS" for i in range(n)])
    assert all(t not in cfg.sector_map for t in held)
    ok, why, qty = can_open(RiskState(cfg, 1e8), 1e8, 1e8, held,
                            "ANOTHER.NS", 100.0, cfg)
    assert (ok, why) == (True, "ok")


def test_a_mapped_sector_keeps_its_cap():
    cfg = Config()
    sector = "Financial"
    names = [t for t, s in cfg.sector_map.items() if s == sector]
    n = cfg.max_sector_positions
    if len(names) <= n:
        pytest.skip("not enough mapped names in one sector to fill the cap")
    held = _book(cfg, names[:n])
    ok, why, _ = can_open(RiskState(cfg, 1e8), 1e8, 1e8, held, names[n],
                          100.0, cfg)
    assert (ok, why) == (False, "sector_cap_reached")
