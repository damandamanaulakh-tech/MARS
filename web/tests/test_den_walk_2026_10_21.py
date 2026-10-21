"""Den §1 walk tree (owner order 2026-10-21: "walk tree + stock picker +
owner branches"). Display only — nothing here opens, sizes or blocks a trade.

Pins:
  * the 26-stage status table stays (folded) and every stage opens into the
    code-built branches — route methods, share bridge, normalised earnings,
    quality, market / rates inputs, the scenario chain's 82 cells, range
    family rules, context legs, verdict rules and the 52 finance terms —
    with stable ids ("s15", "s15.revenue_growth", …);
  * ?walk=TCS puts TCS's numbers on the nodes (or DATA_NEEDED + reason),
    computed ONCE under §12's guards: a kept row is reused, a scan rotation,
    the memory ceiling, another computing open and the time budget each
    stop it with their reason;
  * owner branches (settings doc sequence_branches): add under any node or
    another branch, edit by id, refused parents / cycles / cap, remove takes
    the subtree, tagged "your branch · not yet computed", orphans printed;
    owner-only, the viewer gets 403.
"""
from __future__ import annotations

import re
from html import unescape

import pytest
from fastapi.testclient import TestClient

from trading.valuation import route as R
from trading.valuation import scenarios as SC
from trading.valuation import terms as T
from web import auth as auth_mod
from web import den_walk as W
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app
from web.routes import den_routes as D


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD}, follow_redirects=False)
    assert r.status_code == 303
    return c


def _viewer(monkeypatch) -> TestClient:
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in",
                                  "password": "v-test"}).status_code == 200
    return c


def _flat(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _walk(html: str) -> str:
    i = html.find("den_walk_marker")
    assert i >= 0
    return html[i:html.find("den_walk_form_marker", i)]


def _node(html: str, nid: str) -> str:
    """One node's own line (its <li> up to its first child list)."""
    i = html.find(f'data-walk-node="{nid}"')
    assert i >= 0, nid
    j = min(x for x in (html.find("<ul>", i), html.find("</li>", i)) if x > 0)
    return html[i:j]


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store (no Mongo), fixed peers, no feeds."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    W.LAST_BRANCHES.clear()
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0, "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0,
                                                    "ev_sales_median": 2.5, "source": "test peers"})
    monkeypatch.setattr(cv, "peer_table_rows", lambda: {})
    monkeypatch.setattr(cv, "_macro_feeds", lambda: None)
    # reviewer defect 10 (2026-10-21): §12 also computes every HELD name, and
    # the persisted /tmp book can hold a stale fixture position (HOLD1.NS from
    # test_positions_dashboard_v2) — so the row-call pins here must not read
    # the live book
    monkeypatch.setattr(cv, "_held_names", lambda p: [])
    yield docs
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    W.LAST_BRANCHES.clear()


STAGES = [f"s{r[0]}" for r in D.SEQUENCE]


# ------------------------------------------------------------ the tree

def test_the_table_folds_and_every_stage_opens_into_code_branches(stored):
    html = _owner().get("/den").text
    seq = html[html.find('id="sequence"'):html.find("den_walk_form_marker")]
    assert "den_sequence_table_marker" in seq and "<details" in seq
    assert "status as of <strong>2026-10-21</strong>" in seq
    w = _walk(html)
    assert len(STAGES) == 26 and "s17b" in STAGES
    for sid in STAGES:
        assert f'data-walk-node="{sid}"' in w, sid
    for nid in ("s00.route", "s07.period_end", "s08.currency", "s09.this_stock", "s09.earnings",
                "s09.bank_pb_roe", "s10.reconcile", "s11.normalised_pat", "s11.target_starts_from",
                "s12.score", "s13.repo", "s13.rf", "s13.inst_holding", "s15.revenue_growth",
                "s15.base_year", "s15.base_year.d18", "s16.pe_bridge", "s16.pe_bridge.e132",
                "s17.band", "s17.own_history", "s17.whatif", "s17.families.nav",
                "s17b.rates_leg", "s18.rules.attractive", "s18.warnings"):
        assert f'data-walk-node="{nid}"' in w, nid
    assert w.count("den_walk_node_marker") > 300
    # no stock walked: formulas only, never a DATA_NEEDED
    assert "data-walk-dn" not in w and "data-walk-value" not in w


