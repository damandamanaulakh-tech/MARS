"""The live Den returned a 500 after every scan (found 2026-10-15 by the
layout review after the owner's "merge now n show me the den"; their words
the same morning: "Mars den always go down nt opening"). Money impact NONE.

`_sandbox()` returned its draft under the key "values". In a Jinja template
`sbx.values` is the dict's built-in .values METHOD, not that key, so:
  * every §10 draft box rendered EMPTY (the card says "prefilled from the
    LIVE config");
  * once a breadth reading existed — `engine._cache_breadth_today` writes one
    on every momentum pass, the full-store reading is refreshed daily — the
    line `sbx.values.breadth_low` raised and the WHOLE Den was a 500.
Local renders never had a breadth reading, which is how it shipped.

Same review, same page: §9's on/off flags rendered the word True/False into
a number box — the browser shows it empty and posts "", which the save reads
as OFF, so one Save switched ten flags off. They render 1 / 0 now.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from web.routes import den_routes as D
from web.routes import settings_routes as S


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303, "owner login failed"
    return c


def _with(attr: str, value):
    p = state.portfolio()
    old = getattr(p, attr, None)
    setattr(p, attr, value)
    return p, old


def test_den_opens_after_a_scan_wrote_its_breadth_reading():
    today = date.today().isoformat()
    p, old = _with("breadth_today", {"pct": 0.388, "sample": 251, "asof": today,
                                     "mode": "nifty200_momentum",
                                     "ts": datetime.utcnow().isoformat()})
    try:
        r = _owner().get("/den")
        assert r.status_code == 200, r.text[:300]
        text = " ".join(r.text.split())
        assert "draft band 40.0–65.0%" in text
        assert ("would block" in text) or ("would allow" in text)
    finally:
        p.breadth_today = old


def test_den_opens_with_a_fresh_full_store_breadth_reading():
    """Both stored shapes open the Den (the 500 this file pins): the
    2026-10-16 study-panel reading, named on the page, and the pre-fix
    whole-store reading the live box holds until its next 09:20 refresh."""
    p, old = _with("breadth_store", {"pct": 0.314, "sample": 189,
                                     "asof": date.today().isoformat(),
                                     "population": "study_panel",
                                     "panel_size": 191})
    try:
        r = _owner().get("/den")
        assert r.status_code == 200, r.text[:300]
        # The sandbox's own source line (the engine card always says "study
        # panel" while the gate is on, so that phrase proves nothing here).
        assert "study panel — 189 of the 191" in r.text
        p.breadth_store = {"pct": 0.452, "sample": 2233,
                           "asof": date.today().isoformat()}
        r = _owner().get("/den")
        assert r.status_code == 200, r.text[:300]
        # The pre-fix whole-store number is set aside by the engine gate, so
        # the sandbox must not present it as the live reading either.
        assert "whole store (2233 names)" not in r.text
        assert "2233 names above their 200-DMA" not in r.text
    finally:
        p.breadth_store = old


def test_sandbox_boxes_are_prefilled_with_the_live_values():
    html = _owner().get("/den").text
    sec = html.split("den_sandbox", 1)[-1] if "den_sandbox" in html else html
    for k in D.SANDBOX_KNOBS:
        m = re.search(r'name="%s" value="([^"]*)"' % re.escape(k["name"]), sec)
        assert m, f"no draft box for {k['name']}"
        assert m.group(1).strip() != "", f"draft box {k['name']} renders empty"
        float(m.group(1))                       # a number, not a word


def test_algo_flags_render_as_one_or_zero_matching_the_config():
    cfg = state.cfg()
    html = _owner().get("/den").text
    flags = [name for name, *_ in S.EDITABLE_FIELDS
             if isinstance(getattr(cfg, name, None), bool)]
    assert flags, "expected on/off flags on the algo form"
    for name in flags:
        m = re.search(r'name="%s"\s+value="([^"]*)"' % re.escape(name), html)
        assert m, f"flag {name} not on the algo form"
        assert m.group(1) == str(int(getattr(cfg, name))), (name, m.group(1))
