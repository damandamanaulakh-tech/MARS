"""Phone alerts via ntfy.sh (owner-approved 2026-07-27: "ok u can make the
phone alerts").

Why ntfy and not Web Push: pywebpush's native deps (http-ece/cryptography)
fail to build in this environment and would put Render's `pip install` — and
therefore the LIVE app deploy — at risk for a convenience feature. ntfy.sh is
plain HTTP POST over httpx (already a dependency), no accounts, no keys.

How it works for the owner:
  1. Install the free "ntfy" app (Play Store / App Store).
  2. Open AM07 Settings -> Phone alerts -> Enable. The card shows a private
     topic name (random 24-hex, unguessable — treat it like a password).
  3. In the ntfy app: Subscribe to topic -> paste the topic name. Done.

What gets pushed: BUY / SELL journal events, SCAN_FAILED, DATA_STALE, and
CORRECTION_VIX_REENTRY (the re-entry signal the owner is waiting on).
OCEAN_BLOCKED and HOLD lines are deliberately excluded — daily noise.

Persistence: topic + enabled flag + last-push cursor live in the Mongo
settings collection (doc_id="push"). Without Mongo the feature reports
itself unavailable rather than generating a topic that dies on restart.
NTFY_TOPIC env overrides the stored topic (escape hatch).
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

import httpx

from . import db

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"
DOC_ID = "push"

# Journal actions worth a phone buzz. Everything else is dashboard material.
PUSH_ACTIONS = {
    "BUY": "🟢",
    "SELL": "🔴",
    "SCAN_FAILED": "⚠️",
    "DATA_STALE": "⚠️",
    "CORRECTION_VIX_REENTRY": "🚀",
}


def _load() -> dict:
    return db.load_settings(doc_id=DOC_ID)


def _save(doc: dict) -> None:
    db.save_settings(doc, doc_id=DOC_ID)


def get_state() -> dict:
    """Current push config for the Settings card.

    available=False means Mongo is offline — a generated topic could not
    survive a restart, so the feature declines to pretend it works.
    """
    env_topic = os.environ.get("NTFY_TOPIC", "").strip()
    doc = _load()
    topic = env_topic or doc.get("topic", "")
    return {
        "available": bool(env_topic) or db.is_connected(),
        "enabled": bool(doc.get("enabled", False)),
        "topic": topic,
    }


def enable() -> dict:
    """Owner pressed Enable: create the private topic if needed, switch on."""
    doc = _load()
    if not doc.get("topic") and not os.environ.get("NTFY_TOPIC", "").strip():
        doc["topic"] = f"am07-{secrets.token_hex(12)}"
    doc["enabled"] = True
    _save(doc)
    return get_state()


def disable() -> dict:
    doc = _load()
    doc["enabled"] = False
    _save(doc)
    return get_state()


def send(title: str, message: str, priority: str = "default") -> bool:
    """One push. Never raises — a dead notification service must not be able
    to break a scan pass. Returns False (and logs) on any failure."""
    st = get_state()
    if not (st["enabled"] and st["topic"]):
        return False
    try:
        r = httpx.post(
            f"{NTFY_URL}/{st['topic']}",
            content=message.encode(),
            headers={"Title": title, "Priority": priority},
            timeout=10,
        )
        ok = r.status_code < 300
        if not ok:
            log.warning("ntfy push failed: HTTP %s %s", r.status_code, r.text[:120])
        return ok
    except Exception as e:
        log.warning("ntfy push failed: %s", e)
        return False


def push_journal_events() -> int:
    """Batch new push-worthy journal entries into one notification.

    Called by the scheduler after every scan / refresh / square-off pass.
    A timestamp cursor (persisted) guarantees each entry pushes at most once,
    across restarts. Returns how many entries were included.
    """
    st = get_state()
    if not (st["enabled"] and st["topic"]):
        return 0

    from . import state
    doc = _load()
    cursor = doc.get("last_push_ts", "")
    journal = state.portfolio().decision_journal

    fresh = [
        e for e in journal[-300:]
        if e.get("action") in PUSH_ACTIONS and str(e.get("ts", "")) > cursor
    ]
    if not fresh:
        return 0

    lines = []
    for e in fresh[:12]:
        icon = PUSH_ACTIONS[e["action"]]
        tic = e.get("ticker", "")
        extra = e.get("reason") or e.get("why") or ""
        lines.append(f"{icon} {e['action']} {tic} {str(extra)[:60]}".strip())
    if len(fresh) > 12:
        lines.append(f"…and {len(fresh) - 12} more (see journal)")

    urgent = any(e["action"] in ("SCAN_FAILED", "DATA_STALE") for e in fresh)
    sent = send(
        title=f"AM07: {len(fresh)} event{'s' if len(fresh) > 1 else ''}",
        message="\n".join(lines),
        priority="high" if urgent else "default",
    )
    if sent:
        doc["last_push_ts"] = max(str(e.get("ts", "")) for e in fresh)
        _save(doc)
    return len(fresh) if sent else 0
