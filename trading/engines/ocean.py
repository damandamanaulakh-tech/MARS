"""Indian Ocean engine — deep reversion (contrarian/value).

Buys high-quality companies after panic crashes. Active only when
trading.regime says correction or panic.

Selection rules (stock must pass ALL):

  Survival check (fundamentals):
    1. Debt-to-Equity < 0.5
    2. Current ratio > 1.5
    3. Free Cash Flow positive last 3 years

  Capitulation trigger (technicals):
    4. Price >= 30% below 200-day SMA  (deep discount)
    5. Weekly RSI < 25                  (extreme oversold)
    6. Recent climax volume: at least one day in last 5 trading days
       with volume > 3x its 20-day average

Tranche execution:
  - Position size: max 10% of capital per stock (already config-capped)
  - Buy in 3 stages: 30% at trigger, 30% at first higher low,
    40% above the 20-day EMA reclaim
  - Hard stop: 12% below entry on each tranche
  - Exit half at 50-day SMA reversion; trail remainder under 20-day EMA

Honest limitation: fundamentals (D/E, current ratio, FCF) come from
yfinance.Ticker.info which is current-snapshot only. In backtest mode
these checks are skipped (returns no Ocean picks during replay) to
avoid lookahead bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ..config import Config
from ..data import is_backtest_mode

log = logging.getLogger(__name__)


@dataclass
class OceanCandidate:
    ticker: str
    price: float
    pct_below_200dma: float
    weekly_rsi: float
    climax_volume_ratio: float    # max(vol[-5:]) / vol_20d_mean
    survival_passed: bool
    reasons: list[str]


# ---------------- Technical filters (pure functions of OHLCV) -----------------

def _sma(s: pd.Series, n: int) -> Optional[float]:
    if s is None or len(s.dropna()) < n:
        return None
    return float(s.dropna().rolling(n).mean().iloc[-1])


def weekly_rsi(daily_close: pd.Series, length: int = 14) -> Optional[float]:
    """RSI on weekly resampled bars."""
    if daily_close is None or len(daily_close.dropna()) < length * 7:
        return None
    weekly = daily_close.dropna().resample("W").last().dropna()
    if len(weekly) < length + 1:
        return None
    delta = weekly.diff().dropna()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / length, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / length, adjust=False).mean()
    eps = 1e-9
    rs = roll_up / roll_down.where(roll_down > 0, eps)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.dropna()
    return float(val.iloc[-1]) if not val.empty else None


def climax_volume_ratio(volume: pd.Series, lookback: int = 5, baseline: int = 20) -> Optional[float]:
    """max(vol[-lookback:]) / mean(vol[-baseline:])."""
    if volume is None or len(volume.dropna()) < baseline:
        return None
    recent_max = float(volume.iloc[-lookback:].max())
    base_mean = float(volume.iloc[-baseline:].mean())
    if base_mean <= 0:
        return None
    return recent_max / base_mean


# ---------------- Fundamental survival check (snapshot-only) ------------------

def survival_check(ticker: str) -> tuple[bool, list[str]]:
    """Returns (passes_all, reasons_list).

    In backtest mode returns (False, ['backtest-mode-skip']) — fundamentals
    are not available historically from yfinance.
    """
    if is_backtest_mode():
        return False, ["backtest-mode: fundamentals skipped (no historical snapshot)"]

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        return False, [f"info fetch failed: {e}"]

    reasons: list[str] = []
    debt_eq = info.get("debtToEquity")
    if debt_eq is None:
        reasons.append("D/E unknown")
        return False, reasons
    if debt_eq > 50:                  # yfinance reports as %, so 50 = 0.5
        reasons.append(f"D/E {debt_eq / 100:.2f} > 0.5")
        return False, reasons
    reasons.append(f"D/E {debt_eq / 100:.2f} ok")

    current_ratio = info.get("currentRatio")
    if current_ratio is None:
        reasons.append("Current ratio unknown")
        return False, reasons
    if current_ratio < 1.5:
        reasons.append(f"Current ratio {current_ratio:.2f} < 1.5")
        return False, reasons
    reasons.append(f"Current ratio {current_ratio:.2f} ok")

    fcf = info.get("freeCashflow")
    if fcf is None or fcf <= 0:
        reasons.append(f"FCF {fcf} not positive")
        return False, reasons
    reasons.append(f"FCF {fcf:.0f} positive")

    return True, reasons


# ---------------- Composite selector ------------------------------------------

def evaluate(ticker: str, history: pd.DataFrame, cfg: Config) -> Optional[OceanCandidate]:
    """Returns an OceanCandidate if the stock passes all rules; else None."""
    if history is None or len(history) < 200:
        return None
    close = history["Close"]
    volume = history["Volume"]

    last_price = float(close.iloc[-1])
    sma200 = _sma(close, 200)
    if sma200 is None or sma200 <= 0:
        return None

    pct_below_200 = (last_price / sma200 - 1) * 100
    if pct_below_200 > -30:
        return None       # not deep enough

    wrsi = weekly_rsi(close)
    if wrsi is None or wrsi >= 25:
        return None

    cvr = climax_volume_ratio(volume)
    if cvr is None or cvr < 3.0:
        return None

    survived, surv_reasons = survival_check(ticker)
    if not survived:
        return None

    reasons = [
        f"{pct_below_200:.1f}% below 200-DMA",
        f"Weekly RSI {wrsi:.1f}",
        f"Climax volume {cvr:.1f}x average",
        *surv_reasons,
    ]

    return OceanCandidate(
        ticker=ticker, price=last_price,
        pct_below_200dma=pct_below_200,
        weekly_rsi=wrsi,
        climax_volume_ratio=cvr,
        survival_passed=True,
        reasons=reasons,
    )


def scan(history_map: dict[str, pd.DataFrame], cfg: Config) -> list[OceanCandidate]:
    """Returns Ocean candidates ranked by depth-below-200-DMA (deepest first)."""
    out: list[OceanCandidate] = []
    for ticker, hist in history_map.items():
        try:
            c = evaluate(ticker, hist, cfg)
            if c is not None:
                out.append(c)
        except Exception as e:
            log.warning("Ocean evaluate failed for %s: %s", ticker, e)
            continue
    out.sort(key=lambda c: c.pct_below_200dma)   # deepest first
    return out
