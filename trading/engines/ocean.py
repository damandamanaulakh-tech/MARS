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
import math
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


# ---------------- Fundamental survival check ---------------------------------
# REWRITTEN 2026-08-26 (owner order: wire Ocean with the crash guard). The
# old check called yfinance .info live — Yahoo 429-blocks Render, so in
# production it ALWAYS failed and Ocean could never have picked a single
# stock even when enabled. It now reads the fundamentals STORE (committed
# CSV + the monthly Node 3 refresh via data_refresh's injected reader):
# D/E < 0.5 and latest annual FCF > 0. The original spec's current-ratio
# leg is dropped honestly — current assets/liabilities are not in the
# store; the two remaining legs are the original thresholds. Missing
# fundamentals = FAIL (a dip-buyer must prove survival, never assume it).

_fund_cache: dict[str, dict[str, dict[str, float]]] = {}


def _fundamentals(symbol: str) -> dict[str, dict[str, float]]:
    global _fund_cache
    if not _fund_cache:
        import csv
        from pathlib import Path
        path = (Path(__file__).resolve().parents[2] / "data" /
                "fundamentals_n200_2026-08-09.csv")
        try:
            with open(path) as f:
                for r in csv.DictReader(f):
                    _fund_cache.setdefault(r["symbol"], {}).setdefault(
                        r["field"], {})[r["as_of"]] = float(r["value"])
        except OSError:
            log.warning("ocean: fundamentals CSV missing")
        try:
            from trading import data_refresh as _dr
            for r in _dr.stored_fundamentals_rows():
                _fund_cache.setdefault(r["symbol"], {}).setdefault(
                    r["field"], {})[r["as_of"]] = float(r["value"])
        except Exception as e:                                # noqa: BLE001
            log.warning("ocean: fundamentals overlay failed: %s", e)
    return _fund_cache.get(symbol, {})


def survival_check(ticker: str) -> tuple[bool, list[str]]:
    """(passes, reasons) from the fundamentals store. Backtests return
    False — no historical snapshot exists, and pretending otherwise would
    be lookahead."""
    if is_backtest_mode():
        return False, ["backtest-mode: fundamentals skipped (no snapshot)"]
    f = _fundamentals(ticker.replace(".NS", ""))
    # XR-056 (2026-10-22 entry-path batch): a missing value (NaN / inf)
    # used to PASS — every comparison with NaN is False, so "equity <= 0",
    # "D/E >= 0.5" and "FCF <= 0" all read "fine". Unfinite values are
    # dropped before anything is judged, so they can only make the check
    # FAIL ("missing"), never pass it. And debt and equity are now read on
    # the SAME balance-sheet date (the latest one that has both), not each
    # on its own latest date.
    def _clean(series: dict) -> dict:
        out = {}
        for d, v in (series or {}).items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                out[d] = fv
        return out

    debt = _clean(f.get("annualTotalDebt", {}))
    eq = _clean(f.get("annualStockholdersEquity", {}))
    fcf = _clean(f.get("annualFreeCashFlow", {}))
    if not debt or not eq or not fcf:
        return False, ["fundamentals missing from store"]
    common = sorted(set(debt) & set(eq))
    if not common:
        return False, ["debt and equity not reported for the same year"]
    bs_date = common[-1]
    d_latest = debt[bs_date]
    e_latest = eq[bs_date]
    if e_latest <= 0:
        return False, ["negative/zero equity"]
    de = d_latest / e_latest
    if not math.isfinite(de) or de >= 0.5:
        return False, [f"D/E {de:.2f} >= 0.5"]
    fcf_latest = fcf[max(fcf)]
    if fcf_latest <= 0:
        return False, [f"latest FCF {fcf_latest:.0f} not positive"]
    return True, [f"D/E {de:.2f} ok ({bs_date})", f"FCF positive ({max(fcf)})"]


# ---------------- Crash guard (owner-approved 2026-08-26) ---------------------
# Four rails; every threshold traces to the owner's own evidence:
#   R1 VIX knife  — v4 study: VIX-crush was the ONLY dip rule that tested
#                   positive (+2.3pp); buying while VIX spikes is the knife.
#   R2 damage     — the canonical tail_down3_5d store (Node 9), judged
#                   against the PANEL'S OWN rolling 90th percentile.
#                   OWNER ORDER 2026-10-04 ("own-panel damage rule"): it
#                   used the frozen 0.213 from the owner's 1996-2024 file,
#                   which on our stock list is ON 84% of days (calm bull
#                   days included) — measured on the committed 191-stock
#                   panel, 2014-2026, with Ocean opening ZERO positions in
#                   12 years. The own-panel line is ON ~10% of days. The
#                   same correction is in docs/PREFALL_FRAMEWORK_2026-09-29.md.
#   R3 reclaim    — their reclaim family: buy the turn, never the fall.
#   R4 danger veto— the 12 calibration-VERIFIED tripwires.

VIX_Q_WINDOW = 120
VIX_Q = 0.90


def crash_guard_market(vix_close: Optional[pd.Series],
                       damage_store: Optional[dict]) -> tuple[bool, str]:
    """(ok, reason). Fail-SAFE: anything unknowable blocks — a dip-buyer
    must prove the market is not in free-fall, never assume it."""
    if vix_close is None or len(vix_close.dropna()) < VIX_Q_WINDOW:
        return False, "vix_unknown"
    v = vix_close.dropna()
    q90 = float(v.tail(VIX_Q_WINDOW).quantile(VIX_Q))
    last = float(v.iloc[-1])
    rising = len(v) >= 6 and last > float(v.iloc[-6])
    if last > q90 and rising:
        # spike still building — unless the crush rule says the fear break
        # already happened (the one measured re-entry, +2.3pp in v4)
        try:
            from trading.regime import vix_crush_ok
            if not vix_crush_ok(v):
                return False, f"vix_knife({last:.1f}>q90 {q90:.1f}, rising)"
        except Exception:
            return False, "vix_knife_unverifiable"
    try:
        from trading import internals
        if not internals.is_fresh(damage_store):
            return False, "damage_reading_missing_or_stale"
        q90_cluster = damage_store.get("q90_cluster")
        if q90_cluster is None:
            return False, "damage_reading_unreadable"
        if q90_cluster:
            return False, "damage_cluster_own_q90"
    except Exception:
        return False, "damage_reading_unreadable"
    return True, "ok"


def crash_guard_stock(df: pd.DataFrame,
                      danger_fired: list[str]) -> tuple[bool, str]:
    """(ok, reason) for one candidate. R3 reclaim + R4 danger veto."""
    if danger_fired:
        return False, "danger_" + ",".join(danger_fired)
    try:
        close = df["Close"].dropna()
        low = df["Low"].dropna()
        high = df["High"].dropna()
        if len(close) < 22:
            return False, "too_short"
        if float(low.iloc[-1]) <= float(low.iloc[-21:-1].min()):
            return False, "fresh_20d_low"
        if float(close.iloc[-1]) <= float(high.iloc[-2]):
            return False, "no_reclaim_above_prior_high"
    except (KeyError, ValueError, IndexError):
        return False, "ohlc_unreadable"
    return True, "ok"


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
