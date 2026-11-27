"""Pre-rise — compression and volume ignition read as ONE signal.

Owner decision 2026-11-26: "pre-rise = compression (T12W_0005) + volume
ignition (T12W_0057) as ONE signal"; approved default: the ignition within 5
trading sessions of the compression.

  compression : T12W_0005 — 5 or more inside bars among the last 10
  ignition    : T12W_0057 — the 5 bars before a bar average under 0.75x the
                20-bar mean volume, and that bar trades above 1.5x it
  PRE-RISE    : an ignition bar with a compression bar on the same day or in
                the 5 sessions before it

DISPLAY ONLY. Both halves are the owner's own triggers, evaluated by the
production functions in `trading.triggers` (no second copy of either formula
lives here). No engine, risk, order, sizing or exit module may import this
file — a test asserts it — and nothing in it trades, fetches or stores.

MEASURED, not assumed (scripts/pre_rise_study.py, 191 stocks x 12 years,
every trading day, the BT01-BT08 labels and hit rule): the combination fired
373 times in 469,947 stock-days and a 10% rise followed 29.5% of the time
against 32.1% for any day — no better than chance, and below it in the
2020-2026 half. `RECORD` carries those numbers from the committed run so the
screens can never print a different figure; if the file is missing the
screens say "not measured" instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

WITHIN_SESSIONS = 5       # owner default: ignition within 5 sessions of the compression
# How long the label stays after the day it fires. A DISPLAY choice (the
# signal is read on daily screens and is about the next one to two weeks),
# not part of the measured rule: the study judges the day it fires.
SHOWN_FOR_SESSIONS = 5
LOOKBACK = 10             # how far back each half is looked for, for the screen
MIN_BARS = 60             # T12W_0005 / 0057 need 30; the rest is the look-back
TAIL = 400                # bars handed to the trigger functions (as the calibration did)

_RUN = (Path(__file__).resolve().parents[1] / "research" / "pre_rise_study"
        / "run_2026-11-26" / "pre_rise_calibration.json")


def _load_record() -> Optional[dict]:
    """The committed measurement, reduced to what the screens print."""
    try:
        data = json.loads(_RUN.read_text())
        cells = data["cells"]

        def pick(label: str, half: str) -> dict:
            c = cells[f"signal|{label}|{half}"]
            return {"fires": c["fires"], "hit_pct": c["hit_pct"],
                    "chance_pct": c["chance_pct"], "lift": c["lift"]}
        return {"run": "research/pre_rise_study/run_2026-11-26",
                "stocks": data["panel"], "stock_days": data["stock_days"],
                "within_sessions": data["within_sessions"],
                "main": pick("main_10pct", "all"),
                "main_train": pick("main_10pct", "train"),
                "main_test": pick("main_10pct", "test"),
                "strict": pick("strict_15pct", "all")}
    except Exception:                                          # noqa: BLE001
        return None


RECORD = _load_record()


def record_text() -> str:
    """One plain sentence for the screens. Never a figure that is not in the
    committed run."""
    r = RECORD
    if not r or r["within_sessions"] != WITHIN_SESSIONS:
        return "Not measured: the calibration run for this rule is not in the repository."
    m, tr, te = r["main"], r["main_train"], r["main_test"]
    return (f"Measured on {r['stocks']} stocks over 12 years: it fired {m['fires']:,} times in "
            f"{r['stock_days']:,} stock-days, and a 10% rise followed {m['hit_pct']:g}% of the time "
            f"against {m['chance_pct']:g}% for any day — no better than chance "
            f"(lift {m['lift']:g}; {tr['lift']:g} before July 2020, {te['lift']:g} after).")


def _flags(df, bars: int) -> Optional[tuple[list, list]]:
    """(compression, ignition) for each of the last `bars` sessions, newest
    first, from the production trigger functions. None when the history is
    too short or the functions cannot read it."""
    from . import triggers as TR
    n = len(df)
    if n < MIN_BARS:
        return None
    comp, ign = [], []
    for ago in range(bars):
        try:
            x = TR._Ctx(df.iloc[:n - ago].tail(TAIL))
            comp.append(bool(TR._FNS["T12W_0005"](x)))
            ign.append(bool(TR._FNS["T12W_0057"](x)))
        except Exception:                                      # noqa: BLE001
            return None
    return comp, ign


def reading(df) -> Optional[dict]:
    """The pre-rise reading for one stock's stored daily bars.

    Returns None when it cannot be read (too little history, missing
    columns) — unknown, never "no signal". Otherwise:
      compression_ago / ignition_ago : sessions since each half last fired
          (0 = the latest stored bar), None = not within the last LOOKBACK
      signal_ago : sessions since the pair last completed, None = not within
          the last SHOWN_FOR_SESSIONS
      on : the label is showing
    """
    if df is None or getattr(df, "empty", True):
        return None
    span = SHOWN_FOR_SESSIONS + WITHIN_SESSIONS          # an old ignition needs its own look-back
    flags = _flags(df, max(LOOKBACK, span))
    if flags is None:
        return None
    comp, ign = flags

    def first(seq, limit):
        return next((i for i, fired in enumerate(seq[:limit]) if fired), None)

    signal_ago = None
    for ago in range(SHOWN_FOR_SESSIONS):
        if ign[ago] and any(comp[ago:ago + WITHIN_SESSIONS + 1]):
            signal_ago = ago
            break
    stamp = df.index[-1]
    return {"asof": stamp.strftime("%Y-%m-%d") if hasattr(stamp, "strftime") else str(stamp)[:10],
            "compression_ago": first(comp, LOOKBACK),
            "ignition_ago": first(ign, LOOKBACK),
            "signal_ago": signal_ago,
            "on": signal_ago is not None}


RULE_TEXT = ("PRE-RISE = a volume ignition bar (five quiet bars, then one above 1.5x the 20-bar "
             "average volume) with a compression bar (5 or more inside bars among the last 10) on "
             f"the same day or in the {WITHIN_SESSIONS} sessions before it. The label stays for "
             f"{SHOWN_FOR_SESSIONS} sessions from the day it fires. Display only: no buy, size or "
             "exit rule reads it.")


def _ago(sessions: Optional[int]) -> str:
    if sessions is None:
        return f"not in the last {LOOKBACK} sessions"
    if sessions == 0:
        return "on the latest stored bar"
    return f"{sessions} session{'' if sessions == 1 else 's'} ago"


def describe(reading_: Optional[dict]) -> str:
    """The reading in plain words, the same on every screen."""
    if not isinstance(reading_, dict):
        return "cannot be read from the stored bars (too little history)"
    halves = (f"compression {_ago(reading_.get('compression_ago'))}; "
              f"volume ignition {_ago(reading_.get('ignition_ago'))}")
    if reading_.get("on"):
        return f"ON, fired {_ago(reading_.get('signal_ago'))} ({halves})"
    return f"not on ({halves})"
