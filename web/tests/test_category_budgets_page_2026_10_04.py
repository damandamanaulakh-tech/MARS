"""Owner order 2026-10-04 ("fix it so all 5 categories get cash"): the Risk
page shows each category's share, the Settings page can edit them, and the
Settings save no longer rewrites the live slab sizing to "fixed"."""
from __future__ import annotations

import re
from datetime import datetime

from fastapi.testclient import TestClient

from trading.paper import PaperPosition
from web import auth as auth_mod
from web import db, state
from web.main import app


def _client():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _pos(t, rupees, mode):
    return PaperPosition(
        ticker=t, qty=int(rupees // 100), entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=95.0, target_price=120.0)


def test_the_risk_page_shows_all_five_shares():
    p = state.portfolio()
    saved = dict(p.positions)
    try:
        p.positions = {"MOM.NS": _pos("MOM.NS", 10_00_000.0, "nifty200_momentum"),
                       "SW.NS": _pos("SW.NS", 3_00_000.0, "swing")}
        r = _client().get("/risk")
        assert r.status_code == 200
        html = r.text
        assert "category_budget_marker" in html
        card = html.split("category_budget_marker")[1].split("IFR OVERLAY")[0]
        for label in ("Momentum", "Intraday", "Short", "Swing", "Positional"):
            assert label in card, label
        assert "40%" in card and "15%" in card
        assert "₹10,00,000" in card and "₹3,00,000" in card
    finally:
        p.positions = saved


def test_settings_carries_the_switch_and_the_five_shares():
    from web.routes import settings_routes
    keys = [row[0] for row in settings_routes.EDITABLE_FIELDS]
    for k in ("category_budgets_enabled", "budget_share_momentum",
              "budget_share_intraday", "budget_share_short",
              "budget_share_swing", "budget_share_positional"):
        assert k in keys, k
    pct = settings_routes._PCT_FIELDS
    assert "budget_share_momentum" in pct          # shown and typed as %


def _save_settings_page(c):
    html = c.get("/settings").text
    fields = dict(re.findall(r'name="([a-z_]+)"[^>]*value="([^"]*)"', html))
    assert fields, "the settings form rendered no fields"
    return fields


def test_saving_settings_keeps_slab_sizing():
    """The bug: 'slab' was missing from the save's validation list, so ANY
    save — even one that only flipped a toggle — rewrote the live sizing to
    'fixed' (flat Rs 1.25 lakh). Reproduced 2026-10-04 before the fix."""
    cfg = state.cfg()
    before = cfg.sizing_mode
    cfg.sizing_mode = "slab"
    try:
        c = _client()
        fields = _save_settings_page(c)
        assert fields.get("sizing_mode") == "slab"
        r = c.post("/settings", data=fields, follow_redirects=False)
        assert r.status_code == 303
        assert state.cfg().sizing_mode == "slab"
    finally:
        state.cfg().sizing_mode = before


def test_a_typo_lands_on_slab_not_fixed():
    cfg = state.cfg()
    before = cfg.sizing_mode
    try:
        c = _client()
        fields = _save_settings_page(c)
        fields["sizing_mode"] = "slabb"
        c.post("/settings", data=fields, follow_redirects=False)
        assert state.cfg().sizing_mode == "slab"
    finally:
        state.cfg().sizing_mode = before


def test_a_share_entered_out_of_range_is_clamped():
    cfg = state.cfg()
    before = cfg.budget_share_swing
    try:
        c = _client()
        fields = _save_settings_page(c)
        fields["budget_share_swing"] = "-20"
        c.post("/settings", data=fields, follow_redirects=False)
        assert state.cfg().budget_share_swing == 0.0
    finally:
        state.cfg().budget_share_swing = before


def test_the_boot_clears_the_bugs_stored_fixed(monkeypatch):
    store = {"default": {"sizing_mode": "fixed"}, "migrations": {}}
    monkeypatch.setattr(db, "load_settings",
                        lambda doc_id="default": dict(store.get(doc_id, {})))
    monkeypatch.setattr(db, "save_settings",
                        lambda d, doc_id="default":
                        store.setdefault(doc_id, {}).update(d))

    def unset(keys, doc_id="default"):
        for k in keys:
            store.get(doc_id, {}).pop(k, None)
    monkeypatch.setattr(db, "unset_settings_keys", unset)

    from trading.config import Config
    cfg = Config()
    cfg.sizing_mode = "fixed"                     # as the override applied it
    assert state._migrate_slab_restore(cfg) is True
    assert cfg.sizing_mode == "slab"
    assert "sizing_mode" not in store["default"]
    assert store["migrations"]["slab_restore_2026_10_04"] is True
    # once only: a deliberate "fixed" chosen after this boot is respected
    store["default"]["sizing_mode"] = "fixed"
    cfg.sizing_mode = "fixed"
    assert state._migrate_slab_restore(cfg) is False
    assert cfg.sizing_mode == "fixed"


def test_the_scan_page_names_the_category_share():
    p = state.portfolio()
    saved = (list(p.last_scan_picks or []), dict(p.scan_matrix or {}))
    try:
        p.last_scan_picks = [{"ticker": "TCS.NS", "score": 70.0,
                              "components": {}, "timing_pass": True,
                              "reasoning": "fixture", "last_close": 3000.0}]
        p.scan_matrix = {"mode": "swing", "rows": {}, "score_floor": 60.0}
        html = _client().get("/scan").text
        assert "category_budget_slot_marker" in html
        assert "<strong>swing</strong>" in html
    finally:
        p.last_scan_picks, p.scan_matrix = saved


# The Signal page test that sat here was deleted with the page itself
# (owner order 2026-10-11, item 1: "signal file also can be removed, not
# working and zero utility"). The per-category weights it checked are still
# pinned on the scorer in trading/tests and on the /den page.
