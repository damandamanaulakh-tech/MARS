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

    def find_one(self, where, *args):
        return self.rows.get(where["_id"])


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
    run = v.identity(Config(mode="nifty200_momentum"))
    stamp = "2026-11-17T06:00:00Z"
    events = [{"kind": "entry", "path": "scan", "run": run, "trade_id": "a",
               "position": {"qty": 2}, "observed_at": stamp},
              {"kind": "exit", "run": run, "observed_at": stamp,
               "trade": {"trade_id": "a", "qty": 2, "net_pnl": 1234.50, "costs": 1.,
                         "cost_evidence": {"status": "paper_estimate"}}}]
    monkeypatch.setattr(db, "load_validation_events", lambda: (events, False, None))
    populated = client.get("/validation")
    assert populated.status_code == 200
    assert "Momentum" in populated.text and "1,234.50" in populated.text
    assert "2026-11-17 11:30 IST" in populated.text


def test_query_failure_is_explicit_unknown(monkeypatch):
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.load_validation_events() == ([], False, "database_unavailable")


def test_mongo_outbox_waits_for_book_ack_and_retries_after_restart(monkeypatch, tmp_path):
    runs, events = Collection(), Collection()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(validation_runs=runs, validation_events=events))
    cfg = Config(state_dir=str(tmp_path)); book = Portfolio(cfg=cfg, cash=100.)
    writes = []
    def refused(doc):
        writes.append(deepcopy(doc))
        return False
    monkeypatch.setattr(db, "save_portfolio", refused)
    assert book.save() is False
    assert book.validation_pending and not events.rows and not runs.rows
    restored = Portfolio(cfg=cfg, cash=0.)
    state._hydrate_portfolio_from_mongo(restored, writes[-1])
    def accepted(doc):
        writes.append(deepcopy(doc))
        return True
    monkeypatch.setattr(db, "save_portfolio", accepted)
    assert restored.save() is True
    assert not restored.validation_pending and len(events.rows) == 2 and len(runs.rows) == 1
    # Crash after acknowledged export but before the next book checkpoint:
    # old pending rows re-deliver, but append-only IDs prevent duplicates.
    again = Portfolio(cfg=cfg, cash=0.)
    state._hydrate_portfolio_from_mongo(again, writes[-1])
    assert again.validation_pending and again.save() is True
    assert len(events.rows) == 2 and len(runs.rows) == 1


def test_health_exposes_only_capture_status_from_memory(monkeypatch, tmp_path):
    book = Portfolio(cfg=Config(state_dir=str(tmp_path)), cash=100.)
    book.validation_state = {"started_at": "2026-11-17T06:00:00Z", "seen": ["private-id"],
                             "last_acknowledged": "2026-11-17T06:01:00Z"}
    monkeypatch.setattr(state, "portfolio", lambda: book)
    body = TestClient(app).get("/healthz").json()["forward_validation"]
    assert body["pending_exports"] == 0 and body["last_acknowledged"]
    assert "seen" not in body and "settings" not in body
