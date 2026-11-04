"""Source outages preserve evidence, and public feeds never invoke a scan."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from web import db, resource_feed as rf, scheduler, state


@pytest.fixture
def cache(monkeypatch):
    from trading import online_resources as source
    box = {}
    monkeypatch.setattr(rf, "_VOLATILE", {})
    monkeypatch.setattr(db, "LAST_RESOURCE_READ_ERROR", "")
    monkeypatch.setattr(db, "LAST_MACRO_RESOURCE_READ_ERROR", "")
    monkeypatch.setattr(db, "load_resource_feeds", lambda: dict(box))
    monkeypatch.setattr(db, "save_resource_feed", lambda key, row: box.update({key: dict(row)}) or True)
    monkeypatch.setattr(db, "latest_macro_resources", lambda: [])
    monkeypatch.setattr(db, "latest_cash_flow_resources", lambda: {"fii": {}, "dii": {}})
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(source, "FEEDS", [{"key": "sebi", "name": "SEBI", "url": "https://www.sebi.gov.in/sebirss.xml", "kind": "Regulatory"}])
    monkeypatch.setattr(state, "portfolio", lambda: SimpleNamespace(news_watch={}))
    return box


def test_fetch_failure_retains_last_good_notices_and_reports_failed(cache, monkeypatch):
    from trading import online_resources as source
    cache["sebi"] = {"status": "OK", "last_success": "2026-11-03T12:00:00+00:00", "items": [{"title": "Order", "url": "https://www.sebi.gov.in/order", "published_at": "2026-11-03"}]}
    monkeypatch.setattr(source, "fetch_feed", lambda *a, **k: {"status": "FAILED", "error": "HTTP 503"})
    rf.refresh_resources()
    row = cache["sebi"]
    assert row["status"] == "FAILED" and row["last_success"] == "2026-11-03T12:00:00+00:00"
    assert row["items"][0]["title"] == "Order" and row["retained"]
    view = rf.resource_view()
    assert view["sources"][0]["error"] == "HTTP 503"
    assert view["notices"][0]["feed_status"] == "FAILED"


def test_refused_write_is_visible_and_never_claimed_persisted(cache, monkeypatch):
    from trading import online_resources as source
    monkeypatch.setattr(source, "fetch_feed", lambda *a, **k: {"status": "OK", "items": [{"title": "Fresh", "published_at": "2026-11-04"}]})
    monkeypatch.setattr(db, "save_resource_feed", lambda *a: False)
    assert rf.refresh_resources()["sources"]["sebi"] == "NOT_STORED"
    assert not cache
    assert rf.resource_view()["sources"][0]["status"] == "NOT_STORED"


def test_budget_exhaustion_defers_without_network_or_losing_items(cache, monkeypatch):
    from trading import online_resources as source
    monkeypatch.setattr(source, "fetch_feed", lambda *a, **k: pytest.fail("No budget for network"))
    assert rf.refresh_resources(budget_s=0)["sources"]["sebi"] == "DEFERRED"


def test_freshness_uses_observation_date_and_news_actual_ts(cache, monkeypatch):
    monkeypatch.setattr(db, "latest_macro_resources", lambda: [{"series": "fred_sp500", "date": "2026-10-01", "value": 100}])
    monkeypatch.setattr(state, "portfolio", lambda: SimpleNamespace(news_watch={
        "ts": "2026-11-04T02:00:00", "fetch_failed": ["TCS.NS"],
        "rows": {"TCS.NS": {"asof": "2026-11-04T07:30:00+05:30"}, "ITC.NS": {"verdict": "PASS"}}}))
    view = rf.resource_view(datetime(2026, 11, 4, 3, tzinfo=timezone.utc))
    rows = {r["key"]: r for r in view["sources"]}
    assert rows["fred_sp500"]["status"] == "STALE"
    assert rows["company_news"]["status"] == "PARTIAL"
    assert rows["company_news"]["last_attempt"] == "2026-11-04T02:00:00"
    assert rows["company_news"]["as_of"].startswith("2026-11-04")


def test_market_failure_stays_visible_beside_stored_observation(cache, monkeypatch):
    monkeypatch.setattr(db, "latest_macro_resources", lambda: [{"series": "fred_sp500", "date": "2026-11-03", "value": 100}])
    rf.record_attempt("fred_sp500", "FAILED", "Request timed out.")
    item = next(r for r in rf.resource_view()["sources"] if r["key"] == "fred_sp500")
    assert item["status"] == "FAILED" and item["as_of"] == "2026-11-03"
    assert item["error"] == "Request timed out."


def test_resources_job_is_registered_outside_trading_callbacks(monkeypatch):
    monkeypatch.setenv("DAILY_SCHEDULER", "1")
    monkeypatch.setenv("SB_SCHEDULER_NO_START", "1")
    monkeypatch.setattr(scheduler, "_SCHED", None)
    scheduler.start_if_enabled()
    jobs = {j.id: j for j in scheduler._SCHED.get_jobs()}
    assert jobs["online_resources"].func is scheduler._online_resources_refresh
    assert jobs["online_resources_startup"].func is scheduler._online_resources_refresh
    assert "hour='9,12,16'" in str(jobs["online_resources"].trigger)
    monkeypatch.setattr(rf, "refresh_resources", lambda: {"status": "COMPLETE"})
    monkeypatch.setattr(state, "engine", lambda: pytest.fail("Public feed must not reach trading"))
    scheduler._online_resources_refresh()


def test_resource_snapshot_requires_database_ack(monkeypatch):
    class Store:
        def replace_one(self, *a, **kw):
            return SimpleNamespace(acknowledged=False)
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(resource_feeds=Store()))
    assert db.save_resource_feed("sebi", {"status": "OK"}) is False


def test_bse_existing_feed_reaches_resources_with_failed_attempt_visible(cache, monkeypatch):
    monkeypatch.setattr(state, "portfolio", lambda: SimpleNamespace(
        news_watch={}, filings=[{"ticker": "TCS.NS", "headline": "Filed result", "pdf": "https://www.bseindia.com/result.pdf", "when": "2026-11-03 10:00"}],
        decision_journal=[{"action": "FILINGS_REFRESH", "ts": "2026-11-03T04:00:00"},
                          {"action": "FILINGS_FAILED", "ts": "2026-11-04T04:00:00"}]))
    view = rf.resource_view()
    row = next(r for r in view["sources"] if r["key"] == "bse_filings")
    assert row["status"] == "FAILED" and row["last_success"] == "2026-11-03T04:00:00"
    assert row["as_of"] == "2026-11-03 10:00"
    notice = next(n for n in view["notices"] if n.get("ticker") == "TCS.NS")
    assert notice["title"] == "Filed result" and notice["feed_status"] == "FAILED"


def test_cash_flows_use_independent_dates_and_preserve_real_zero(cache, monkeypatch):
    monkeypatch.setattr(db, "latest_cash_flow_resources", lambda: {
        "fii": {"date": "2026-10-01", "fii_net": -150, "source": "nsdl_settlement"},
        "dii": {"date": "2026-11-03", "dii_net": 0, "dii_source": "upstox_market_dii"}})
    rows = {s["key"]: s for s in rf.resource_view(datetime(2026, 11, 4, tzinfo=timezone.utc))["sources"]}
    assert rows["fii_cash"]["status"] == "STALE" and rows["fii_cash"]["as_of"] == "2026-10-01"
    assert rows["dii_cash"]["status"] == "STORED" and "Net 0.00 crore INR" in rows["dii_cash"]["reason"]


def test_failed_cache_read_cannot_erase_last_good_snapshot(cache, monkeypatch):
    from trading import online_resources as source
    cache["sebi"] = {"items": [{"title": "Last good notice"}], "last_success": "2026-11-03"}
    monkeypatch.setattr(db, "load_resource_feeds", lambda: {})
    monkeypatch.setattr(db, "LAST_RESOURCE_READ_ERROR", "Read temporarily failed")
    monkeypatch.setattr(source, "fetch_feed", lambda *a, **k: pytest.fail("Do not fetch into an unreadable snapshot"))
    monkeypatch.setattr(db, "save_resource_feed", lambda *a: pytest.fail("Do not replace unreadable prior evidence"))
    assert rf.refresh_resources()["status"] == "STORE_UNAVAILABLE"
    rf.record_attempt("sebi", "FAILED", "Request failed")
    assert cache["sebi"]["items"] == [{"title": "Last good notice"}]
