"""Wiring for the 2026-10-16 breadth + fund-unit fix (owner: "new position
are not added, its not using the all 5 trade segements as well" -> "relook n
fix"): the 09:20 refresh stores the study-panel reading with the whole store
beside it and fund units left out; the daily top-up keeps the panel fresh;
the weekly rebuild never ranks a fund unit; the dashboard says which
population the gate reads, for every stored shape.
"""
from __future__ import annotations

from trading.breadth import study_panel
from trading.universes import fund_unit_tickers

PANEL = sorted(study_panel())


def _doc(t, up, end="2026-10-15"):
    closes = list(range(100, 300)) if up else list(range(300, 100, -1))
    return {"ticker": t, "end": end,
            "candles": [[end, c, c, c, c, 1e6] for c in closes]}


def test_the_0920_refresh_stores_the_panel_reading(monkeypatch):
    from web import scheduler, state
    funds = sorted(fund_unit_tickers())[:40]
    docs = ([_doc(t, i < 48) for i, t in enumerate(PANEL[:160])]
            + [_doc(f"R{i}.NS", i < 600) for i in range(1300)]
            + [_doc(t, True) for t in funds])
    monkeypatch.setattr("web.db.history_tails", lambda rows: iter(docs))
    p = state.portfolio()
    old = p.breadth_store
    try:
        r = scheduler.refresh_breadth_store("test")
        assert r["population"] == "study_panel"
        assert r["sample"] == 160 and r["pct"] == 0.3
        assert r["full_sample"] == 1460            # the 40 funds left out
        assert r["full_excluded_fund"] == 40
        assert state.portfolio().breadth_store["population"] == "study_panel"
    finally:
        p.breadth_store = old
        p.save()


def test_the_daily_topup_keeps_the_study_panel_fresh(monkeypatch):
    from web import db, scheduler, state
    seen = {}

    def fake_topup(broker, keys, **kw):
        seen["keys"] = list(keys)
        return {"ok": len(keys), "skipped_fresh": 0, "failed": 0,
                "total": len(keys)}
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = ("TCS.NS",)
    monkeypatch.setattr("trading.history_store.top_up_history", fake_topup)
    monkeypatch.setattr(
        "trading.brokers.upstox_instruments.to_instrument_keys",
        lambda names: {n: n for n in names})
    monkeypatch.setattr(db, "is_connected", lambda: True)
    eng = state.engine()
    monkeypatch.setattr(eng, "broker", object(), raising=False)
    try:
        assert scheduler._daily_universe_topup() is True
        keys = seen["keys"]
        names = set(keys if not isinstance(keys, dict) else keys.keys())
        assert "TCS.NS" in names
        assert set(PANEL) <= names
    finally:
        cfg.custom_universe = old


def test_the_weekly_rebuild_leaves_fund_units_out(monkeypatch):
    from web import db, scheduler, state
    fund = sorted(fund_unit_tickers())[0]
    docs = ([{"ticker": fund, "candles": [["2026-10-01", 100, 100, 100, 100,
                                           9e7]] * 40}]
            + [{"ticker": f"C{i}.NS", "candles": [["2026-10-01", 500, 500, 500,
                                                   500, 2e6]] * 40}
               for i in range(5)])
    monkeypatch.setattr(db, "history_tails", lambda rows: iter(docs))
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_settings", lambda *a, **k: True)
    cfg, p = state.cfg(), state.portfolio()
    old_u, old_b = cfg.custom_universe, getattr(p, "universe_batch", None)
    old_l = list(getattr(p, "universe_ledger", []) or [])
    n_journal = len(p.decision_journal)
    try:
        res = scheduler.refresh_liquid_universe("test")
        assert fund not in res["tickers"]
        assert res["skipped_fund"] == 1
        assert p.universe_batch["skipped_fund"] == 1
    finally:
        cfg.custom_universe = old_u
        p.universe_batch = old_b
        p.universe_ledger = old_l
        del p.decision_journal[n_journal:]      # this test's own lines only
        p.save()


