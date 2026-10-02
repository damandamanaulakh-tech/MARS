"""Web tests must never hit the network: quote lookups return empty
instantly (pages then render with entry-price fallback marks)."""

import os

import pytest

# The startup catchup-scan (scheduler.py) must never fire inside a test
# run — it would launch a real multi-mode network scan mid-suite.
os.environ.setdefault("SB_DISABLE_CATCHUP", "1")

# The login brute-force throttle (1s per attempt) would add minutes to the
# suite — hundreds of tests log in. Off under tests, on in production.
os.environ.setdefault("SB_LOGIN_DELAY_SEC", "0")

# The market-hours trade gate (owner order 2026-09-10) would block every
# trading test that happens to run outside 09:15-15:30 IST — i.e. most CI
# runs. Off under tests, ON in production; test_market_hours.py pins that the
# production default is gated and that this variable is nowhere in render.yaml.
os.environ.setdefault("SB_IGNORE_MARKET_HOURS", "1")
os.environ.setdefault("SB_CHAIN_PACE_S", "0")      # no sleeps between chain names in tests
os.environ.setdefault("SB_SKIP_UPSTOX_PREFLIGHT", "1")  # tests never GET Upstox's dialog


@pytest.fixture(autouse=True)
def _no_network_quotes(monkeypatch):
    monkeypatch.setattr(
        "trading.quote_router.fetch_quotes",
        lambda tickers, upstox_client=None: {},
    )


class _UpstoxNoNetwork:
    """Audit T-2 (2026-10-02): a web test reached the LIVE Upstox option
    endpoint (401 in the log) — an earlier test leaves the engine "logged
    in" with a fake token, and run_trigger_layer then ran the real chain
    snapshot. The Upstox client's own HTTP calls are blocked for every web
    test; a test that needs them mocks `trading.brokers.upstox.httpx.get`
    itself (monkeypatching over this) or hands the engine a fake broker."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def _blocked(self, *a, **k):
        raise self._real.ConnectError("network blocked in tests (audit T-2)")

    get = _blocked
    post = _blocked


@pytest.fixture(autouse=True)
def _no_upstox_network(monkeypatch):
    import httpx
    import trading.brokers.upstox as up
    monkeypatch.setattr(up, "httpx", _UpstoxNoNetwork(httpx))
