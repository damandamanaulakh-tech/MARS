"""Tests for v5 risk/scoring additions:

- mcap filter in passes_liquidity (the spread filter was switched off by
  the owner 2026-10-22 — pinned as off below)
- dynamic_cash_reserve_pct
- score-decay trailing tighten
- compute_hot_sectors / scoring context
- alerts dedupe
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from trading.alerts import _RECENT_FINGERPRINTS, _should_send, notify_drawdown
from trading.risk_v2 import (
    LiquidityRules, dynamic_cash_reserve_pct, passes_liquidity,
)
from trading.scoring import compute_hot_sectors, set_scoring_context, _SCORING_CONTEXT


def _series(values):
    return pd.Series(
        values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"),
    )


def _ohlcv(n=60, close_start=1000.0):
    return pd.DataFrame({
        "Close":  _series(np.linspace(close_start, close_start * 1.1, n)),
        "Volume": _series([5_000_000] * n),
    })


# -------- mcap filter --------

def test_mcap_filter_blocks_smallcap():
    df = _ohlcv()
    ok, reason = passes_liquidity(
        df["Close"], df["Volume"], market_cap_cr=1_000.0,
    )
    assert ok is False
    assert "mcap" in reason


def test_mcap_filter_passes_largecap():
    df = _ohlcv()
    ok, _ = passes_liquidity(
        df["Close"], df["Volume"], market_cap_cr=50_000.0,
    )
    assert ok is True


def test_mcap_none_skipped():
    """When mcap is None (not fetched), the check is skipped."""
    df = _ohlcv()
    ok, _ = passes_liquidity(df["Close"], df["Volume"], market_cap_cr=None)
    assert ok is True


# -------- spread filter --------

def test_spread_filter_is_switched_off():
    # REPOINTED 2026-10-22 (owner card "Switch the spread check off"): a
    # 0.5% bid-ask spread used to fail here; only the Rs 5 Cr 5-day rupee
    # turnover rule gates liquidity now, and the spread is ignored.
    df = _ohlcv()
    ok, reason = passes_liquidity(
        df["Close"], df["Volume"], spread_pct=0.005,  # 0.5%
    )
    assert ok is True
    assert "spread" not in reason


def test_spread_filter_passes_tight_spread():
    df = _ohlcv()
    ok, _ = passes_liquidity(
        df["Close"], df["Volume"], spread_pct=0.0008,  # 0.08%
    )
    assert ok is True


# -------- dynamic cash reserve --------

def test_dynamic_cash_reserve_buckets():
    # P2/B2: full deployment in bull/neutral; protective cash in stress.
    assert dynamic_cash_reserve_pct("bull_extension") == 0.00
    assert dynamic_cash_reserve_pct("neutral") == 0.00
    assert dynamic_cash_reserve_pct("correction") == 0.20
    assert dynamic_cash_reserve_pct("panic") == 0.40


def test_dynamic_cash_reserve_unknown_uses_floor():
    assert dynamic_cash_reserve_pct(None) == 0.20
    assert dynamic_cash_reserve_pct("garbage") == 0.20
    assert dynamic_cash_reserve_pct(None, floor=0.30) == 0.30


# -------- hot sectors --------

def test_compute_hot_sectors_ranks_top3():
    # Build a synthetic universe where IT > Banks > Pharma
    @dataclass
    class _Cfg:
        momentum_lookback_days: int = 21

    cfg = _Cfg()
    history = {
        "INFY.NS":  pd.DataFrame({"Close": _series(np.linspace(100, 130, 30))}),
        "TCS.NS":   pd.DataFrame({"Close": _series(np.linspace(100, 130, 30))}),
        "HDFC.NS":  pd.DataFrame({"Close": _series(np.linspace(100, 115, 30))}),
        "SBIN.NS":  pd.DataFrame({"Close": _series(np.linspace(100, 115, 30))}),
        "CIPLA.NS": pd.DataFrame({"Close": _series(np.linspace(100, 105, 30))}),
    }
    sector_map = {
        "INFY.NS": "IT", "TCS.NS": "IT",
        "HDFC.NS": "Banks", "SBIN.NS": "Banks",
        "CIPLA.NS": "Pharma",
    }
    hot = compute_hot_sectors(history, sector_map, cfg, top_n=2)
    assert hot == {"IT", "Banks"}


def test_scoring_context_set_clears_old():
    set_scoring_context(fii_score=80.0, hot_sectors={"IT"})
    assert _SCORING_CONTEXT["fii_score"] == 80.0
    assert _SCORING_CONTEXT["hot_sectors"] == {"IT"}
    set_scoring_context(fii_score=None)
    assert "hot_sectors" not in _SCORING_CONTEXT


# -------- alerts dedupe --------

def test_alerts_dedupe_blocks_repeat_within_window():
    _RECENT_FINGERPRINTS.clear()
    assert _should_send("test:foo") is True
    assert _should_send("test:foo") is False   # deduped
    assert _should_send("test:bar") is True


def test_notify_drawdown_skips_below_threshold():
    """normal / watchful don't fire."""
    _RECENT_FINGERPRINTS.clear()
    r = notify_drawdown("normal", -1.0, 10_00_000, 9_90_000)
    assert r.sent is False
    r = notify_drawdown("watchful", -5.5, 10_00_000, 9_45_000)
    assert r.sent is False


# -------- score-decay trailing tighten --------

def test_trailing_tightens_on_score_decay():
    from trading.exits import check_exit
    from trading.config import Config

    @dataclass
    class _Pos:
        ticker: str
        entry_price: float
        peak_price: float
        entry_score: float
        entry_date: datetime
        qty: int = 1
        sector: str = "IT"
        reasoning: str = ""

    cfg = Config()
    cfg.disable_exits = False         # this test exercises the trailing-stop exit
    cfg.trailing_stop_pct = -0.08    # 8% normal trail
    cfg.stop_loss_pct = -0.20        # avoid hitting hard stop in this test
    cfg.score_decay_exit = -1.0      # avoid score-decay exit firing
    cfg.take_profit_pct = 0.50

    pos = _Pos(
        ticker="X.NS", entry_price=100.0, peak_price=110.0,
        entry_score=75.0, entry_date=datetime(2024, 1, 1),
    )
    # Score normal: 5% off peak -> NO exit (under 8% trail)
    exit_, _ = check_exit(pos, current_price=104.5, current_score=70.0, cfg=cfg)
    assert exit_ is False

    # Score decayed >15: trailing tightens to ~4.8% -> 5% off peak fires
    pos.peak_price = 110.0
    exit_, reason = check_exit(pos, current_price=104.5, current_score=55.0, cfg=cfg)
    assert exit_ is True
    assert reason == "TRAILING_STOP"
