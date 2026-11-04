"""Global cards consume actual stored observations; never relabel them live."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from trading import macro_feeds as mf
from web import db, market_panels as mp
from web.main import app

TODAY = date(2026, 11, 4)


def _row(series="fred_sp500", day="2026-11-03", value=5000, fetched="2026-11-04T01:00:00+05:30"):
    return {"series": series, "date": day, "value": value, "fetched": fetched}


def test_stored_closes_reach_cards_with_observation_and_fetch_dates():
    rows = [_row(value=4900, day="2026-11-02"), _row()]
    cards = mp.global_market_panel(rows, TODAY)
    sp = cards[0]
    assert sp["value"] == 5000 and sp["status"] == "STORED"
    assert sp["as_of"] == "2026-11-03"
    assert sp["fetched"] == "2026-11-04T01:00:00+05:30"
    assert sp["frequency"] == "Daily close"
    assert sp["url"] == "https://fred.stlouisfed.org/series/SP500"
    assert len(cards) == 5
    assert all(c["value"] is None for c in cards[1:])


def test_stale_closes_are_visible_and_not_freshened_by_recent_fetch():
    cards = mp.global_market_panel([_row(day="2026-10-20")], TODAY)
    assert cards[0]["value"] == 5000 and cards[0]["status"] == "STALE"
    assert cards[0]["as_of"] == "2026-10-20"


def test_weekly_fx_lag_is_distinct_from_daily_index_freshness():
    rows = [_row(day="2026-10-25"), _row(series="fred_usdinr", day="2026-10-25", value=90)]
    cards = {r["series"]: r for r in mp.global_market_panel(rows, TODAY)}
    assert cards["fred_sp500"]["status"] == "STALE"
    fx = cards["fred_usdinr"]
    assert fx["status"] == "STORED" and fx["value"] == 90
    assert fx["frequency"] == "Daily observation · weekly release"


@pytest.mark.parametrize("row", [_row(value=float("inf")), _row(value=float("nan")),
                                  _row(value=0), _row(value=-10), _row(day="2026-11-05"),
                                  _row(day="invalid")])
def test_invalid_observations_remain_data_needed(row):
    sp = mp.global_market_panel([row], TODAY)[0]
    assert sp["value"] is None and sp["status"] == "DATA_NEEDED"


def test_global_panel_only_reads_bounded_latest_store_not_external_http(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "latest_macro_resources", lambda: calls.append("latest") or [_row()])
    monkeypatch.setattr("httpx.get", lambda *a, **kw: pytest.fail("page must not fetch internet"))
    cards = mp.global_market_panel(today=TODAY)
    assert calls == ["latest"] and cards[0]["value"] == 5000


def test_dashboard_renders_real_global_observation_and_missing_peers(monkeypatch):
    from web import auth
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "owner", "name": "Fixture"})
    monkeypatch.setattr(db, "latest_macro_resources", lambda: [_row()])
    monkeypatch.setattr(mf, "_today_ist", lambda: TODAY)
    response = TestClient(app).get("/")
    assert response.status_code == 200
    section = response.text.split('data-international="international_chip_marker"')[1].split("</section>")[0]
    assert "5000.00" in section and "as of 2026-11-03" in section
    assert "fetched 2026-11-04T01:00:00+05:30" in section
    assert "Daily close" in section and "not live quotes" in section
    assert 'href="https://fred.stlouisfed.org/series/SP500"' in section
    assert 'href="/resources"' in section
    assert section.count("DATA_NEEDED") == 4
    assert "no reachable free feed yet" not in section



def test_viewer_sees_dated_global_data_but_no_owner_status_link(monkeypatch):
    from web import auth
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "viewer", "name": "Fixture"})
    monkeypatch.setattr(db, "latest_macro_resources", lambda: [_row()])
    monkeypatch.setattr(mf, "_today_ist", lambda: TODAY)
    response = TestClient(app).get("/")
    assert response.status_code == 200
    section = response.text.split('data-international="international_chip_marker"')[1].split("</section>")[0]
    assert "5000.00" in section and "as of 2026-11-03" in section
    assert 'href="/resources"' not in section
