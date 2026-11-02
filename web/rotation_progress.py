"""Daily category progress saved in the same snapshot as the portfolio."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4


def complete(rot, modes, date, *, live=True):
    return bool(rot and rot.get("date") == date
                and rot.get("status") == "complete"
                and (not live or rot.get("phase") == "live")
                and all(done(rot, m, live=live) for m in modes))


def done(rot, mode, *, live):
    rec = (rot.get("passes") or {}).get(mode) or {}
    return bool(rec.get("completed") and not rec.get("error")
                and (not live or rec.get("live")))


def begin(portfolio, where, date, modes, live):
    previous = (getattr(portfolio, "rotation_report", None) or {}).get("daily") or {}
    phase = "live" if live else "look_only"
    if where == "09:20" and previous.get("date") == date and previous.get("phase") == phase:
        rot = deepcopy(previous)
    else:
        rot = {"run_id": uuid4().hex, "date": date, "where": where,
               "started": datetime.now(timezone.utc).isoformat(),
               "phase": phase, "passes": {}, "opened_total": 0}
    rot.update(order=list(modes), status="running", finished=None)
    return rot


def checkpoint(portfolio, rot):
    """Only an explicit acknowledgement permits the runner to continue.

    The snapshot contains holdings and category progress together. An
    interrupted pass may be retried; the engine still checks held names.
    This is not a cross-process transaction or exactly-once order protocol.
    """
    rot["checkpoint"] = uuid4().hex
    rot["opened_total"] = sum(int(r.get("opened") or 0) for r in rot["passes"].values())
    report = dict(getattr(portfolio, "rotation_report", None) or {})
    report["active"] = deepcopy(rot)
    momentum = rot["passes"].get("nifty200_momentum") or {}
    if momentum.get("completed") and momentum.get("live") and not momentum.get("error"):
        report["momentum_completed"] = {**deepcopy(momentum), "date": rot["date"]}
    if rot["where"] == "09:20":
        report["daily"] = deepcopy(rot)
    portfolio.rotation_report = report
    try:
        acknowledged = portfolio.save() is True
    except Exception:
        acknowledged = False
    if not acknowledged:
        rot["status"] = "save_failed"
        report["active"] = deepcopy(rot)
        if rot["where"] == "09:20":
            report["daily"] = deepcopy(rot)
    return acknowledged
