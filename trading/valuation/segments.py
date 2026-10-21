"""Conglomerate segments and holding-company stakes (owner choices
2026-10-21, VALFIX batch 2, VAL-01 / VAL-14b).

Reads two committed files built by scripts/fetch_segments_holdco.py from
NSE filings (every row keeps its XBRL URL and fetch date):

  segment_ebit(symbol)  -> [{segment, ebit_cr, revenue_cr, fy_end, source}]
      data/segment_ebit.csv — each reportable segment's full-year EBIT
      (profit before tax and finance costs) from the consolidated results.
  holdco_stakes(symbol) -> [{subsidiary, shares_held, stake_pct, asof, source}]
      data/holdco_stakes.csv — the holding company's share count in each
      listed subsidiary, read by name from the subsidiary's shareholding
      filing.

Pure: no network, no web import (the valuation boundary test pins it).
DISPLAY ONLY. A symbol with no rows returns [] — the caller then says what
to type, never invents a part.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

_DATA = Path(__file__).resolve().parents[2] / "data"
SEGMENT_CSV = _DATA / "segment_ebit.csv"
HOLDCO_CSV = _DATA / "holdco_stakes.csv"

_CACHE: Dict[str, dict] = {}


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _bare(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    return s[:-3] if s.endswith((".NS", ".BO")) else s


def _read(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    key = (path.stat().st_mtime, path.stat().st_size)
    c = _CACHE.get(str(path))
    if not c or c["key"] != key:
        with path.open(newline="", encoding="utf-8") as f:
            c = {"key": key, "rows": [dict(r) for r in csv.DictReader(f)]}
        _CACHE[str(path)] = c
    return [dict(r) for r in c["rows"]]


def segment_ebit(symbol: str, path: Optional[Path] = None) -> List[Dict]:
    """Segment EBIT rows (₹ crore) for a conglomerate, filing order kept."""
    sym = _bare(symbol)
    out = []
    for r in _read(Path(path) if path else SEGMENT_CSV):
        if _bare(r.get("symbol")) != sym:
            continue
        ebit = _num(r.get("ebit_cr"))
        if not r.get("segment") or ebit is None:
            continue
        out.append({
            "segment": r["segment"].strip(), "ebit_cr": ebit,
            "revenue_cr": _num(r.get("revenue_cr")),
            "fy_end": str(r.get("fy_end") or "")[:10],
            "source": (f"NSE {r.get('basis') or 'results'} XBRL FY {r.get('fy_start')}–"
                       f"{r.get('fy_end')}, segment profit before tax and finance costs"
                       f"; filed {r.get('filed')} · {r.get('xbrl_url')} · read "
                       f"{r.get('fetched')}"),
        })
    return out


def holdco_stakes(symbol: str, path: Optional[Path] = None) -> List[Dict]:
    """Listed-stake rows of a holding company (share counts, not values)."""
    sym = _bare(symbol)
    out = []
    for r in _read(Path(path) if path else HOLDCO_CSV):
        if _bare(r.get("holdco")) != sym:
            continue
        sh = _num(r.get("shares_held"))
        sub = _bare(r.get("subsidiary"))
        if not sub or sh is None or sh <= 0:
            continue
        out.append({
            "subsidiary": sub, "shares_held": sh,
            "stake_pct": _num(r.get("stake_pct")),
            "asof": str(r.get("asof") or "")[:10],
            "source": (f"{sub} shareholding pattern as on {r.get('asof')} names "
                       f"{r.get('holder_name')}; filed {r.get('filed')} · "
                       f"{r.get('xbrl_url')} · read {r.get('fetched')}"),
        })
    return out


def symbols_with_segments(path: Optional[Path] = None) -> List[str]:
    return sorted({_bare(r.get("symbol")) for r in _read(Path(path) if path else SEGMENT_CSV)
                   if r.get("symbol")})


def holdcos(path: Optional[Path] = None) -> List[str]:
    return sorted({_bare(r.get("holdco")) for r in _read(Path(path) if path else HOLDCO_CSV)
                   if r.get("holdco")})
