"""Point-in-time (PIT) universe for Nifty 200 backtests.

FIXES: survivorship bias. The static NIFTY_200_TICKERS list in universes.py
is the membership *as of May 2026*. Backtesting earlier periods against that
list only trades stocks already known to have survived into today's top 200 —
inflating momentum returns by construction.

DESIGN (SB-URR style — label contamination, never hide it):
  1. A snapshot ledger: {date -> frozenset(tickers)} loaded from NSE's own
     constituent CSVs (niftyindices.com publishes them; download by browser,
     drop into data/universe_snapshots/ as ind_nifty200list_YYYY-MM-DD.csv).
  2. get_universe_asof(d): returns the most recent snapshot ON OR BEFORE d.
  3. If a backtest date precedes the earliest snapshot, the engine does NOT
     silently fall back — it returns the static list WITH a bias tag that the
     backtest must surface in its result. Fact vs. contaminated-fact stays
     separated, exactly like an H-level.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime
from typing import Optional

SNAPSHOT_DIR = os.environ.get(
    "PIT_SNAPSHOT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "universe_snapshots"),
)

BIAS_TAG = "SURVIVORSHIP-BIASED (static current-day list; no PIT snapshot covers this date)"
CLEAN_TAG = "POINT-IN-TIME"

_FNAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.csv$")


def _parse_nse_csv(path: str) -> frozenset[str]:
    """Parse an NSE constituent CSV. Column 'Symbol' -> 'SYMBOL.NS'."""
    out = set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rdr = csv.DictReader(fh)
        for row in rdr:
            sym = (row.get("Symbol") or row.get("SYMBOL") or "").strip().upper()
            if sym and sym != "SYMBOL":
                out.add(sym + ".NS")
    return frozenset(out)


def load_snapshots(snapshot_dir: str = SNAPSHOT_DIR) -> dict[date, frozenset[str]]:
    """Load every ind_nifty200list_YYYY-MM-DD.csv in the snapshot dir."""
    snaps: dict[date, frozenset[str]] = {}
    if not os.path.isdir(snapshot_dir):
        return snaps
    for fn in os.listdir(snapshot_dir):
        m = _FNAME_RE.search(fn)
        if not m:
            continue
        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        members = _parse_nse_csv(os.path.join(snapshot_dir, fn))
        if members:
            snaps[d] = members
    return snaps


class PITUniverse:
    """As-of universe lookup with explicit contamination labelling."""

    def __init__(self, snapshots: Optional[dict[date, frozenset[str]]] = None,
                 static_fallback: Optional[list[str]] = None):
        self.snapshots = dict(sorted((snapshots or {}).items()))
        self.static_fallback = list(static_fallback or [])

    @classmethod
    def from_dir(cls, snapshot_dir: str = SNAPSHOT_DIR,
                 static_fallback: Optional[list[str]] = None) -> "PITUniverse":
        return cls(load_snapshots(snapshot_dir), static_fallback)

    def earliest(self) -> Optional[date]:
        return min(self.snapshots) if self.snapshots else None

    def asof(self, d: date) -> tuple[list[str], str]:
        """Return (tickers, tag). tag is CLEAN_TAG or BIAS_TAG.

        Most recent snapshot on-or-before d wins. If none exists, the static
        fallback is returned — loudly tagged. The caller MUST propagate the tag
        into the backtest result; hiding it re-creates the original bug.
        """
        eligible = [sd for sd in self.snapshots if sd <= d]
        if eligible:
            return sorted(self.snapshots[max(eligible)]), CLEAN_TAG
        return list(self.static_fallback), BIAS_TAG

    def coverage(self) -> str:
        if not self.snapshots:
            return "NO SNAPSHOTS LOADED — every backtest date is biased"
        ds = sorted(self.snapshots)
        return f"{len(ds)} snapshots: {ds[0].isoformat()} .. {ds[-1].isoformat()}"
