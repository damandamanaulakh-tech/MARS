"""Owner paper batches require explicit authority and survive real storage round trips."""
from copy import deepcopy
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.paper import Portfolio
from web import auth, db, state, strategy_batch
from web.main import app
from web.routes import scan_routes


REQUEST_ID = "2026-11-26:9bf842f6-09a3-4548-bf94-55ee57adc4a9"


@pytest.fixture
def client(monkeypatch, tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "engine", lambda: "isolated-engine")
    monkeypatch.setattr(auth, "current_user",
                        lambda request: {"role": "owner", "email": "owner@example.test"})
    monkeypatch.setattr(scan_routes, "_buy_desk_preview", lambda *args: None)
    monkeypatch.setattr(scan_routes, "_institutional", lambda *args: {})
    monkeypatch.setattr(scan_routes.mp, "safe_panel", lambda *args, **kwargs: None)
    return TestClient(app), p


@pytest.mark.parametrize("user", [None, {"role": "viewer"}, {"role": "member"}])
def test_batch_endpoints_require_owner(client, monkeypatch, user):
    c, _ = client
    monkeypatch.setattr(auth, "current_user", lambda request: user)
    calls = []
    monkeypatch.setattr(strategy_batch, "start", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(strategy_batch, "status", lambda p: calls.append("read"))
    assert c.get("/scan/strategy-batch").status_code == 403
    assert c.post("/scan/strategy-batch", data={"request_id": REQUEST_ID},
                  headers={"Origin": "http://testserver"}).status_code == 403
    assert calls == []


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null"}, {"Origin": "https://attacker.example"},
    {"Origin": "http://testserver.evil"}, {"Origin": "https://testserver"},
    {"Origin": "http://attacker.example", "Referer": "http://testserver/scan"},
])
def test_foreign_or_missing_origin_never_submits(client, monkeypatch, headers):
    c, _ = client
    calls = []
    monkeypatch.setattr(strategy_batch, "start", lambda *a, **k: calls.append(k))
    response = c.post("/scan/strategy-batch", data={"request_id": REQUEST_ID},
                      headers=headers)
    assert response.status_code == 403
    assert response.json()["reason"] == "same_origin_required"
    assert calls == []


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("header", [{"Origin": "http://testserver"},
                                  {"Referer": "http://testserver/scan"}])
def test_actor_and_scope_come_from_owner_session(client, monkeypatch, override, header):
    c, _ = client
    calls = []
    def start(engine, **kwargs):
        calls.append((engine, kwargs))
        return {"status": "accepted", "request_id": REQUEST_ID, "opened_total": 0}
    monkeypatch.setattr(strategy_batch, "start", start)
    form = {"request_id": REQUEST_ID, "requested_by": "forged actor",
            "modes": "intraday", "include_intraday": "true"}
    if override:
        form["override_measured_performance"] = "true"
    response = c.post("/scan/strategy-batch", data=form, headers=header)
    assert response.status_code == 202
    assert calls == [("isolated-engine", {
        "request_id": REQUEST_ID, "requested_by": "owner@example.test",
        "override_measured_performance": override,
    })]


@pytest.mark.parametrize("outcome,code", [
    ("running", 202), ("busy", 409), ("save_failed", 409),
    ("rejected", 400), ("expired", 410), ("complete", 200),
])
def test_http_status_explains_batch_outcome(client, monkeypatch, outcome, code):
    c, _ = client
    monkeypatch.setattr(strategy_batch, "start",
                        lambda *a, **k: {"status": outcome, "request_id": REQUEST_ID})
    response = c.post("/scan/strategy-batch", data={"request_id": REQUEST_ID},
                      headers={"Origin": "http://testserver"})
    assert response.status_code == code
    assert response.json()["status"] == outcome


def test_status_get_does_not_create_engine_trade_or_mutate_checkpoint(client, monkeypatch):
    c, p = client
    p.strategy_batch = {"date": "2026-11-26", "requests": {
        REQUEST_ID: {"status": "running", "request_id": REQUEST_ID}
    }, "latest_request_id": REQUEST_ID}
    before = deepcopy(p.strategy_batch)
    def forbidden(*args, **kwargs):
        raise AssertionError("Read page must not start work or save")
    monkeypatch.setattr(state, "engine", forbidden)
    monkeypatch.setattr(strategy_batch, "start", forbidden)
    monkeypatch.setattr(p, "save", forbidden)
    assert c.get("/scan/strategy-batch").status_code == 200
    assert p.strategy_batch == before


def test_owner_panel_has_explicit_unchecked_override_and_readable_results(client):
    c, _ = client
    page = c.get("/scan")
    assert page.status_code == 200
    html = page.text
    assert 'data-strategy-batch' in html
    assert 'id="strategy-batch-results"' in html
    assert 'Intraday is excluded' in html
    checkbox = re.search(r'<input[^>]+name="override_measured_performance"[^>]*>', html)
    assert checkbox and "checked" not in checkbox.group(0)
    assert 'id="strategy-batch-form"' in html
    # The initial script may poll status, but cannot submit a paper instruction.
    script = next(s for s in re.findall(r"<script>(.*?)</script>", html, re.S)
                  if "strategy-batch-form" in s)
    harness = """
const listeners = {};
const calls = [];
const element = () => ({disabled:false,hidden:false,textContent:'',
  className:'',value:'',elements:{request_id:{value:'fixture'}},
  querySelector:()=>element(),replaceChildren:()=>{},appendChild:()=>{},
  addEventListener:(name, fn)=>{listeners[name]=fn;}});
global.document = {getElementById:()=>element(),createElement:()=>element()};
global.setTimeout=()=>1;global.clearTimeout=()=>{};
global.fetch=(url,opts={})=>{calls.push(opts.method||'GET'); throw Error('unexpected fetch');};
"""
    result = subprocess.run(["node", "-e", harness + script +
                             "\nif(calls.includes('POST'))throw Error('automatic entry request');"
                             "\nif(typeof listeners.submit!=='function')throw Error('missing explicit submit');"],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_nonowner_scan_page_hides_entry_authority(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "viewer"})
    html = c.get("/scan").text
    assert 'data-strategy-batch' not in html
    assert 'id="strategy-batch-form"' not in html


def test_batch_checkpoint_round_trips_with_cash_in_json_and_mongo(tmp_path, monkeypatch):
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "closed_history_policy", lambda: {})
    monkeypatch.setattr(db, "_RECOVERED_PENDING", False)
    stored = {}
    def save(doc):
        stored.update(deepcopy(doc))
        return True
    monkeypatch.setattr(db, "save_portfolio", save)
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.cash = 12345.67
    p.strategy_batch = {"date": "2026-11-26", "latest_request_id": REQUEST_ID,
                        "requests": {REQUEST_ID: {
                            "status": "complete", "request_id": REQUEST_ID,
                            "override_measured_performance": True,
                            "opened": ["EXAMPLE.NS"], "opened_total": 1,
                        }}}
    expected = deepcopy(p.strategy_batch)
    assert p.save() is True
    q = Portfolio.fresh(cfg)
    state._hydrate_portfolio_from_mongo(q, stored)
    local = Portfolio.load_or_fresh(cfg)
    assert q.cash == local.cash == p.cash
    assert q.strategy_batch == local.strategy_batch == expected
