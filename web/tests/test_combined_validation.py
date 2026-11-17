from copy import deepcopy
from datetime import datetime, timezone
import base64
import json
from types import SimpleNamespace
import zlib

from fastapi.testclient import TestClient
from trading.config import Config
from trading.paper import Portfolio
from trading import forward_validation as fv, validation as v
from web import auth, db, state
from web.main import app


class Collection:
    def __init__(self, acknowledged=True):
        self.rows = {}; self.acknowledged = acknowledged

    def update_one(self, where, update, **kwargs):
        if self.acknowledged:
            self.rows.setdefault(where["_id"], deepcopy(update["$setOnInsert"]))
        return SimpleNamespace(acknowledged=self.acknowledged)


def test_append_only_export_is_idempotent_and_preserves_original_inputs(monkeypatch):
    runs, events = Collection(), Collection()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(validation_runs=runs, validation_events=events))
    row = {"_id": "one", "run": v.identity(Config()), "kind": "entry", "input": {"data": {"price": 123}}}
    before = deepcopy(row)
    assert db.save_validation_event(row)
    assert db.save_validation_event(row)
    assert len(events.rows) == len(runs.rows) == 1 and row == before
    saved = events.rows["one"]
    assert json.loads(zlib.decompress(base64.b64decode(saved["input_zlib_base64"]))) == row["input"]


def test_unacknowledged_export_never_counts_as_saved(monkeypatch):
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(validation_runs=Collection(False), validation_events=Collection()))
    assert db.save_validation_event({"_id": "x", "run": v.identity(Config())}) is False


def test_mongo_hydration_restores_forward_outbox(tmp_path):
    p = Portfolio(cfg=Config(state_dir=str(tmp_path)), cash=100.)
    fv.stage(p, datetime(2026, 11, 17, tzinfo=timezone.utc))
    q = Portfolio(cfg=p.cfg, cash=0.)
    state._hydrate_portfolio_from_mongo(q, {"cash": 100., "validation_state": p.validation_state,
                                    "validation_pending": p.validation_pending})
    assert q.validation_pending == p.validation_pending


def test_read_page_is_authenticated_and_does_not_run_or_save_portfolio(monkeypatch, tmp_path):
    p = Portfolio(cfg=Config(state_dir=str(tmp_path)), cash=100.)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(db, "load_validation_events", lambda: ([], False, None))
    def forbidden(*a, **k): raise AssertionError("Read route attempted mutation")
    monkeypatch.setattr(Portfolio, "save", forbidden)
    monkeypatch.setattr(state, "engine", forbidden)
    client = TestClient(app)
    monkeypatch.setattr(auth, "current_user", lambda r: None)
    assert client.get("/api/validation", follow_redirects=False).status_code == 303
    monkeypatch.setattr(auth, "current_user", lambda r: {"role": "owner", "email": "test@example.test"})
    response = client.get("/validation")
    assert response.status_code == 200 and "No new versioned observations" in response.text
    assert client.get("/api/validation").json()["promotion"] == "none"


def test_query_failure_is_explicit_unknown(monkeypatch):
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.load_validation_events() == ([], False, "database_unavailable")
