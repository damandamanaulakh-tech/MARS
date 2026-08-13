"""Danger layer — the first of the owner's triggers to touch the money path.

OWNER ORDER 2026-08-11: "danger layer, go with c then auto with B".
  Mode C (first {ALERT_DAYS} days): every danger fire on a HELD stock is
  journaled DANGER_ALERT + pushed to the phone with exactly what auto mode
  WOULD have done. No position is touched.
  Mode B (automatic after the alert window): per-stock defence only —
    1 danger trigger fired on a held name  -> stop tightens to
      current price x (1 {TIGHTEN_PCT:+.1%}), never loosened;
    2+ fired                               -> exit HALF the position at the
      live price (full close if qty == 1).
  The layer NEVER opens, sizes, or blocks a new position — sell side only,
  the owner's own F-006 rule.

EVIDENCE (why exactly these triggers): the BT01-BT08 calibration. Only
triggers tagged VERIFIED on a FALL/COLLAPSE side qualify, and the list is
loaded from the committed calibration CSVs at import so the money path can
never drift from the evidence files (guard-tested). Two sources now feed it:

  1. OHLCV tripwires — research/bt_calibration/run_2026-08-09_strict15/,
     VERIFIED at the STRICT labels (+/-15%): 10 triggers, lifts 1.6-4.1x,
     leads 1-9 days.
  2. PANEL-FED tripwires — research/bt_calibration_external/run_2026-08-13/,
     added on owner order 2026-08-13 "wire 0095 and 0094 both": Index Risk
     Overlay (lift 2.28) and Sector Contagion Count (1.55).

*** THE TWO EVIDENCE BARS ARE NOT THE SAME AND THE DIFFERENCE IS DELIBERATELY
VISIBLE. *** The 10 OHLCV tripwires cleared VERIFIED at STRICT labels
(+/-15% moves). The external run was only ever produced at the MAIN labels
(+/-10%), so the two panel-fed tripwires cleared the LOWER bar. Each entry
carries its `labels` field, `describe()` prints it, and a guard test asserts
the field exists — so nobody, including a future session, can mistake a
main-label verdict for a strict-label one. Re-running the external
calibration at strict labels would settle it; until then this is stated, not
smoothed over.

Boundary: this module may import trading.triggers and trading.external_panels
(to reuse the exact production trigger functions and panels), but consults NO
entry gate of any kind — guard-tested by source inspection here and in
engine.py. engine.py reaches the panels ONLY through this module.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from trading.triggers import _Ctx, TRIGGERS

log = logging.getLogger(__name__)

ALERT_DAYS = 7                 # mode C duration before auto (owner order)
TIGHTEN_PCT = -0.025           # mode B: stop tightens to -2.5% off current
_ROOT = Path(__file__).resolve().parents[1]
_STRICT_CSV = (_ROOT / "research" / "bt_calibration" /
               "run_2026-08-09_strict15" / "trigger_calibration.csv")
_EXTERNAL_CSV = (_ROOT / "research" / "bt_calibration_external" /
                 "run_2026-08-13" / "external_trigger_calibration.csv")
_DANGER_SIDES = ("FALL", "COLLAPSE")

# Panel-fed tripwires the owner ordered wired (2026-08-13). Naming them here
# keeps the admission explicit: a future external run cannot silently promote
# a new trigger into the money path just by scoring well.
_EXTERNAL_ALLOWED = ("T12W_0095", "T12W_0094")


def _read_verified(path: Path, labels: str, needs_panels: bool,
                   allow: Optional[tuple[str, ...]] = None) -> dict[str, dict]:
    """VERIFIED danger-side rows from one committed calibration CSV.

    `allow` restricts admission to explicitly ordered ids, so a future run
    cannot promote a trigger into the money path on its own.
    Missing file -> nothing from that source, logged, layer degrades quietly
    to whatever the other source provides.
    """
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["proof_tag"] != "VERIFIED":
                    continue
                if row["side"] not in _DANGER_SIDES:
                    continue
                if allow is not None and row["trigger_id"] not in allow:
                    continue
                out[row["trigger_id"]] = {
                    "name": row["name"], "side": row["side"],
                    "lift": float(row["lift"]),
                    "lead": (float(row["median_lead_days"])
                             if row["median_lead_days"] else None),
                    "labels": labels,
                    "needs_panels": needs_panels,
                }
    except OSError:
        log.error("danger layer: calibration CSV missing (%s)", path.name)
    return out


def _load_verified() -> dict[str, dict]:
    out = _read_verified(_STRICT_CSV, "strict +/-15%", needs_panels=False)
    if not out:
        log.error("danger layer: strict calibration CSV missing — OHLCV "
                  "tripwires inert")
    out.update(_read_verified(_EXTERNAL_CSV, "main +/-10%", needs_panels=True,
                              allow=_EXTERNAL_ALLOWED))
    return out


VERIFIED: dict[str, dict] = _load_verified()
_FNS = {t.id: t.fn for t in TRIGGERS if t.id in VERIFIED and t.fn is not None}
#: panel-fed tripwires: id -> ext_fn, evaluated only when panels are supplied
_EXT_FNS = {t.id: t.ext_fn for t in TRIGGERS
            if t.id in VERIFIED and t.ext_fn is not None}
PANEL_FED = tuple(sorted(_EXT_FNS))


@dataclass
class DangerReading:
    ticker: str
    fired: list[str]                    # trigger ids
    names: list[str]                    # human names for the journal/push
    action: str                         # ALERT | TIGHTEN | EXIT_HALF | NONE


def evaluate_stock(df: pd.DataFrame, ext=None) -> list[str]:
    """Which VERIFIED danger triggers fire on this stock's daily history.

    Insufficient bars for a trigger -> that trigger abstains (None), never a
    guessed fire. `ext` is an external_panels.StockExternals; without it the
    panel-fed tripwires simply do not run, so the layer degrades to the 10
    OHLCV ones rather than inventing a reading.
    """
    try:
        x = _Ctx(df)
    except (KeyError, ValueError):
        return []
    fired = []
    for tid, fn in _FNS.items():
        try:
            if fn(x) is True:
                fired.append(tid)
        except Exception:                                   # noqa: BLE001
            continue
    if ext is not None:
        for tid, fn in _EXT_FNS.items():
            try:
                if fn(x, ext, {}) is True:      # neither needs family counts
                    fired.append(tid)
            except Exception:                               # noqa: BLE001
                continue
    return fired


def externals_for(tickers: list[str]):
    """Panels for the held book: peer/sector + index ONLY.

    Deliberately excludes delivery, F&O and deals — this runs inside
    engine.refresh and streaming the 3.5M-row delivery file there would be
    unacceptable. Returns None on any failure, which makes the panel-fed
    tripwires abstain rather than guess. The engine reaches the panels only
    through this function.
    """
    if not _EXT_FNS or not tickers:
        return None
    try:
        from trading import external_panels
        ps = external_panels.panel_set(tickers, sources=("panel", "index"))
        return {t: ps.externals_at(t) for t in tickers}
    except Exception as e:                                  # noqa: BLE001
        log.warning("danger layer: panels unavailable (%s) — panel-fed "
                    "tripwires abstain this run", e)
        return None


def plan(ticker: str, fired: list[str], auto: bool) -> DangerReading:
    names = [VERIFIED[t]["name"] for t in fired]
    if not fired:
        action = "NONE"
    elif not auto:
        action = "ALERT"
    elif len(fired) >= 2:
        action = "EXIT_HALF"
    else:
        action = "TIGHTEN"
    return DangerReading(ticker=ticker, fired=fired, names=names, action=action)


def auto_mode_active(alerts_start_iso: str, today_iso: str) -> bool:
    """Mode C for ALERT_DAYS calendar days from first activation, then B."""
    if not alerts_start_iso:
        return False
    try:
        from datetime import date
        start = date.fromisoformat(alerts_start_iso[:10])
        today = date.fromisoformat(today_iso[:10])
        return (today - start).days >= ALERT_DAYS
    except ValueError:
        return False


def describe(reading: DangerReading, auto: bool) -> str:
    # The label basis rides in every message: the panel-fed tripwires cleared
    # the main +/-10% bar, the OHLCV ones the strict +/-15% bar.
    what = ", ".join(f"{n} (lift {VERIFIED[t]['lift']}x, leads "
                     f"{VERIFIED[t]['lead']:.0f}d, {VERIFIED[t]['labels']})"
                     for t, n in zip(reading.fired, reading.names))
    if reading.action == "ALERT":
        would = ("EXIT HALF" if len(reading.fired) >= 2
                 else f"tighten stop to {TIGHTEN_PCT:+.1%} off price")
        return (f"DANGER (alert week): {what} fired on {reading.ticker}. "
                f"Auto mode would {would}. No action taken yet.")
    if reading.action == "TIGHTEN":
        return (f"DANGER: {what} fired on {reading.ticker} — stop tightened "
                f"to {TIGHTEN_PCT:+.1%} off the live price (calibrated "
                "tripwire, sell-side only).")
    if reading.action == "EXIT_HALF":
        return (f"DANGER: {len(reading.fired)} verified tripwires fired on "
                f"{reading.ticker} ({what}) — exiting half.")
    return ""
