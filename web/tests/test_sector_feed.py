"""Real NSE sector indices on the dashboard (owner order 2026-09-09 "fix
it": Nifty IT fell 10% in six sessions and no screen in the app said so).

Pinned here: the reduction is pure and honest about thin data; the job
stores what it fetched and NAMES the sectors it could not get; Mongo down
skips visibly; the card hides rather than inventing a flat market; and the
calibrated sector tripwires keep their own construction (swapping the input
under a VERIFIED trigger would void the evidence it was measured on).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from trading import data_refresh as dr
from web import auth as auth_mod, market_panels as mp, scheduler
from web.main import app


def _rows():
    out = []
    for i, d in enumerate(("2026-09-01", "2026-09-02", "2026-09-03",
                           "2026-09-04", "2026-09-05", "2026-09-08")):
        out.append({"date": d, "sector": "IT", "open": 100.0, "high": 100.0,
                    "low": 100.0, "close": 100.0 - i * 2})
        out.append({"date": d, "sector": "Metal", "open": 50.0, "high": 50.0,
                    "low": 50.0, "close": 50.0 + i})
    out.append({"date": "2026-09-08", "sector": "Realty", "open": 9.0,
                "high": 9.0, "low": 9.0, "close": 9.0})       # one day only
    return out


def test_moves_are_worst_first_and_thin_data_says_so():
    m = dr.sector_moves(_rows())
    by = {r["sector"]: r for r in m}
    assert m[0]["sector"] == "IT"                     # worst 1-day first
    assert by["IT"]["chg_1d"] == -2.17 and by["IT"]["chg_5d"] == -10.0
    assert by["Metal"]["chg_1d"] > 0
    # one stored day = no change to report, and NEVER a 0% that reads flat
    assert by["Realty"]["chg_1d"] is None and by["Realty"]["days"] == 1
    assert dr.sector_moves([]) == []


def test_keys_are_verified_not_guessed():
    """A wrong instrument key would store another index's prices under a
    sector's name. The three that 400'd on 2026-09-09 were a KEY-NAME
    problem: their upper-case keys answered 200 on 2026-10-21 with closes
    equal to NSE's own (VALFIX batch 3, owner answer that day) — they are in
    under those keys; the mixed-case spelling that 400s stays out."""
    assert "IT" in dr.SECTOR_INDEX_KEYS and "Metal" in dr.SECTOR_INDEX_KEYS
    assert dr.SECTOR_INDEX_KEYS["Healthcare"] == "NSE_INDEX|NIFTY HEALTHCARE"
    assert dr.SECTOR_INDEX_KEYS["Consumer Durables"] == "NSE_INDEX|NIFTY CONSR DURBL"
    assert dr.SECTOR_INDEX_KEYS["Oil & Gas"] == "NSE_INDEX|NIFTY OIL AND GAS"
    assert "NSE_INDEX|Nifty Healthcare" not in dr.SECTOR_INDEX_KEYS.values()
    assert all(k.startswith("NSE_INDEX|") for k in dr.SECTOR_INDEX_KEYS.values())


class _Broker:
    def __init__(self, sectors=("IT", "Metal")):
        self.ok = {dr.SECTOR_INDEX_KEYS[s] for s in sectors}

    def get_daily_candles(self, key, start, end):
        import pandas as pd
        if key not in self.ok:
            return None
        idx = pd.to_datetime(["2026-09-07", "2026-09-08"])
        return pd.DataFrame({"Open": [1.0, 1.0], "High": [1.0, 1.0],
                             "Low": [1.0, 1.0], "Close": [10.0, 9.0]}, index=idx)


def test_fetch_skips_a_dead_sector_instead_of_faking_it():
    rows = dr.fetch_sector_tail(_Broker(), "2026-09-06")
    got = {r["sector"] for r in rows}
    assert got == {"IT", "Metal"}
    assert all(r["date"] > "2026-09-06" for r in rows)
    assert dr.fetch_sector_tail(None, "2026-09-06") == []


def test_job_stores_and_names_the_sectors_it_could_not_get(monkeypatch):
    from web import db, state
    p = state.portfolio()
    n0 = len(p.decision_journal)
    saved = []
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_sector_rows", lambda since_iso="": [])
    monkeypatch.setattr(db, "save_sector_rows",
                        lambda rows: (saved.extend(rows), len(rows))[1])
    monkeypatch.setattr("trading.data.history_broker", lambda: _Broker())
    scheduler._sector_index_refresh()
    e = next(x for x in p.decision_journal[n0:]
             if x.get("action") == "DATA_REFRESH_SECTOR")
    assert "2 of" in e["why"] and "No data for:" in e["why"] and "Pharma" in e["why"]
    assert {r["sector"] for r in saved} == {"IT", "Metal"}


def test_mongo_down_skips_visibly(monkeypatch):
    from web import db, state
    p = state.portfolio()
    n0 = len(p.decision_journal)
    monkeypatch.setattr(db, "is_connected", lambda: False)
    scheduler._sector_index_refresh()
    assert any(x.get("action") == "DATA_REFRESH_SECTOR_SKIPPED"
               for x in p.decision_journal[n0:])


def test_card_hides_on_an_empty_store(monkeypatch):
    monkeypatch.setattr("web.db.load_sector_rows", lambda since_iso="": [])
    assert mp.sector_panel() == {}


def test_card_renders_the_real_moves(monkeypatch):
    monkeypatch.setattr("web.db.load_sector_rows", lambda since_iso="": _rows())
    c = TestClient(app)
    assert c.post("/login", data={"email": "owner@sourceborn.in",
                                  "password": auth_mod._SEEDED_PWD}).status_code == 200
    # Repointed 2026-10-12: the strip MOVED from the dashboard to /scan on the
    # owner's order (item 30i).
    body = c.get("/scan").text
    assert "sector_strip_marker" in body
    assert "-2.17%" in body and "Display only" in body
    assert "sector_strip_marker" not in c.get("/").text


def test_the_calibrated_tripwires_keep_their_own_sector_construction():
    """T12W_0094/0095 were measured on external_panels' equal-weight index.
    Feeding them the NSE index would void that evidence silently."""
    from pathlib import Path
    src = Path(__import__("trading.external_panels", fromlist=["x"]).__file__).read_text()
    assert "SECTOR_INDEX_KEYS" not in src and "load_sector_rows" not in src
    for mod in ("trading.engine", "trading.risk", "trading.orders", "trading.danger"):
        s = Path(__import__(mod, fromlist=["x"]).__file__).read_text()
        assert "sector_moves" not in s and "SECTOR_INDEX_KEYS" not in s
