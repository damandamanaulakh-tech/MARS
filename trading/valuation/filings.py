"""Lenders' FILED net worth (owner choice 2026-10-21, VALFIX batch 2, VAL-02).

Reads data/lender_networth_filings.csv, built by
scripts/fetch_lender_filings.py from each lender's exchange filing (NSE
results XBRL, consolidated where filed). Pure: no network, no web import —
the valuation boundary test pins it. DISPLAY ONLY.

  net_worth(symbol, fy_end) -> (net worth ₹ crore, source text) | None

None = no filing on file for that year-end (the caller then uses the feed's
tangible book and says so). A row with a blank net worth (the script could
not read the balance sheet) is None too, never a zero.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "lender_networth_filings.csv"

_CACHE: dict = {}


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _bare(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    return s[:-3] if s.endswith((".NS", ".BO")) else s


def rows(path: Optional[Path] = None) -> List[Dict]:
    """Every row of the filings file (cached per file version)."""
    p = Path(path) if path else CSV_PATH
    if not p.exists():
        return []
    key = (str(p), p.stat().st_mtime, p.stat().st_size)
    if _CACHE.get("key") != key:
        with p.open(newline="", encoding="utf-8") as f:
            _CACHE.update(key=key, rows=[dict(r) for r in csv.DictReader(f)])
    return list(_CACHE["rows"])


def filing(symbol: str, fy_end: str, path: Optional[Path] = None) -> Optional[Dict]:
    """The filing row for a symbol and year-end, or None."""
    sym, fy = _bare(symbol), str(fy_end or "")[:10]
    for r in rows(path):
        if _bare(r.get("symbol")) == sym and str(r.get("fy_end", ""))[:10] == fy:
            return r
    return None


def source_text(r: Dict) -> str:
    """One line naming where the number came from."""
    parts = [f"NSE {r.get('basis') or 'results'} XBRL {r.get('fy_end')}"]
    cap, res = _num(r.get("capital_cr")), _num(r.get("reserves_cr"))
    if cap is not None and res is not None:
        parts.append(f"capital ₹{cap:,.2f} cr + reserves ₹{res:,.2f} cr")
    if r.get("filed"):
        parts.append(f"filed {r['filed']}")
    if r.get("xbrl_url"):
        parts.append(str(r["xbrl_url"]))
    if r.get("fetched"):
        parts.append(f"read {r['fetched']}")
    return " · ".join(parts)


def net_worth(symbol: str, fy_end: str, path: Optional[Path] = None) -> Optional[Tuple[float, str]]:
    """(net worth ₹ crore, source) the lender filed for `fy_end`, else None."""
    r = filing(symbol, fy_end, path)
    if r is None:
        return None
    v = _num(r.get("net_worth_cr"))
    if v is None or v <= 0:
        return None
    return v, source_text(r)
