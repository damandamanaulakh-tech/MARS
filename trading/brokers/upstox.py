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
from urllib.parse import quote, urlencode

import httpx


log = logging.getLogger(__name__)


# Upstox v2 endpoints
AUTH_URL    = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL   = "https://api.upstox.com/v2/login/authorization/token"
QUOTE_URL   = "https://api.upstox.com/v2/market-quote/quotes"
LTP_URL     = "https://api.upstox.com/v2/market-quote/ltp"
LTP_BATCH   = 250          # API allows up to 500 per call; stay well inside
HIST_URL    = "https://api.upstox.com/v2/historical-candle"
PROFILE_URL = "https://api.upstox.com/v2/user/profile"
FUNDS_URL   = "https://api.upstox.com/v2/user/get-funds-and-margin"


def assert_paper_trading() -> None:
    """P2/D3 HARD safety lock: no code path may place a live broker order
    unless the operator explicitly sets PAPER_ONLY=0 in the environment.
    Any future order-placement method MUST call this first. Checked at
    call time (not import) so the lock can't be cached away."""
    if os.environ.get("PAPER_ONLY", "1") != "0":
        raise RuntimeError(
            "PAPER_ONLY lock active: live order placement is disabled. "
            "Set PAPER_ONLY=0 in the environment to explicitly enable."
        )


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

    def get_daily_candles(self, instrument_key: str, from_date: str,
                          to_date: str) -> Optional["object"]:
        """Daily OHLCV history from Upstox (read-only, no order scope).

        Returns a pandas DataFrame (Open/High/Low/Close/Volume, float32,
        ascending DatetimeIndex) or None on any failure. This is the
        yfinance replacement: Yahoo 429-blocks datacenter IPs (Render),
        Upstox does not.

        NO TOKEN REQUIRED — audit F1 fix (2026-08-20, owner: "fix F1").
        Upstox HISTORICAL candles are a PUBLIC endpoint (verified live from
        the sandbox 2026-08-09 and re-verified 2026-08-20: real TCS candles
        with a bare accept header, no Authorization). This method used to
        route through _auth_headers(), which raises when the 03:30-dead
        token isn't back — so the Friday 16:30 weekly top-up silently lost
        any week the owner hadn't logged in yet, for no API reason at all.
        Auth headers are still SENT when a token exists (harmless, and
        matches Upstox's documented usage); their absence is no longer a
        failure. Live quotes/orders still require the daily login — only
        this read-only history path is public.
        """
        import pandas as pd
        try:
            headers = (self._auth_headers() if self.access_token
                       else {"accept": "application/json"})
            url = f"{HIST_URL}/{quote(instrument_key, safe='')}/day/{to_date}/{from_date}"
            r = httpx.get(url, headers=headers, timeout=30.0)
            r.raise_for_status()
            candles = (r.json().get("data") or {}).get("candles") or []
            if not candles:
                return None
            df = pd.DataFrame(
                [c[:6] for c in candles],
                columns=["ts", "Open", "High", "Low", "Close", "Volume"],
            )
            df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None).dt.normalize()
            df = df.set_index("ts").sort_index()
            for c in df.columns:
                df[c] = df[c].astype("float32")
            return df
        except (httpx.HTTPError, UpstoxAuthError, ValueError, KeyError) as e:
            log.warning("Upstox get_daily_candles failed for %s: %s", instrument_key, e)
            return None

    def get_intraday_candles(self, instrument_key: str,
                             interval: str = "30minute") -> Optional["object"]:
        """Today's intraday OHLCV (1minute / 30minute) — the live candle layer.
        Same frame shape as get_daily_candles; None on failure."""
        import pandas as pd
        try:
            url = (f"https://api.upstox.com/v2/historical-candle/intraday/"
                   f"{quote(instrument_key, safe='')}/{interval}")
            r = httpx.get(url, headers=self._auth_headers(), timeout=15.0)
            r.raise_for_status()
            candles = (r.json().get("data") or {}).get("candles") or []
            if not candles:
                return None
            df = pd.DataFrame(
                [c[:6] for c in candles],
                columns=["ts", "Open", "High", "Low", "Close", "Volume"],
            )
            df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            df = df.set_index("ts").sort_index()
            for c in df.columns:
                df[c] = df[c].astype("float32")
            return df
        except (httpx.HTTPError, UpstoxAuthError, ValueError, KeyError) as e:
            log.warning("Upstox get_intraday_candles failed for %s: %s", instrument_key, e)
            return None

    def get_option_contracts(self, instrument_key: str) -> Optional[list]:
        """Option contracts for an underlying (Node 8, 2026-08-23). Returns
        the raw contract list (each row carries an `expiry`) or None — None
        also covers "this underlying has no options", which the caller must
        treat as not-F&O, never as an error worth retrying.

        AUTH REQUIRED — unlike historical candles, the option endpoints
        reject tokenless calls, so this feed honestly depends on the owner's
        daily login (audit F8). Read-only; the paper lock is untouched.
        """
        try:
            r = httpx.get(
                "https://api.upstox.com/v2/option/contract",
                params={"instrument_key": instrument_key},
                headers=self._auth_headers(), timeout=20.0)
            r.raise_for_status()
            return (r.json().get("data") or []) or None
        except (httpx.HTTPError, UpstoxAuthError, ValueError, KeyError) as e:
            log.warning("Upstox option contracts failed for %s: %s",
                        instrument_key, e)
            return None

    def get_option_chain(self, instrument_key: str,
                         expiry_date: str) -> Optional[list]:
        """Full option chain for one underlying + expiry: raw per-strike rows
        (call_options / put_options with market_data.oi and
        option_greeks.iv). None on any failure. Shape per Upstox's published
        v2 schema — the sandbox holds no token, so the FIRST post-login run
        is the live verification; a parse miss journals, never guesses.
        """
        try:
            r = httpx.get(
                "https://api.upstox.com/v2/option/chain",
                params={"instrument_key": instrument_key,
                        "expiry_date": expiry_date},
                headers=self._auth_headers(), timeout=30.0)
            r.raise_for_status()
            return (r.json().get("data") or []) or None
        except (httpx.HTTPError, UpstoxAuthError, ValueError, KeyError) as e:
            log.warning("Upstox option chain failed for %s %s: %s",
                        instrument_key, expiry_date, e)
            return None

    def get_quote(self, instrument_key: str) -> Optional[dict]:
        """Full real-time quote for one instrument.

        instrument_key format: 'NSE_EQ|INE002A01018' (segment|ISIN), or
        'NSE_INDEX|Nifty 50' for indices.

        FIXED 2026-07-31 — this sent params={"symbol": ...}. The Upstox v2
        market-quote endpoints take **instrument_key**, so every request was
        malformed and every quote silently failed, falling back to a yfinance
        path that Render blocks. That is why the app never showed a live price.
        """
        try:
            r = httpx.get(
                QUOTE_URL,
                headers=self._auth_headers(),
                params={"instrument_key": instrument_key},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            # Response is keyed 'NSE_EQ:RELIANCE'; flatten a single entry.
            if isinstance(data, dict) and len(data) == 1:
                return next(iter(data.values()))
            return data
        except (httpx.HTTPError, UpstoxAuthError) as e:
            log.warning("Upstox get_quote(%s) failed: %s", instrument_key, e)
            return None

    def bid_ask(self, ticker_or_key: str) -> tuple[Optional[float], Optional[float]]:
        """(best_bid, best_ask) from the full quote's order-book depth.

        FIXED 2026-07-31 — engine.scan() has always called self.broker.bid_ask()
        inside a try/except to compute a spread and reject wide-spread names.
        The method DID NOT EXIST, so it raised AttributeError every time, was
        swallowed, spread_pct stayed None, and the spread half of the liquidity
        gate never ran once. A risk check that silently does nothing is worse
        than no check, because the code reads as if it is protecting you.

        Accepts a yfinance ticker ("TCS.NS") or a raw instrument key.
        Returns (None, None) when depth is unavailable — the caller then skips
        the spread test explicitly rather than believing a fabricated spread.
        """
        key = ticker_or_key
        if "|" not in key:
            try:
                from .upstox_instruments import to_instrument_keys
                key = to_instrument_keys([ticker_or_key]).get(ticker_or_key) or ""
            except Exception:
                key = ""
        if not key:
            return None, None
        q = self.get_quote(key)
        if not isinstance(q, dict):
            return None, None
        depth = q.get("depth")
        if not isinstance(depth, dict):
            return None, None
        try:
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []
            bid = float(buys[0]["price"]) if buys else None
            ask = float(sells[0]["price"]) if sells else None
        except (KeyError, IndexError, TypeError, ValueError):
            return None, None
        if not bid or not ask or bid <= 0 or ask <= 0:
            return None, None
        return bid, ask

    def get_ltp(self, instrument_keys: list[str]) -> dict[str, float]:
        """BATCHED last-traded prices: {instrument_key: price}.

        One HTTP call per 250 instruments instead of one per stock — a 43-name
        book was making 43 sequential requests, which is slow enough to time
        out and look like "no live prices".

        Results are mapped back by the response's `instrument_token` (which
        echoes the key we sent) rather than by the response dict key, because
        that key is 'SEGMENT:TRADINGSYMBOL' and does not match the input.
        Partial success is honest success: whatever resolved is returned.
        """
        out: dict[str, float] = {}
        keys = [k for k in (instrument_keys or []) if k]
        for i in range(0, len(keys), LTP_BATCH):
            chunk = keys[i:i + LTP_BATCH]
            try:
                r = httpx.get(
                    LTP_URL,
                    headers=self._auth_headers(),
                    params={"instrument_key": ",".join(chunk)},
                    timeout=15.0,
                )
                r.raise_for_status()
                data = r.json().get("data") or {}
            except (httpx.HTTPError, UpstoxAuthError, ValueError) as e:
                log.warning("Upstox get_ltp batch of %d failed: %s", len(chunk), e)
                continue
            if not isinstance(data, dict):
                continue
            wanted = set(chunk)
            for resp_key, row in data.items():
                if not isinstance(row, dict):
                    continue
                px = row.get("last_price")
                if px is None:
                    continue
                token = row.get("instrument_token")
                if token in wanted:
                    out[token] = float(px)
                # AUDIT FIX C10 (2026-08-09): the old "fallback" matched the
                # response key's trading symbol (NSE_EQ:RELIANCE) against
                # k.split("|")[-1] — which for NSE_EQ|INE002A01018 is the
                # ISIN, never the symbol. Dead code that READ as protection.
                # Deleted: instrument_token echoing the request is the API
                # contract; a row without it means that ticker is absent,
                # which callers now surface honestly instead of papering over.
        return out
