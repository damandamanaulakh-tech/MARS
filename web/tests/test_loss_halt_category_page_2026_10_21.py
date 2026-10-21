"""Owner order 2026-10-21 ("Halt only the category that lost") on the
screens: /risk prints the per-category counts and which categories are
halted now (marker loss_halt_category_marker); Den §9 carries the "Loss
halt scope" row and the save accepts only the two words; a viewer sees
nothing new."""
from __future__ import annotations

import re
from datetime import datetime

from fastapi.testclient import TestClient

from trading.paper import PaperPosition
from web import auth as auth_mod
from web import state
from web.main import app

MOM = "nifty200_momentum"


def _client():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _pos(t, mode):
    return PaperPosition(
        ticker=t, qty=100, entry_price=100.0,
        entry_date=datetime(2026, 10, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=95.0, target_price=120.0)


class _Halted:
    """Two losing momentum exits today on the shared engine, restored after."""

    def __enter__(self):
        self.eng = state.engine()
        self.p = self.eng.portfolio
        t = self.eng.loss_tracker
        self.saved = (dict(self.p.positions), t.today, t.losses_today,
                      dict(t.by_category), dict(t.open_by_category),
                      state.cfg().loss_halt_scope)
        self.p.positions = {"M.NS": _pos("M.NS", MOM), "S.NS": _pos("S.NS", "swing")}
        # the engine's own clock (naive UTC), not the machine's local date
        t.today = datetime.utcnow().date()
        t.losses_today = 2
        t.by_category = {MOM: 2}
        return self

    def __exit__(self, *a):
        t = self.eng.loss_tracker
        (self.p.positions, t.today, t.losses_today, t.by_category,
         t.open_by_category, state.cfg().loss_halt_scope) = self.saved


def test_the_risk_card_prints_per_category_counts_and_the_halted_ones():
    with _Halted():
        state.cfg().loss_halt_scope = "category"
        html = _client().get("/risk").text
    assert "loss_halt_category_marker" in html
    card = html.split("CONSECUTIVE LOSS HALT")[1].split("MEASURED KELLY")[0]
    assert "PER CATEGORY" in card
    assert "HALT ACTIVE for momentum" in card and "the others keep buying" in card
    mom = card.split('data-loss-halt-row="nifty200_momentum"')[1].split("</tr>")[0]
    assert "HALTED" in mom and ">2<" in mom
    sw = card.split('data-loss-halt-row="swing"')[1].split("</tr>")[0]
    assert "buying" in sw and "HALTED" not in sw


def test_scope_book_prints_the_whole_book_reading():
    with _Halted():
        state.cfg().loss_halt_scope = "book"
        html = _client().get("/risk").text
    card = html.split("CONSECUTIVE LOSS HALT")[1].split("MEASURED KELLY")[0]
    assert "WHOLE BOOK" in card and "HALT ACTIVE — no new entries today." in card


def _save_settings_page(c):
    html = c.get("/den").text
    html = html.split("settings_algo_form_marker", 1)[1].split("</form>", 1)[0]
    fields = dict(re.findall(r'name="([a-z_]+)"[^>]*value="([^"]*)"', html))
    assert fields, "the settings form rendered no fields"
    return fields


def test_den_carries_the_scope_row_and_the_save_accepts_only_two_words():
    from web.routes import settings_routes
    assert "loss_halt_scope" in [row[0] for row in settings_routes.EDITABLE_FIELDS]
    cfg = state.cfg()
    before = cfg.loss_halt_scope
    # The save posts the WHOLE algo form, so whatever numbers the live cfg
    # shows get stored as this mode's preset overrides. Restore them, or a
    # mode-mixed cfg left by an earlier test is baked into momentum for
    # every later test (found 2026-10-21: owner-buy target +10%, decay 45).
    po_before = {k: dict(v) for k, v in (getattr(cfg, "preset_overrides", {}) or {}).items()}
    mode_before = cfg.mode
    try:
        c = _client()
        fields = _save_settings_page(c)
        assert fields.get("loss_halt_scope") == before
        fields["loss_halt_scope"] = "book"
        assert c.post("/settings", data=fields, follow_redirects=False).status_code == 303
        assert state.cfg().loss_halt_scope == "book"
        fields["loss_halt_scope"] = "everyone"
        c.post("/settings", data=fields, follow_redirects=False)
        assert state.cfg().loss_halt_scope == "category"
        fields["loss_halt_scope"] = "Category "
        c.post("/settings", data=fields, follow_redirects=False)
        assert state.cfg().loss_halt_scope == "category"
    finally:
        live = state.cfg()
        live.loss_halt_scope = before
        live.preset_overrides = po_before
        live.mode = mode_before
        live.apply_mode_preset()


def test_the_den_engine_card_says_per_category():
    html = _client().get("/den").text
    assert "judged PER CATEGORY" in html


def test_a_viewer_sees_nothing_new(monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in",
                                  "password": "v-test"}).status_code == 200
    assert c.get("/risk").status_code == 403
    assert c.get("/den").status_code == 403
    assert c.post("/settings", data={"loss_halt_scope": "book"}).status_code == 403
    for ok in ("/", "/positions", "/trades"):
        html = c.get(ok).text
        assert "loss_halt_category_marker" not in html and "Loss halt scope" not in html


