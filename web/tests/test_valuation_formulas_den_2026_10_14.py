"""Den §13 — every formula printed, every ASSUMED number editable as a draft
(owner order 2026-10-14: "put the formulas in the den now … need editable
as said in 40+ points"). Money impact NONE.

Pins: the section chip/anchor; the 52 terms, 21 routes and the scenario
chain's cells render with their formulas; the 50 editable inputs render
with defaults and sources; an owner's draft is stored, §12 re-reads it and
marks the rows DRAFT, the live Config is untouched; a bad field refuses the
whole form; clear restores the defaults; the viewer role cannot see or post.
"""
from __future__ import annotations

import re
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod
from web import state
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _text(html: str, anchor: str) -> str:
    i = html.find(anchor)
    assert i > 0, anchor
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[i:i + 200000]))


@pytest.fixture
def stored(monkeypatch):
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0, "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0, "ev_sales_median": 2.5,
                                                    "source": "test peers"})
    yield docs
    cv.LAST_ASSUMPTIONS.clear()


def test_the_section_exists_and_prints_every_registry(stored):
    html = _owner().get("/den").text
    assert 'id="formulas"' in html and 'href="#formulas"' in html
    for m in ("den_formulas_marker", "den_formulas_form_marker", "den_formulas_terms_marker",
              "den_formulas_routes_marker", "den_formulas_chain_marker", "den_formulas_rules_marker"):
        assert m in html, m
    t = _text(html, 'id="formulas"')
    assert "13 · Formulas" in t
    assert "The 52 finance terms" in t and "Graham number" in t and "Altman Z" in t
    assert "The 21 sector routes" in t and "Metals (steel/aluminium/zinc)" in t and "metals_midcycle" in t
    assert "ROUND(E22/E28,2)" in t                                 # the EPS cell formula
    assert "E134" in t and "negative multiple" in t                 # section H caveat printed
    # 50 editable inputs, each with its default as placeholder
    assert html.count('name="sheet.') == 29
    assert html.count('name="case.') == 6 and html.count('name="verdict.') == 5
    assert html.count('placeholder="45"') >= 1                       # publication lag default
    assert "No draft stored" in t


def test_an_owner_draft_is_stored_read_by_section_12_and_never_touches_the_config(stored):
    c = _owner()
    cfg = state.cfg()
    snapshot = {f.name: repr(getattr(cfg, f.name)) for f in fields(cfg)}
    before = _text(c.get("/den?chain=TCS").text, 'id="chain"')
    r = c.post("/den/valuation/assumptions",
               data={"case.upside.revenue_growth_pct": "15", "verdict.max_range_width_pct": "200",
                     "verdict.watch_band_pct": ""},                 # blank = default
               follow_redirects=False)
    assert r.status_code == 303 and "fa=saved" in r.headers["location"]
    assert stored["valuation_assumptions"]["values"] == {"case.upside.revenue_growth_pct": 15.0,
                                                         "verdict.max_range_width_pct": 200.0}
    html = c.get("/den?chain=TCS").text
    t12 = _text(html, 'id="chain"')
    t13 = _text(html, 'id="formulas"')
    assert "den_chain_draft_marker" in html
    assert "DRAFT ASSUMPTIONS IN USE" in t12 and "2 number(s) differ" in t12
    assert "draft 15.0" in t13 and "2 number(s) are currently drafted" in t13
    # the TCS row changed: the width cap lifted so the verdict is no longer INSUFFICIENT
    assert "INSUFFICIENT" in before and "INSUFFICIENT" not in t12.split("TCS")[1][:1500]
    # the live Config is byte-for-byte what it was
    assert {f.name: repr(getattr(cfg, f.name)) for f in fields(cfg)} == snapshot


def test_a_bad_field_refuses_the_whole_form_and_stores_nothing(stored):
    c = _owner()
    r = c.post("/den/valuation/assumptions",
               data={"sheet.beta": "abc", "case.upside.revenue_growth_pct": "9"},
               follow_redirects=False)
    assert r.status_code == 303 and "fa=invalid" in r.headers["location"]
    assert "valuation_assumptions" not in stored and not cv.LAST_ASSUMPTIONS
    html = c.get(r.headers["location"].replace("#formulas", "")).text
    assert "Not stored" in html and "sheet.beta" in html


def test_clear_restores_every_default(stored):
    c = _owner()
    c.post("/den/valuation/assumptions", data={"snapshot.publication_lag_days": "60"},
           follow_redirects=False)
    assert cv.load_assumptions() == {"snapshot.publication_lag_days": 60}
    r = c.post("/den/valuation/assumptions/clear", follow_redirects=False)
    assert r.status_code == 303 and "fa=cleared" in r.headers["location"]
    assert cv.load_assumptions() == {}
    assert "den_chain_draft_marker" not in c.get("/den?chain=TCS").text


def test_the_in_process_copy_carries_the_draft_when_mongo_cannot_be_read(stored, monkeypatch):
    c = _owner()
    c.post("/den/valuation/assumptions", data={"terms.dcf_growth_cap_pct": "12"},
           follow_redirects=False)
    monkeypatch.setattr(vv.db, "load_settings", lambda doc_id="default": (_ for _ in ()).throw(RuntimeError("down")))
    assert cv.load_assumptions() == {"terms.dcf_growth_cap_pct": 12.0}


def test_the_viewer_cannot_see_or_post(stored, monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    r = c.post("/login", data={"email": "viewer@sourceborn.in", "password": "v-test"},
               follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/den#formulas", follow_redirects=False).status_code == 403
    assert c.post("/den/valuation/assumptions", data={"sheet.beta": "2"},
                  follow_redirects=False).status_code == 403
    assert "valuation_assumptions" not in stored
