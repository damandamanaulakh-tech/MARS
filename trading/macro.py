"""Macro regime detector.

Tracks India VIX + Gold + Brent crude + USD/INR + 10Y bond proxy and
classifies the current market regime: risk-on / neutral / risk-off.

Used by the engine as a position-sizing multiplier and an entry filter:
  risk-on:   100% deployment OK, full normal sizing
  neutral:   75% deployment cap, normal sizing
  risk-off:  50% deployment cap, half sizing, no new entries if VIX > 25

All inputs are free yfinance tickers. No paid data required.

Honest limitations:
- India 10Y G-Sec doesn't have a clean free yfinance ticker; we use the
  Nifty Bank index ratio (Bank Nifty / Nifty 50) as a credit-risk proxy
  (banks underperform when bond yields spike).
- VIX (^INDIAVIX) is the gold-standard fear gauge for NSE.
- Macro thresholds (VIX > 20 = caution, > 25 = risk-off) are based on
  historical NSE behaviour 2020-2026. Tune via Settings later.
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


# Free yfinance tickers. All accessible without auth.
TICKER_VIX        = "^INDIAVIX"      # India VIX
TICKER_GOLD       = "GC=F"           # Gold futures (USD/oz)
TICKER_BRENT      = "BZ=F"           # Brent crude (USD/bbl)
TICKER_USD_INR    = "INR=X"          # USD/INR FX
TICKER_BANK_NIFTY = "^NSEBANK"
TICKER_NIFTY      = "^NSEI"


@dataclass
class MacroSnapshot:
    """Point-in-time read of the macro environment."""
    timestamp: datetime
    vix: Optional[float]
    vix_pct_change_5d: Optional[float]
    gold_pct_change_5d: Optional[float]
    crude_pct_change_5d: Optional[float]
    usd_inr: Optional[float]
    usd_inr_pct_change_5d: Optional[float]
    bank_nifty_ratio: Optional[float]   # Bank Nifty / Nifty 50
    regime: str                          # "risk_on" | "neutral" | "risk_off"
    deployment_cap_pct: float            # 0.5 / 0.75 / 1.0
    sizing_multiplier: float             # 0.5 / 1.0 (used by position_size)
    halt_new_entries: bool
    reasoning: str

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["timestamp"] = self.timestamp.isoformat()
        return d


def _safe_last(series: pd.Series) -> Optional[float]:
    if series is None or series.empty:
        return None
    try:
        val = float(series.dropna().iloc[-1])
        return val if val == val else None  # filter NaN
    except (IndexError, ValueError):
        return None


def _pct_change_n_days(series: pd.Series, n: int = 5) -> Optional[float]:
    if series is None or len(series.dropna()) <= n:
        return None
    try:
        clean = series.dropna()
        return float((clean.iloc[-1] / clean.iloc[-n - 1] - 1) * 100)
    except (IndexError, ValueError, ZeroDivisionError):
        return None


def fetch_macro_snapshot() -> MacroSnapshot:
    """Pulls last 10 trading days of macro data and classifies the regime.

    Network-bound: ~5s. Cache the result in the engine.
    """
    tickers = [
        TICKER_VIX, TICKER_GOLD, TICKER_BRENT,
        TICKER_USD_INR, TICKER_BANK_NIFTY, TICKER_NIFTY,
    ]
    try:
        from . import data as _data
        hist = _data.fetch_daily(tickers, period="1mo")
    except Exception as e:
        log.warning("Macro snapshot fetch failed: %s", e)
        return _neutral_fallback(reason=f"fetch_failed: {e}")

    def col(ticker: str) -> Optional[pd.Series]:
        df = hist.get(ticker)
        if df is None or "Close" not in df.columns:
            return None
        return df["Close"]

    vix_s = col(TICKER_VIX)
    gold_s = col(TICKER_GOLD)
    brent_s = col(TICKER_BRENT)
    inr_s = col(TICKER_USD_INR)
    bank_s = col(TICKER_BANK_NIFTY)
    nifty_s = col(TICKER_NIFTY)

    vix = _safe_last(vix_s)
    vix_5d = _pct_change_n_days(vix_s)
    gold_5d = _pct_change_n_days(gold_s)
    crude_5d = _pct_change_n_days(brent_s)
    usd_inr = _safe_last(inr_s)
    usd_inr_5d = _pct_change_n_days(inr_s)

    bank_last = _safe_last(bank_s)
    nifty_last = _safe_last(nifty_s)
    bank_ratio = (
        bank_last / nifty_last
        if bank_last is not None and nifty_last not in (None, 0)
        else None
    )

    regime, cap, mult, halt, why = classify_regime(
        vix=vix,
        vix_5d=vix_5d,
        crude_5d=crude_5d,
        gold_5d=gold_5d,
        usd_inr_5d=usd_inr_5d,
    )

    return MacroSnapshot(
        timestamp=datetime.utcnow(),
        vix=vix, vix_pct_change_5d=vix_5d,
        gold_pct_change_5d=gold_5d, crude_pct_change_5d=crude_5d,
        usd_inr=usd_inr, usd_inr_pct_change_5d=usd_inr_5d,
        bank_nifty_ratio=bank_ratio,
        regime=regime, deployment_cap_pct=cap,
        sizing_multiplier=mult, halt_new_entries=halt, reasoning=why,
    )


def classify_regime(
    vix: Optional[float],
    vix_5d: Optional[float],
    crude_5d: Optional[float],
    gold_5d: Optional[float],
    usd_inr_5d: Optional[float],
) -> tuple[str, float, float, bool, str]:
    """Returns (regime_name, deployment_cap, sizing_multiplier, halt_new, reasoning).

    Hard rules (any one triggers risk_off):
      - VIX > 25 (panic level)
      - VIX jumped > 30% in 5 days (fear spike)
      - Crude up > 8% in 5 days (Indian importer pain)
      - Rupee weakened > 1.5% in 5 days (FII outflow signal)
      - Gold up > 5% in 5 days (flight-to-safety)

    Soft rules -> neutral:
      - VIX 18-25, or any single 5d change > half the hard threshold

    Otherwise -> risk_on.
    """
    flags = []

    if vix is not None and vix > 25:
        flags.append(f"VIX {vix:.1f} > 25 (panic)")
    if vix_5d is not None and vix_5d > 30:
        flags.append(f"VIX +{vix_5d:.0f}% in 5d (fear spike)")
    if crude_5d is not None and crude_5d > 8:
        flags.append(f"Crude +{crude_5d:.1f}% in 5d")
    if usd_inr_5d is not None and usd_inr_5d > 1.5:
        flags.append(f"INR weakened {usd_inr_5d:.1f}% in 5d")
    if gold_5d is not None and gold_5d > 5:
        flags.append(f"Gold +{gold_5d:.1f}% in 5d (safety bid)")

    if flags:
        return (
            "risk_off",
            0.5,        # deploy at most 50% of capital
            0.5,        # size each new position at half
            True,       # halt any NEW entries
            "RISK_OFF: " + " | ".join(flags),
        )

    soft = []
    if vix is not None and 18 <= vix <= 25:
        soft.append(f"VIX {vix:.1f} (caution zone)")
    if vix_5d is not None and 15 <= vix_5d <= 30:
        soft.append(f"VIX +{vix_5d:.0f}% in 5d (rising fear)")
    if crude_5d is not None and 4 <= crude_5d <= 8:
        soft.append(f"Crude +{crude_5d:.1f}% in 5d")
    if usd_inr_5d is not None and 0.7 <= usd_inr_5d <= 1.5:
        soft.append(f"INR softer {usd_inr_5d:.1f}% in 5d")

    if soft:
        return (
            "neutral",
            0.75,
            1.0,        # full size, but cap aggregate deployment
            False,
            "NEUTRAL: " + " | ".join(soft),
        )

    msg = "RISK_ON: macro indicators calm"
    if vix is not None:
        msg += f" (VIX {vix:.1f})"
    return ("risk_on", 1.0, 1.0, False, msg)


def _neutral_fallback(reason: str) -> MacroSnapshot:
    """When the fetch fails entirely, default to neutral (don't take the
    safe-or-aggressive path on bad data)."""
    return MacroSnapshot(
        timestamp=datetime.utcnow(),
        vix=None, vix_pct_change_5d=None,
        gold_pct_change_5d=None, crude_pct_change_5d=None,
        usd_inr=None, usd_inr_pct_change_5d=None,
        bank_nifty_ratio=None,
        regime="neutral",
        deployment_cap_pct=0.75,
        sizing_multiplier=1.0,
        halt_new_entries=False,
        reasoning=f"NEUTRAL (fallback): {reason}",
    )