def test_a_held_fund_rides_along_but_never_takes_a_ranked_slot(monkeypatch):
    """Bot-review P2a: with held funds left out of `exclude`, a held fund with
    big turnover took one of the target slots by rank. Now: held = carried,
    never ranked; a watched fund is not carried at all."""
    from datetime import datetime

    from trading.paper import PaperPosition
    from web import db, scheduler, state
    funds = sorted(fund_unit_tickers())
    held_fund, watched_fund, listed_fund = funds[0], funds[1], funds[2]
    docs = ([{"ticker": held_fund, "candles": [["2026-10-01", 100, 100, 100,
                                                100, 9e8]] * 40}]
            + [{"ticker": f"C{i}.NS", "candles": [["2026-10-01", 500, 500, 500,
                                                   500, 2e6]] * 40}
               for i in range(5)])
    monkeypatch.setattr(db, "history_tails", lambda rows: iter(docs))
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_settings", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "_watchlist_names",
                        lambda p: {watched_fund})
    cfg, p = state.cfg(), state.portfolio()
    old_u, old_t = cfg.custom_universe, cfg.universe_target_size
    old_b = getattr(p, "universe_batch", None)
    old_l = list(getattr(p, "universe_ledger", []) or [])
    n_journal = len(p.decision_journal)
    # a fund unit already on the list (the 09-30 rebuild put ~56 there): the
    # rebuild drops it and the ledger must say WHY
    cfg.custom_universe = ("C0.NS", listed_fund)
    p.positions[held_fund] = PaperPosition(
        ticker=held_fund, qty=10, entry_price=100.0,
        entry_date=datetime(2026, 10, 1), stop_price=95.0, target_price=110.0,
        peak_price=100.0, entry_score=70.0, sector="Unknown",
        reasoning="test", mode="nifty200_momentum")
    cfg.universe_target_size = 3
    try:
        res = scheduler.refresh_liquid_universe("test")
        assert [r["ticker"] for r in res["ranked"]] == ["C0.NS", "C1.NS",
                                                        "C2.NS"]
        assert held_fund in res["tickers"]            # carried for its exits
        assert watched_fund not in res["tickers"]
        assert listed_fund not in res["tickers"]
        assert res["skipped_fund"] == 1
        out = [r for r in p.universe_ledger
               if r["ticker"] == listed_fund and r["move"] == "out"]
        assert out and "fund unit" in out[-1]["reason"]
    finally:
        p.positions.pop(held_fund, None)
        cfg.custom_universe, cfg.universe_target_size = old_u, old_t
        p.universe_batch = old_b
        p.universe_ledger = old_l
        del p.decision_journal[n_journal:]      # this test's own lines only
        p.save()


