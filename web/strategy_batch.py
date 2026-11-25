"""Dated, explicit owner requests through the existing paper strategy engine.

There is no startup replay. The ledger makes repeated HTTP requests idempotent;
an interrupted worker requires a new explicit, same-day owner request. Only the
measured-performance entry veto can be excepted. Strategy settings are restored
and every actual entry retains its own mode, stops, targets and cost evidence.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import logging
import threading
from uuid import UUID
from zoneinfo import ZoneInfo

from trading.calendars import is_trading_day, MARKET_OPEN_IST, MARKET_CLOSE_IST
from trading.modes import MODE_LOCK

log = logging.getLogger(__name__)
MODES = ("nifty200_momentum", "short", "swing", "positional")
MAX_REQUESTS_PER_DAY = 16
_LOCK = threading.Lock()
_ACTIVE = None
_IST = ZoneInfo("Asia/Kolkata")
_RUNNING = {"accepted", "running"}


def _utc_now():
    return datetime.now(timezone.utc)


def _today_ist():
    return _utc_now().astimezone(_IST).date().isoformat()


def _session_open():
    clock = _utc_now().astimezone(_IST)
    return (is_trading_day(clock.date()) and
            MARKET_OPEN_IST <= (clock.hour, clock.minute) < MARKET_CLOSE_IST)


def _launch(worker):
    thread = threading.Thread(target=worker, name="owner-strategy-batch", daemon=True)
    thread.start()
    return thread


def _stamp():
    return _utc_now().isoformat()


def _response(state, reason, request_id=None):
    return {"status": state, "reason": reason, "request_id": request_id}


def _record_copy(portfolio, request_id=None):
    with portfolio._lock:
        ledger = getattr(portfolio, "strategy_batch", {}) or {}
        key = request_id or ledger.get("latest_request_id")
        return deepcopy((ledger.get("requests") or {}).get(key))


def _view(portfolio, record):
    if record is None:
        return {"status": "idle", "request_id": None, "passes": {}, "opened_total": None}
    with _LOCK:
        live = bool(_ACTIVE and _ACTIVE["portfolio"] == id(portfolio)
                    and _ACTIVE["request_id"] == record.get("request_id"))
    if record.get("status") in _RUNNING and not live:
        record["status"] = "interrupted"
        record["reason"] = "Worker is no longer active; no automatic replay. A new dated request is required."
        for item in (record.get("passes") or {}).values():
            if item.get("status") == "running":
                item["status"] = "interrupted"
    return record


def status(portfolio):
    """Read only, including orphan detection: never save or resume from a GET."""
    return _view(portfolio, _record_copy(portfolio))


def _valid_id(request_id):
    if not isinstance(request_id, str) or len(request_id) != 47:
        return False
    try:
        day, token = request_id.split(":", 1)
        datetime.strptime(day, "%Y-%m-%d")
        parsed = UUID(token)
        return parsed.version == 4 and str(parsed) == token
    except (ValueError, TypeError, AttributeError):
        return False


def start(engine, requested_by, request_id, override_measured_performance=False):
    """Reserve an idempotent request; worker must checkpoint before any work."""
    global _ACTIVE
    if not _valid_id(request_id):
        return _response("rejected", "request_id must be YYYY-MM-DD:uuid4", request_id)
    if not isinstance(requested_by, str) or not requested_by.strip() or len(requested_by) > 200:
        return _response("rejected", "A bounded owner identity is required", request_id)
    if not isinstance(override_measured_performance, bool):
        return _response("rejected", "override_measured_performance must be boolean", request_id)
    portfolio = engine.portfolio
    # Check duplicates before date/session guards: an old POST can report its
    # historical result, but can never execute again.
    existing = _record_copy(portfolio, request_id)
    if existing is not None:
        return _view(portfolio, existing)
    if request_id[:10] != _today_ist():
        return _response("expired", "This request is not authorized for the current IST date", request_id)
    if not _session_open():
        return _response("rejected", "Market session is closed; requests are never queued for another session", request_id)
    reason = portfolio.entry_block_reason()
    if reason:
        return _response("save_failed", reason, request_id)
    from web.scheduler import _ROTATION_LOCK
    with _LOCK:
        # Another request may have reserved this same ID after the first read.
        # Repeated concurrent POSTs observe its record, never another worker.
        existing = _record_copy(portfolio, request_id)
        if existing is not None:
            if existing.get("status") in _RUNNING and not (
                    _ACTIVE and _ACTIVE["portfolio"] == id(portfolio)
                    and _ACTIVE["request_id"] == request_id):
                existing["status"] = "interrupted"
            return existing
        if _ACTIVE is not None:
            return _response("busy", "Another owner strategy batch is running", request_id)
        if not _ROTATION_LOCK.acquire(blocking=False):
            return _response("busy", "A category rotation is running", request_id)
        try:
            with portfolio._lock:
                ledger = getattr(portfolio, "strategy_batch", {}) or {}
                existing = (ledger.get("requests") or {}).get(request_id)
                if existing is not None:
                    _ROTATION_LOCK.release()
                    # No active worker can appear while _LOCK is held.
                    answer = deepcopy(existing)
                    if answer.get("status") in _RUNNING:
                        answer["status"] = "interrupted"
                    return answer
                if ledger.get("date") != _today_ist():
                    ledger = {"date": _today_ist(), "requests": {}, "latest_request_id": None}
                if len(ledger.get("requests") or {}) >= MAX_REQUESTS_PER_DAY:
                    _ROTATION_LOCK.release()
                    return _response("rejected", "Daily request limit reached; existing IDs are retained", request_id)
                for old in ledger["requests"].values():
                    if old.get("status") in _RUNNING:
                        old["status"] = "interrupted"
                        old["reason"] = "Previous worker stopped; never automatically replayed"
                record = {
                    "status": "accepted", "request_id": request_id,
                    "date": request_id[:10], "requested_by": requested_by.strip(),
                    "override_measured_performance": override_measured_performance,
                    "modes": list(MODES), "include_ocean": True,
                    "created_at": _stamp(), "completed_at": None,
                    "passes": {}, "opened": [], "opened_total": 0,
                    "opened_segments": {}, "reason": "Awaiting acknowledged checkpoint",
                }
                ledger["requests"][request_id] = record
                ledger["latest_request_id"] = request_id
                portfolio.strategy_batch = ledger
                _ACTIVE = {"portfolio": id(portfolio), "request_id": request_id}
        except Exception:
            _ROTATION_LOCK.release()
            raise
    try:
        # Never hold _LOCK here: test launchers may execute synchronously.
        _launch(lambda: _run(engine, request_id, _ROTATION_LOCK))
    except Exception as exc:
        log.exception("Owner strategy batch worker could not start")
        with portfolio._lock:
            record.update(status="failed", reason="worker_start_" + type(exc).__name__, completed_at=_stamp())
        with _LOCK:
            _ACTIVE = None
        _ROTATION_LOCK.release()
    return _view(portfolio, _record_copy(portfolio, request_id))


def _checkpoint(portfolio):
    try:
        return portfolio.save() is True and not portfolio.entry_block_reason()
    except Exception:
        log.exception("Owner strategy batch checkpoint failed")
        return False


def _update(portfolio, record, **fields):
    with portfolio._lock:
        record.update(fields)


def _pass_result(engine, result):
    opened = list(result.opened)
    by_segment = {}
    with engine.portfolio._lock:
        for ticker in opened:
            pos = engine.portfolio.positions.get(ticker)
            if pos is None:
                # A same-pass protective close can remove an opened position.
                rows = [r for r in engine.portfolio.decision_journal
                        if r.get("action") == "BUY" and r.get("ticker") == ticker]
                row = rows[-1] if rows else {}
                evidence = row.get("evidence") or {}
                segment = "ocean" if (evidence.get("strategy_batch") or {}).get("original_path") == "ocean" else row.get("mode", "unknown")
            else:
                segment = "ocean" if "ocean" in (getattr(pos, "tags", None) or []) else getattr(pos, "mode", "unknown")
            by_segment.setdefault(segment, []).append(ticker)
    return {
        "status": "complete", "ranked_count": len(result.ranked),
        "selected_count": len(result.picks), "opened": opened, "opened_count": len(opened),
        "skipped_count": len(result.skipped),
        "gate_counts": dict(Counter(str(row.get("reason") or "unknown") for row in result.skipped)),
        "opened_segments": by_segment, "completed_at": _stamp(),
    }


def _run(engine, request_id, rotation_lock):
    global _ACTIVE
    portfolio = engine.portfolio
    record = portfolio.strategy_batch["requests"][request_id]
    acquired_mode = False
    cfg_snapshot = None
    scan_in_progress = False
    try:
        # RLock ownership is established and released here, in THIS worker.
        acquired_mode = MODE_LOCK.acquire(blocking=False)
        if not acquired_mode:
            _update(portfolio, record, status="busy", reason="Another scan holds the mode lock", completed_at=_stamp())
            return
        cfg_snapshot = deepcopy(vars(engine.cfg))
        with portfolio._lock:
            record.update(status="running", reason="Saving request checkpoint", started_at=_stamp())
            portfolio.decision_journal.append({"ts": _stamp(), "action": "OWNER_STRATEGY_BATCH_REQUEST",
                "request_id": request_id, "actor": record["requested_by"], "date": record["date"],
                "override_measured_performance": record["override_measured_performance"],
                "modes": list(MODES), "execution": "paper"})
        if not _checkpoint(portfolio):
            _update(portfolio, record, status="save_failed", reason="Request checkpoint was not acknowledged")
            return
        with engine.strategy_batch_request(
                batch_id=request_id, actor=record["requested_by"],
                authorized_date_ist=record["date"],
                override_measured_performance=record["override_measured_performance"]):
            for mode in MODES:
                if _today_ist() != record["date"] or not _session_open():
                    _update(portfolio, record, status="expired", reason="Authorized session ended before all passes completed")
                    break
                engine.cfg.mode = mode
                engine.cfg.apply_mode_preset()
                # Existing positions retain protective exits. The scoped
                # engine context suppresses ALL pyramid adds, including an
                # existing intraday holding. Pending owner orders are not run.
                engine.refresh()
                if portfolio.entry_block_reason():
                    _update(portfolio, record, status="save_failed", reason=portfolio.entry_block_reason())
                    break
                with portfolio._lock:
                    record["passes"][mode] = {
                        "status": "running", "ranked_count": None, "selected_count": None,
                        "opened": [], "opened_count": None, "skipped_count": None,
                        "gate_counts": {}, "opened_segments": {}, "completed_at": None}
                    record["reason"] = "Scanning " + mode
                if not _checkpoint(portfolio):
                    with portfolio._lock:
                        record["passes"][mode]["status"] = "save_failed"
                        record.update(status="save_failed", reason="Pass checkpoint was not acknowledged")
                    break
                # An in-progress scan may already have booked an entry before
                # returning. Its total is unknown until its actual result is
                # available; zero would be a false claim on an interruption.
                _update(portfolio, record, opened_total=None)
                scan_in_progress = True
                result = engine.scan()
                summary = _pass_result(engine, result)
                authority_ended = any(str(item.get("reason")) in (
                    "strategy_batch_expired", "strategy_batch_market_closed")
                    for item in result.skipped)
                with portfolio._lock:
                    record["passes"][mode] = summary
                    record["opened"].extend(summary["opened"])
                    record["opened_total"] = len(record["opened"])
                    for segment, tickers in summary["opened_segments"].items():
                        record["opened_segments"].setdefault(segment, []).extend(tickers)
                scan_in_progress = False
                del result
                if portfolio.entry_block_reason() or not _checkpoint(portfolio):
                    with portfolio._lock:
                        summary["status"] = "save_failed"
                        record.update(status="save_failed", reason="Completed pass save was not acknowledged")
                    break
                if authority_ended:
                    _update(portfolio, record, status="expired",
                            reason="Authorized session ended during the last completed pass")
                    break
                from web.memory_relief import release
                release("owner_strategy_batch_" + mode)
            else:
                _update(portfolio, record, status="complete", reason="All non-intraday passes finished with acknowledged saves")
    except Exception as exc:
        log.exception("Owner strategy batch interrupted")
        with portfolio._lock:
            record.update(status="failed", reason=type(exc).__name__)
            if scan_in_progress:
                record["opened_total"] = None
                record["reason"] += "; interrupted pass entry count is unknown; inspect its trade audit"
            for item in record["passes"].values():
                if item.get("status") == "running":
                    item["status"] = "failed"
    finally:
        try:
            if cfg_snapshot is not None:
                vars(engine.cfg).clear()
                vars(engine.cfg).update(cfg_snapshot)
            _update(portfolio, record, completed_at=_stamp())
            if acquired_mode:
                # This immutable event describes execution, not an already
                # acknowledged final save. The saved ledger is the receipt.
                with portfolio._lock:
                    portfolio.decision_journal.append({"ts": _stamp(), "action": "OWNER_STRATEGY_BATCH",
                        "request_id": request_id, "result": deepcopy(record), "execution": "paper",
                        "final_save_confirmation": "pending"})
                if not _checkpoint(portfolio):
                    with portfolio._lock:
                        record.update(status="save_failed", reason="Final result save was not acknowledged")
                        portfolio.decision_journal.append({"ts": _stamp(), "action": "OWNER_STRATEGY_BATCH_SAVE_FAILED",
                            "request_id": request_id, "reason": record["reason"],
                            "execution": "paper"})
        finally:
            if acquired_mode:
                MODE_LOCK.release()
            with _LOCK:
                _ACTIVE = None
            rotation_lock.release()
