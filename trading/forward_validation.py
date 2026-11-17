"""Passive forward-paper evidence. The existing engine alone decides trades.

An outbox is saved WITH the paper portfolio before an append-only export is
attempted. A failed export is retried after restart; no observation is silently
treated as durable. Existing holdings are baseline, never new forward entries.
"""
from copy import deepcopy
from datetime import datetime, timezone
from . import validation as v


def stage(portfolio, now=None):
    now = now or datetime.now(timezone.utc)
    state = portfolio.validation_state
    pending = portfolio.validation_pending
    seen = set(state.get("seen", []))
    run = v.identity(portfolio.cfg)
    def add(key, payload):
        if key not in seen and key not in pending:
            # Manifest once per run, not once per position or equity mark.
            for field in ("run", "exit_run"):
                full = payload.get(field)
                if full and full.get("settings"):
                    mk = "manifest:" + full["id"]
                    if mk not in seen and mk not in pending:
                        pending[mk] = {"_id": mk, "model": v.MODEL, "kind": "manifest",
                                       "observed_at": now.isoformat(), "run": full}
                    payload[field] = {"id": full["id"]}
            pending[key] = {"_id": key, "model": v.MODEL, "observed_at": now.isoformat(), "book_epoch": state.get("started_at"), **payload}
    # Do not relabel pre-deployment trades as future observations.
    if not state.get("started_at"):
        state["started_at"] = now.isoformat()
        add(v.digest(["baseline", state["started_at"]]), {
            "kind": "baseline", "run": run, "cash": float(portfolio.cash),
            "positions": [{"ticker": p.ticker, "mode": p.mode, "qty": p.qty,
                           "entry_date": str(p.entry_date)} for p in portfolio.positions.values()],
            "historical_legs": len(portfolio.trades)})
    for pos in portfolio.positions.values():
        evidence = pos.trade_evidence or {}
        record = evidence.get("validation")
        if evidence.get("validation_error"):
            state["capture_error"] = "entry_capture_incomplete"
        if record:
            key = v.digest(["entry", evidence["trade_id"]])
            if key in seen or key in pending:
                continue
            add(key, {"kind": "entry", "path": record.get("path", "unknown"), "trade_id": evidence["trade_id"],
                      "run": record["run"], "input": record,
                      "position": {**{k: str(getattr(pos, k)) if k == "entry_date" else getattr(pos, k)
                                      for k in ("ticker", "entry_price", "entry_date", "mode", "stop_price", "target_price")},
                                   "qty": record.get("original_qty")}})
    for trade in portfolio.trades:
        evidence = trade.trade_evidence or {}
        record = evidence.get("validation")
        if evidence.get("validation_error") or (evidence.get("exit") or {}).get("validation_error"):
            state["capture_error"] = "trade_capture_incomplete"
        if not record:
            continue
        # Exit decision identity is immutable. A later annotation/tag change
        # must not create another realised profit/loss observation.
        exit_id = (evidence.get("exit") or {}).get("id")
        if not exit_id:
            portfolio.validation_state["capture_error"] = "exit_identity_unavailable"
            continue
        key = v.digest(["exit", evidence["trade_id"], exit_id])
        if key in seen or key in pending:
            continue
        row = trade.to_dict()
        row.pop("trade_evidence", None)
        row.pop("reasoning", None)
        row.pop("entry_notes", None)
        row["trade_id"] = evidence["trade_id"]
        add(key, {"kind": "exit", "trade_id": evidence["trade_id"],
                  "run": record["run"],
                  "exit_run": (evidence.get("exit") or {}).get("validation_run"),
                  "exit_code_sha256": ((evidence.get("exit") or {}).get("validation_run") or {}).get("code", {}).get("sha256"),
                  "exit_settings_sha256": ((evidence.get("exit") or {}).get("validation_run") or {}).get("settings_sha256"),
                  "trade": row})
        # A position may have opened and closed before the first mirror save.
        add(v.digest(["entry", evidence["trade_id"]]), {
            "kind": "entry", "path": record.get("path", "unknown"), "trade_id": evidence["trade_id"], "run": record["run"],
            "input": record, "position": {"ticker": trade.ticker, "mode": trade.mode,
                "entry_date": str(trade.entry_date), "entry_price": trade.entry_price, "qty": record.get("original_qty")},
            "captured_from_closed_trade": True})
    book = {"open_trade_ids": [(p.trade_evidence or {}).get("trade_id") for p in portfolio.positions.values()],
            "quantities": {t: p.qty for t, p in portfolio.positions.items()}, "cash": float(portfolio.cash),
            "last_scan": portfolio.last_scan, "last_exit": str(portfolio.trades[-1].exit_date) if portfolio.trades else None}
    add(v.digest(["book", state["started_at"], run["id"], book]), {"kind": "book", "run": run, **book})
    point = portfolio.equity_history[-1] if portfolio.equity_history else None
    from .quote_evidence import utc
    if (point and utc(point[0]) and utc(state["started_at"]) <= utc(point[0]) <= utc(now)):
        key = v.digest(["equity", state["started_at"], run["id"], point])
        add(key, {"kind": "equity", "run": run, "point": list(point),
                  "capital_base": portfolio.capital_base or portfolio.cfg.starting_capital,
                  "marks": {k: x for k, x in (portfolio.mark_snapshot or {}).items() if k != "positions"},
                  "open_trade_ids": [(p.trade_evidence or {}).get("trade_id") for p in portfolio.positions.values()],
                  "open_legacy_positions": sum(not (p.trade_evidence or {}).get("validation") for p in portfolio.positions.values())})
    state["pending"] = len(pending)
    state["last_attempt"] = now.isoformat()