def test_the_dashboard_says_which_population_the_gate_reads():
    """Every stored shape renders (the /positions-500 lesson): the new panel
    reading with the whole store beside it, the thin-panel fallback, and the
    pre-fix reading the live box holds until its next 09:20 refresh."""
    from datetime import datetime

    from fastapi.testclient import TestClient
    from web import auth as _a, state
    from web.main import app
    # Dated today, not a fixed day: a fixed date would go stale under the
    # gate's 10-day freshness rule and silently test the stale branch.
    TODAY = datetime.utcnow().date().isoformat()
    p = state.portfolio()
    saved_m, saved_b = p.scan_matrix, p.breadth_store
    p.scan_matrix = {"ts": "2026-10-16T03:50:00", "score_floor": 62.0,
                     "rows": {"A.NS": {"score": 70.0, "timing": True,
                                       "abs_mom": True}}}
    shapes = [
        ({"pct": 0.314, "sample": 189, "asof": TODAY,
          "population": "study_panel", "panel_size": 191,
          "full_pct": 0.414, "full_sample": 1969}, ["breadth_panel_marker",
                                                    "31.4", "41.4", "1969"]),
        ({"pct": 0.395, "sample": 2249, "asof": TODAY,
          "population": "full_store"}, ["too few study-panel names"]),
        ({"pct": 0.395, "sample": 2249, "asof": TODAY},
         ["taken before 2026-10-16"]),
        # Bot-review P2b: a panel reading the gate's freshness test rejects
        # must not read as "the number the gate reads".
        ({"pct": 0.52, "sample": 185, "asof": "2026-01-02",
          "population": "study_panel", "panel_size": 191},
         ["breadth_store_stale_marker", "does NOT use"]),
    ]
    try:
        c = TestClient(app)
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": _a._SEEDED_PWD})
        for stored, words in shapes:
            p.breadth_store = stored
            p.save()
            r = c.get("/")
            assert r.status_code == 200
            assert "breadth_store_marker" in r.text
            for w in words:
                assert w in r.text, (stored.get("population"), w)
            if stored.get("asof") == "2026-01-02":
                assert "breadth_panel_marker" not in r.text
    finally:
        p.scan_matrix, p.breadth_store = saved_m, saved_b
        p.save()


def _owner_client():
    from fastapi.testclient import TestClient
    from web import auth as _a
    from web.main import app
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": _a._SEEDED_PWD})
    assert r.status_code in (200, 303)
    return c


def test_scan_counts_what_it_scans_and_names_the_fund_units_skipped():
    """Review finding: after the fix the scan drops unheld fund units, but
    the /scan badge printed the list's full length as "scanned in full"
    and the batch card never showed `skipped_fund`."""
    from web import state
    funds = sorted(fund_unit_tickers())[:3]
    cfg, p = state.cfg(), state.portfolio()
    old_u, old_b = cfg.custom_universe, getattr(p, "universe_batch", None)
    cfg.custom_universe = ("TCS.NS", "SBIN.NS", *funds)
    p.universe_batch = {"ts": "2026-10-16T11:00:00", "count": 5,
                        "target": 1200, "sample": 10, "judged": 5,
                        "short_by": 1195, "skipped_thin": 0,
                        "skipped_illiquid": 5, "skipped_fund": 7,
                        "added": [], "dropped": [], "held_added": []}
    try:
        r = _owner_client().get("/scan")
        assert r.status_code == 200, r.text[:300]
        assert "Your 2-stock universe" in r.text
        assert "data-universe-funds-skipped" in r.text
        assert "3 fund unit(s) on the list are skipped" in r.text
        assert "7 fund unit(s) left out" in r.text
    finally:
        cfg.custom_universe = old_u
        p.universe_batch = old_b
        p.save()


def test_the_register_market_panel_leaves_fund_units_out(monkeypatch):
    """Fund units left both breadth readings; the 16:45 register panel
    (dispersion, tail damage) must not count them as stocks either."""
    import trading.register_v4 as rv
    from web import db, scheduler, state
    funds = sorted(fund_unit_tickers())[:5]
    names = [f"S{i}.NS" for i in range(160)]
    docs = [{"ticker": t, "end": "2026-10-15",
             "candles": [[f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                          100, 101, 99, 100, 1e6] for i in range(80)]}
            for t in names + funds]
    monkeypatch.setattr(db, "history_tails", lambda rows: iter(docs))
    monkeypatch.setattr(db, "is_connected", lambda: True)
    seen = {}

    def fake_view(panel, *a, **k):
        seen["names"] = set(panel)
        return {}
    monkeypatch.setattr(rv, "market_view", fake_view)
    p = state.portfolio()
    old_r = getattr(p, "register_market", None)
    n_journal = len(p.decision_journal)
    try:
        scheduler.run_register_precompute("test")
        assert seen, "market_view was never called"
        assert not (seen["names"] & set(funds))
        assert len(seen["names"]) == 160
    finally:
        p.register_market = old_r
        del p.decision_journal[n_journal:]
        p.save()
