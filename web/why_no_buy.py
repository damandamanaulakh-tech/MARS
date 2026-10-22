"""WHY NO BUY — the owner's 2026-10-21 ask: "why there is no pop for gates
or book, how do i know its blocked".

Pure functions. The rotation (web/scheduler._run_category_rotation) records
one entry per category pass in `portfolio.rotation_report` and journals ONE
ROTATION_SUMMARY line (pushed to the phone); the dashboard card reads the
record back. Nothing here buys, sells or gates — it only says what the
engine did and what stopped it, in the engine's own words.

Since 2026-10-22 (owner card D5) the record is saved after EVERY pass with
`finished` empty until the end, so a rotation the app died in is visible:
the card labels it "cut short after <category>" (the one impure read:
whether THIS process is the one still running it, `_running_rid`).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Optional

from trading import category_budget as cb

# Journal actions written INSIDE a category pass that explain a refusal.
BLOCK_ACTIONS = (
    "BREADTH_BLOCKED", "CATEGORY_BUDGET_FULL", "MEASURED_KELLY_BLOCKED",
    "CONSECUTIVE_LOSS_HALT", "KILL_SWITCH_SCAN", "OCEAN_BLOCKED",
    "SCAN_COVERAGE_SHORT", "MULTI_FORMULA_BLOCKED",
    "ROTATION_NOTHING_TO_FUND", "CORRECTION_BRAKE_OFF",
)

# Plain words for the engine's skip reasons (first token of `reason`).
REASON_WORDS = {
    "candle_wait_no_pattern": "waiting for a candle pattern",
    "candle_wait_insufficient_bars": "too few bars for the candle check",
    "already_held": "already held",
    # B-6 (2026-10-22, owner card D4): a category's share is now counted on
    # what is left after the owner's own buys and Ocean (category_budget).
    "category_budget_full": "share full (of what is left after your own buys and Ocean)",
    "liquidity_rupee_vol": "turnover under the ₹5 Cr bar",
    "liquidity_spread": "spread too wide",
    "measured_kelly_no_edge": "Kelly check: no edge in the record",
    "consecutive_loss_halt": "daily loss halt",
    "qty_zero": "size came out zero",
    # B-6 (2026-10-22): the old words blamed the regime reserve alone, but
    # the deploy cap counts the owner's own buys and Ocean holdings too.
    "reserve_breached": ("deploy cap reached (regime reserve, your own buys "
                         "and Ocean holdings included)"),
    "portfolio_heat_cap": "portfolio heat cap",
    "sector_cap": "sector cap",
    "sector_capital_cap": "sector capital cap",
    "correlation_gate": "too correlated with a held name",
    "timing_not_ok": "entry timing not met",
    "max_positions": "position ceiling",
    "insufficient_cash": "not enough cash",
    "blocklisted": "blocklisted",
}

BLOCK_WORDS = {
    "BREADTH_BLOCKED": "breadth band",
    "CATEGORY_BUDGET_FULL": "share full (of what is left after your own buys and Ocean)",
    "MEASURED_KELLY_BLOCKED": "Kelly check: no edge",
    "CONSECUTIVE_LOSS_HALT": "daily loss halt",
    "KILL_SWITCH_SCAN": "kill switch",
    "OCEAN_BLOCKED": "Ocean rail",
    "SCAN_COVERAGE_SHORT": "scan coverage short",
    "MULTI_FORMULA_BLOCKED": "outside-formula gate",
    "ROTATION_NOTHING_TO_FUND": "rotation found nothing to fund",
    "CORRECTION_BRAKE_OFF": "correction brake off (info)",
}

KEEP_ROTATIONS = 4


def _reason_word(reason: str) -> str:
    key = str(reason or "").split(" ")[0][:40]
    for k, w in REASON_WORDS.items():
        if key.startswith(k):
            return w
    return key.replace("_", " ") or "refused"


def pass_record(mode: str, res: Any, journal_since: list, floor: float,
                breadth: Optional[dict], now: Optional[datetime] = None) -> dict:
    """One category pass, counted from the engine's own ScanResult."""
    now = now or datetime.utcnow()
    ranked = list(getattr(res, "ranked", None) or [])
    picks = list(getattr(res, "picks", None) or [])
    opened = list(getattr(res, "opened", None) or [])
    skipped = list(getattr(res, "skipped", None) or [])
    above = 0
    for r in ranked:
        sc = r.get("score") if isinstance(r, dict) else None
        try:
            if sc is not None and float(sc) >= float(floor):
                above += 1
        except (TypeError, ValueError):
            pass
    names = []
    for o in opened:
        names.append(o.get("ticker") if isinstance(o, dict) else str(o))
    reasons = Counter(str(s.get("reason", "")).split(" ")[0][:40]
                      for s in skipped if isinstance(s, dict))
    blocks = [{"action": str(e.get("action")),
               "why": str(e.get("why") or e.get("detail") or "")[:240]}
              for e in journal_since
              if isinstance(e, dict) and e.get("action") in BLOCK_ACTIONS]
    b = breadth or {}
    return {
        "mode": mode,
        "label": cb.LABELS.get(mode, mode),
        "ts": now.isoformat(),
        "scanned": len(ranked),
        "floor": float(floor or 0),
        "above_floor": above,
        "picks": len(picks),
        "opened": len(opened),
        "opened_names": names[:15],
        "skipped": [[k, n] for k, n in reasons.most_common(8)],
        "blocks": blocks[:6],
        "breadth": {"pct": b.get("pct"), "population": b.get("population"),
                    "sample": b.get("sample")} if b else {},
        "error": None,
    }


