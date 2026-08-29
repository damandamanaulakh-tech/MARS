"""Production readiness — fail closed, never fall back silently.

Adopted 2026-08-29 from the external AM07-vs-AshStocks review (owner order
"go ... 1"). AshStocks refuses to become ready without a durable store and
a real password; AM07 fell back instead. Both of the review's HIGH findings
were verified against this code before adopting:

* **F-001** — if ``MONGO_URL`` is absent the live paper book falls back to
  JSON under /tmp, which Render erases on restart. The existing
  degraded-boot entry halt does NOT cover this: ``db.mongo_expected()`` is
  ``bool(MONGO_URL)``, so "the secret was never set" was indistinguishable
  from "the operator wants the JSON dev path". Positions, closed trades and
  the journal could be lost or split across a restart.
* **F-002** — if ``OWNER_PWD`` and ``APP_PASSWORD`` are both absent,
  ``auth`` seeds the guessable password ``"sourceborn"`` and only logs an
  error, so a deployment slip becomes an open door on a public URL.

Neither was live when adopted — the owner's Render carries MONGO_URL and
APP_PASSWORD (confirmed by /healthz and by the owner on 2026-08-09). The
point of this module is that the NEXT config slip cannot quietly become a
lost book or an open door: it becomes a boot that refuses to serve, while
Render keeps the last good deploy running.

Production means Render, detected by the ``RENDER`` variable the platform
sets — the same marker the Secure-cookie flag already uses, so there is one
definition of "deployed", not two. ``SB_REQUIRE_PROD=1`` forces the checks
anywhere (staging, a box under test); ``SB_ALLOW_INSECURE=1`` is the
documented escape hatch and says so loudly in the log every time.

This module reads environment only. It never imports the engine, the
portfolio or the database, so it cannot be the reason a boot fails.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# The values that mean "nobody configured this" — a fallback wearing a
# real setting's clothes is worse than an empty one, because it looks set.
FALLBACK_PASSWORDS = {"sourceborn"}
FALLBACK_SESSION_SECRETS = {"dev-only-change-me-in-render-env"}


def is_production() -> bool:
    """True on Render, or wherever SB_REQUIRE_PROD=1 asks for the same bar."""
    return bool(os.environ.get("RENDER")
                or os.environ.get("SB_REQUIRE_PROD") == "1")


def insecure_override() -> bool:
    """The escape hatch. Deliberately loud — see check()."""
    return os.environ.get("SB_ALLOW_INSECURE") == "1"


def problems() -> list[str]:
    """Every readiness failure, each naming the exact env var to set.

    Returns [] when the deployment is sound OR when this is not production.
    Never raises, never logs — so callers can render it on a page as easily
    as they can refuse to boot on it.
    """
    if not is_production():
        return []

    out: list[str] = []

    if not (os.environ.get("MONGO_URL") or "").strip():
        out.append(
            "MONGO_URL is not set. Without it the paper book, closed trades "
            "and the decision journal live in JSON under /tmp, which Render "
            "erases on every restart — the day's trades would silently "
            "vanish. Set MONGO_URL in the Render dashboard.")

    pwd = (os.environ.get("OWNER_PWD")
           or os.environ.get("APP_PASSWORD") or "").strip()
    if not pwd:
        out.append(
            "Neither OWNER_PWD nor APP_PASSWORD is set. The login password "
            "would fall back to a guessable default on a public URL. Set "
            "APP_PASSWORD in the Render dashboard.")
    elif pwd.lower() in FALLBACK_PASSWORDS:
        out.append(
            "The configured password is the old built-in fallback. Choose a "
            "different APP_PASSWORD in the Render dashboard.")

    secret = (os.environ.get("SESSION_SECRET") or "").strip()
    if not secret:
        out.append(
            "SESSION_SECRET is not set. Session cookies would be signed with "
            "a public development key, so a login cookie could be forged. "
            "render.yaml generates this automatically — set it if you deploy "
            "outside that blueprint.")
    elif secret in FALLBACK_SESSION_SECRETS:
        out.append(
            "SESSION_SECRET is still the development placeholder. Set a real "
            "value in the Render dashboard.")

    return out


def check() -> list[str]:
    """problems(), plus the logging policy. Returns what still blocks.

    With SB_ALLOW_INSECURE=1 the problems are logged as warnings and an
    empty list is returned, so the boot proceeds — that is the whole point
    of an escape hatch, and it is never silent.
    """
    found = problems()
    if not found:
        return []
    if insecure_override():
        log.warning(
            "SB_ALLOW_INSECURE=1 — starting DESPITE %d production readiness "
            "failure(s). This deployment is not safe for a live book.",
            len(found))
        for p in found:
            log.warning("  readiness (overridden): %s", p)
        return []
    for p in found:
        log.error("READINESS: %s", p)
    return found


def assert_ready() -> None:
    """Refuse to start a misconfigured production boot.

    Raised at import time in web.main so `uvicorn web.main:app` fails
    immediately with the reason. On Render a failed boot leaves the LAST
    GOOD DEPLOY serving, so this is strictly safer than starting on /tmp
    with a guessable password.
    """
    found = check()
    if not found:
        return
    raise RuntimeError(
        "Refusing to start: this deployment is not production-ready.\n  - "
        + "\n  - ".join(found)
        + "\nFix the variables above in the Render dashboard, or set "
          "SB_ALLOW_INSECURE=1 to start anyway (not safe for a live book).")


def status() -> dict:
    """Booleans for /healthz — never the values themselves."""
    found = problems()
    return {
        "production": is_production(),
        "ready": not found,
        "insecure_override": insecure_override(),
        "problems": found,
    }
