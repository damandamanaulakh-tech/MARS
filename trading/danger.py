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

EVIDENCE (why exactly these triggers): the BT01-BT08 calibration, both
label sets (research/bt_calibration/). Only triggers tagged VERIFIED at
the STRICT labels qualify, and only FALL/COLLAPSE sides — measured lifts
1.6-4.1x over chance, median leads 1-9 days, 94,479 windows. The list is
loaded from the committed strict-run CSV at import so the money path can
never drift from the evidence file (guard-tested).

Boundary: this module may import trading.triggers (to reuse the exact
production trigger functions), but consults NO entry gate of any kind —
guard-tested by source inspection here and in engine.py.
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
_STRICT_CSV = (Path(__file__).resolve().parents[1] / "research" /
               "bt_calibration" / "run_2026-08-09_strict15" /
               "trigger_calibration.csv")
_DANGER_SIDES = ("FALL", "COLLAPSE")


def _load_verified() -> dict[str, dict]:
    """VERIFIED danger-side triggers from the committed strict calibration.
    Missing file -> empty dict -> the layer stays inert and says so."""
    out: dict[str, dict] = {}
    try:
        with open(_STRICT_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["proof_tag"] == "VERIFIED" and row["side"] in _DANGER_SIDES:
                    out[row["trigger_id"]] = {
                        "name": row["name"], "side": row["side"],
                        "lift": float(row["lift"]),
                        "lead": float(row["median_lead_days"])
                        if row["median_lead_days"] else None,
                    }
    except OSError:
        log.error("danger layer: strict calibration CSV missing — layer inert")
    return out


VERIFIED: dict[str, dict] = _load_verified()
_FNS = {t.id: t.fn for t in TRIGGERS if t.id in VERIFIED and t.fn is not None}


@dataclass
class DangerReading:
    ticker: str
    fired: list[str]                    # trigger ids
    names: list[str]                    # human names for the journal/push
    action: str                         # ALERT | TIGHTEN | EXIT_HALF | NONE


def evaluate_stock(df: pd.DataFrame) -> list[str]:
    """Which VERIFIED danger triggers fire on this stock's daily history.
    Insufficient bars for a trigger -> that trigger abstains (None), never
    a guessed fire."""
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
    return fired


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
    what = ", ".join(f"{n} (lift {VERIFIED[t]['lift']}x, leads "
                     f"{VERIFIED[t]['lead']:.0f}d)"
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
