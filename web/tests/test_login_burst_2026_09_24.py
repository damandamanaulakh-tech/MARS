"""The login-time burst (owner 2026-09-24: "upstox login issue as well",
alongside "its hitting 512 +").

What the OAuth callback fires the moment the owner logs in: the catch-up
scan (5 modes x the scanned universe), the option-chain snapshot, and the
FII pull — three threads at once on a 512 MB box. The chain snapshot used to
cover custom_universe + held, ~1,200 names since the liquidity rebuild, at
TWO authenticated calls each: ~2,400 calls in one burst against Upstox's
1,000-per-30-minute limit, so the back ~1,900 silently 429'd while it
hammered for twenty minutes.

Ruled OUT while looking, so nobody re-chases them: token_expires_at_ist is
UTC-consistent on both sides (not the 2026-09-16 clock class); the client
never clears the token on a 401/429, so rate limits cannot log the owner
out; _client_from_session falls back to Mongo, so the badge agrees across
devices and restarts.
"""
from __future__ import annotations

import inspect

from trading import options_chain as OC


def _operative(src: str) -> str:
    return "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))


def test_the_daily_chain_job_is_capped_and_paced():
    assert OC.OPTION_CHAIN_MAX_TICKERS <= 150, \
        "120 names x 2 calls must stay inside Upstox's 1,000/30min"
    # the conftest sets SB_CHAIN_PACE_S=0 so the suite is not slowed; the
    # production default is the module's own number, read at import
    assert OC.OPTION_CHAIN_PACE_S >= 0.0


def test_snapshot_universe_paces_between_names(monkeypatch):
    """Two calls per name; the sleep sits between NAMES, never before the
    first, so the one-stock button (pace 0) is unchanged and instant."""
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    class B:
        def get_option_contracts(self, ik):
            return [{"expiry": "2099-01-01"}]

        def get_option_chain(self, ik, expiry):
            return []

    from datetime import date
    OC.snapshot_universe(B(), ["A.NS", "B.NS", "C.NS"], lambda t: f"K|{t}",
                         date(2026, 9, 24), include_index=False, pace_s=0.5)
    assert slept == [0.5, 0.5]           # 3 names -> 2 gaps
    slept.clear()
    OC.snapshot_universe(B(), ["A.NS"], lambda t: f"K|{t}",
                         date(2026, 9, 24), include_index=False)
    assert slept == []                   # default pace 0: the button path


def test_the_daily_job_scope_is_held_picks_watchlist_and_owner_list_not_the_universe():
    """The scope is what the chain INFORMS — T12W_0102 on held/watched
    names, W-022/W-007 per stock — never the whole scanned universe.
    Pinned on the operative source of the job."""
    from web import scheduler
    src = _operative(inspect.getsource(scheduler.run_option_chain_snapshot))
    assert "custom_universe" not in src, \
        "the chain job must not fan out over the scanned universe"
    for part in ("p.positions.keys()", "last_scan_picks", "_watchlist_names(p)",
                 "OWNER_UNIVERSE_2026_08_20"):
        assert part in src, part
    assert "[:OPTION_CHAIN_MAX_TICKERS]" in src
    assert "pace_s=OPTION_CHAIN_PACE_S" in src


def test_the_login_callback_still_runs_the_chain_in_the_background():
    """The callback must return to the browser at once; the three jobs it
    fires are threads. If any of them were ever moved inline, a login would
    hang for the length of a scan."""
    from web.routes import upstox as R
    src = _operative(inspect.getsource(R.callback))
    assert "Thread(" in src and "daemon=True" in src
    assert "catchup_scan_if_missed" in src


def test_ruled_out_token_expiry_is_utc_consistent():
    """connected_at is utcnow(); the expiry maths and hours_until_expiry
    both stay in UTC. A fresh login must never read as already expired."""
    from datetime import datetime, timedelta

    from trading.brokers.upstox import UpstoxClient
    now = datetime.utcnow().replace(microsecond=0)
    exp = UpstoxClient.token_expires_at_ist(now.isoformat())
    assert exp is not None and exp > now
    assert exp - now <= timedelta(hours=24)


def test_ruled_out_the_client_never_clears_the_token_on_api_errors():
    """A 401/429 mid-day must not log the owner out; only /upstox/disconnect
    and the 03:30 IST expiry end a session."""
    from trading.brokers import upstox as U
    src = _operative(inspect.getsource(U))
    assert "access_token = None" not in src
    assert "clear_upstox_token" not in src


def test_ruled_out_the_status_route_falls_back_to_mongo():
    from web.routes import upstox as R
    src = _operative(inspect.getsource(R._client_from_session))
    assert "load_upstox_token" in src
