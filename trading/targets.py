"""Per-horizon targets as k x ATR — pure, money-path safe.

OWNER ORDER 2026-09-10: "all Horizon have target formula" -> then "C- k x ATR"
picked from the options card.

WHY ATR AND NOT THE SCORE. The obvious formula would scale the target with the
entry score: better score, further target. I measured that first, over 22,749
monthly observations on the committed 191 x 12y panel, and it does not hold —
above the 200DMA the 12-month median by score band runs 45-55: +21.5%,
55-62: +15.3%, 62-70: +16.6%, 70+: +11.2%. The score does not order forward
returns, so a score-driven target would be fitting noise. What the score DOES
order is size (see sizing.py), and what actually varies a target's reachability
is the stock's own volatility. Hence ATR.

WHAT k MEANS. A target of k x ATR is "k average days of range away". k is not
a free parameter and it was not guessed: it is ANCHORED so that the MEDIAN
name reproduces the target the owner already decided on. The anchor was
measured, not estimated — ATR as a share of price over the committed 191-name
panel:

    p10 1.89%   p25 2.09%   MEDIAN 2.50%   p75 3.02%   p90 3.48%

so k = old_fixed_target / 2.50%:

    horizon      sessions    k     target at median ATR    old fixed
    intraday        1       1.0            2.5%              2.5%   same
    swing          20       4.0           10.0%             10.0%   same
    short          63       5.0           12.5%              6.0%   see below
    positional    252      10.0           25.0%             25.0%   same
    momentum      200       8.0           20.0%             20.0%   SAME

The momentum bracket is unchanged IN EFFECT and that is deliberate: +20% is
the owner's own Node-12 decision of 2026-08-24, measured over four
out-of-sample windows, and nothing here supersedes it. k=8 reproduces it for
the median name and only moves the target for names whose range is far from
ordinary — 15.1% at the 10th percentile of volatility, 27.8% at the 90th.

`short` is the one horizon whose target genuinely moves, from 6% to 12.5%,
because its HORIZON moved: it used to hold 7 days and now holds up to 3
months (see modes.py — the owner's short and swing were swapped against their
own meaning). k=5 sits between swing's 4 and momentum's 8, in proportion to
the horizon. Holding 63 sessions for a 6% target would have been the old
number surviving a change it no longer fits.

FIRST CUT WAS WRONG, recorded so it is not repeated: k was set from an
ESTIMATE that ATR is about the close-to-close daily sigma (1.26% at 20%
annual vol). ATR is materially larger because it includes the high-low range
and the overnight gap — really 2.50%. The estimate would have put the
momentum target at 33-55% on real names and silently overturned an owner
decision. Anchor a constant to a measurement, never to an estimate.

FLOOR AND CEILING. A target is clamped into [MIN_TARGET_PCT, MAX_TARGET_PCT]
per horizon so a freakishly quiet stock cannot get a 0.5% target (which the
costs alone would eat) and a freakishly wild one cannot get a 90% target no
position would ever reach.

Pure: no network, no config mutation, no engine/risk/orders import.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# k per mode, and the [min, max] target band as fractions.
ATR_TARGET_K: dict[str, float] = {
    "intraday": 1.0,
    "swing": 4.0,
    "short": 5.0,
    "positional": 10.0,
    "nifty200_momentum": 8.0,
}
# The measurement k is anchored to. Regenerate with scripts/ if the panel
# changes; a test pins that k x MEDIAN_ATR_PCT still reproduces each
# horizon's owner-decided target.
MEDIAN_ATR_PCT_OF_PRICE = 2.50
TARGET_BAND: dict[str, tuple[float, float]] = {
    "intraday": (0.010, 0.060),
    "swing": (0.030, 0.200),
    "short": (0.050, 0.350),
    "positional": (0.100, 0.600),
    "nifty200_momentum": (0.100, 0.600),
}
ATR_WINDOW = 14


def atr(df: pd.DataFrame, n: int = ATR_WINDOW) -> Optional[float]:
    """Average true range over `n` bars, in rupees. None when unprovable.

    None is not zero: a name with too little history has an UNKNOWN range,
    and the caller falls back to the mode's fixed percentage rather than
    inventing a target from a number it does not have.
    """
    if df is None or len(df) < n + 1:
        return None
    need = {"High", "Low", "Close"}
    if not need.issubset(df.columns):
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(),
                    (high - prev).abs(),
                    (low - prev).abs()], axis=1).max(axis=1)
    v = tr.tail(n).mean()
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def target_pct_for(mode: str, price: float, df: Optional[pd.DataFrame],
                   fallback_pct: float) -> tuple[float, dict]:
    """(target as a fraction of entry, diagnostics).

    Falls back to the mode's fixed percentage — never to a guess — when the
    ATR cannot be computed, and the diagnostic says which happened, so a page
    can never show an ATR target that was really the old fixed number.
    """
    m = str(mode or "nifty200_momentum")
    k = ATR_TARGET_K.get(m)
    if k is None or not price or price <= 0:
        return float(fallback_pct), {"source": "fixed", "mode": m,
                                     "reason": "unknown_mode_or_price"}
    a = atr(df)
    if a is None:
        return float(fallback_pct), {"source": "fixed", "mode": m,
                                     "reason": "no_atr", "k": k}
    lo, hi = TARGET_BAND.get(m, (0.0, 1.0))
    raw = k * a / float(price)
    pct = max(lo, min(hi, raw))
    return pct, {
        "source": "atr", "mode": m, "k": k,
        "atr": round(a, 2),
        "atr_pct_of_price": round(a / float(price) * 100, 3),
        "raw_pct": round(raw * 100, 2),
        "clamped": bool(abs(pct - raw) > 1e-12),
        "band_pct": [round(lo * 100, 1), round(hi * 100, 1)],
        "target_pct": round(pct * 100, 2),
    }


def target_price_for(mode: str, entry_price: float,
                     df: Optional[pd.DataFrame],
                     fallback_pct: float) -> tuple[float, dict]:
    """Absolute target price for a new position."""
    pct, diag = target_pct_for(mode, entry_price, df, fallback_pct)
    return round(float(entry_price) * (1.0 + pct), 2), diag
