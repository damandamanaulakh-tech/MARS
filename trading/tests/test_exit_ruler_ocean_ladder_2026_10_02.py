"""Owner order 2026-10-02 ("fix 1 to 3"), from docs/AUDIT_2026-10-02.md:

1. F-1  Momentum positions were BOUGHT on the momentum + quality blend and
        SOLD for score decay on the AM07 composite — a different number on a
        different floor. The live exit pass now re-scores each position on
        the ruler its own trade category buys on (the backtest always did).
2. F-2  Ocean dip buys read near zero on any momentum-style score by
        construction, so the first 30-minute pass sold them for decay. Ocean
        positions are now exempt from score-based exits; stop, target,
        trailing stop and max-hold still protect them.
3. F-3  The drawdown ladder re-cut on EVERY refresh (a 40-name book empty in
        7 passes), an empty book in a halting drawdown stayed halted for
        good, and a capital change read as a drawdown. Each level now cuts
        once (persisted), an empty halted book resets its peak, and the peak
        moves with the capital.

The engine tests drive the REAL refresh()/scan helpers with only the network
seams replaced, the same pattern as test_engine_direct.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.exits import check_exit
from trading.paper import PaperPosition, Portfolio
from trading.quality import blend_scores, quality_score
from trading.scoring import MOMENTUM_MODE, score_for_mode
from trading.strategies.nifty200_momentum import momentum_score


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
    cfg.danger_layer_enabled = False     # the danger layer is not under test
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    frames = {
        "UPP.NS": _frame(drift=0.60, seed=2),       # strong up-trender
        cfg.benchmark: _frame(drift=0.05, seed=4),
        "^INDIAVIX": _frame(start=12.0, drift=0.0, seed=5),
    }
    for i, t in enumerate(("AA.NS", "BB.NS", "CC.NS", "DD.NS")):
        frames[t] = _frame(drift=0.30, seed=10 + i)
    periods = []

    def fake_fetch_daily(tickers, period="6mo"):
        periods.append(period)
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}

    def fake_quotes(tickers, upstox_client=None):
        return {t: float(frames[t]["Close"].iloc[-1])
                for t in tickers if t in frames}
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", fake_quotes)
    e._frames = frames
    e._periods = periods
    return e


def _composite_reads(monkeypatch, value):
    """Pin the AM07 composite to `value` under BOTH names it is reachable by:
    the scoring module (what score_for_mode calls) and the engine's own
    import (what the pre-fix exit pass called). Stubbing only one would let
    the old code slip past the test."""
    stub = lambda *a, **k: (value, {})  # noqa: E731
    monkeypatch.setattr("trading.scoring.compute_score", stub)
    monkeypatch.setattr("trading.engine.compute_score", stub)


def _hold(p, ticker, last, *, mode=MOMENTUM_MODE, tags=None, entry_score=70.0):
    """A position sitting comfortably inside its stop/target, opened
    yesterday, so only a score-based rule could close it."""
    p.positions[ticker] = PaperPosition(
        ticker=ticker, qty=100, entry_price=last,
        entry_date=datetime.utcnow() - timedelta(days=1), sector="Test",
        peak_price=last, entry_score=entry_score, reasoning="fixture",
        mode=mode, stop_price=last * 0.50, target_price=last * 2.0,
        tags=list(tags or []))
    p.cash -= 100 * last


# ---------------------------------------------------------------- F-1 ----

def test_momentum_ruler_is_the_entry_blend():
    df = _frame(drift=0.60, seed=2)
    cfg = Config(mode=MOMENTUM_MODE)
    cfg.apply_mode_preset()
    got, comps = score_for_mode(MOMENTUM_MODE, "UPP.NS", df, None, {}, cfg)
    want = blend_scores(momentum_score(df["Close"]),
                        quality_score(df["Close"], df["Volume"]),
                        cfg.quality_blend_pct)
    assert got == pytest.approx(want)
    assert set(comps) == {"momentum", "quality"}


def test_short_history_reads_missing_never_a_collapse():
    df = _frame(n=200, drift=0.60, seed=2)          # under the 252 bars needed
    cfg = Config(mode=MOMENTUM_MODE)
    assert score_for_mode(MOMENTUM_MODE, "X.NS", df, None, {}, cfg,
                          missing=None)[0] is None
    # the backtest keeps its historical 0.0
    assert score_for_mode(MOMENTUM_MODE, "X.NS", df, None, {}, cfg)[0] == 0.0


def test_a_strong_momentum_position_is_not_sold_on_the_composite(eng, monkeypatch):
    """The fault itself: the composite reads 20 (below the 40 decay floor)
    while the momentum blend that bought the stock is high. Before the fix
    the position was sold for SCORE_DECAY; now it is held."""
    _composite_reads(monkeypatch, 20.0)
    p = eng.portfolio
    _hold(p, "UPP.NS", float(eng._frames["UPP.NS"]["Close"].iloc[-1]))
    eng.refresh()
    assert "UPP.NS" in p.positions
    assert not any(t.ticker == "UPP.NS" for t in p.trades)


def test_other_categories_still_judge_on_the_composite(eng, monkeypatch):
    """Only the momentum ruler changed. A swing position was bought on the
    composite and is still judged on it."""
    _composite_reads(monkeypatch, 20.0)
    p = eng.portfolio
    _hold(p, "UPP.NS", float(eng._frames["UPP.NS"]["Close"].iloc[-1]),
          mode="swing")
    eng.refresh()
    assert "UPP.NS" not in p.positions
    assert p.trades[-1].reason == "SCORE_DECAY"


def test_legacy_blank_mode_is_judged_as_momentum(eng, monkeypatch):
    _composite_reads(monkeypatch, 20.0)
    p = eng.portfolio
    _hold(p, "UPP.NS", float(eng._frames["UPP.NS"]["Close"].iloc[-1]), mode="")
    eng.refresh()
    assert "UPP.NS" in p.positions


def test_a_short_history_never_sells_for_decay(eng):
    """Under a year of bars the momentum ruler has no reading. That is not
    evidence of decay: the position keeps its entry score."""
    eng._frames["UPP.NS"] = _frame(n=200, drift=-0.40, seed=2)
    p = eng.portfolio
    _hold(p, "UPP.NS", float(eng._frames["UPP.NS"]["Close"].iloc[-1]))
    eng.refresh()
    assert "UPP.NS" in p.positions


def test_the_exit_pass_loads_enough_history_for_the_momentum_ruler(eng):
    p = eng.portfolio
    _hold(p, "UPP.NS", float(eng._frames["UPP.NS"]["Close"].iloc[-1]))
    eng.refresh()
    assert "2y" in eng._periods, eng._periods


# ---------------------------------------------------------------- F-2 ----

def _pos(tags, entry_score=65.0, peak=118.0):
    return PaperPosition(
        ticker="X.NS", qty=10, entry_price=100.0,
        entry_date=datetime.utcnow() - timedelta(days=1), sector="Test",
        peak_price=peak, entry_score=entry_score, reasoning="fixture",
        mode=MOMENTUM_MODE, stop_price=95.0, target_price=200.0,
        tags=list(tags))


def test_an_ocean_buy_is_never_sold_for_score_decay():
    cfg = Config(mode=MOMENTUM_MODE)
    cfg.apply_mode_preset()
    # no gain yet (peak 100), so only the score rule can act
    sell, why = check_exit(_pos(["ocean"], peak=100.0), 99.0, 10.0, cfg,
                           datetime.utcnow())
    assert (sell, why) == (False, "")
    # the same position without the tag would go — proves the floor is live
    sell, why = check_exit(_pos([], peak=100.0), 99.0, 10.0, cfg,
                           datetime.utcnow())
    assert (sell, why) == (True, "SCORE_DECAY")


def test_an_ocean_buy_keeps_its_full_trailing_stop():
    """Entry 65 vs a current score of 10 would tighten the trail by 40% on a
    momentum position. Ocean's entry score is on a different scale, so its
    trail stays at the configured width."""
    cfg = Config(mode=MOMENTUM_MODE)
    cfg.apply_mode_preset()
    peak = 118.0
    tight = peak * (1 + cfg.trailing_stop_pct * 0.6)    # tightened trail line
    price = tight - 0.5           # under the tightened line, above the full one
    assert price > peak * (1 + cfg.trailing_stop_pct)
    assert check_exit(_pos(["ocean"]), price, 10.0, cfg,
                      datetime.utcnow()) == (False, "")


def test_ocean_still_has_its_hard_stop():
    cfg = Config(mode=MOMENTUM_MODE)
    cfg.apply_mode_preset()
    assert check_exit(_pos(["ocean"]), 94.0, 10.0, cfg,
                      datetime.utcnow()) == (True, "STOP_LOSS")


# ---------------------------------------------------------------- F-3 ----

def _book_in_drawdown(eng, dd):
    p = eng.portfolio
    for t in ("AA.NS", "BB.NS", "CC.NS", "DD.NS"):
        _hold(p, t, float(eng._frames[t]["Close"].iloc[-1]), tags=["owner_pick"])
    prices = {t: float(eng._frames[t]["Close"].iloc[-1]) for t in p.positions}
    p.dd_peak_equity = p.equity(prices) / (1 + dd)
    return p


def test_each_drawdown_level_cuts_once(eng):
    p = _book_in_drawdown(eng, -0.12)              # cut_half
    eng.refresh()
    assert len(p.positions) == 2
    assert p.dd_cut_level == "cut_half"
    for _ in range(3):                              # the old code re-cut here
        eng.refresh()
    assert len(p.positions) == 2
    cuts = [e for e in p.decision_journal
            if e.get("action") == "DRAWDOWN_LADDER_CUT"]
    assert len(cuts) == 1


def test_a_deeper_level_cuts_again(eng):
    p = _book_in_drawdown(eng, -0.12)
    eng.refresh()
    assert len(p.positions) == 2
    prices = {t: float(eng._frames[t]["Close"].iloc[-1]) for t in p.positions}
    p.dd_peak_equity = p.equity(prices) / (1 - 0.16)     # mostly_cash
    eng.refresh()
    assert len(p.positions) == 1
    assert p.dd_cut_level == "mostly_cash"


def test_a_restart_does_not_cut_the_same_level_again(eng):
    p = _book_in_drawdown(eng, -0.12)
    eng.refresh()
    assert len(p.positions) == 2
    p.save()
    back = Portfolio.load_or_fresh(eng.cfg)
    assert back.dd_cut_level == "cut_half"
    fresh = TradingEngine(eng.cfg, back)            # new governor, peak 0
    fresh.refresh()
    assert len(back.positions) == 2


def test_an_empty_book_in_a_halting_drawdown_resets_its_peak(eng):
    p = eng.portfolio
    eq = p.equity({})
    p.dd_peak_equity = eq / (1 - 0.09)              # stop_adding: halts entries
    eng._update_drawdown({}, datetime.utcnow(), cut_allowed=False)
    assert p.dd_peak_equity == pytest.approx(eq)
    assert eng.drawdown_gov.halt_new_entries is False
    assert any(e.get("action") == "DRAWDOWN_PEAK_RESET"
               for e in p.decision_journal)


def test_a_held_book_in_drawdown_keeps_its_peak(eng):
    """The reset is only for the stuck, EMPTY book. A book with positions can
    still recover, so its drawdown keeps counting."""
    p = _book_in_drawdown(eng, -0.09)
    peak = p.dd_peak_equity
    eng._update_drawdown(
        {t: float(eng._frames[t]["Close"].iloc[-1]) for t in p.positions},
        datetime.utcnow(), cut_allowed=False)
    assert p.dd_peak_equity == pytest.approx(peak)
    assert eng.drawdown_gov.halt_new_entries is True


def test_the_peak_moves_with_a_capital_change(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.dd_peak_equity = p.capital_base + 20_00_000
    cfg.starting_capital = p.capital_base - 1_00_00_000      # cut capital 1 Cr
    p.sync_capital(cfg)
    assert p.dd_peak_equity == pytest.approx(cfg.starting_capital + 20_00_000)


def test_the_cut_level_is_mirrored_to_mongo_both_ways():
    src = Path("web/state.py").read_text()
    hydrate = src.split("def _hydrate_portfolio_from_mongo")[1].split("\ndef ")[0]
    mirror = src.split("def _patch_portfolio_save_to_mirror_mongo")[1].split("\ndef ")[0]
    assert "dd_cut_level" in hydrate, "a restart would cut the same level again"
    assert "dd_cut_level" in mirror, "the cut level is never written to Mongo"


def test_hydrate_reads_the_cut_level(tmp_path):
    from web.state import _hydrate_portfolio_from_mongo
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    _hydrate_portfolio_from_mongo(p, {"cash": p.cash, "dd_cut_level": "cut_half"})
    assert p.dd_cut_level == "cut_half"
