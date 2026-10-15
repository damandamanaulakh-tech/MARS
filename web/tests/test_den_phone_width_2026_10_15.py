"""MARS Den on a phone (2026-10-15, found by the first local render after
the owner's "merge now n show me the den"). Money impact NONE — layout only.

At 390 px the Risk governor's drawdown-ladder line pushed the whole Den to
472 px wide (a sideways-scrolling page): every rung was ONE no-wrap span
holding the level AND its long action text ("-15.0% mostly_cash (Move to
mostly cash; minimal positions, size x0.0)"). Now only the number and the
level name stay together; the action text wraps.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.main import app


def _den_html() -> str:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303, "owner login failed"
    page = c.get("/den")
    assert page.status_code == 200
    assert "den_risk_marker" in page.text, "the Risk governor card did not render"
    return page.text


def test_ladder_rungs_wrap_their_action_text():
    html = _den_html()
    m = re.search(r"<!-- den_ladder_marker -->(.*?)</div>", html, re.S)
    assert m, "the drawdown-ladder line did not render"
    line = m.group(1)
    rungs = re.findall(r'<span class="whitespace-nowrap">(.*?)</span>', line, re.S)
    assert len(rungs) >= 3, "expected one no-wrap label per ladder level"
    for r in rungs:
        # a rung label is "<strong>-5.0%</strong> watchful" — short; the
        # action text in brackets must sit OUTSIDE the no-wrap span
        assert "(" not in r, f"action text inside a no-wrap span: {r!r}"
        assert len(re.sub(r"<[^>]+>", "", r)) <= 30, r
    # the action text is still printed, just free to wrap
    assert "(Move to mostly cash" in line or "mostly_cash (" in line
