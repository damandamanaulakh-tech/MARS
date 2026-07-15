"""Alerts — send notifications via Slack webhook (and optional email).

Env vars:
  SLACK_WEBHOOK_URL — Slack incoming webhook for posting messages
  ALERT_EMAIL_TO    — optional recipient email
  SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS — optional SMTP config

Each alert kind has a dedupe key so the same drawdown-level escalation
isn't shouted on every scan. We track recent fingerprints in memory.
"""

from __future__ import annotations

import logging
import os
import smtplib
import time
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import Optional

import httpx

log = logging.getLogger(__name__)


_RECENT_FINGERPRINTS: dict[str, float] = {}
_DEDUPE_WINDOW_SEC = 60 * 60  # 1 hour


def _should_send(fingerprint: str) -> bool:
    now = time.time()
    last = _RECENT_FINGERPRINTS.get(fingerprint, 0)
    if now - last < _DEDUPE_WINDOW_SEC:
        return False
    _RECENT_FINGERPRINTS[fingerprint] = now
    return True


@dataclass
class AlertResult:
    sent: bool = False
    channels: list[str] = field(default_factory=list)
    error: Optional[str] = None


def send_slack(text: str, fingerprint: Optional[str] = None) -> AlertResult:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return AlertResult(sent=False, error="SLACK_WEBHOOK_URL not set")
    if fingerprint and not _should_send(f"slack:{fingerprint}"):
        return AlertResult(sent=False, error="deduped")
    try:
        r = httpx.post(url, json={"text": text}, timeout=5.0)
        r.raise_for_status()
        return AlertResult(sent=True, channels=["slack"])
    except Exception as e:
        log.warning("Slack alert failed: %s", e)
        return AlertResult(sent=False, error=str(e))


def send_email(subject: str, body: str, fingerprint: Optional[str] = None) -> AlertResult:
    to = os.environ.get("ALERT_EMAIL_TO", "").strip()
    host = os.environ.get("SMTP_HOST", "").strip()
    if not to or not host:
        return AlertResult(sent=False, error="email not configured")
    if fingerprint and not _should_send(f"email:{fingerprint}"):
        return AlertResult(sent=False, error="deduped")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pw = os.environ.get("SMTP_PASS", "")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user or "sourceborn-am07@localhost"
    msg["To"] = to
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            if user and pw:
                s.login(user, pw)
            s.send_message(msg)
        return AlertResult(sent=True, channels=["email"])
    except Exception as e:
        log.warning("Email alert failed: %s", e)
        return AlertResult(sent=False, error=str(e))


def notify_drawdown(level: str, dd_pct: float, peak: float, current: float) -> AlertResult:
    """Fired by the engine when DrawdownGovernor crosses a new level."""
    if level in ("normal", "watchful"):
        return AlertResult(sent=False, error="below alert threshold")
    text = (
        f":warning: *AM07 drawdown {level.upper()}*\n"
        f"Drawdown: *{dd_pct:+.2f}%* from peak ₹{peak:,.0f}\n"
        f"Current equity: ₹{current:,.0f}"
    )
    return send_slack(text, fingerprint=f"dd:{level}")


def notify_regime_switch(prev: str, new: str, reasoning: str) -> AlertResult:
    """Fired when regime classifier changes label."""
    text = (
        f":zap: *Regime switch:* `{prev}` → `{new}`\n"
        f"{reasoning}"
    )
    return send_slack(text, fingerprint=f"regime:{new}")


def notify_kill_switch(reason: str) -> AlertResult:
    text = f":rotating_light: *AM07 KILL SWITCH FIRED*\nReason: {reason}"
    return send_slack(text, fingerprint=f"kill:{reason}")


def alerts_status() -> dict:
    """For the /alerts page: which channels are configured + recent dedupes."""
    return {
        "slack_configured": bool(os.environ.get("SLACK_WEBHOOK_URL", "").strip()),
        "email_configured": bool(
            os.environ.get("ALERT_EMAIL_TO", "").strip()
            and os.environ.get("SMTP_HOST", "").strip()
        ),
        "recent": [
            {"fingerprint": fp, "minutes_ago": int((time.time() - ts) / 60)}
            for fp, ts in sorted(
                _RECENT_FINGERPRINTS.items(),
                key=lambda x: -x[1],
            )[:10]
        ],
    }
