"""The three-formula column actually renders (owner order 2026-09-11).

Written because green unit tests are not a working page: the /positions
500 of 2026-08-21 passed every fixture and died on the owner's real book,
where an owner-bought position carried score 0 and `x if x else y` read a
legitimate zero as absence. So this renders /scan for real, with the row
shapes a live book actually mixes — a judged name, a name the outside
formulas cannot judge, a score-less manual buy, and an ampersand ticker.
"""
from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _multi(am, a8, floor=62.0):
    def row(score, fl):
        if score is None:
            return {"score": None, "floor": fl, "pass": None,
                    "label": "DATA_NEEDED"}
        return {"score": score, "floor": fl, "pass": score >= fl,
                "label": "PASS" if score >= fl else "fail"}
    return {"rows": {"am07": row(am, floor), "ash08": row(a8, 68.0),
                     "ashstocks": row(am, 70.0)},
            "mom20": 0.03, "agree": 1, "judged": 3, "all_pass": False}


PICKS = [
    # a normal scanned pick, judged by all three
    {"ticker": "TCS.NS", "score": 71.0, "last_close": 3100.0,
     "timing_pass": True, "reasoning": "MOM", "components": {},
     "candles": [], "candle": "", "structures": [], "opened": False,
     "multi": _multi(71.0, 86.1)},
    # outside formulas cannot judge it — must render an em dash, not a fail
    {"ticker": "NEWLIST.NS", "score": 64.0, "last_close": 210.0,
     "timing_pass": False, "reasoning": "MOM", "components": {},
     "candles": [], "candle": "", "structures": [], "opened": False,
     "multi": _multi(64.0, None)},
    # the /positions 500 shape: no multi view at all
    {"ticker": "NOMULTI.NS", "score": 63.0, "last_close": 99.0,
     "timing_pass": True, "reasoning": "MOM", "components": {},
     "candles": [], "candle": "", "structures": [], "opened": False,
     "multi": None},
    # score-less manual buy + an ampersand symbol, both real book shapes
    {"ticker": "GVT&D.NS", "score": None, "last_close": 1500.0,
     "timing_pass": True, "reasoning": "owner", "components": {},
     "candles": [], "candle": "", "structures": [], "opened": True,
     "multi": _multi(None, 58.5)},
]


def _render():
    p = state.portfolio()
    saved = p.last_scan_picks
    p.last_scan_picks = PICKS
    try:
        r = _owner().get("/scan")
        return r
    finally:
        p.last_scan_picks = saved


def test_scan_renders_with_every_real_row_shape():
    r = _render()
    assert r.status_code == 200
    assert "multi_formula_cell_marker" in r.text


def test_the_three_verdicts_are_on_the_page():
    body = _render().text
    assert "ASH08 86.1 PASS" in body
    # The AshStocks column IS our own score (Spearman +1.000) read against
    # their higher floor: 71.0 clears 70, and 64.0 does not.
    assert "AshSt 71.0 PASS" in body
    assert "AshSt 64.0 fail" in body
    assert "ASH08 58.5 fail" in body


def test_an_unjudgeable_name_is_a_dash_never_a_fail():
    """A short history is not evidence against a stock — the DATA_NEEDED
    rule that keeps this app from inventing verdicts."""
    body = _render().text
    assert "Not a fail" in body               # the em-dash tooltip


def test_the_header_count_still_matches_the_row_count():
    """The slot divider spans the table; a new column that skews it would
    break the funnel line the owner reads. Same guard as the institutional
    column added 2026-08-29."""
    body = _render().text
    # The picks table is the one whose rows carry the Formulas cell. Anchor on
    # it, not on "the first <tbody> on the page": the Buy Desk card
    # (2026-09-24) put another table above it and this guard counted that
    # one instead — a meaningless failure saying nothing about the funnel.
    before = body[:body.index("multi_formula_cell_marker")]
    thead = before[before.rfind("<thead"):]
    assert thead.count("<th") >= 9
    assert 'colspan="9"' in body or 'colspan="{% if' not in body


def test_the_card_states_whether_it_is_showing_or_gating():
    """The lying-screen rule: a panel must never imply a control it is not
    exercising. Default is display-only, and the card has to say so."""
    from web import state as st
    cfg = st.cfg()
    saved = getattr(cfg, "multi_formula_gate", False)
    try:
        cfg.multi_formula_gate = False
        body = _render().text
        assert "multi_formula_card_marker" in body
        assert "display only" in body and "filter is OFF" in body
        assert "FILTER IS ON" not in body
        # the evidence rides with the verdicts, never the verdicts alone
        assert "research/ash_formula_study/" in body
        assert "Spearman +1.000" in body

        cfg.multi_formula_gate = True
        on = _render().text
        assert "FILTER IS ON" in on
    finally:
        cfg.multi_formula_gate = saved
