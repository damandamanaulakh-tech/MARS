"""Market regime master switch.

Reads a few cheap signals to decide which engine should be active:

  - bull_extension  : Nifty > 200-DMA AND rising, FII net buying past 5d
                      -> Everest engine ON, Ocean OFF
  - neutral         : Nifty near 200-DMA, mixed FII flow
                      -> Both engines reduced size; prefer Everest
  - correction      : Nifty < 200-DMA AND falling, FII net selling
                      -> Ocean engine ON (vulture mode), Everest OFF
  - panic           : Nifty < -10% from 52w high, VIX > 25, FII heavy selling
                      -> Ocean ON with tighter rules; new entries restricted

The classifier is intentionally simple. Only a handful of inputs to avoid
overfitting. Tunable thresholds live in trading/config.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


NIFTY_50    = "^NSEI"
NIFTY_500   = "^CRSLDX"      # Nifty 500
INDIA_VIX   = "^INDIAVIX"


@dataclass
class RegimeReading:
    """Snapshot of which engines should be live and why."""
    timestamp: datetime
    nifty_close: Optional[float]
    nifty_200dma: Optional[float]
    pct_below_52w_high: Optional[float]
    vix: Optional[float]
    fii_5d_net: Optional[float]            # +ve = buying, -ve = selling (₹ Cr)

    regime: str                            # bull_extension|neutral|correction|panic
    everest_active: bool
    ocean_active: bool
    sizing_multiplier_everest: float       # 0.0..1.0
    sizing_multiplier_ocean: float
    reasoning: str

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["timestamp"] = self.timestamp.isoformat()
        return d


def _safe_last(s: Optional[pd.Series]) -> Optional[float]:
    if s is None or s.dropna().empty:
        return None
    try:
        v = float(s.dropna().iloc[-1])
        return v if v == v else None
    except (IndexError, ValueError):
        return None


def fetch_regime(fii_5d_net_override: Optional[float] = None) -> RegimeReading:
    """Pulls last 8 months of Nifty + VIX, classifies regime.

    `fii_5d_net_override` lets the caller plug in fresh FII data from
    trading.flows. If None, we proceed without it (less precise classification).
    """
    try:
        raw = yf.download(
            [NIFTY_50, INDIA_VIX], period="9mo", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker", threads=True,
        )
    except Exception as e:
        log.warning("Regime fetch failed: %s", e)
        return _neutral_fallback(f"fetch_failed: {e}")

    def col(tic: str) -> Optional[pd.Series]:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if tic not in raw.columns.get_level_values(0):
                    return None
                return raw[tic]["Close"]
            return raw["Close"]
        except (KeyError, AttributeError):
            return None

    nifty_s = col(NIFTY_50)
    vix_s = col(INDIA_VIX)

    nifty_close = _safe_last(nifty_s)
    vix = _safe_last(vix_s)

    nifty_200dma = None
    pct_below_52w_high = None
    if nifty_s is not None and len(nifty_s.dropna()) >= 200:
        nifty_200dma = float(nifty_s.dropna().rolling(200).mean().iloc[-1])
    if nifty_s is not None and not nifty_s.dropna().empty:
        last_252 = nifty_s.dropna().iloc[-252:]
        if not last_252.empty:
            high_52w = float(last_252.max())
            if high_52w > 0 and nifty_close is not None:
                pct_below_52w_high = (nifty_close - high_52w) / high_52w * 100

    regime, everest_on, ocean_on, mult_e, mult_o, why = classify(
        nifty_close=nifty_close,
        nifty_200dma=nifty_200dma,
        pct_below_52w_high=pct_below_52w_high,
        vix=vix,
        fii_5d_net=fii_5d_net_override,
    )

    return RegimeReading(
        timestamp=datetime.utcnow(),
        nifty_close=nifty_close,
        nifty_200dma=nifty_200dma,
        pct_below_52w_high=pct_below_52w_high,
        vix=vix,
        fii_5d_net=fii_5d_net_override,
        regime=regime,
        everest_active=everest_on,
        ocean_active=ocean_on,
        sizing_multiplier_everest=mult_e,
        sizing_multiplier_ocean=mult_o,
        reasoning=why,
    )


def classify(
    nifty_close: Optional[float],
    nifty_200dma: Optional[float],
    pct_below_52w_high: Optional[float],
    vix: Optional[float],
    fii_5d_net: Optional[float],
) -> tuple[str, bool, bool, float, float, str]:
    """Pure classifier — no I/O. Easy to unit-test."""
    # PANIC: Nifty crashed AND VIX panicking
    if (
        pct_below_52w_high is not None and pct_below_52w_high <= -10
        and vix is not None and vix > 25
    ):
        return (
            "panic", False, True, 0.0, 0.5,
            f"PANIC: Nifty {pct_below_52w_high:.1f}% off 52w high, VIX {vix:.1f}. "
            "Everest off; Ocean active but half-size, tighter rules."
        )

    # CORRECTION: Nifty below 200-DMA OR meaningfully off 52w high
    nifty_below_200 = (
        nifty_close is not None and nifty_200dma is not None
        and nifty_close < nifty_200dma
    )
    deep_below_high = (
        pct_below_52w_high is not None and pct_below_52w_high <= -7
    )
    if nifty_below_200 or deep_below_high:
        flags = []
        if nifty_below_200:
            flags.append(f"Nifty {nifty_close:.0f} < 200-DMA {nifty_200dma:.0f}")
        if deep_below_high:
            flags.append(f"{pct_below_52w_high:.1f}% off 52w high")
        if vix is not None and vix > 18:
            flags.append(f"VIX {vix:.1f}")
        if fii_5d_net is not None and fii_5d_net < -1000:
            flags.append(f"FII net sold ₹{abs(fii_5d_net):.0f}Cr last 5d")
        return (
            "correction", False, True, 0.0, 1.0,
            "CORRECTION: " + " | ".join(flags) +
            ". Everest off (no momentum to ride); Ocean active for reversion buys."
        )

    # NEUTRAL: Nifty very close to 200-DMA, neither extreme
    if (
        nifty_close is not None and nifty_200dma is not None
        and abs(nifty_close - nifty_200dma) / nifty_200dma < 0.02
    ):
        return (
            "neutral", True, True, 0.5, 0.5,
            f"NEUTRAL: Nifty {nifty_close:.0f} within 2% of 200-DMA. "
            "Both engines active at half-size; prefer Everest for new picks."
        )

    # BULL EXTENSION: Nifty well above 200-DMA and not at fresh top
    if nifty_close is not None and nifty_200dma is not None:
        ext = (nifty_close / nifty_200dma - 1) * 100
        msg_parts = [f"Nifty {ext:.1f}% above 200-DMA"]
        if vix is not None:
            msg_parts.append(f"VIX {vix:.1f}")
        if fii_5d_net is not None and fii_5d_net > 0:
            msg_parts.append(f"FII bought ₹{fii_5d_net:.0f}Cr last 5d")
        return (
            "bull_extension", True, False, 1.0, 0.0,
            "BULL EXTENSION: " + " | ".join(msg_parts) +
            ". Everest active full-size; Ocean dormant (no oversold names left)."
        )

    # Insufficient data — neutral fallback
    return ("neutral", True, False, 1.0, 0.0, "NEUTRAL (insufficient data)")


def _neutral_fallback(reason: str) -> RegimeReading:
    return RegimeReading(
        timestamp=datetime.utcnow(),
        nifty_close=None, nifty_200dma=None,
        pct_below_52w_high=None, vix=None, fii_5d_net=None,
        regime="neutral",
        everest_active=True, ocean_active=False,
        sizing_multiplier_everest=1.0, sizing_multiplier_ocean=0.0,
        reasoning=f"NEUTRAL (fallback): {reason}",
    )
