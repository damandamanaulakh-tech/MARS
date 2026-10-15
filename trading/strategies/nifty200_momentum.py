"""Nifty 200 Momentum 30 strategy preset.

This module implements NSE's published Nifty 200 Momentum 30 index
methodology as an alternate scoring + selection scheme.

Methodology (from NSE white paper, Sep 2020):
  - Universe: top 200 NSE stocks by market cap (Nifty 200)
  - Score: average of two normalised momentum scores:
      * 6-month return / 6-month annualised volatility
      * 12-month return / 12-month annualised volatility
  - Top 30 by score = portfolio
  - Rebalance: semi-annually (June and December)
  - Weight: factor-tilt (score x free-float mcap), 5% cap per stock

Historical CAGR (per NSE factsheets):
  - 5-year:  29.7% (as of Nov 2024)
  - 10-year: 23.3%
  - vs Nifty 50 ~12-14%

Implementation here is the **scoring** part. The engine reuses this scoring
when cfg.mode == "nifty200_momentum". Universe expansion to ~200 tickers is
in config.py / universe presets.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def momentum_score(close: pd.Series) -> Optional[float]:
    """Normalised momentum score per NSE methodology.

    Returns the average of (6m_return / 6m_vol) and (12m_return / 12m_vol),
    expressed in the 0-100 score scale used by the existing engine.

    Requires at least 252 daily bars (~1 year). Returns None for shorter
    series, and the scan then leaves that name out entirely (it is not
    scored or bought).
    """
    if close is None or len(close.dropna()) < 252:
        return None
    clean = close.dropna()

    # 6-month leg (~126 trading days)
    p_6m_ago = float(clean.iloc[-127])
    last = float(clean.iloc[-1])
    if p_6m_ago <= 0:
        return None
    r6 = (last / p_6m_ago - 1)
    daily_6m = clean.iloc[-126:].pct_change().dropna()
    vol6 = float(daily_6m.std() * math.sqrt(252))
    if vol6 <= 0:
        return None
    sharpe6 = r6 / vol6

    # 12-month leg (~252 trading days)
    p_12m_ago = float(clean.iloc[-253] if len(clean) >= 253 else clean.iloc[0])
    if p_12m_ago <= 0:
        return None
    r12 = (last / p_12m_ago - 1)
    daily_12m = clean.iloc[-252:].pct_change().dropna()
    vol12 = float(daily_12m.std() * math.sqrt(252))
    if vol12 <= 0:
        return None
    sharpe12 = r12 / vol12

    raw_avg = (sharpe6 + sharpe12) / 2.0

    # Map sharpe-like average (-2 to +4 typical range) onto 0-100.
    # +2 -> 100 (very strong momentum)
    #  0 -> 50  (no edge)
    # -2 -> 0   (negative momentum)
    score = 50.0 + raw_avg * 25.0
    return float(max(0.0, min(100.0, score)))


def rank_universe(history_map: dict[str, pd.DataFrame], top_n: int = 30) -> list[dict]:
    """Returns [{ticker, score, last_close}] sorted desc, top_n only.

    Stocks without enough history (252 bars) are excluded.
    """
    ranked = []
    for ticker, hist in history_map.items():
        if hist is None or "Close" not in hist.columns:
            continue
        score = momentum_score(hist["Close"])
        if score is None:
            continue
        ranked.append({
            "ticker": ticker,
            "score": score,
            "last_close": float(hist["Close"].iloc[-1]),
        })
    ranked.sort(key=lambda r: -r["score"])
    return ranked[:top_n]
