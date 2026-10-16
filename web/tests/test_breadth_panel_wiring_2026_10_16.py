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
    try:
        res = scheduler.refresh_liquid_universe("test")
        assert fund not in res["tickers"]
        assert res["skipped_fund"] == 1
        assert p.universe_batch["skipped_fund"] == 1
    finally:
        cfg.custom_universe = old_u
        p.universe_batch = old_b
        p.universe_ledger = old_l
        p.save()


def test_the_dashboard_says_which_population_the_gate_reads():
    """Every stored shape renders (the /positions-500 lesson): the new panel
    reading with the whole store beside it, the thin-panel fallback, and the
    pre-fix reading the live box holds until its next 09:20 refresh."""
    from fastapi.testclient import TestClient
    from web import auth as _a, state
    from web.main import app
    p = state.portfolio()
    saved_m, saved_b = p.scan_matrix, p.breadth_store
    p.scan_matrix = {"ts": "2026-10-16T03:50:00", "score_floor": 62.0,
                     "rows": {"A.NS": {"score": 70.0, "timing": True,
                                       "abs_mom": True}}}
    shapes = [
        ({"pct": 0.314, "sample": 189, "asof": "2026-10-15",
          "population": "study_panel", "panel_size": 191,
          "full_pct": 0.414, "full_sample": 1969}, ["breadth_panel_marker",
                                                    "31.4", "41.4", "1969"]),
        ({"pct": 0.395, "sample": 2249, "asof": "2026-10-15",
          "population": "full_store"}, ["too few study-panel names"]),
        ({"pct": 0.395, "sample": 2249, "asof": "2026-10-15"},
         ["taken before 2026-10-16"]),
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
    finally:
        p.scan_matrix, p.breadth_store = saved_m, saved_b
        p.save()
