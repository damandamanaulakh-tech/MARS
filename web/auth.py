"""Session-based auth. Single seeded account: the owner.

Role: `owner`. There is no second account — the owner is the only user
(their words, 2026-07-27). A `viewer` account with the hardcoded password
"viewer" used to sit here alongside a login-page button that carried both
passwords in the markup; both are gone. Phase 2 swaps the seeded dict for a
Mongo `users` collection.

OWNER_PWD MUST be set in the Render environment. The fallback below exists so
local dev and the test suite work; on a public URL it is a guessable password.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request


# Owner password. Two env names accepted: OWNER_PWD (documented in
# render.yaml) and APP_PASSWORD (the name the owner actually used in the
# Render dashboard, 2026-07-27 — code bends to the owner, not the reverse).
# OWNER_PWD wins if both are set. The "sourceborn" fallback exists only for
# local dev and tests; on a public URL it is a guessable password.
# AUDIT E (2026-08-09): if the owner never set OWNER_PWD, the app runs on a
# guessable brand-word password ON A PUBLIC URL. Impossible to miss now.
import logging as _logging
_log = _logging.getLogger(__name__)
if not (os.environ.get("OWNER_PWD") or os.environ.get("APP_PASSWORD")):
    _log.error("SECURITY: OWNER_PWD is not set — the login password is the "
               "guessable fallback. Set OWNER_PWD in the Render dashboard.")
_SEEDED_PWD = (os.environ.get("OWNER_PWD")
               or os.environ.get("APP_PASSWORD")
               or "sourceborn")

SEEDED_USERS = {
    "owner@sourceborn.in": {"role": "owner", "password": _SEEDED_PWD, "name": "Owner"},
}


def _hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def verify(email: str, password: str) -> Optional[dict]:
    user = SEEDED_USERS.get(email.strip().lower())
    if not user:
        return None
    if not hmac.compare_digest(_hash(user["password"]), _hash(password)):
        return None
    return {"email": email, "role": user["role"], "name": user["name"]}


def current_user(request: Request) -> Optional[dict]:
    return request.session.get("user")


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_owner(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user
