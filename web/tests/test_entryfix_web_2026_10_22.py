"""Entry-path fix batch, web side (owner 2026-10-22: "review and find flaws,
fix them for app not buying or gates which are halting ... keep asking me for
confirmation"; the owner's cards D1 and D4 decide C-5 and C-6).

  C-1 ROOT-ROUTE-06  a REFUSED Settings save leaves the live mode unchanged
  C-2 ROOT-ROUTE-08  the Upstox callback refuses a reply or session with no
                     security state, instead of letting it through
  C-3 XR-070         Mongo back with a conflicting local book: the Mongo book
                     stays live, the local one is parked, the journal says so
  C-4 XR-083         the Buy Desk never pays more than the typed budget
  C-5 D1             /risk says whether a negative Kelly reading CAPS (thin
                     record, under 30 usable exits) or BLOCKS (30 or more)
  C-6 D4             /risk says what the five shares are taken from: what is
                     left after your own buys and Ocean

Every behavioural test here fails on bdc82ec (checked by running this file
against a copy of the bdc82ec web/ files — see the batch report).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from web import auth as auth_mod
from web import db, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _pos(t, rupees, mode="nifty200_momentum", tags=None):
    return PaperPosition(
        ticker=t, qty=int(rupees // 100), entry_price=100.0,
        entry_date=datetime(2026, 10, 20), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=95.0, target_price=120.0, tags=list(tags or []))


# ==========================================================================
# C-1  ROOT-ROUTE-06: a refused Settings save changes nothing
# ==========================================================================

def _other_mode(cur: str) -> str:
    from web.routes import settings_routes
    return next(m[0] for m in settings_routes.MODE_CHOICES if m[0] != cur)


@pytest.fixture
def mode_guard():
    cfg = state.cfg()
    before = (cfg.mode, tuple(cfg.custom_universe or ()))
    try:
        yield cfg
    finally:
        cfg = state.cfg()
        cfg.mode, cfg.custom_universe = before[0], before[1]
        cfg.apply_mode_preset()
        state.reload_config()


def test_a_save_refused_for_an_unknown_name_leaves_the_mode_unchanged(mode_guard):
    cfg = mode_guard
    before = cfg.mode
    r = _owner().post("/settings", data={
        "mode": _other_mode(before),
        "custom_universe": "TCS ZZQQNOTANSESTOCK",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "saved=universe_unknown" in r.headers["location"]
    assert state.cfg().mode == before, \
        "a refused save switched the live mode anyway (ROOT-ROUTE-06)"


def test_a_save_refused_as_too_big_leaves_the_mode_unchanged(mode_guard):
    from web.routes import settings_routes
    cfg = mode_guard
    before = cfg.mode
    cap = settings_routes._universe_paste_cap(cfg)
    names = " ".join(f"FAKE{i}" for i in range(cap + 5))
    r = _owner().post("/settings", data={
        "mode": _other_mode(before), "custom_universe": names,
    }, follow_redirects=False)
    assert "saved=universe_too_big" in r.headers["location"]
    assert state.cfg().mode == before


def test_an_accepted_save_still_switches_the_mode(mode_guard):
    cfg = mode_guard
    target = _other_mode(cfg.mode)
    r = _owner().post("/settings", data={"mode": target}, follow_redirects=False)
    assert "saved=ok" in r.headers["location"]
    assert state.cfg().mode == target


# ==========================================================================
# C-2  ROOT-ROUTE-08: no security state = no connect
# ==========================================================================

@pytest.fixture
def upstox_env(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "test_key")
    monkeypatch.setenv("UPSTOX_API_SECRET", "test_secret")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "http://testserver/upstox/callback")


def _token_ok():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"access_token": "T", "user_id": "U",
                              "user_name": "Daman"}
    return fake


def _last_connect_failure() -> dict:
    for e in reversed(state.portfolio().decision_journal[-20:]):
        if e.get("action") == "UPSTOX_CONNECT_FAILED":
            return e
    return {}


def test_a_reply_without_state_is_refused_and_never_exchanged(upstox_env):
    c = _owner()
    c.get("/upstox/login", follow_redirects=False)        # session holds a state
    with patch("trading.brokers.upstox.httpx.post",
               return_value=_token_ok()) as post:
        r = c.get("/upstox/callback?code=ABC", follow_redirects=False)
    assert r.status_code == 303
    assert "upstox_error=state_missing" in r.headers["location"]
    assert post.call_count == 0, "the code was exchanged without a state"
    e = _last_connect_failure()
    assert e.get("reason") == "state_missing" and "Upstox's reply" in e["detail"]


def test_a_session_without_state_is_refused(upstox_env):
    c = _owner()                                          # never hit /upstox/login
    with patch("trading.brokers.upstox.httpx.post",
               return_value=_token_ok()) as post:
        r = c.get("/upstox/callback?code=ABC&state=anything",
                  follow_redirects=False)
    assert "upstox_error=state_missing" in r.headers["location"]
    assert post.call_count == 0
    assert "session" in _last_connect_failure().get("detail", "")


def test_the_dashboard_explains_state_missing_in_plain_words():
    from web.routes.upstox import CONNECT_NOTICES
    level, text = CONNECT_NOTICES["state_missing"]
    assert level == "error" and "Nothing was connected" in text
    html = _owner().get("/?upstox_error=state_missing").text
    assert "without its security check" in html


# ==========================================================================
# C-3  XR-070: Mongo wins, the local book is parked, the journal says so
# ==========================================================================

def _mongo_book_doc() -> dict:
    real = _pos("REAL.NS", 10_00_000.0)
    return {
        "cash": 4_90_00_000.0,
        "positions": {"REAL.NS": real.to_dict()},
        "trades": [],
        "capital_base": 5_00_00_000.0,
        "decision_journal": [{"ts": "2026-10-01T04:00:00", "action": "BUY",
                              "ticker": "REAL.NS", "why": "the real book"}],
    }


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    """A boot during an outage (a fresh local book that then traded) and a
    Mongo that has come back holding the real book."""
    store: dict = {"default": _mongo_book_doc()}
    cfg = Config(state_dir=str(tmp_path / "state"))
    local = Portfolio.fresh(cfg)
    local.positions = {"PHANTOM.NS": _pos("PHANTOM.NS", 2_00_000.0)}
    local.cash -= 2_00_000.0
    local.decision_journal.append({"ts": "2026-10-22T04:00:00", "action": "BUY",
                                   "ticker": "PHANTOM.NS", "why": "outage-day buy"})
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(state, "portfolio", lambda: local)
    monkeypatch.setattr(db, "recovered_pending", lambda: True)
    monkeypatch.setattr(db, "claim_recovery", lambda: None)
    monkeypatch.setattr(db, "boot_was_degraded", lambda: True)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "archive_journal", lambda entries: True)
    monkeypatch.setattr(db, "archive_trades", lambda docs: True)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", None, raising=False)

    def _save(doc, doc_id="default"):
        store[doc_id] = json.loads(json.dumps(doc, default=str))

    def _load(doc_id="default"):
        d = store.get(doc_id)
        return json.loads(json.dumps(d)) if d is not None else None
    monkeypatch.setattr(db, "save_portfolio", _save)
    monkeypatch.setattr(db, "load_portfolio", _load)
    return SimpleNamespace(store=store, local=local, cfg=cfg)


def test_mongo_book_stays_live_and_the_local_book_is_parked(recovery):
    (Path(recovery.cfg.state_dir)).mkdir(parents=True, exist_ok=True)
    state.heal_after_reconnect()
    p = recovery.local
    # the live book in memory is the Mongo one
    assert set(p.positions) == {"REAL.NS"}, "the outage-day book replaced the real one"
    # what was written back over Mongo is the Mongo book, not the phantom
    assert set(recovery.store["default"]["positions"]) == {"REAL.NS"}
    # the local book is parked, in the database and on disk
    parked = [k for k in recovery.store if k.startswith("portfolio_conflict_")]
    assert len(parked) == 1
    assert set(recovery.store[parked[0]]["positions"]) == {"PHANTOM.NS"}
    files = list(Path(recovery.cfg.state_dir).glob("portfolio_conflict_*.json"))
    assert len(files) == 1 and "PHANTOM.NS" in files[0].read_text()
    # the journal says which won, why, and where the local copy is
    line = [e for e in p.decision_journal
            if e.get("action") == "MONGO_RECOVERED_CONFLICT"][-1]
    assert line["kept"] == "mongo" and line["parked_as"] == parked[0]
    assert line["parked_in_mongo"] is True
    assert "NOTHING was overwritten" not in line["why"]
    assert "MONGO book is kept as the live book" in line["why"]
    assert parked[0] in line["why"]
    # append-only: the local session's lines are carried across, marked
    carried = [e for e in p.decision_journal
               if e.get("ticker") == "PHANTOM.NS" and e.get("action") == "BUY"]
    assert carried and carried[0]["parked_book"] == parked[0]


def test_a_park_that_lands_nowhere_still_keeps_mongo_and_lists_the_book(
        recovery, monkeypatch):
    """The database refuses the parked copy and the state dir is missing:
    Mongo still wins, and the journal line itself carries the local
    positions so they are not lost."""
    def _refuse(doc, doc_id="default"):
        if doc_id.startswith("portfolio_conflict_"):
            db.LAST_WRITE_ERROR = "OperationFailure: over quota"
            return
        db.LAST_WRITE_ERROR = None
        recovery.store[doc_id] = json.loads(json.dumps(doc, default=str))
    monkeypatch.setattr(db, "save_portfolio", _refuse)
    state.heal_after_reconnect()
    p = recovery.local
    assert set(p.positions) == {"REAL.NS"}
    line = [e for e in p.decision_journal
            if e.get("action") == "MONGO_RECOVERED_CONFLICT"][-1]
    assert line["parked_in_mongo"] is False and line["parked_file"] is None
    assert "could NOT be parked" in line["why"] and "over quota" in line["why"]
    assert [r["ticker"] for r in line["local_positions"]] == ["PHANTOM.NS"]
    assert "PHANTOM.NS x2000" in line["why"]


# ==========================================================================
# C-4  XR-083: the Buy Desk never pays more than the typed budget
# ==========================================================================

class _DeskBook:
    def __init__(self):
        self.positions: dict = {}
        self.cash = 10_00_00_000.0
        self.decision_journal: list = []

    def equity(self, marks):
        return 5_00_00_000.0


class _DeskEngine:
    def __init__(self, book):
        self.book = book

    def _open(self, pick, qty, fill, now):
        self.book.positions[pick["ticker"]] = SimpleNamespace(tags=[])
        self.book.cash -= qty * fill


BUDGET = 2_50_00_000.0


def test_plan_and_buy_never_pass_a_2_5_crore_budget(monkeypatch):
    from web import buy_desk
    monkeypatch.setattr("trading.sizing.position_notional_for",
                        lambda cfg, equity, score=None, close=None:
                        (25_00_000.0, {"mode": "test"}))
    cfg = Config()
    book = _DeskBook()
    rows = [{"ticker": f"N{i}.NS", "score": 80.0} for i in range(10)]
    prices = {r["ticker"]: 100.0 for r in rows}
    pl = buy_desk.plan(book, cfg, rows, BUDGET, prices, lambda t: None)
    assert pl["planned_total"] <= BUDGET
    summary = buy_desk.execute(_DeskEngine(book), book, cfg, pl["rows"],
                               datetime(2026, 10, 22, 10, 0), budget_rs=BUDGET,
                               market_hours_bypassed=False)
    assert summary["spent"] <= BUDGET, (
        f"the batch paid Rs {summary['spent']:,.2f} on a Rs {BUDGET:,.0f} budget")
    assert len(summary["opened"]) == 10


def test_a_plan_sized_on_the_raw_quote_is_cut_at_the_budget():
    """A stale or hand-built plan (qty sized on the raw quote, as before the
    fix) still cannot overspend: the buy keeps a running total."""
    from web import buy_desk
    cfg = Config()
    book = _DeskBook()
    rows = [{"ticker": f"N{i}.NS", "score": 80.0, "price": 100.0, "qty": 25_000,
             "cost": 25_00_000.0, "recommended": True} for i in range(10)]
    summary = buy_desk.execute(_DeskEngine(book), book, cfg, rows,
                               datetime(2026, 10, 22, 10, 0), budget_rs=BUDGET,
                               market_hours_bypassed=False)
    assert summary["spent"] <= BUDGET
    assert summary["skipped"] == [{"ticker": "N9.NS", "reason": "budget_short"}]


# ==========================================================================
# C-5  D1: the measured-Kelly card names its mode
# ==========================================================================

def _reading(usable: int, block: bool, thin: bool):
    from trading.measured_kelly import KellyReading
    r = KellyReading(
        status="NO_POSITIVE_EDGE", usable=usable, excluded=0, unknown=0,
        by_reason={}, wins=1, losses=max(1, usable - 1), win_rate=0.5,
        raw_win_rate=0.5, payoff_ratio=0.5, raw_kelly=-0.5, confidence=0.01,
        fraction=0.0, max_position_pct=0.0, block_new_entries=block,
        note="fixture reading", category="nifty200_momentum")
    # KellyReading is a frozen dataclass; set the flag directly so this test
    # reads the same before and after the thin-record field joins it
    object.__setattr__(r, "thin_record", thin)
    return r


def _kelly_card(monkeypatch, reading) -> str:
    import trading.measured_kelly as mk
    monkeypatch.setattr(state.cfg(), "measured_kelly_enabled", True)
    monkeypatch.setattr(mk, "measure", lambda *a, **k: reading)
    html = _owner().get("/risk").text
    return html.split("measured_kelly_marker")[1].split("IFR OVERLAY")[0]


def test_a_thin_record_says_capped_at_the_slab_not_blocked(monkeypatch):
    card = _kelly_card(monkeypatch, _reading(2, block=False, thin=True))
    assert ("Thin record (2 of 30 usable exits): a negative reading CAPS "
            "momentum buys at the ₹1,50,000 slab, it does not block") in card
    assert "NOT BLOCKED" in card
    assert "NEW SCAN ENTRIES BLOCKED" not in card
    assert "NOT ENOUGH EXITS YET" not in card


def test_a_full_record_says_the_block_works(monkeypatch):
    card = _kelly_card(monkeypatch, _reading(40, block=True, thin=False))
    assert "NEW SCAN ENTRIES BLOCKED" in card
    assert ("Full record (40 usable exits, 30 or more): the negative reading "
            "blocks new momentum scan buys") in card
    assert "Thin record" not in card


# ==========================================================================
# C-6  D4: what the five shares are taken from
# ==========================================================================

@pytest.fixture
def owner_ocean_book(monkeypatch):
    p = state.portfolio()
    saved = (dict(p.positions), p.cash)
    monkeypatch.setattr(state.cfg(), "category_budgets_enabled", True)
    monkeypatch.setattr(state.cfg(), "cash_reserve_pct", 0.0)
    p.positions = {
        "OWN.NS": _pos("OWN.NS", 1_00_00_000.0, tags=["owner_pick", "buy_desk"]),
        "OCN.NS": _pos("OCN.NS", 20_00_000.0, tags=["ocean"]),
        "MOM.NS": _pos("MOM.NS", 10_00_000.0),
    }
    p.cash = 3_00_00_000.0                  # equity on the page = Rs 4.3 Cr
    try:
        yield p
    finally:
        p.positions, p.cash = saved


def _share_line(html: str) -> str:
    return html.split("share_base_marker")[1].split("</div>")[0]


def _d4_rule(monkeypatch, *, table_too: bool = True):
    """The owner's D4 rule as trading/category_budget.py carries it once the
    gate side lands (share_base, and the table's limits taken from it) — or,
    with table_too=False, share_base present while the table's limits are
    still taken from ALL deployable capital. Both pinned here explicitly, so
    the tests read the same before and after the gate-side change merges."""
    from trading import category_budget as cb

    def share_base(cfg, positions, base_capital):
        vals = list(positions.values()) if isinstance(positions, dict) else list(positions)
        return (cb.deployable(cfg, base_capital) - cb.used(vals, cb.OWNER)
                - cb.used(vals, cb.OCEAN))
    monkeypatch.setattr(cb, "share_base", share_base, raising=False)
    orig = cb.usage

    def usage(cfg, positions, base_capital):
        rows = orig(cfg, positions, base_capital)
        base = (share_base(cfg, positions, base_capital) if table_too
                else cb.deployable(cfg, base_capital))
        for r in rows:
            if r["share"] is not None:
                r["limit"] = r["share"] * base
                r["left"] = max(0.0, r["limit"] - r["used"])
        return rows
    monkeypatch.setattr(cb, "usage", usage)


def test_the_category_card_names_the_base_after_owner_and_ocean(
        owner_ocean_book, monkeypatch):
    _d4_rule(monkeypatch)
    html = _owner().get("/risk").text
    line = _share_line(html)
    assert ("of <strong>₹3,10,00,000</strong> left after your own buys "
            "₹1,00,00,000 and Ocean ₹20,00,000") in line
    assert "₹4,30,00,000" in line
    # the line and the table agree: momentum's 40% of Rs 3.1 Cr
    card = html.split("category_budget_marker")[1].split("IFR OVERLAY")[0]
    assert "₹1,24,00,000" in card.split("Momentum")[1].split("</tr>")[0]


def test_the_line_never_claims_a_base_the_table_does_not_use(
        owner_ocean_book, monkeypatch):
    """share_base present but the limits still taken from all deployable
    capital: the line prints the table's base and the older wording."""
    _d4_rule(monkeypatch, table_too=False)
    line = _share_line(_owner().get("/risk").text)
    assert "left after your own buys" not in line
    assert "whole deployable <strong>₹4,30,00,000</strong>" in line


def test_without_the_rule_the_card_says_owner_and_ocean_are_not_taken_off(
        owner_ocean_book, monkeypatch):
    """A category_budget module with no share_base (the pre-D4 rule): the
    page still renders and says the shares are of ALL deployable capital.
    usage() is pinned to that older rule here, self-contained, because the
    D4 usage() reads share_base itself."""
    from trading import category_budget as cb

    def old_usage(cfg, positions, base_capital):
        vals = list(positions.values())
        rows = []
        for cat in cb.SHARE_FIELDS:
            sh = cb.share(cfg, cat)
            lim = None if sh is None else sh * cb.deployable(cfg, base_capital)
            u = cb.used(vals, cat)
            rows.append({"category": cat, "label": cb.LABELS[cat], "share": sh,
                         "limit": lim, "used": u,
                         "left": None if lim is None else max(0.0, lim - u),
                         "open": sum(1 for q in vals if cb.category_of(q) == cat),
                         "full": False})
        return rows
    monkeypatch.delattr(cb, "share_base", raising=False)
    monkeypatch.setattr(cb, "usage", old_usage)
    line = _share_line(_owner().get("/risk").text)
    assert "whole deployable <strong>₹4,30,00,000</strong>" in line
    assert "not taken off first" in line


def test_the_filter_list_no_longer_claims_a_spread_check():
    """D3 (owner card 2026-10-22: "Switch the spread check off"): the /risk
    filter list said the app skips wide bid-ask spreads."""
    html = _owner().get("/risk").text
    assert "wide bid-ask spreads" not in html
    assert "spread check is switched off" in html
