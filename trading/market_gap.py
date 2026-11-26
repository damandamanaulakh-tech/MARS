"""Index versus the typical stock — the owner's "bubble" measure.

Owner, 2026-11-26: "now market goes quite high but not much movement in
stocks, this bubble I want to define". On 25 November the Nifty was +7.2%
over 60 sessions while the median stock in the scan list was -2.9%: a gap of
10.1 points, with about a quarter of stocks beating the index.

DISPLAY ONLY. A pure reduction over daily closes the scan already holds: no
network, no config change, and no gate, size, stop or target reads it (a
test asserts engine entry code never branches on it). It describes what has
happened; nothing here was tested as a predictor, and the page says so.

For each window (20 and 60 sessions):
  index      = benchmark close now / close `window` sessions ago - 1
  median     = the median of the same return over the scan list's stocks
  gap        = index - median, in percentage points
  beat       = share of stocks whose return is above the index's
  up         = share of stocks with a positive return
A stock votes only with a full window of closes ending within STALE_DAYS of
the benchmark's last bar, so a halted or newly listed name cannot dilute the
count. Fewer than MIN_SAMPLE voters = no reading (None), never a thin one.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

WINDOWS = (20, 60)
MIN_SAMPLE = 100
STALE_DAYS = 7

# The words on the card. ASSUMED cut-offs — chosen to describe the owner's
# own example (gap 10.1 points, 26% of stocks ahead of the index), not
# measured against any outcome. Shown on the page as assumptions.
NARROW_GAP_POINTS = 5.0
NARROW_BEAT_SHARE = 0.40
ASSUMED = {
    "narrow_gap_points": NARROW_GAP_POINTS,
    "narrow_beat_share": NARROW_BEAT_SHARE,
    "source": "owner's 25 Nov example; descriptive, not tested as a signal",
}


def _closes(frame) -> Optional[pd.Series]:
    try:
        series = frame["Close"].dropna()
    except Exception:                                          # noqa: BLE001
        return None
    return series if len(series) else None


def _window_return(series: pd.Series, window: int) -> Optional[float]:
    if len(series) <= window:
        return None
    then, now = float(series.iloc[-1 - window]), float(series.iloc[-1])
    if then <= 0 or now <= 0 or then != then or now != now:
        return None
    return now / then - 1.0


def state_for(gap_points: Optional[float], beat_share: Optional[float],
              index_return: Optional[float]) -> str:
    """'narrow' = the index is up and carried by few stocks; 'broad' = the
    typical stock is keeping up or ahead; 'falling' = the index itself is
    down; '' = not enough to say."""
    if gap_points is None or beat_share is None or index_return is None:
        return ""
    if index_return <= 0:
        return "falling"
    if gap_points >= NARROW_GAP_POINTS and beat_share < NARROW_BEAT_SHARE:
        return "narrow"
    return "broad"


def reading(history: dict, benchmark, exclude: Iterable[str] = (),
            windows: Iterable[int] = WINDOWS) -> Optional[dict]:
    """The gap reading, or None when the benchmark or the sample is too thin.

    `history` = {ticker: daily OHLCV frame}; `benchmark` = the index's frame;
    `exclude` = names that are not company shares (fund units)."""
    bench = _closes(benchmark)
    if bench is None:
        return None
    last_bar = bench.index[-1]
    skip = set(exclude or ())
    out: dict = {"asof": last_bar.strftime("%Y-%m-%d") if hasattr(last_bar, "strftime") else str(last_bar),
                 "windows": {}}
    for window in windows:
        index_return = _window_return(bench, int(window))
        if index_return is None:
            continue
        returns = []
        for ticker, frame in (history or {}).items():
            if ticker in skip:
                continue
            series = _closes(frame)
            if series is None:
                continue
            try:
                if (last_bar - series.index[-1]).days > STALE_DAYS:
                    continue
            except Exception:                                  # noqa: BLE001
                continue
            value = _window_return(series, int(window))
            if value is not None:
                returns.append(value)
        if len(returns) < MIN_SAMPLE:
            continue
        ordered = pd.Series(returns)
        median = float(ordered.median())
        beat = float((ordered > index_return).mean())
        gap = (index_return - median) * 100.0
        out["windows"][str(int(window))] = {
            "index_pct": round(index_return * 100.0, 2),
            "median_pct": round(median * 100.0, 2),
            "gap_points": round(gap, 2),
            "beat_share": round(beat, 4),
            "up_share": round(float((ordered > 0).mean()), 4),
            "sample": int(len(returns)),
            "state": state_for(gap, beat, index_return),
        }
    return out if out["windows"] else None
