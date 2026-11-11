"""Versioned observations made by the actual decision path (SEQ-03).

No reconstruction from today's config. ContextVar isolates concurrent scans,
requests and replays; stored snapshots are detached JSON values.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
import copy
import math
import json
import os
import uuid

ENTRY_GATES = (
    "history", "score", "breakout", "volume", "absolute_momentum", "regime",
    "multi_formula", "breadth", "sector_blacklist", "market_session",
    "intraday_deadline", "portfolio_marks", "measured_kelly", "system_event",
    "ticker_event", "benchmark_trend", "drawdown_halt", "loss_halt",
    "already_held", "data_age", "liquidity_history", "turnover", "market_cap",
    "spread", "circuit", "correlation", "candle", "quote", "kill_switch",
    "slots", "reserve", "estimated_edge", "category_budget", "cash",
    "quantity", "sector_count", "sector_cap", "portfolio_heat", "minimum_value",
    "size_throttle", "entry_deadline_final",
    "ocean_slots", "ocean_discount", "ocean_weekly_rsi", "ocean_climax",
    "ocean_fundamentals", "ocean_equity", "ocean_debt_equity", "ocean_fcf",
    "ocean_vix", "ocean_vix_crush", "ocean_damage", "ocean_danger",
    "ocean_reclaim_history", "ocean_fresh_low", "ocean_reclaim",
)
EXIT_GATES = ("quote", "market_session", "stop", "target", "trailing",
              "score_decay", "max_hold", "valuation_target", "square_off",
              "kill_switch", "drawdown", "rotation", "danger", "owner")
STATES = {"passed", "failed", "skipped", "unavailable"}
_CURRENT = ContextVar("mars_decision_evidence", default=None)
AMENDMENT_TAIL = 32
AMENDMENT_BYTES_CAP = 8192


def stamp(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def clean(value):
    """Detach primitive evidence, with NaN/Infinity explicitly unavailable."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return stamp(value)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        return clean(value.item())
    return str(value)[:500]


def bar_context(frame, interval="1d"):
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unavailable", "interval": interval, "last_bar": None}
    return {"status": "observed", "interval": interval,
            "last_bar": str(frame.index[-1]), "rows": len(frame),
            "completion": "not_validated_by_daily_path",
            "source": (getattr(frame, "attrs", {}) or {}).get("source", "unknown")}


def new(ticker, mode, path, now=None, history=None):
    return {"schema": 1, "id": uuid.uuid4().hex, "ticker": ticker, "mode": mode,
            "path": path, "at": stamp(now), "gates": {},
            "revision": os.environ.get("RENDER_GIT_COMMIT") or None,
            "bar": bar_context(history)}


@contextmanager
def capture(decision):
    token = _CURRENT.set(decision)
    try:
        yield decision
    finally:
        _CURRENT.reset(token)


def current():
    return _CURRENT.get()


def put(decision, name, value=None, threshold=None, *, passed=None,
        state=None, reason="", role="gate"):
    if decision is None:
        return passed
    state = state or ("passed" if passed else "failed")
    if state not in STATES:
        raise ValueError("invalid evidence state")
    decision.setdefault("gates", {})[name] = {
        "state": state, "value": clean(value), "threshold": clean(threshold),
        "reason": str(reason)[:700], "role": role}
    return passed


def record(name, value=None, threshold=None, **kwargs):
    return put(current(), name, value, threshold, **kwargs)


def finish(decision, outcome, reason="", *, exit=False):
    out = clean(decision)
    out.update(outcome=outcome, reason=str(reason)[:700])
    for name in (EXIT_GATES if exit else ENTRY_GATES):
        if name not in out["gates"]:
            put(out, name, state="skipped", reason="Not reached on this decision path")
    return out


def protection(pos):
    return {"stop": float(pos.stop_price or 0), "target": float(pos.target_price or 0),
            "entry": float(pos.entry_price), "qty": int(pos.qty),
            "peak": float(pos.peak_price), "mode": pos.mode,
            "custom": bool(pos.custom_levels)}


def ensure_trade(pos):
    if not getattr(pos, "trade_evidence", None):
        pos.trade_evidence = {"schema": 1, "trade_id": uuid.uuid4().hex,
            "entry": {"status": "unavailable", "reason": "Entry predates recorded decision evidence"},
            "amendments": [], "amendments_omitted": 0}
    return pos.trade_evidence


def amendment(pos, before, reason, now=None, *, portfolio=None, details=None):
    after = protection(pos)
    if before == after:
        return
    ev = ensure_trade(pos)
    row = {"id": uuid.uuid4().hex, "at": stamp(now), "reason": reason,
           "before": clean(before), "after": after, "details": clean(details or {})}
    ev.setdefault("amendments", []).append(row)
    if portfolio is not None:
        portfolio.decision_journal.append({"ts": stamp(now), "action": "TRADE_AMENDMENT",
            "ticker": pos.ticker, "trade_id": ev["trade_id"], "evidence": copy.deepcopy(row)})
        # Only trim after placing the complete event on the archival journal.
        overflow = max(0, len(ev["amendments"]) - AMENDMENT_TAIL)
        recent = ev["amendments"][overflow:]
        while recent and len(json.dumps(recent).encode()) > AMENDMENT_BYTES_CAP:
            recent = recent[1:]
            overflow += 1
        if overflow:
            ev["amendments"] = recent
            ev["amendments_omitted"] = ev.get("amendments_omitted", 0) + overflow


def exit_snapshot(pos, raw_price, fill_price, qty, reason, now, cfg):
    from .quote_evidence import observation
    ctx = current()
    decision = clean(ctx) if ctx is not None and ctx.get("path") == "exit" and ctx.get("ticker") == pos.ticker else new(
        pos.ticker, pos.mode, "exit", now)
    decision.setdefault("quote", observation({pos.ticker: raw_price}, pos.ticker, now))
    decision.update(trigger=reason, protection=protection(pos),
        fill={"raw_price": raw_price, "price": fill_price, "qty": qty,
              "slippage_bps": cfg.slippage_bps}, at=stamp(now))
    ev = copy.deepcopy(ensure_trade(pos))
    ev["exit"] = finish(decision, "sold", reason, exit=True)
    return ev


def exit_decision(pos, quotes, now, *, session=True):
    from . import quote_evidence as qe
    out = new(pos.ticker, pos.mode, "exit", now)
    out["quote"] = qe.observation(quotes, pos.ticker, now)
    put(out, "quote", out["quote"], "Positive routed LTP or intraday close; daily/retained marks excluded; age is advisory",
        passed=qe.executable(quotes, pos.ticker, now),
        state="unavailable" if out["quote"].get("source") in ("unknown", "unavailable") else None)
    put(out, "market_session", session, True, passed=session)
    return out


def journal_exit(portfolio, decision, outcome, reason=""):
    row = finish(decision, outcome, reason, exit=True)
    portfolio.decision_journal.append({"ts": row["at"], "action": "EXIT_DECISION",
        "ticker": row["ticker"], "decision_id": row["id"], "evidence": row, "why": reason})


def journal_decision(portfolio, decision, outcome, reason=""):
    row = finish(decision, outcome, reason)
    portfolio.decision_journal.append({"ts": row["at"], "action": "SCAN_DECISION",
        "ticker": row["ticker"], "mode": row["mode"], "decision_id": row["id"],
        "why": reason, "evidence": row})
    return row
