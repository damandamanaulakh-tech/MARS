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


_SEEDED_PWD = os.environ.get("OWNER_PWD", "sourceborn")

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
