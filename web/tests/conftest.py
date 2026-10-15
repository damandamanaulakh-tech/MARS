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
# Den §12 guards (2026-10-15): the memory ceiling reads THIS process's
# resident memory — a full test run sits far above the live box's 380 MiB,
# so every §12 row would read "not computed"; and the 20 s time budget
# would make a slow CI box skip rows. Both off under tests, ON in
# production; test_den_chain_cost_2026_10_15.py sets them explicitly and
# pins the production defaults and that neither variable is in render.yaml.
os.environ.setdefault("SB_DEN_CHAIN_RSS_CEILING_MB", "0")
os.environ.setdefault("SB_DEN_CHAIN_BUDGET_S", "0")


@pytest.fixture(autouse=True)
def _fresh_den_chain_rows():
    """§12 keeps computed rows for 30 min per process — one test's row must
    never be served to the next (fixtures differ, the ticker may not)."""
    try:
        from web import valuation_chain_view as cv, valuation_view as vv
        cv.clear_row_cache()
        vv.clear_series_cache()
    except Exception:                                   # noqa: BLE001
        pass
    yield


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
