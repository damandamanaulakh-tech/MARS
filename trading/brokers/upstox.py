"""Upstox API v2 client.

Phase 3 scope:
  - OAuth code -> access token exchange (authorization_code grant)
  - Real-time quote fetcher (REST)
  - Profile + funds lookup
  - Connection status helpers

Out of scope for Phase 3 (Phase 4 plans):
  - WebSocket live ticks (sub-second updates)
  - Order placement (gated by paper-to-live toggle)
  - Token auto-refresh (Upstox issues single-day tokens; no refresh token)
  - Instrument master file caching

Upstox token lifecycle: tokens expire at 03:30 IST every day. The user
must re-login each morning. We surface "expires in X hours" on the
dashboard so the operator knows when to re-auth.

API reference: https://upstox.com/developer/api-documentation/open-api/
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx


log = logging.getLogger(__name__)


# Upstox v2 endpoints
AUTH_URL    = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL   = "https://api.upstox.com/v2/login/authorization/token"
QUOTE_URL   = "https://api.upstox.com/v2/market-quote/quotes"
PROFILE_URL = "https://api.upstox.com/v2/user/profile"
FUNDS_URL   = "https://api.upstox.com/v2/user/get-funds-and-margin"


class UpstoxConfigError(Exception):
    """Raised when required env vars are missing."""


class UpstoxAuthError(Exception):
    """Token exchange or invalid token."""


@dataclass
class UpstoxClient:
    """Thin wrapper around Upstox v2 REST. One client per signed-in user."""

    api_key: str
    api_secret: str
    redirect_uri: str
    access_token: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    connected_at: Optional[str] = None  # ISO timestamp

    @classmethod
    def from_env(cls) -> "UpstoxClient":
        api_key = os.environ.get("UPSTOX_API_KEY")
        api_secret = os.environ.get("UPSTOX_API_SECRET")
        redirect_uri = os.environ.get("UPSTOX_REDIRECT_URI")
        if not (api_key and api_secret and redirect_uri):
            raise UpstoxConfigError(
                "Missing one or more env vars: "
                "UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI"
            )
        return cls(api_key=api_key, api_secret=api_secret, redirect_uri=redirect_uri)

    # ---------- OAuth flow ----------

    def authorization_url(self, state: Optional[str] = None) -> str:
        """The URL we redirect the user to so they can grant access."""
        params = {
            "response_type": "code",
            "client_id": self.api_key,
            "redirect_uri": self.redirect_uri,
        }
        if state:
            params["state"] = state
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """POST the auth code back to Upstox; receive access_token + user info.

        Returns the raw JSON body on success. Raises UpstoxAuthError on failure.
        Sets self.access_token / user_id / user_name / connected_at on success.
        """
        data = {
            "code": code,
            "client_id": self.api_key,
            "client_secret": self.api_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        headers = {
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            r = httpx.post(TOKEN_URL, data=data, headers=headers, timeout=10.0)
        except httpx.HTTPError as e:
            log.warning("Upstox token exchange network error: %s", e)
            raise UpstoxAuthError(f"Network error contacting Upstox: {e}") from e

        if r.status_code >= 400:
            log.warning("Upstox token exchange %s: %s", r.status_code, r.text[:200])
            raise UpstoxAuthError(
                f"Upstox returned {r.status_code}: {r.text[:200]}"
            )
        body = r.json()
        token = body.get("access_token")
        if not token:
            raise UpstoxAuthError(f"No access_token in response: {body!r}")

        self.access_token = token
        self.user_id = body.get("user_id")
        self.user_name = body.get("user_name") or body.get("email") or "Upstox User"
        self.connected_at = datetime.utcnow().isoformat()
        return body

    # ---------- token lifecycle ----------

    def is_connected(self) -> bool:
        return bool(self.access_token)

    @staticmethod
    def token_expires_at_ist(connected_at_iso: Optional[str]) -> Optional[datetime]:
        """Upstox tokens expire at 03:30 IST every day, regardless of when issued.

        Returns the next-3:30-IST datetime in UTC. Returns None if no
        connected_at given (i.e. never connected).
        """
        if not connected_at_iso:
            return None
        try:
            connected = datetime.fromisoformat(connected_at_iso)
        except ValueError:
            return None
        # 03:30 IST = 22:00 UTC the previous day
        # Easier: tomorrow's 03:30 IST after connected_at
        # Express in UTC: IST = UTC + 5:30, so 03:30 IST = 22:00 UTC the previous day
        # If connected_at_utc < today's 22:00 UTC, expiry = today's 22:00 UTC
        # Else expiry = tomorrow's 22:00 UTC
        today_2200 = connected.replace(hour=22, minute=0, second=0, microsecond=0)
        if connected < today_2200:
            return today_2200
        return today_2200 + timedelta(days=1)

    def hours_until_expiry(self) -> Optional[float]:
        expiry = self.token_expires_at_ist(self.connected_at)
        if not expiry:
            return None
        delta = expiry - datetime.utcnow()
        return max(0.0, delta.total_seconds() / 3600.0)

    # ---------- REST helpers ----------

    def _auth_headers(self) -> dict:
        if not self.access_token:
            raise UpstoxAuthError("Not connected; call exchange_code first")
        return {
            "accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    def get_profile(self) -> Optional[dict]:
        try:
            r = httpx.get(PROFILE_URL, headers=self._auth_headers(), timeout=10.0)
            r.raise_for_status()
            return r.json().get("data")
        except (httpx.HTTPError, UpstoxAuthError) as e:
            log.warning("Upstox get_profile failed: %s", e)
            return None

    def get_funds(self) -> Optional[dict]:
        try:
            r = httpx.get(FUNDS_URL, headers=self._auth_headers(), timeout=10.0)
            r.raise_for_status()
            return r.json().get("data")
        except (httpx.HTTPError, UpstoxAuthError) as e:
            log.warning("Upstox get_funds failed: %s", e)
            return None

    def get_quote(self, instrument_key: str) -> Optional[dict]:
        """Real-time quote for one instrument.

        instrument_key format: 'NSE_EQ|INE002A01018' (segment|ISIN). For NSE
        stocks the segment is always NSE_EQ. We resolve symbol -> ISIN via
        the instrument master file (Phase 4 will cache this).

        For Phase 3 we accept the instrument key as-is.
        """
        try:
            r = httpx.get(
                QUOTE_URL,
                headers=self._auth_headers(),
                params={"symbol": instrument_key},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            # Upstox returns {'NSE_EQ:RELIANCE': {...}}; flatten if single entry
            if isinstance(data, dict) and len(data) == 1:
                return next(iter(data.values()))
            return data
        except (httpx.HTTPError, UpstoxAuthError) as e:
            log.warning("Upstox get_quote(%s) failed: %s", instrument_key, e)
            return None
