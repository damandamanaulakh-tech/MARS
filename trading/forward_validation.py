"""Passive forward-paper evidence. The existing engine alone decides trades.

An outbox is saved WITH the paper portfolio before an append-only export is
attempted. A failed export is retried after restart; no observation is silently
treated as durable. Existing holdings are baseline, never new forward entries.
"""
from copy import deepcopy
from datetime import datetime, timezone
from . import validation as v


def _entry_authority(record):
    """Small immutable entry provenance, also stored outside compressed inputs."""
    authority = {"path": record.get("path", "unknown")}
    if isinstance(record.get("strategy_batch"), dict):
        authority["strategy_batch"] = deepcopy(record["strategy_batch"])
    return authority


def _is_owner_batch(event):
    return (event.get("path") == "owner_strategy_batch"
            or isinstance(event.get("strategy_batch"), dict))


def _event_trade_key(event):
    return ((event.get("run") or {}).get("id"), event.get("book_epoch"),
            event.get("trade_id") or (event.get("trade") or {}).get("trade_id"))


def _owner_batch_authorities(events):
    """Use every retained witness before grouping newest-first event queries.

    A missing entry in a truncated query cannot turn an owner batch exit into
    ordinary strategy evidence. Any retained batch witness excludes that trade.
    """
    owners = {}
    for event in events:
        if event.get("kind") not in ("entry", "exit"):
            continue
        witness = event if _is_owner_batch(event) else event.get("input") or {}
        if _is_owner_batch(witness):
            key = _event_trade_key(event)
            if key not in owners or event.get("kind") == "entry":
                owners[key] = _entry_authority(witness)
    return owners