def failed_record(mode: str, err: str, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    return {"mode": mode, "label": cb.LABELS.get(mode, mode), "ts": now.isoformat(),
            "scanned": 0, "floor": 0.0, "above_floor": 0, "picks": 0, "opened": 0,
            "opened_names": [], "skipped": [], "blocks": [], "breadth": {},
            "error": str(err)[:160]}


def bare_record(mode: str, opened: int = 0, now: Optional[datetime] = None) -> dict:
    """A pass that RAN but whose full record could not be built (2026-10-22).
    It still marks the category as done, so a catch-up never re-runs it."""
    now = now or datetime.utcnow()
    return {"mode": mode, "label": cb.LABELS.get(mode, mode), "ts": now.isoformat(),
            "scanned": 0, "floor": 0.0, "above_floor": 0, "picks": 0,
            "opened": int(opened or 0), "opened_names": [], "skipped": [],
            "blocks": [], "breadth": {}, "error": None,
            "note": "pass ran; its detail record could not be built"}


def stopped_by(rec: dict) -> str:
    """One plain sentence per category: what it did, or what stopped it."""
    if rec.get("error"):
        return f"scan failed: {rec['error']}"
    if rec.get("opened"):
        return f"bought {rec['opened']}"
    blocks = [b for b in (rec.get("blocks") or []) if b.get("action") != "CORRECTION_BRAKE_OFF"]
    if blocks:
        b = blocks[0]
        word = BLOCK_WORDS.get(b["action"], b["action"].lower().replace("_", " "))
        why = (b.get("why") or "").strip()
        return f"{word}" + (f" — {why[:140]}" if why else "")
    scanned = int(rec.get("scanned") or 0)
    above = int(rec.get("above_floor") or 0)
    floor = rec.get("floor") or 0
    if int(rec.get("picks") or 0) == 0:
        if above == 0:
            return f"no name cleared the floor {floor:g} ({scanned} scanned)"
        return (f"{above} above the floor {floor:g} but none passed momentum, "
                f"timing or liquidity")
    parts = [f"{_reason_word(k)} {n}" for k, n in (rec.get("skipped") or [])[:3]]
    return f"{rec.get('picks')} picks refused: " + ", ".join(parts) if parts else \
        f"{rec.get('picks')} picks, none opened"


def summary_text(rot: dict) -> str:
    """The phone line: one sentence per category."""
    total = int(rot.get("opened_total") or 0)
    head = f"{rot.get('where', '?')} rotation: {'NO BUYS' if total == 0 else f'{total} buys'}."
    if rot.get("resumed"):
        names = [cb.LABELS.get(m, m) for r in rot["resumed"] for m in (r.get("modes") or [])]
        head += (" Resumed after a restart" + (f" ({', '.join(names)})" if names else "")
                 + ".")
    parts = []
    for m in rot.get("order") or list(cb.SHARE_FIELDS):
        rec = (rot.get("passes") or {}).get(m)
        if not rec:
            parts.append(f"{cb.LABELS.get(m, m)}: not run")
            continue
        parts.append(f"{rec.get('label', m)}: {stopped_by(rec)}")
    return (head + " " + "; ".join(parts))[:900]


def add_rotation(report: Optional[dict], rot: dict) -> dict:
    """Newest first, bounded."""
    old = list((report or {}).get("rotations") or [])
    return {"rotations": ([rot] + old)[:KEEP_ROTATIONS]}


def _same_rotation(a: dict, b: dict) -> bool:
    if a.get("rid") and b.get("rid"):
        return a["rid"] == b["rid"]
    return bool(a.get("started")) and a.get("started") == b.get("started") \
        and a.get("where") == b.get("where")


def upsert_rotation(report: Optional[dict], rot: dict) -> dict:
    """Store `rot` in place of the stored entry for the SAME rotation (its
    `rid`, else `started` + `where`), or newest-first when new; bounded.
    Entry-fix 2026-10-22: the rotation saves itself after every pass, so one
    rotation must stay ONE entry however many times it is saved. A copy is
    stored — later passes must not reach the record until they are saved."""
    snap = {**rot, "passes": dict(rot.get("passes") or {})}
    old = [r for r in ((report or {}).get("rotations") or []) if isinstance(r, dict)]
    for i, r in enumerate(old):
        if _same_rotation(r, snap):
            out = list(old)
            out[i] = snap
            return {"rotations": out[:KEEP_ROTATIONS]}
    return {"rotations": ([snap] + old)[:KEEP_ROTATIONS]}


def _running_rid() -> Optional[str]:
    """The rotation this process is running right now, if any."""
    try:
        from .scheduler import rotation_in_progress
        return rotation_in_progress()
    except Exception:                                   # noqa: BLE001
        return None


#: The two halt lines the scheduler writes when it refuses to start a scan.
HALT_ACTIONS = ("MONGO_OFFLINE_HALT", "MONGO_WRITE_REFUSED_HALT")


_UNSET = object()


def rotation_status(rot: dict, running_rid: Optional[str]) -> tuple[str, str, str]:
    """(status, label, text for a category that has not run) of one stored
    rotation. Entry-fix 2026-10-22 (owner card D5): a record with no
    `finished` stamp is EITHER the rotation this process is running right now
    ("running") OR one a process died in the middle of ("cut_short")."""
    order = rot.get("order") or list(cb.SHARE_FIELDS)
    passes = rot.get("passes") or {}
    ran = [m for m in order if m in passes]
    if not rot.get("finished"):
        if rot.get("rid") and rot.get("rid") == running_rid:
            return ("running", f"in progress — {len(ran)} of {len(order)} categories done",
                    "not run yet (rotation in progress)")
        last = cb.LABELS.get(ran[-1], ran[-1]) if ran else ""
        where = f"cut short after {last}" if last else "cut short before the first category"
        return ("cut_short",
                where + " — the app stopped mid-rotation (restart or memory kill)",
                "not run (process restarted)")
    if rot.get("resumed"):
        names = [cb.LABELS.get(m, m) for r in rot["resumed"] for m in (r.get("modes") or [])]
        return ("finished", "resumed after a restart" + (f": {', '.join(names)}" if names else ""),
                "not run (rotation cut short)")
    return ("finished", "", "not run (rotation cut short)")


def card(portfolio, cfg, today_iso: str, running=_UNSET) -> dict:
    """What the dashboard shows. Reads the stored record only (plus, for an
    unfinished record, whether THIS process is the one running it)."""
    report = getattr(portfolio, "rotation_report", None) or {}
    rots = [r for r in (report.get("rotations") or []) if r.get("date") == today_iso]
    latest = rots[0] if rots else None
    running_rid = _running_rid() if running is _UNSET else running
    rows = []
    status, status_label, opened_total = "", "", None
    if latest:
        status, status_label, not_run = rotation_status(latest, running_rid)
        opened_total = sum(int((r or {}).get("opened") or 0)
                           for r in (latest.get("passes") or {}).values())
        for m in latest.get("order") or list(cb.SHARE_FIELDS):
            rec = (latest.get("passes") or {}).get(m)
            if not rec:
                rows.append({"mode": m, "label": cb.LABELS.get(m, m), "scanned": 0,
                             "floor": 0, "above_floor": 0, "picks": 0, "opened": 0,
                             "opened_names": [], "stopped_by": not_run,
                             "blocks": []})
                continue
            rows.append({**rec, "stopped_by": stopped_by(rec)})
    book = []
    bt = getattr(portfolio, "breadth_today", None) or {}
    if bt.get("pct") is not None:
        lo = float(getattr(cfg, "breadth_block_low", 0.40) or 0.40) * 100
        hi = float(getattr(cfg, "breadth_block_high", 0.65) or 0.65) * 100
        pct = float(bt["pct"]) * 100
        inside = lo <= pct < hi
        book.append(f"Breadth {pct:.1f}% ({bt.get('population') or 'scan'} panel, "
                    f"{bt.get('sample') or '?'} names) — "
                    + (f"INSIDE the {lo:.0f}-{hi:.0f}% band = every category blocked"
                       if inside else f"outside the {lo:.0f}-{hi:.0f}% band = buyable"))
    if getattr(portfolio, "risk_day_killed", False):
        book.append("Kill switch tripped today — no entries until tomorrow")
    lc = int(getattr(portfolio, "loss_count", 0) or 0)
    if lc:
        book.append(f"Losing exits counted today: {lc} (the daily loss halt stops entries at "
                    f"max(2, 10% of open + lost))")
    for e in reversed(list(getattr(portfolio, "decision_journal", None) or [])[-400:]):
        if e.get("action") == "MEASURED_KELLY_BLOCKED" and str(e.get("ts", ""))[:10] == today_iso:
            book.append(f"Kelly check blocked momentum: {str(e.get('why', ''))[:160]}")
            break
    # 2026-10-22: a halted scan never starts a rotation, so without this line
    # the card would only say "no rotation recorded" and blame sleep.
    for e in reversed(list(getattr(portfolio, "decision_journal", None) or [])[-400:]):
        if e.get("action") in HALT_ACTIONS and str(e.get("ts", ""))[:10] == today_iso:
            book.append("Entries halted today — " + str(e.get("why", ""))[:200])
            break
    note = ""
    if not latest:
        note = ("No rotation recorded today — the 09:20 / 14:30 chain has not run on this "
                "process (asleep, killed, or before the slot). The journal (Reports CSV) is "
                "the record.")
    return {"rotation": latest, "rows": rows, "book": book, "note": note,
            "status": status, "status_label": status_label,
            "opened_total": opened_total,
            "earlier": [{"where": r.get("where"), "opened_total": r.get("opened_total"),
                         "cut_short": rotation_status(r, running_rid)[0] == "cut_short"}
                        for r in rots[1:]]}
