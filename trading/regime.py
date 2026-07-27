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

from .data import YF_THREADS

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
        from . import data as _data
        # 200-DMA FIX (2026-07-11, user-approved): 9mo (~185 bars) could never
        # fill a 200-bar average -> nifty_200dma was ALWAYS None and the
        # correction/neutral split silently degraded ("insufficient data").
        # 14mo ≈ 290 bars: real 200-DMA, real regime classification.
        hist = _data.fetch_daily([NIFTY_50, INDIA_VIX], period="14mo")
    except Exception as e:
        log.warning("Regime fetch failed: %s", e)
        return _neutral_fallback(f"fetch_failed: {e}")

    def col(tic: str) -> Optional[pd.Series]:
        df = hist.get(tic)
        if df is None or "Close" not in df.columns:
            return None
        return df["Close"]

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

    # F3: global lead-lag / risk-off cascade. A US/SGX overnight crash shows up
    # domestically as a sharp 1-day Nifty drop + a VIX spike (the "COVID
    # cascade" the user described). We detect that reflection from signals we
    # reliably have (a true US/SGX feed stays DATA_NEEDED — no cloud source).
    global_risk_off, leadlag_why = lead_lag_risk_off(nifty_s, vix_s)

    regime, everest_on, ocean_on, mult_e, mult_o, why = classify(
        nifty_close=nifty_close,
        nifty_200dma=nifty_200dma,
        pct_below_52w_high=pct_below_52w_high,
        vix=vix,
        fii_5d_net=fii_5d_net_override,
        global_risk_off=global_risk_off,
    )
    if global_risk_off:
        why = f"{why} | ⚠ lead-lag risk-off: {leadlag_why}"

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


def lead_lag_risk_off(nifty_s, vix_s) -> tuple[bool, str]:
    """F3: detect a global-cascade risk-off day from domestic reflections.

    Fires when the Nifty just dropped hard in one session AND the VIX is
    spiking — the shape an overnight US/SGX crash takes on the NSE tape.
    Pure function of the two price series; returns (fired, reason)."""
    try:
        n = nifty_s.dropna() if nifty_s is not None else None
        if n is None or len(n) < 2:
            return False, ""
        nifty_1d = float(n.iloc[-1]) / float(n.iloc[-2]) - 1
        vix_spike = False
        vix_txt = ""
        if vix_s is not None:
            v = vix_s.dropna()
            if len(v) >= 6:
                vix_now, vix_5d = float(v.iloc[-1]), float(v.iloc[-6])
                if vix_5d > 0 and vix_now > vix_5d * 1.15:
                    vix_spike = True
                    vix_txt = f"VIX {vix_now:.1f} up {(vix_now/vix_5d-1)*100:.0f}% in 5d"
        if nifty_1d <= -0.02 and vix_spike:
            return True, f"Nifty {nifty_1d*100:.1f}% 1-day drop + {vix_txt}"
        if nifty_1d <= -0.035:                     # a >3.5% single-day fall alone
            return True, f"Nifty {nifty_1d*100:.1f}% single-day fall"
    except (IndexError, ValueError, ZeroDivisionError):
        pass
    return False, ""


def classify(
    nifty_close: Optional[float],
    nifty_200dma: Optional[float],
    pct_below_52w_high: Optional[float],
    vix: Optional[float],
    fii_5d_net: Optional[float],
    global_risk_off: bool = False,
) -> tuple[str, bool, bool, float, float, str]:
    """Pure classifier — no I/O. Easy to unit-test."""
    # PANIC: Nifty crashed AND VIX panicking — OR a correction that coincides
    # with a fresh lead-lag risk-off cascade (F3: escalate defence early).
    if (
        pct_below_52w_high is not None and pct_below_52w_high <= -10
        and vix is not None and vix > 25
    ):
        return (
            "panic", False, True, 0.0, 0.5,
            f"PANIC: Nifty {pct_below_52w_high:.1f}% off 52w high, VIX {vix:.1f}. "
            "Everest off; Ocean active but half-size, tighter rules."
        )
    if (
        global_risk_off
        and pct_below_52w_high is not None and pct_below_52w_high <= -10
    ):
        return (
            "panic", False, True, 0.0, 0.5,
            f"PANIC (lead-lag escalation): Nifty {pct_below_52w_high:.1f}% off "
            "high during a global risk-off cascade. Defence maxed early."
        )

    # CORRECTION: Nifty below 200-DMA OR deep off its 52w high.
    # 52w-high trigger was -7%; loosened to -12% so a normal pullback
    # (a -7 to -10% dip) keeps the Everest momentum engine on — only a
    # genuine correction (>12% off the high) hands over to Ocean.
    nifty_below_200 = (
        nifty_close is not None and nifty_200dma is not None
        and nifty_close < nifty_200dma
    )
    deep_below_high = (
        pct_below_52w_high is not None and pct_below_52w_high <= -12
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

    # F3: a fresh lead-lag risk-off cascade tilts an otherwise-calm tape
    # defensive early (raise cash, pause new momentum) before the slow
    # 200-DMA / 52w-high triggers would catch it.
    if global_risk_off:
        return (
            "correction", False, True, 0.0, 1.0,
            "CORRECTION (lead-lag early-warning): global risk-off cascade "
            "detected — Everest paused, defensive reserve on, ahead of the "
            "200-DMA trigger."
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


def vix_crush_ok(vix_close, ratio: float = 0.75, window: int = 120) -> bool:
    """v4 correction-playbook rule (preregistered, tested on 10 episodes
    2015-2026): the fear spike is 'crushed' when VIX falls to <= 75% of
    its recent peak — the tested signal that a correction's V-recovery
    has begun (+24% COVID, +7.3% 2022, +2.0% current episode; harmless
    in mild corrections where it simply never fires)."""
    try:
        v = vix_close.dropna()
    except AttributeError:
        return False
    if len(v) < 20:
        return False
    peak = float(v.tail(window).max())
    return peak > 0 and float(v.iloc[-1]) <= ratio * peak


def compute_breadth(history: dict, dma: int = 200) -> tuple:
    """Fraction of tickers whose last close is above their own `dma`-day SMA.

    A3 breadth gate (owner: "wire it", 2026-07-27; preregistered study in
    docs/breadth_gate_results.json). Live breadth is measured on the scan's
    own panel — the ~50-name rotating bucket plus held names — a daily
    rotating sample of the full universe, whereas the study used the full
    191-ticker panel. Returns (fraction 0..1 or None, sample_size); tickers
    with fewer than `dma` bars are excluded from the sample.
    """
    above = total = 0
    for t, df in history.items():
        try:
            c = df["Close"].dropna()
            if len(c) < dma:
                continue
            total += 1
            if float(c.iloc[-1]) > float(c.rolling(dma).mean().iloc[-1]):
                above += 1
        except Exception:
            continue
    if total == 0:
        return None, 0
    return above / total, total
