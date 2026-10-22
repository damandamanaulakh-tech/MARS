"""Web tests must never hit the network: quote lookups return empty
instantly (pages then render with entry-price fallback marks)."""

import os

import pytest

# The startup catchup-scan (scheduler.py) must never fire inside a test
# run — it would launch a real multi-mode network scan mid-suite.
os.environ.setdefault("SB_DISABLE_CATCHUP", "1")
# 2026-10-22: the app scheduler is BUILT but never STARTED under tests — a
# CI run crossing 09:20 IST had the real rotation cron fire mid-suite.
os.environ.setdefault("SB_SCHEDULER_NO_START", "1")

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
# Corporate actions (owner order 2026-10-21): no NSE fetch before the
# ex-date adjuster and no pacing sleeps in tests; production fetches the
# held names once a day before it (test_corporate_actions_jobs pins both).
os.environ.setdefault("SB_CA_FETCH_ON_APPLY", "0")
os.environ.setdefault("SB_CA_FETCH_PACE_S", "0")
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
        # VALFIX batch 3: the day's sector totals and the remap list are kept
        # per process too — one test's copy must not hide the next one's reads
        from web import valuation_market_view as mv
        mv.clear_caches()
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


@pytest.fixture(autouse=True)
def _no_nse_or_candle_network(monkeypatch):
    """2026-10-21: the NSE corporate-action API and the trade audit's v3
    candle GETs are network calls; a web test that needs them injects a
    fetcher. Blocked here = an honest "fetch failed", never a live GET."""
    import trading.corporate_actions as tca
    monkeypatch.setattr(tca, "_nse_get", lambda url, timeout=None: None)
    try:
        from web import trade_audit as ta
        monkeypatch.setattr(ta, "_get_json", lambda url: None)
    except Exception:                                   # noqa: BLE001
        pass


def _macro_feed_blocked(*a, **k):
    raise ConnectionError("network blocked in tests (macro feeds)")


@pytest.fixture(autouse=True)
def _no_macro_feed_network(monkeypatch):
    """VALFIX batch 3 (2026-10-21): the 09:20 and 15:35 jobs now also pull
    FRED and NSE allIndices, and a job test that sets db.is_connected True
    (test_morning_topup …) would reach them — and batch 2's RBI pages —
    over the real network. Every network fetcher in trading.macro_feeds is
    blocked for every web test; a test that needs one stubs it itself
    (monkeypatching over this, as the macro-feed job tests do)."""
    import trading.macro_feeds as mf
    for name in ("fetch_fred", "fetch_nse_all_indices", "fetch_rbi_home"):
        if hasattr(mf, name):
            monkeypatch.setattr(mf, name, _macro_feed_blocked)
    if hasattr(mf, "fetch_gsec_list"):
        # this one never raises: an unreachable RBI page = the committed table
        monkeypatch.setattr(mf, "fetch_gsec_list", lambda timeout=30.0: (
            mf.reference_securities(), "committed table (network blocked in tests)"))
