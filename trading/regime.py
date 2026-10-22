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
    status: str = "OK"                     # a dead feed never builds a reading

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


class RegimeFeedDead(RuntimeError):
    """The Nifty feed gave nothing the regime can be judged on.

    XR-050 (2026-10-22 entry-path batch, owner: "fix all 14"). fetch_regime
    used to swallow its own failure and answer NEUTRAL — Everest on, cash
    reserve 0 — so on a dead-feed day in a correction the engine dropped the
    20% correction reserve, journaled (and pushed to the phone) a regime
    change to neutral that never happened, and its own sticky dead-feed
    brake (AUDIT FIX C7, which lives in the engine's `except` around
    fetch_regime) could never run. The common failure did not even raise:
    fetch_daily returns no rows, and the classifier fell through to
    "NEUTRAL (insufficient data)". Now both cases RAISE this, so the engine
    keeps its last known regime and reserve and takes the C7 path exactly
    as it does for any other regime failure."""
    status = "FEED_DEAD"

    def __init__(self, reason: str):
        super().__init__(f"FEED_DEAD: {reason}")
        self.reason = reason


def fetch_regime(fii_5d_net_override: Optional[float] = None) -> RegimeReading:
    """Pulls last 14 months of Nifty + VIX, classifies regime.

    `fii_5d_net_override` lets the caller plug in fresh FII data from
    trading.flows. If None, we proceed without it (less precise classification).

    Raises RegimeFeedDead when the fetch fails or returns no Nifty close /
    no 200-DMA — never a made-up NEUTRAL (XR-050, see the class).
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
        raise RegimeFeedDead(f"fetch_failed: {e}") from e
    if not isinstance(hist, dict):
        raise RegimeFeedDead("fetch returned no price table")

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

    if nifty_close is None or nifty_200dma is None:
        n_bars = 0 if nifty_s is None else int(nifty_s.dropna().shape[0])
        raise RegimeFeedDead(
            f"Nifty close {'missing' if nifty_close is None else 'ok'}, "
            f"200-DMA {'missing' if nifty_200dma is None else 'ok'} "
            f"({n_bars} Nifty bars; 200 needed)")

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


def correction_episode_start(nifty_close, dma: int = 200):
    """First date of the CURRENT run of Nifty closes below their 200-DMA,
    or None when the latest close is not below it. This is the episode the
    v4 correction study measured its VIX peak over."""
    try:
        s = nifty_close.dropna()
    except AttributeError:
        return None
    if len(s) < dma:
        return None
    ma = s.rolling(dma).mean()
    below = ((s < ma) & ma.notna()).to_numpy()
    if not below[-1]:
        return None
    i = len(below) - 1
    while i > 0 and below[i - 1]:
        i -= 1
    return s.index[i]


def vix_crush_ok(vix_close, ratio: float = 0.75, window: int = 120,
                 since=None) -> bool:
    """v4 correction-playbook rule (preregistered, tested on 10 episodes
    2015-2026): the fear spike is 'crushed' when VIX falls to <= 75% of
    its peak — the tested signal that a correction's V-recovery has begun
    (+24% COVID, +7.3% 2022, +2.0% current episode).

    OWNER ORDER 2026-10-02 (audit item 4): the study took the peak from the
    START OF THE CORRECTION EPISODE (scripts/param_search_v4_correction.py,
    `vix.iloc[a:b].cummax()`). The live engine took it over a fixed 120
    days, so a spike from BEFORE the correction counted, and the rule read
    true on 59% of correction days instead of the tested 39% (measured on
    the committed 15-year file). Pass `since` = the episode start to get
    the tested rule. Without `since` the old 120-day window stands, for the
    Ocean knife rail, which uses it only as a rare override."""
    try:
        v = vix_close.dropna()
    except AttributeError:
        return False
    if since is not None:
        cutoff = pd.Timestamp(since).date()
        v = v[[d >= cutoff for d in v.index.date]]
        if v.empty:
            return False
        peak = float(v.max())
        return peak > 0 and float(v.iloc[-1]) <= ratio * peak
    if len(v) < 20:
        return False
    peak = float(v.tail(window).max())
    return peak > 0 and float(v.iloc[-1]) <= ratio * peak


def compute_breadth(history: dict, dma: int = 200) -> tuple:
    """Fraction of tickers whose last close is above their own `dma`-day SMA.

    A3 breadth gate (owner: "wire it", 2026-07-27; preregistered study in
    docs/breadth_gate_results.json). Pure: it reads whatever `history` it is
    handed. Since 2026-10-16 the engine hands it the study-panel names of the
    scan (the 191 the 40-65% band was measured on) and only falls back to the
    whole scan when too few of them are present — see
    engine._cache_breadth_today. Returns (fraction 0..1 or None,
    sample_size); tickers with fewer than `dma` bars are excluded.
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