# ------------------------------------------------- review fixes 2026-10-21
# Each failed on the first build of this order.

def test_owner_losers_never_print_a_halt():
    """Owner buys are not gated by the loss halt: two losing owner exits
    show their count and say the row gates nothing — never HALT ACTIVE."""
    with _Halted() as h:
        state.cfg().loss_halt_scope = "category"
        h.eng.loss_tracker.by_category = {"owner": 2}
        html = _client().get("/risk").text
    card = html.split("CONSECUTIVE LOSS HALT")[1].split("MEASURED KELLY")[0]
    assert "HALT ACTIVE" not in card and "OK — no category is halted." in card
    row = card.split('data-loss-halt-row="owner"')[1].split("</tr>")[0]
    assert ">2<" in row and "not gated" in row and "HALTED" not in row


def test_yesterdays_halt_is_not_printed_as_active_today():
    """The gate reads nothing halted on a new day; the card agrees."""
    from datetime import timedelta
    with _Halted() as h:
        state.cfg().loss_halt_scope = "category"
        h.eng.loss_tracker.today = datetime.utcnow().date() - timedelta(days=1)
        html = _client().get("/risk").text
    card = html.split("CONSECUTIVE LOSS HALT")[1].split("MEASURED KELLY")[0]
    assert "HALT ACTIVE" not in card and "OK — no category is halted." in card
    mom = card.split('data-loss-halt-row="nifty200_momentum"')[1].split("</tr>")[0]
    assert "HALTED" not in mom and ">0<" in mom


def test_the_per_category_counts_ride_the_real_mirror_save(monkeypatch):
    """Behavioural twin of the trading-side source check: the real patched
    Portfolio.save hands Mongo a doc carrying loss_by_category, and the boot
    hydrate reads it back — a field missing from the mirror dies on every
    Render restart (the risk_day lesson)."""
    from trading.config import Config
    from trading.paper import Portfolio
    from web import db
    p = state.portfolio()                   # boot: Portfolio.save is patched
    saved = []
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_portfolio", lambda doc: saved.append(doc) or True)
    before = (p.loss_day, p.loss_count, dict(p.loss_by_category))
    try:
        p.loss_day = datetime.utcnow().date().isoformat()
        p.loss_count = 3
        p.loss_by_category = {MOM: 2, "swing": 1}
        p.save()
    finally:
        p.loss_day, p.loss_count, p.loss_by_category = before
    assert saved and saved[-1]["loss_by_category"] == {MOM: 2, "swing": 1}
    fresh = Portfolio.fresh(Config())
    state._hydrate_portfolio_from_mongo(fresh, saved[-1])
    assert fresh.loss_by_category == {MOM: 2, "swing": 1}
