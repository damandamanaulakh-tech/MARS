"""A received FII/DII payload is not proof that both consumers have data."""
from types import SimpleNamespace

import pytest

from web import db, scheduler, state
from web.tests.test_market_fii_snapshot import FII_PAYLOAD, DII_PAYLOAD


@pytest.fixture
def feed(monkeypatch):
    box = {"guard": {}, "fii": FII_PAYLOAD, "dii": DII_PAYLOAD, "fii_calls": 0, "dii_calls": 0}

    class Broker:
        access_token = "test-only"
        def is_connected(self):
            return True
        def get_market_fii(self, *a):
            box["fii_calls"] += 1
            return box["fii"]
        def get_market_dii(self):
            box["dii_calls"] += 1
            return box["dii"]
    monkeypatch.setattr(state, "engine", lambda: SimpleNamespace(broker=Broker()))
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_settings", lambda **kw: dict(box["guard"]))
    monkeypatch.setattr(db, "save_settings", lambda d, **kw: box["guard"].update(d))
    monkeypatch.setattr(db, "save_market_info_raw", lambda *a: None)
    monkeypatch.setattr(db, "save_pwoi_rows", lambda rows: len(rows))
    monkeypatch.setattr(db, "update_dii_rows", lambda rows: len(rows))
    monkeypatch.setattr(scheduler, "_refresh_journal", lambda *a: None)
    monkeypatch.setattr(scheduler, "_ist_today_iso", lambda: "2026-11-04")
    return box


def test_unmapped_dii_retries_while_saved_fii_does_not_repeat(feed):
    feed["dii"] = {"unexpected": []}
    assert scheduler.run_market_fii_snapshot("test") is False
    assert feed["guard"] == {"market_fii_stored_day": "2026-11-04"}
    feed["dii"] = DII_PAYLOAD
    assert scheduler.run_market_fii_snapshot("retry") is True
    assert feed["fii_calls"] == 1 and feed["dii_calls"] == 2
    assert feed["guard"]["market_fii_day"] == "2026-11-04"
    assert scheduler.run_market_fii_snapshot("done") is True
    assert feed["fii_calls"] == 1 and feed["dii_calls"] == 2


def test_partial_storage_and_legacy_success_marker_cannot_suppress_retry(feed, monkeypatch):
    feed["guard"]["market_fii_day"] = "2026-11-04"
    monkeypatch.setattr(db, "save_pwoi_rows", lambda rows: len(rows) - 1)
    assert scheduler.run_market_fii_snapshot("short-write") is False
    assert "market_fii_stored_day" not in feed["guard"]
    assert feed["guard"]["market_dii_stored_day"] == "2026-11-04"
    monkeypatch.setattr(db, "save_pwoi_rows", lambda rows: len(rows))
    assert scheduler.run_market_fii_snapshot("retry") is True
    assert feed["fii_calls"] == 2 and feed["dii_calls"] == 1


def test_missing_payload_is_not_completed(feed):
    feed["fii"] = feed["dii"] = None
    assert scheduler.run_market_fii_snapshot("offline") is False
    assert feed["guard"] == {}