def test_the_tree_is_built_from_the_registries_with_unique_ids():
    t = W.build_tree(None, branches=[])
    idx = W._index(t["nodes"])
    assert len(idx) == t["n_nodes"] == len(t["ids"])          # unique ids
    for r in R.SECTOR_METHODS:
        assert f"s09.{W._slug(r['method'])}" in idx, r["method"]
    names = [n["name"] for n in idx.values()]
    for term in T.TERMS:                                       # all 52 terms, each once …
        assert term in names, term
    assert len(T.TERMS) == 52
    term_nodes = [n for n in idx.values() if "[terms registry stage" in n["formula"]]
    assert len(term_nodes) == 52                               # … and only once
    cells = [n for nid, n in idx.items()
             if re.match(r"s1[56]\.(base_year|revenue_costs|forecast|eps|pe_bridge|price)\.", nid)]
    assert len(cells) == len(SC.chain_formulas()) == 82
    # a term hangs under the first stage its registry names
    assert "s12.roe" in idx and "s15.revenue_growth" in idx and "s03.sharpe_like_momentum" in idx


def test_walking_a_stock_puts_its_numbers_on_the_nodes(stored):
    from web import state
    html = _owner().get("/den?walk=tcs").text
    w = _walk(html)
    assert 'data-den-walk-status="computed"' in w
    rec = cv.walk_record(state.cfg(), None, "TCS")["rec"]      # the kept row
    assert rec is not None
    eps = _flat(_node(w, "s11.normalised_eps"))
    assert f"= ₹{rec['earnings']['normalised_eps']:,.2f}" in eps
    rng = rec["range"]
    assert f"= ₹{rng['low']:,.2f} / ₹{rng['base']:,.2f} / ₹{rng['high']:,.2f}" in \
        _flat(_node(w, "s17.range"))
    assert rec["verdict"]["verdict"].upper() in _flat(_node(w, "s18.verdict"))
    assert "this stock's route" in _flat(_node(w, "s09.earnings")) \
        or rec["route"]["method"] != "earnings"
    # a missing input reads DATA_NEEDED with its reason, never blank
    repo = _flat(_node(w, "s13.repo"))
    assert "= DATA_NEEDED — " in repo and "counts 0" in repo
    # chain stages open by default when a stock is walked
    assert re.search(r'data-walk-node="s17"[^>]*>(<!--[^>]*-->)?<details open', w)


def test_a_bad_symbol_says_so(stored):
    w = _walk(_owner().get("/den?walk=%24%24").text)
    assert "is not a symbol" in _flat(w)
    assert "data-walk-value" not in w


# ------------------------------------------------------------ the guards

def _spy_rows(monkeypatch) -> list:
    calls: list = []
    real = cv.row_for

    def spy(t, *a, **k):
        calls.append(t)
        return real(t, *a, **k)

    monkeypatch.setattr(cv, "row_for", spy)
    return calls


def test_a_kept_row_is_reused(stored, monkeypatch):
    calls = _spy_rows(monkeypatch)
    c = _owner()
    c.get("/den?walk=TCS")
    w = _walk(c.get("/den?walk=TCS").text)
    assert calls == ["TCS.NS"]
    assert 'data-den-walk-status="cached"' in w and "kept from an earlier open" in _flat(w)


def test_a_scan_rotation_stops_the_walk(stored, monkeypatch):
    from web import scheduler
    calls = _spy_rows(monkeypatch)
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        w = _walk(_owner().get("/den?walk=TCS").text)
    finally:
        scheduler._ROTATION_LOCK.release()
    assert calls == []
    t = _flat(w)
    assert 'data-den-walk-status="busy"' in w and "NOT COMPUTED on this open" in t
    assert "a scan rotation is running" in t and "when the scan finishes" in t
    assert "data-walk-value" not in w                          # no number without a row


def test_the_memory_ceiling_stops_the_walk(stored, monkeypatch):
    from web import memory_relief
    calls = _spy_rows(monkeypatch)
    monkeypatch.setattr(cv, "rss_ceiling_mb", lambda: 380.0)
    monkeypatch.setattr(memory_relief, "rss_mb", lambda: 455.0)
    t = _flat(_walk(_owner().get("/den?walk=TCS").text))
    assert calls == [] and "455 MiB" in t and "after the next restart" in t


def test_another_open_computing_stops_the_walk(stored, monkeypatch):
    calls = _spy_rows(monkeypatch)
    assert cv._SECTION_LOCK.acquire(blocking=False)
    try:
        t = _flat(_walk(_owner().get("/den?walk=TCS").text))
    finally:
        cv._SECTION_LOCK.release()
    assert calls == [] and "another open of the Den" in t


def test_a_spent_budget_is_said(stored, monkeypatch):
    def spent(*a, **k):
        raise cv._BudgetSpent("the peer table was not built")

    monkeypatch.setattr(cv, "row_for", spent)
    w = _walk(_owner().get("/den?walk=TCS").text)
    assert 'data-den-walk-status="budget"' in w and "time budget" in _flat(w)


# ------------------------------------------------------- owner branches

def _add(c, **data):
    return c.post("/den/sequence/branch", data=data, follow_redirects=False)


