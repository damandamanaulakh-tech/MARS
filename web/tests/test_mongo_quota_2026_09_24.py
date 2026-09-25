"""Owner 2026-09-24 21:40 IST: "issue is at mongo / 513 MB on 512 plan / lets
fix that first". Atlas's free tier refuses EVERY write past 512 MB, cluster
wide. The app's own db measures 68 MB, so the rest sits in another database
on the same cluster — and until now nothing could name it, and a refused
write left no trace except a log line while the STORAGE chip stayed green.

Pinned: a refused book save is recorded and shown (chip red, top banner
with the database's words); /healthz lists every database on the cluster
with its logical size; a later successful save clears the error.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from web import db


class _Refuse:
    def replace_one(self, *a, **k):
        raise RuntimeError("you are over your space quota, using 513 MB of 512 MB")


class _Accept:
    def replace_one(self, *a, **k):
        return None


@pytest.fixture
def mongo_up(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", fake)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", None)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR_AT", None)
    return fake


def test_a_refused_save_is_recorded_and_a_later_success_clears_it(mongo_up):
    mongo_up.portfolio = _Refuse()
    db.save_portfolio({"cash": 1.0})
    assert "over your space quota" in db.LAST_WRITE_ERROR
    assert db.LAST_WRITE_ERROR_AT is not None
    st = db.status()
    assert st["connected"] is True and "space quota" in st["write_error"]
    mongo_up.portfolio = _Accept()
    db.save_portfolio({"cash": 1.0})
    assert db.LAST_WRITE_ERROR is None and db.status()["write_error"] is None


def test_healthz_lists_every_database_on_the_cluster(mongo_up, monkeypatch):
    client = MagicMock()
    client.list_database_names.return_value = ["admin", "local", "sourceborn", "sourceborn_engine"]
    sizes = {"sourceborn": (68.0, 0.2), "sourceborn_engine": (440.0, 5.0)}

    def _db(name):
        m = MagicMock()
        d, i = sizes[name]
        m.command.return_value = {"dataSize": d, "indexSize": i, "storageSize": d / 2,
                                  "collections": 3}
        return m
    client.__getitem__.side_effect = _db
    monkeypatch.setattr(db, "_CLIENT", client)
    mongo_up.command.return_value = {"dataSize": 68.0, "indexSize": 0.2,
                                     "storageSize": 36.0, "objects": 7109}
    mongo_up.list_collection_names.return_value = []

    out = db.storage_stats()
    assert out["cluster"]["total_mb"] == pytest.approx(513.2)
    names = [r["name"] for r in out["cluster"]["databases"]]
    assert names == ["sourceborn_engine", "sourceborn"]     # hog first
    assert "admin" not in names and "local" not in names
    assert out["cluster"]["databases"][0]["data_mb"] == 440.0


def test_dashboard_turns_red_when_writes_are_refused(monkeypatch):
    from web.main import app
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR",
                        "OperationFailure: you are over your space quota, using 513 MB of 512 MB")
    monkeypatch.setattr(db, "LAST_WRITE_ERROR_AT", "2026-09-24T12:40:00")
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in", "password": "sourceborn"},
           follow_redirects=False)
    page = c.get("/").text
    assert 'data-db-write-error="1"' in page
    assert "REFUSING WRITES" in page and "513 MB of 512 MB" in page
    assert "WRITES REFUSED, book not being saved" in page


def test_dashboard_stays_green_when_writes_land(monkeypatch):
    from web.main import app
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", None)
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in", "password": "sourceborn"},
           follow_redirects=False)
    page = c.get("/").text
    assert "data-db-write-error" not in page
    assert "WRITES REFUSED" not in page
