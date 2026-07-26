"""Structural flags (owner-approved 2026-07-26) — context markers computed
at scan time from data the engine already holds. Display + journal only;
no flag changes any buy/sell decision.

  cascade    — scanned/held names that fell ≥5% today (heavyweight shock
               vs company failure needs the residual view; v1 marks the day)
  compressed — sectors whose scanned names sit ≥40% below their own 252d
               highs on average (sector rout, not single-stock news)
  f1_confirmed — held names whose last three monthly lows rise (the turn
               test that TCS's false bottom failed and ICICI passed)
"""

from __future__ import annotations


def compute_flags(history: dict, held: list, sector_map: dict) -> dict:
    """history: {ticker: OHLCV DataFrame} as the scan already loaded."""
    cascade = []
    off_high = {}
    for t, df in history.items():
        try:
            close = df["Close"].dropna()
            if len(close) < 2:
                continue
            day = float(close.iloc[-1] / close.iloc[-2] - 1) * 100
            if day <= -5.0:
                cascade.append({"ticker": t, "day_pct": round(day, 1)})
            hi = float(close.iloc[-252:].max())
            if hi > 0:
                off_high[t] = float(close.iloc[-1] / hi - 1) * 100
        except Exception:
            continue

    by_sector: dict[str, list] = {}
    for t, v in off_high.items():
        by_sector.setdefault(sector_map.get(t, "Unknown"), []).append(v)
    compressed = [
        {"sector": s, "avg_off_high_pct": round(sum(v) / len(v), 1), "n": len(v)}
        for s, v in by_sector.items()
        if len(v) >= 3 and sum(v) / len(v) <= -40.0
    ]

    f1 = []
    for t in held:
        df = history.get(t)
        if df is None or "Low" not in df:
            continue
        try:
            lows = df["Low"].dropna().groupby(df["Low"].dropna().index.to_period("M")).min()
            if len(lows) >= 4:
                a, b, c = float(lows.iloc[-4]), float(lows.iloc[-3]), float(lows.iloc[-2])
                f1.append({"ticker": t, "confirmed": bool(a < b < c)})
        except Exception:
            continue

    return {"cascade": sorted(cascade, key=lambda x: x["day_pct"])[:12],
            "compressed_sectors": compressed,
            "f1_held": f1}