def test_an_owner_branch_hangs_under_its_node_tagged_as_yours(stored):
    c = _owner()
    r = _add(c, parent="s15", name="Order-book cover", formula="order book ÷ TTM revenue",
             note="from results PDFs", walk="TCS")
    assert r.status_code == 303 and "sb=saved" in r.headers["location"]
    assert "walk=TCS" in r.headers["location"]                 # the walked stock is kept
    b = stored["sequence_branches"]["branches"]
    assert [(x["id"], x["parent"], x["name"]) for x in b] == [("ob1", "s15", "Order-book cover")]
    assert _add(c, parent="ob1", name="Cover in years").headers["location"].count("sb=saved")
    w = _walk(c.get("/den").text)
    s15 = w[w.find('data-walk-node="s15"'):w.find('data-walk-node="s16"')]
    assert 'data-walk-node="ob1"' in s15 and 'data-walk-node="ob2"' in s15
    assert s15.find('data-walk-node="ob2"') > s15.find('data-walk-node="ob1"')
    ob1 = _flat(_node(w, "ob1"))
    assert "your branch · not yet computed" in ob1 and "den_walk_owner_branch_marker" in _node(w, "ob1")
    assert "order book ÷ TTM revenue" in _flat(w) and "note: from results PDFs" in _flat(w)


def test_edit_refusals_and_remove_takes_the_subtree(stored):
    c = _owner()
    _add(c, parent="s17.band", name="Sector P/E band")
    _add(c, parent="ob1", name="Child")
    _add(c, parent="s18", name="Keep me")
    # edit by id
    r = _add(c, id="ob1", parent="s17", name="Sector P/E band (renamed)")
    assert "sb=saved" in r.headers["location"]
    b = {x["id"]: x for x in stored["sequence_branches"]["branches"]}
    assert b["ob1"]["name"] == "Sector P/E band (renamed)" and b["ob1"]["parent"] == "s17"
    # refusals: unknown parent, no name, a cycle, an unknown id
    for data, why in ((dict(parent="s99", name="x"), "no+node"),
                      (dict(parent="s15", name=""), "needs+a+name"),
                      (dict(id="ob1", parent="ob2", name="x"), "cannot+hang"),
                      (dict(id="ob9", parent="s15", name="x"), "no+branch")):
        loc = _add(c, **data).headers["location"]
        assert "sb=invalid" in loc and (why in loc or why.replace("+", "%20") in loc), (data, loc)
    assert len(stored["sequence_branches"]["branches"]) == 3
    r = c.post("/den/sequence/branch/remove", data={"id": "ob1"}, follow_redirects=False)
    assert "sb=removed" in r.headers["location"]
    assert [x["id"] for x in stored["sequence_branches"]["branches"]] == ["ob3"]
    loc = c.post("/den/sequence/branch/remove", data={"id": "ob1"},
                 follow_redirects=False).headers["location"]
    assert "sb=invalid" in loc


def test_the_branch_cap_and_orphans(stored, monkeypatch):
    c = _owner()
    monkeypatch.setattr(W, "MAX_BRANCHES", 1)
    assert "sb=saved" in _add(c, parent="s15", name="one").headers["location"]
    loc = _add(c, parent="s15", name="two").headers["location"]
    assert "sb=invalid" in loc and ("at+most+1" in loc or "at%20most%201" in loc)
    stored["sequence_branches"]["branches"].append(
        {"id": "ob7", "parent": "s99.gone", "name": "Lost one", "ts": "2026-10-21T00:00:00"})
    W.LAST_BRANCHES.clear()                                    # a fresh process reads the doc
    w = _walk(c.get("/den").text)
    assert "den_walk_orphans_marker" in w and "Lost one" in _flat(w)


def test_a_viewer_gets_403_and_never_stores_a_branch(stored, monkeypatch):
    v = _viewer(monkeypatch)
    assert v.get("/den").status_code == 403
    assert v.get("/den?walk=TCS").status_code == 403
    for path, data in (("/den/sequence/branch", {"parent": "s15", "name": "x"}),
                       ("/den/sequence/branch/remove", {"id": "ob1"})):
        assert v.post(path, data=data, follow_redirects=False).status_code == 403
    assert "sequence_branches" not in stored


def test_the_sequence_rows_this_batch_touched_say_what_changed():
    rows = {r["code"]: r for r in D._sequence()["rows"]}
    assert D.SEQUENCE_STATUS_ASOF == "2026-10-21"
    assert "Upstox 10-year G-sec yield" in rows["13"]["where"]
    assert rows["13"]["status"] == "partly"                    # CPI / GDP / Brent not fed
    assert "normalised PAT on every route" in rows["11"]["where"]
    assert "never sorted" in rows["17"]["where"]
    assert "what-if price" in rows["16"]["where"]