def _observed_economics(events, owner_authorities):
    """Ledger amounts by segment/epoch, including partial and owner realizations.

    These are recorded outcomes, not automatic-strategy expectancy. Unknown
    amounts are counted and never substituted with zero. No ledger is rewritten.
    """
    from types import SimpleNamespace
    from .category_budget import category_of
    groups = {}
    for event in events:
        if event.get("kind") != "exit":
            continue
        row = event.get("trade") or {}
        mode = category_of(SimpleNamespace(mode=row.get("mode") or "unknown",
                                           tags=row.get("tags") or []))
        group = groups.setdefault((event.get("book_epoch"), mode), {
            "book_epoch": event.get("book_epoch"), "mode": mode, "exit_legs": 0,
            "owner_batch_exit_legs": 0, "unknown_net_legs": 0,
            "unknown_cost_legs": 0, "unverified_cost_legs": 0,
            "known_net_pnl": 0., "known_costs": 0., "versions": set(),
            "from": None, "through": None})
        observed = event.get("observed_at")
        if observed:
            group["from"] = min(group["from"] or observed, observed)
            group["through"] = max(group["through"] or observed, observed)
        group["exit_legs"] += 1
        group["owner_batch_exit_legs"] += _event_trade_key(event) in owner_authorities
        group["versions"].add((event.get("run") or {}).get("id", "unknown"))
        if v.finite(row.get("net_pnl")):
            group["known_net_pnl"] += float(row["net_pnl"])
        else:
            group["unknown_net_legs"] += 1
        if v.finite(row.get("costs")) and float(row["costs"]) >= 0:
            group["known_costs"] += float(row["costs"])
        else:
            group["unknown_cost_legs"] += 1
        group["unverified_cost_legs"] += (row.get("cost_evidence") or {}).get("status") != "paper_estimate"
    out = []
    for group in groups.values():
        for value, missing in (("known_net_pnl", "unknown_net_legs"),
                               ("known_costs", "unknown_cost_legs")):
            group[value] = (round(group[value], 2)
                            if group[missing] < group["exit_legs"] else None)
        group["net_pnl"] = group["known_net_pnl"] if not group["unknown_net_legs"] else None
        group["costs"] = group["known_costs"] if not group["unknown_cost_legs"] else None
        group["versions"] = sorted(group["versions"])
        group["metric_unit"] = "recorded_exit_legs_including_partial_realizations"
        group["time_scope"] = "retained_event_observation_timestamps"
        out.append(group)
    return out


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
            add(key, {"kind": "entry", **_entry_authority(record), "trade_id": evidence["trade_id"],
                      "run": record["run"], "input": deepcopy(record),
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
        add(key, {"kind": "exit", **_entry_authority(record), "trade_id": evidence["trade_id"],
                  "run": record["run"],
                  "exit_run": (evidence.get("exit") or {}).get("validation_run"),
                  "exit_code_sha256": ((evidence.get("exit") or {}).get("validation_run") or {}).get("code", {}).get("sha256"),
                  "exit_settings_sha256": ((evidence.get("exit") or {}).get("validation_run") or {}).get("settings_sha256"),
                  "trade": row})
        # A position may have opened and closed before the first mirror save.
        add(v.digest(["entry", evidence["trade_id"]]), {
            "kind": "entry", **_entry_authority(record), "trade_id": evidence["trade_id"], "run": record["run"],
            "input": deepcopy(record), "position": {"ticker": trade.ticker, "mode": trade.mode,
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
    owner_authorities = _owner_batch_authorities(events)
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
        authority = owner_authorities.get(_event_trade_key(event))
        owner_batch = authority is not None and event.get("kind") in ("entry", "exit")
        mode = run.get("settings", {}).get("mode")
        if owner_batch:
            batch = authority.get("strategy_batch") or {}
            mode = ("ocean" if batch.get("original_path") == "ocean" else
                    (event.get("position") or event.get("trade") or {}).get("mode") or mode)
        cohort = "owner_strategy_batch" if owner_batch else "automatic"
        # Keep owner-requested outcomes separate even when code/settings match.
        group_key = (run["id"], event.get("book_epoch"), cohort, mode if owner_batch else None)
        group = groups.setdefault(group_key, {"run_id": run["id"], "book_epoch": event.get("book_epoch"), "mode": mode,
            "cohort": cohort, "automatic_strategy_evidence": not owner_batch,
            "batch_ids": set(), "override_authorized_entries": set(),
            "performance_override_entries": set(), "authority_unavailable_entries": set(),
            "code_sha256": run.get("code", {}).get("sha256"), "settings_sha256": run.get("settings_sha256"),
            "trades": [], "entries": {}, "points": [], "mixed_exit_code": 0, "mixed_exit_settings": 0,
            "unknown_cost_legs": 0, "owner_exit_legs": 0,
            "from": None, "through": None, "legacy_marks": False,
            "capital": run.get("settings", {}).get("starting_capital", 0), "sessions": set()})
        observed = event.get("observed_at")
        if observed:
            group["from"] = min(group["from"] or observed, observed)
            group["through"] = max(group["through"] or observed, observed)
        if owner_batch and (authority.get("strategy_batch") or {}).get("batch_id"):
            # The entry may have fallen outside a bounded query; exit witnesses
            # still identify the owner request without proving original size.
            group["batch_ids"].add(str(authority["strategy_batch"]["batch_id"]))
        kind = event.get("kind")
        if kind == "entry":
            if owner_batch:
                excluded_owner += 1
                group["entries"][event["trade_id"]] = event.get("position", {})
                batch = authority.get("strategy_batch") or {}
                if not batch.get("batch_id"):
                    group["authority_unavailable_entries"].add(event["trade_id"])
                override = batch.get("override") or {}
                if override.get("authorized") is True:
                    group["override_authorized_entries"].add(event["trade_id"])
                if override.get("applied") is True:
                    group["performance_override_entries"].add(event["trade_id"])
            elif event.get("path") not in ("scan", "ocean"):
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
    out, owner_batch_cohorts = [], []
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
        group["entries_observed"] = len(group.pop("entries"))
        group["observed_sessions"] = len(group.pop("sessions"))
        group["batch_ids"] = sorted(group["batch_ids"])
        for name in ("override_authorized_entries", "performance_override_entries", "authority_unavailable_entries"):
            group[name] = len(group[name])
        result = {**group, **metrics, "book_equity_points": len(points)}
        if group["cohort"] == "owner_strategy_batch":
            result["exclusion_reason"] = "owner_requested_batch_not_untouched_automatic_strategy"
            owner_batch_cohorts.append(result)
        else:
            out.append(result)
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
    return {"model": v.MODEL, "status": "collecting_forward_evidence", "cohorts": out,
            "owner_batch_cohorts": owner_batch_cohorts,
            "observed_economics": _observed_economics(events, owner_authorities),
            "observed_economics_scope": "Recorded exit legs by segment and book epoch, including owner batches and partial realizations; retained-query coverage only, not automatic-strategy expectancy",
            "shared_book": book_metrics,
            "query_truncated": truncated, "pending_exports": pending,
            "excluded_owner_or_unknown_entries": excluded_owner,
            "coverage": "own_symbol_entry_inputs; no full peer/provider replay archive",
            "acceptance": "pending_new_sessions_and_complete_cohort_coverage",
            "promotion": "none"}
