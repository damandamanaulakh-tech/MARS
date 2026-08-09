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


@pytest.fixture(autouse=True)
def _no_network_quotes(monkeypatch):
    monkeypatch.setattr(
        "trading.quote_router.fetch_quotes",
        lambda tickers, upstox_client=None: {},
    )
