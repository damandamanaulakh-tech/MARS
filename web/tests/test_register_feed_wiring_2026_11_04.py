from datetime import date
from types import SimpleNamespace

import pytest

from trading import register_v4 as register
from web import state


@pytest.mark.parametrize("symbol", ["TCS", "TCS.NS", "tcs"])
def test_register_reads_stored_exchange_ticker_news(monkeypatch, symbol):
    monkeypatch.setattr(state, "portfolio", lambda: SimpleNamespace(news_watch={"rows": {
        "TCS.NS": {"verdict": "TRIGGER", "sentiment": 0.5, "asof": "2026-11-04", "tags": ["order_win"], "headlines": [{}]}}}))
    out = {}
    register._wire_news_item(out, symbol)
    assert out["W-019"].state == "VALUE" and out["W-019"].value == 0.5


def test_register_never_substitutes_nse_news_for_explicit_bse_symbol(monkeypatch):
    monkeypatch.setattr(state, "portfolio", lambda: SimpleNamespace(news_watch={"rows": {
        "TCS.NS": {"verdict": "PASS", "sentiment": 0.5}}}))
    out = {}
    register._wire_news_item(out, "TCS.BO")
    assert "W-019" not in out


@pytest.mark.parametrize("fresh", [True, False])
def test_fresh_option_chain_overrides_proxy_but_stale_keeps_proxy(monkeypatch, fresh):
    from trading import external_panels as xp, options_chain
    monkeypatch.setattr(state, "portfolio", lambda: SimpleNamespace(
        news_watch={}, option_chain={"asof": date.today().isoformat(), "rows": {
            "TCS.NS": {"pcr_oi": 1.7, "expiry": "2026-11-05"}}}))
    monkeypatch.setattr(options_chain, "is_fresh", lambda stamp: fresh)
    monkeypatch.setattr(xp, "shareholding_quarters", lambda symbol: [])
    monkeypatch.setattr(xp, "deal_summary", lambda symbol: None)
    monkeypatch.setattr(xp, "insider_events", lambda symbol: None)
    monkeypatch.setattr(xp, "pcr_oi", lambda symbol: {"pcr_oi": 0.4, "basis": "old proxy", "asof": "2026-01-01"})
    monkeypatch.setattr(register, "_wire_fetched_feeds", lambda *args: None)
    out = {}
    register._wire_intake_items(out, "TCS", None)
    assert out["W-022"].value == (1.7 if fresh else 0.4)
    assert ("TRUE chain" in out["W-022"].note) is fresh