def flush(portfolio, writer):
    """Call ONLY after portfolio.save is durably acknowledged. Idempotent IDs."""
    state, pending = portfolio.validation_state, portfolio.validation_pending
    seen = list(state.get("seen", []))
    # Mongo/JSON key order is not an acknowledgement dependency.
    for key, event in sorted(pending.items(), key=lambda item: item[1].get("kind") != "manifest"):
        try:
            ok = writer(deepcopy(event)) is True
        except Exception:
            ok = False
        if not ok:
            state["error"] = "forward_evidence_write_unacknowledged"
            break
        pending.pop(key)
        if key not in seen:
            seen.append(key)
        state["last_acknowledged"] = event["observed_at"]
        state["error"] = None
    # Ledger/archives retain original evidence. Keeping IDs for recent RAM
    # trades prevents repeat exports; older identical inserts are idempotent.
    state["seen"] = seen[-12000:]
    state["pending"] = len(pending)


def report(events, *, truncated=False, pending=0):
    """Do not pool code/settings cohorts, partial legs, or inherited positions."""
    groups = {}
    books = [e for e in events if e.get("kind") == "book"]
    latest_book = max(books, key=lambda e: e.get("observed_at", ""), default={})
    open_now = set(latest_book.get("open_trade_ids", []))
    excluded_owner = 0
    for event in events:
        if event.get("kind") == "manifest":
            continue
        run = event.get("run") or {}
        if not run.get("id"):
            continue
        group = groups.setdefault((run["id"], event.get("book_epoch")), {"run_id": run["id"], "book_epoch": event.get("book_epoch"), "mode": run.get("settings", {}).get("mode"),
            "code_sha256": run.get("code", {}).get("sha256"), "settings_sha256": run.get("settings_sha256"),
            "trades": [], "entries": {}, "points": [], "mixed_exit_code": 0, "mixed_exit_settings": 0,
            "unknown_cost_legs": 0, "owner_exit_legs": 0,
            "from": None, "through": None, "legacy_marks": False,
            "capital": run.get("settings", {}).get("starting_capital", 0), "sessions": set()})
        observed = event.get("observed_at")
        if observed:
            group["from"] = min(group["from"] or observed, observed)
            group["through"] = max(group["through"] or observed, observed)
        kind = event.get("kind")
        if kind == "entry":
            if event.get("path") not in ("scan", "ocean"):
                excluded_owner += 1
            else:
                group["entries"][event["trade_id"]] = event.get("position", {})
        if kind == "exit":
            row = deepcopy(event["trade"])
            # Keep the original ledger intact. Unverified legacy/overnight
            # charges cannot establish net expectancy for the new cohort.
            if (row.get("cost_evidence") or {}).get("status") != "paper_estimate":
                group["unknown_cost_legs"] += 1
                row["costs"] = None
            if any(word in str(row.get("reason", "")).lower() for word in ("owner", "manual")):
                group["owner_exit_legs"] += 1
            group["trades"].append(row)
            if event.get("exit_code_sha256") != group["code_sha256"]:
                group["mixed_exit_code"] += 1
            if event.get("exit_settings_sha256") != group["settings_sha256"]:
                group["mixed_exit_settings"] += 1
        if kind == "equity":
            group["points"].append(event["point"])
            group["legacy_marks"] |= bool(event.get("open_legacy_positions"))
            from .quote_evidence import utc
            from .calendars import is_trading_day
            from zoneinfo import ZoneInfo
            at = utc(event["point"][0])
            if at and is_trading_day(at.astimezone(ZoneInfo("Asia/Kolkata")).date()):
                group["sessions"].add(at.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat())
    out = []
    for group in groups.values():
        # Completion requires the sum of exit legs to match known original qty.
        quantities = {}
        for row in group["trades"]:
            quantities[row["trade_id"]] = quantities.get(row["trade_id"], 0) + row["qty"]
        incomplete = {key for key, pos in group["entries"].items()
                      if not pos.get("qty") or quantities.get(key, 0) != pos["qty"]}
        incomplete |= open_now & (set(group["entries"]) | {r["trade_id"] for r in group["trades"]})
        incomplete |= {r["trade_id"] for r in group["trades"] if r["trade_id"] not in group["entries"]}
        points = sorted(group.pop("points"), key=lambda p: p[0])
        # Equity includes the shared book and inherited holdings, so it is never
        # silently attributed as this cohort's strategy drawdown.
        metrics = v.performance(group.pop("trades"), [], group.pop("capital"), incomplete)
        metrics["max_drawdown_pct"] = None
        metrics["final_equity"] = None
        metrics["drawdown_reason"] = "shared_book_equity_not_attributable_to_this_cohort"
        group.pop("entries")
        group["observed_sessions"] = len(group.pop("sessions"))
        out.append({**group, **metrics, "book_equity_points": len(points)})
    epoch = latest_book.get("book_epoch")
    points = sorted([e for e in events if e.get("kind") == "equity" and e.get("book_epoch") == epoch], key=lambda e: str(e["point"][0]))
    # Operational drawdown of the actual shared book, with capital transfers
    # removed. It includes inherited holdings and explicitly spans versions;
    # it is not assigned as a strategy cohort's performance.
    curve, capital, offset = [], None, 0.
    for event in points:
        current = event.get("capital_base")
        if not v.finite(current):
            continue
        if capital is not None:
            offset += float(current) - capital
        capital = float(current)
        curve.append([event["point"][0], float(event["point"][1]) - offset
                      if v.finite(event["point"][1]) else None])
    book_metrics = v.performance([], curve, curve[0][1] if curve else 0)
    book_metrics["scope"] = "observed shared book including inherited holdings and all recorded code/settings versions; capital transfers removed"
    book_metrics["from"] = curve[0][0] if curve else None
    book_metrics["through"] = curve[-1][0] if curve else None
    book_metrics["versions"] = sorted({e.get("run", {}).get("id", "unknown") for e in points})
    return {"model": v.MODEL, "status": "collecting_forward_evidence", "cohorts": out, "shared_book": book_metrics,
            "query_truncated": truncated, "pending_exports": pending,
            "excluded_owner_or_unknown_entries": excluded_owner,
            "coverage": "own_symbol_entry_inputs; no full peer/provider replay archive",
            "acceptance": "pending_new_sessions_and_complete_cohort_coverage",
            "promotion": "none"}
