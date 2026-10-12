"""Visual candle chart — owner ask 2026-07-29 ("a & c" → "go").

Draws a REAL candlestick chart as server-side SVG so the owner's eyes can
verify what the engine claims: the last ~90 daily bars, volume underneath,
▲/▼ markers on the exact bars where any of the 34 candle structures fired,
and the entry/stop/target lines for held positions.

Display only — nothing here feeds a trading decision. Zero new
dependencies (no JS chart library, nothing for Render's 512 MB): pure
Python string-building, testable like everything else. Data comes from
the same stores the engine reads (Mongo OHLCV, repo zip fallback);
no data = the page says DATA_NEEDED, never a fake chart.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pandas as pd

from trading.candles import (BEARISH_PATTERNS, BULLISH_PATTERNS,
                             scan_all_patterns)

log = logging.getLogger(__name__)

CHART_BARS = 90          # sessions drawn
_PATTERN_TAIL = 220      # bars kept for detection (urr needs ~31; margin)

_GREEN = "#059669"
_RED = "#dc2626"
_INK_FAINT = "rgba(0,0,0,0.45)"
_GRID = "rgba(0,0,0,0.08)"


def load_ohlcv(ticker: str, tail: int = _PATTERN_TAIL) -> pd.DataFrame | None:
    """Daily OHLCV for one ticker: Mongo store first (freshest, Friday
    top-up), repo zip second (ships with every deploy). None = DATA_NEEDED.
    `tail` bounds RAM (512 MB box); the old /signal page (removed 2026-10-12) passed a longer one because
    momentum_score needs >=252 bars."""
    try:
        from trading.history_store import records_to_df
        from . import db
        if db.is_connected():
            df = records_to_df(db.load_history_doc(ticker))
            if df is not None and not df.empty:
                return df.tail(tail)
    except Exception as e:
        log.warning("chart: mongo load failed for %s: %s", ticker, e)
    try:
        zpath = Path(__file__).resolve().parents[1] / "data" / \
            "history_upstox_export_2026-07-19.zip"
        with zipfile.ZipFile(zpath) as z:
            name = f"{ticker}.csv"
            if name in set(z.namelist()):
                df = pd.read_csv(io.BytesIO(z.read(name)),
                                 parse_dates=["Date"], index_col="Date")
                return df.sort_index().tail(tail)
    except Exception as e:
        log.warning("chart: zip load failed for %s: %s", ticker, e)
    return None


def per_bar_structures(df: pd.DataFrame, bars: int = CHART_BARS) -> dict:
    """{bar position in the drawn window: [(name, 'bull'|'bear'), ...]} —
    every structure re-detected on the data as it stood at that bar's close,
    exactly how the engine would have read it that day."""
    out: dict[int, list] = {}
    n = len(df)
    start = max(0, n - bars)
    for j in range(start, n):
        try:
            fired = scan_all_patterns(df.iloc[:j + 1])
        except Exception:
            continue
        hits = [(name, "bull") for name in BULLISH_PATTERNS
                if fired.get(name)]
        hits += [(name, "bear") for name in BEARISH_PATTERNS
                 if fired.get(name)]
        if fired.get("breakout"):
            hits.append(("breakout", "bull"))
        if fired.get("urr"):
            hits.append(("urr", "bull"))
        if hits:
            out[j - start] = hits
    return out


def render_svg(df: pd.DataFrame, hits: dict | None = None,
               levels: list | None = None, bars: int = CHART_BARS) -> str:
    """The chart itself. `levels` = [(label, price, color), ...] horizontal
    lines (entry/stop/target). Pure function of its inputs."""
    tail = df.dropna(subset=["Close"]).tail(bars)
    n = len(tail)
    if n == 0:
        return ""
    hits = hits or {}
    levels = [(str(l), float(p), c) for l, p, c in (levels or [])
              if p and p > 0]

    W, H = 960, 470
    pad_l, pad_r, pad_t = 56, 12, 14
    price_h, vol_top, vol_h, x_lbl_y = 300, 336, 96, H - 8
    lo = float(tail["Low"].min())
    hi = float(tail["High"].max())
    for _, p, _c in levels:
        lo, hi = min(lo, p), max(hi, p)
    span = (hi - lo) or 1.0
    lo -= span * 0.03
    hi += span * 0.03
    span = hi - lo

    def y(v: float) -> float:
        return pad_t + (hi - v) / span * price_h

    plot_w = W - pad_l - pad_r
    slot = plot_w / n
    bw = max(2.0, slot * 0.6)
    vols = tail["Volume"].fillna(0.0) if "Volume" in tail.columns \
        else pd.Series(0.0, index=tail.index)
    vmax = float(vols.max()) or 1.0

    s: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="system-ui" font-size="11" role="img" '
        f'aria-label="daily candlestick chart">']

    for i in range(5):                                   # price grid
        v = lo + span * i / 4
        yy = y(v)
        s.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{W - pad_r}" '
                 f'y2="{yy:.1f}" stroke="{_GRID}"/>')
        s.append(f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end" '
                 f'fill="{_INK_FAINT}">₹{v:,.0f}</text>')

    for label, price, color in levels:                   # entry/stop/target
        yy = y(price)
        s.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{W - pad_r}" '
                 f'y2="{yy:.1f}" stroke="{color}" stroke-width="1.5" '
                 f'stroke-dasharray="6 4"/>')
        s.append(f'<text x="{W - pad_r}" y="{yy - 4:.1f}" text-anchor="end" '
                 f'fill="{color}" font-weight="600">{label} '
                 f'₹{price:,.2f}</text>')

    dates = list(tail.index)
    for i, (ts, row) in enumerate(tail.iterrows()):      # candles + volume
        o, h, l, c = (float(row[k]) for k in ("Open", "High", "Low", "Close"))
        up = c >= o
        color = _GREEN if up else _RED
        cx = pad_l + slot * i + slot / 2
        date_s = ts.strftime("%d %b %Y") if hasattr(ts, "strftime") else str(ts)
        tip = (f"{date_s} · O {o:,.2f} H {h:,.2f} L {l:,.2f} C {c:,.2f}")
        names = [nm for nm, _f in hits.get(i, [])]
        if names:
            tip += " · " + ", ".join(nm.replace("_", " ") for nm in names)
        s.append(f'<g><title>{tip}</title>')
        s.append(f'<line x1="{cx:.1f}" y1="{y(h):.1f}" x2="{cx:.1f}" '
                 f'y2="{y(l):.1f}" stroke="{color}"/>')
        body_top = y(max(o, c))
        body_h = max(1.0, abs(y(o) - y(c)))
        s.append(f'<rect x="{cx - bw / 2:.1f}" y="{body_top:.1f}" '
                 f'width="{bw:.1f}" height="{body_h:.1f}" fill="{color}"/>')
        vh = (float(vols.iloc[i]) / vmax) * vol_h
        s.append(f'<rect x="{cx - bw / 2:.1f}" y="{vol_top + vol_h - vh:.1f}" '
                 f'width="{bw:.1f}" height="{max(vh, 0.5):.1f}" '
                 f'fill="{color}" opacity="0.35"/>')
        s.append('</g>')

        for name, family in hits.get(i, []):             # structure markers
            bear = family == "bear"
            sym = "▼" if bear else "▲"
            mcolor = _RED if bear else _GREEN
            my = y(h) - 6 if bear else y(l) + 14
            s.append(f'<g><title>{date_s}: {name.replace("_", " ")}'
                     f'{" (bearish warning)" if bear else ""}</title>'
                     f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" '
                     f'fill="{mcolor}" font-size="10">{sym}</text></g>')

    for i in (0, n // 2, n - 1):                          # x labels
        ts = dates[i]
        lbl = ts.strftime("%d %b %y") if hasattr(ts, "strftime") else str(ts)
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        s.append(f'<text x="{pad_l + slot * i + slot / 2:.1f}" y="{x_lbl_y}" '
                 f'text-anchor="{anchor}" fill="{_INK_FAINT}">{lbl}</text>')

    s.append("</svg>")
    return "".join(s)
