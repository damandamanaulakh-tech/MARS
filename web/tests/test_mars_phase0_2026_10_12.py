"""MARS Phase 0 (owner 2026-10-11 → "do the cleaning n dashboard etc works").

Pins for the pieces this session built itself: branding and ribbon (items
42/45/46), the IST clock (37), the Signal page removal (1), the clubbed V1
docs (3), the viewer login (35), the universe move ledger (15) and the
pre-15-September archive migration (5). Each test models the state it
asserts — none inherits it.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.paper import ClosedTrade, Portfolio
from web import auth as auth_mod, clock, state
from web.main import app

ROOT = Path(__file__).resolve().parents[2]


def _owner():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _strip_comments(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ---------------------------------------------------------------- branding
def test_the_app_is_mars_above_and_sourceborn_below():
    body = _owner().get("/trades").text
    assert 'data-brand-mars="1"' in body and ">MARS<" in body
    assert "Sourceborn" in body
    assert "Sourceborn AM07" not in body
    assert "Owner View" not in body and "OWNER VIEW" not in body


def test_the_ribbon_welcomes_the_owner_by_name(monkeypatch):
    monkeypatch.setenv("OWNER_NAME", "Daman")
    body = _owner().get("/").text
    assert 'data-welcome-ribbon="1"' in body
    assert "Welcome to Sourceborn" in body and "Daman" in body


def test_the_ribbon_falls_back_to_the_upstox_name_then_the_account(monkeypatch):
    monkeypatch.delenv("OWNER_NAME", raising=False)
    from web.templating import display_name

    class _Req:
        session = {"upstox_user_name": "MANPREET KAUR"}

    assert display_name(_Req(), {"name": "Owner"}) == "MANPREET KAUR"
    _Req.session = {}
    assert display_name(_Req(), {"name": "Owner"}) == "Owner"


def test_the_pwa_manifest_and_login_page_say_mars():
    c = TestClient(app)
    m = c.get("/static/manifest.webmanifest").json()
    assert m["short_name"] == "MARS" and m["name"] == "Sourceborn MARS"
    login = c.get("/login").text
    assert ">MARS<" in login and "Sourceborn MARS" in login


def test_the_mark_is_thicker():
    svg = (ROOT / "web/static/img/logo.svg").read_text()
    widths = [int(w) for w in re.findall(r'stroke-width="(\d+)"', svg)]
    assert widths and min(widths) >= 11, widths


# ---------------------------------------------------------------- IST clock
def test_the_header_clock_is_ist_not_utc():
    body = _owner().get("/").text
    m = re.search(r'data-ist-clock="1">([^<]+)<', body)
    assert m, "header clock missing"
    assert m.group(1).strip().endswith("IST")
    # the printed date equals IST's calendar date, never UTC's
    assert clock.ist_now().strftime("%d %b %Y") in m.group(1)


def test_to_ist_renders_stored_utc_stamps_and_never_guesses():
    # 2026-10-11 20:00 UTC is 2026-10-12 01:30 IST — a different DAY
    assert clock.to_ist("2026-10-11T20:00:00") == "2026-10-12 01:30 IST"
    assert clock.to_ist(datetime(2026, 10, 11, 20, 0)) == "2026-10-12 01:30 IST"
    assert clock.to_ist("not a date") == "—"
    assert clock.to_ist(None) == "—"


def test_the_engine_page_no_longer_prints_utc():
    body = _owner().get("/engine").text
    assert " UTC" not in body


# ---------------------------------------------------------------- Signal removed
def test_the_signal_page_is_gone_everywhere():
    c = _owner()
    assert c.get("/signal").status_code == 404
    assert not (ROOT / "web/routes/signal_routes.py").exists()
    assert not (ROOT / "web/templates/signal.html").exists()
    for page in ("/", "/scan", "/triggers"):
        assert 'href="/signal' not in c.get(page).text, page


# ---------------------------------------------------------------- docs clubbed
def test_the_superseded_docs_live_in_the_archive_and_one_index_points_at_the_live_set():
    for name in ("FINAL_SET_V1_1.md", "FINAL_SET_V1_2.md", "PARAM_SEARCH_V1.md",
                 "PARAM_SEARCH_V2.md", "PARAM_SEARCH_V4.md"):
        assert (ROOT / "docs/archive" / name).exists(), name
        assert not (ROOT / "docs" / name).exists(), name
    assert (ROOT / "docs/FINAL_SET_V1_3.md").exists()          # the live record, test-pinned
    assert (ROOT / "docs/PARAM_SEARCH_V3.md").exists()         # its results feed the Evidence panel
    index = (ROOT / "docs/MARS_LIVE_SET.md").read_text()
    assert "FINAL_SET_V1_3.md" in index and "archive/FINAL_SET_V1_2.md" in index


# ---------------------------------------------------------------- viewer login
def _viewer(monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    r = c.post("/login", data={"email": "viewer@sourceborn.in", "password": "v-test"})
    assert r.status_code == 200
    return c


def test_a_viewer_sees_the_book_only(monkeypatch):
    c = _viewer(monkeypatch)
    for ok in ("/", "/positions", "/trades"):
        assert c.get(ok).status_code == 200, ok
    for blocked in ("/scan", "/den", "/settings", "/risk", "/lens", "/triggers",
                    "/register", "/strategy", "/engine", "/reports", "/backtest",
                    "/chart/TCS.NS", "/scan/parameter/momentum"):
        assert c.get(blocked).status_code == 403, blocked
    # no POST at all — a viewer can never buy, sell, purge or save
    assert c.post("/positions/purge-universe").status_code == 403
    assert c.post("/scan/buy", data={"ticker": "TCS"}).status_code == 403


def test_a_viewer_sidebar_has_no_formula_pages(monkeypatch):
    body = _viewer(monkeypatch).get("/").text
    aside = body[body.index("<aside"):body.index("</aside>")]
    assert 'href="/den"' not in aside and 'href="/scan"' not in aside
    assert 'href="/settings"' not in aside and 'href="/risk"' not in aside
    assert 'href="/positions"' in aside and 'href="/trades"' in aside


def test_the_viewer_account_exists_only_with_a_password_in_the_env():
    src = _strip_comments((ROOT / "web/auth.py").read_text())
    assert 'os.environ.get("VIEWER_PASSWORD")' in src
    assert '"password": "viewer"' not in src


def test_the_viewer_guard_runs_inside_the_session_middleware():
    """Registered after SessionMiddleware it would wrap OUTSIDE it, see no
    session, and wave every viewer through."""
    src = _strip_comments((ROOT / "web/main.py").read_text())
    assert src.index("_viewer_guard") < src.index("SessionMiddleware,")


# ---------------------------------------------------------------- universe ledger
def test_the_ledger_survives_json_and_mongo_both_ways(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.universe_ledger = [{"ts": "2026-10-09T11:00:00", "ticker": "NEW.NS",
                          "move": "in", "turnover": 5.5e7, "rank": 12,
                          "reason": "ranked 12 of 1200 by median turnover"}]
    p.save()
    assert Portfolio.load_or_fresh(cfg).universe_ledger == p.universe_ledger
    src = Path(state.__file__).read_text()
    assert 'p.universe_ledger = list(doc.get("universe_ledger"' in src
    assert '"universe_ledger": self.universe_ledger' in src


def test_the_rebuild_writes_one_ledger_row_per_move_with_a_reason(monkeypatch):
    from web import db, scheduler
    p, cfg = state.portfolio(), state.cfg()
    saved = (list(p.universe_ledger), dict(p.universe_batch),
             tuple(getattr(cfg, "custom_universe", ()) or ()), dict(p.positions))
    # stored rows are [date, o, h, l, c, v]; turnover = close x volume
    docs = [{"ticker": f"T{i}.NS",
             "candles": [[f"2026-09-{d+1:02d}", 100.0, 101.0, 99.0, 100.0,
                          1_000_000 * (10 - i)] for d in range(25)]}
            for i in range(4)]
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "history_tails", lambda n: docs)
    monkeypatch.setattr(db, "save_settings", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_watchlist_names", lambda _p: set())
    try:
        p.universe_ledger = []
        p.positions = {}
        cfg.custom_universe = ("OLD.NS", "T0.NS")
        res = scheduler.refresh_liquid_universe("test")
        assert res
        moves = {(m["ticker"], m["move"]): m for m in p.universe_ledger}
        assert ("OLD.NS", "out") in moves and "turnover cut" in moves[("OLD.NS", "out")]["reason"]
        ins = [m for m in p.universe_ledger if m["move"] == "in" and m["rank"] is not None]
        assert ins and all("ranked" in m["reason"] for m in ins)
        body = _owner().get("/scan").text
        assert "universe_ledger_marker" in body and "OLD" in body
    finally:
        p.universe_ledger, p.universe_batch, cfg.custom_universe, p.positions = saved


# ---------------------------------------------------------------- pre-MARS archive
class _FakeDB:
    """Archive collections that answer like Mongo, with a switch for a
    short read-back and one for a refused write."""
    PRE_MARS_FLAG = "pre_mars"

    def __init__(self, write_ok=True, short_readback=False):
        self.trades, self.journal, self.migr = [], [], {}
        self.write_ok, self.short = write_ok, short_readback

    def is_connected(self): return True
    def load_settings(self, doc_id="default"): return self.migr if doc_id == "migrations" else {}
    def save_settings(self, d, doc_id="default"):
        if doc_id == "migrations": self.migr.update(d)
    def archive_trades(self, docs):
        if not self.write_ok: return False
        self.trades += docs; return True
    def archive_journal(self, docs):
        if not self.write_ok: return False
        self.journal += docs; return True
    def count_pre_mars(self, coll):
        rows = self.trades if coll == "trades" else self.journal
        n = sum(1 for r in rows if r.get("pre_mars"))
        return n - 1 if (self.short and n) else n


def _book(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    def trade(exit_dt):
        return ClosedTrade(ticker="X.NS", qty=1, entry_date=datetime(2026, 8, 1),
                           exit_date=exit_dt, entry_price=100.0, exit_price=101.0,
                           gross_pnl=1.0, costs=0.0, net_pnl=1.0, reason="TARGET",
                           sector="IT", entry_score=70.0, reasoning="fixture")
    p.trades = [trade(datetime(2026, 9, 1)),            # pre
                trade(datetime(2026, 9, 14, 18, 29)),   # 23:59 IST on the 14th -> pre
                trade(datetime(2026, 9, 14, 18, 31)),   # 00:01 IST on the 15th -> stays
                trade(datetime(2026, 10, 1))]            # stays
    p.decision_journal = [{"ts": "2026-09-01T04:00:00", "action": "BUY"},
                          {"ts": "2026-09-14T18:29:00", "action": "SELL"},
                          {"ts": "2026-09-14T18:31:00", "action": "BUY"},
                          {"ts": "2026-10-01T04:00:00", "action": "HOLD"}]
    return p


def test_pre_mars_rows_move_to_the_archive_and_leave_the_book_after_read_back(tmp_path, monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(state, "db", fake)
    p = _book(tmp_path)
    out = state._migrate_archive_pre_mars(p)
    assert out == {"trades": 2, "journal": 2, "done": True}
    assert len(p.trades) == 2 and all(t.exit_date >= datetime(2026, 9, 14, 18, 30) for t in p.trades)
    assert all(r["pre_mars"] is True for r in fake.trades + fake.journal)
    assert [e["action"] for e in p.decision_journal][:2] == ["BUY", "HOLD"]
    assert p.decision_journal[-1]["action"] == "MARS_ARCHIVE_PRE_2026_09_15"
    assert fake.migr.get(state.PRE_MARS_MARKER) is True
    # second boot: marker set, nothing happens
    assert state._migrate_archive_pre_mars(p)["done"] is False


def test_nothing_is_trimmed_when_the_write_is_refused_or_the_read_back_is_short(tmp_path, monkeypatch):
    for fake in (_FakeDB(write_ok=False), _FakeDB(short_readback=True)):
        monkeypatch.setattr(state, "db", fake)
        p = _book(tmp_path)
        out = state._migrate_archive_pre_mars(p)
        assert out["done"] is False
        assert len(p.trades) == 4 and len(p.decision_journal) == 4
        assert state.PRE_MARS_MARKER not in fake.migr


def test_live_readers_skip_pre_mars_rows_by_default():
    from web import db
    assert db._not_pre_mars(False) == {"pre_mars": {"$ne": True}}
    assert db._not_pre_mars(True) == {}
    assert db.count_pre_mars("trades") == -1       # Mongo down in tests = never "zero landed"
