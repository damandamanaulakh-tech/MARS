"""Batch-3 review (2026-10-21): the item-19 exit RECORD is valued the way
the pre-registered study valued it. MONEY PATH — the record's base is the
exit target the engine sets at a buy (A1) and on a held name (A3), and its
sector index is the leg the monthly re-mark reads.

Pins (each fails on 5f08aa3 except T5, which must keep passing):
  T1  a date typed in the inputs table AND a §11 pushed date do not move
      the stored record's base — the precompute reads the bridge to the
      valuation day (the §12 display row still moves with them);
  T2  an IT-services route with a USD/INR move: the record's row
      (row_for(tested_rule=True)) carries the rupee row E101, the display
      row counts it 0; with no move and no selection date in play the two
      rows are the same;
  T3  the record's sector index is the map the study measured
      (bench_for_tested: override → the pre-2026-10-21 app labels → Nifty
      50), never the batch-3 remap bench_for gives the display;
  T4  the precompute asks row_for for the tested rule and every stored
      record says so (rule "tested-2026-10-21", tested_rule True); a row
      built without it says False;
  T5  the display row (row_for default) still ends the bridge at the
      selection date (the batch-3 pin in test_valfix_b3_den_2026_10_21
      stays untouched; the first T5 test here passes on 5f08aa3 too), while
      the record's row reads it to the newest stored close.
"""
from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from trading import valuation_exit as VE
from trading.valuation import snapshot as S
from web import scheduler as SCH
from web import state
from web import valuation_chain_view as cv
from web import valuation_exit_feed as feed
from web import valuation_market_view as mv
from web import valuation_view as vv

ROOT = Path(__file__).resolve().parents[2]

#: The map the item-19 study read (git 0c9f986, trading/valuation_context.py)
#: — written out here so a later change to the live dicts cannot move the
#: record's sector leg without this pin failing.
STUDY_LABEL_INDEX = {"Auto": "Auto", "Financial": "Fin Service", "IT": "IT",
                     "Pharma": "Pharma", "FMCG": "FMCG", "Metal": "Metal",
                     "Energy": "Energy", "Utilities": "Energy", "Media": "Media",
                     "Construction": "Infra"}
STUDY_OVERRIDE = {"DLF.NS": "Realty", "GODREJPROP.NS": "Realty", "LODHA.NS": "Realty",
                  "OBEROIRLTY.NS": "Realty", "BRIGADE.NS": "Realty",
                  "PRESTIGE.NS": "Realty", "PHOENIXLTD.NS": "Realty"}


def _no_feeds():
    return SimpleNamespace(latest=lambda series: None, at=lambda series, when: None,
                           inst_holding=lambda sym, pe: None,
                           series_points=lambda s: [], latest_row=lambda s: None)


def _peers(with_pe: bool):
    def peers(method, exclude=""):
        if not with_pe:
            return {"n": 0, "source": "test: no peers"}
        return {"n": 3, "pe_median": 24.0, "pb_median": 2.0, "ev_ebitda_median": 9.0,
                "ev_sales_median": 2.5, "source": "test peers"}
    return peers


def _reset():
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    mv.LAST_SECTOR_FX.clear()
    mv.clear_caches()
    cv.clear_feed_cache()
    cv.clear_row_cache()
    feed.clear_cache()


@pytest.fixture
def env(monkeypatch):
    """A fake settings store, stub peers / feeds, no Mongo, no network."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    monkeypatch.setattr(vv.db, "is_connected", lambda: False)
    _reset()
    monkeypatch.setattr(cv, "peer_multiples", _peers(True))
    monkeypatch.setattr(cv, "peer_table_rows", lambda: {})
    monkeypatch.setattr(cv, "_macro_feeds", _no_feeds)
    monkeypatch.setattr(cv, "_held_names", lambda p: [])
    yield docs
    _reset()


def _tcs_closes(since: str = "") -> pd.Series:
    s = cv._zip_close_series("TCS.NS")
    assert s is not None and len(s) > 300, "the repo zip carries TCS"
    return s[s.index >= since].copy() if since else s.copy()


def _seed_dates(docs: dict) -> None:
    """A selection date typed in the inputs table AND a §11 pushed date."""
    docs[cv.INPUTS_DOC_ID] = {"inputs": {"TCS": {"selection_date": "2026-06-15"}}}
    docs[vv.DOC_ID] = {"targets": {"TCS.NS": {"target": 4200.0, "asof": "2026-06-01",
                                              "basis": "pushed from §12 · valued as of "
                                                       "2026-06-01", "ts": "x"}},
                       "assumptions": {}}


def _clear_dates(docs: dict) -> None:
    docs.pop(cv.INPUTS_DOC_ID, None)
    docs.pop(vv.DOC_ID, None)
    _reset()


def _precompute(monkeypatch, closes: pd.Series) -> dict:
    monkeypatch.setattr(feed, "precompute_scope", lambda p, cap: (["TCS.NS"], []))
    monkeypatch.setattr(cv, "_prefetch_closes",
                        lambda names: {"TCS.NS": closes.copy()} if "TCS.NS" in names else {})
    feed.clear_cache()
    res = SCH.run_valuation_precompute("test")
    assert res["status"] == "ok", res
    return dict(feed.LAST["rows"]["TCS.NS"])


# ------------------------------------------------- T1 dates never reach it

def test_t1_typed_and_pushed_selection_dates_do_not_move_the_stored_record(env, monkeypatch):
    # no peer P/E and a price history too short for an own band: the earnings
    # range is then each case's closing P/E — the P/E bridge itself — so
    # where the bridge stops reading the market reaches the base
    monkeypatch.setattr(cv, "peer_multiples", _peers(False))
    closes = _tcs_closes(since="2025-06-01")
    cfg = state.cfg()

    _seed_dates(env)
    shown_dated = cv.row_for("TCS.NS", cfg, None, closes=closes.copy())
    assert shown_dated["macro_inputs"]["index_end_date"] == "2026-06-15"
    with_dates = _precompute(monkeypatch, closes)

    _clear_dates(env)
    shown_plain = cv.row_for("TCS.NS", cfg, None, closes=closes.copy())
    assert shown_plain["macro_inputs"]["index_end_date"] == "2026-05-15"
    plain = _precompute(monkeypatch, closes)

    # the setup is sensitive: on §12 the two dates DO move the base
    a, b = shown_dated["range"].get("base"), shown_plain["range"].get("base")
    assert a is not None and b is not None and a != pytest.approx(b, rel=1e-6)
    # ... and the money-path record does not move
    assert with_dates["base_target"] is not None
    assert with_dates["base_target"] == plain["base_target"]
    assert (with_dates["low"], with_dates["high"]) == (plain["low"], plain["high"])
    assert with_dates["verdict"] == plain["verdict"]
    assert with_dates["tested_rule"] is True and plain["tested_rule"] is True


# --------------------------------------- T2 E101 live in the record's row

def _usd_move(monkeypatch, pct: float) -> float:
    """USD/INR "now" = the base year-end rate × (1 + pct/100)."""
    base = S.usdinr_at("2026-03-31")
    assert base, "the committed USD-INR file reaches the FY26 year end"
    now = round(base[0] * (1 + pct / 100.0), 4)
    monkeypatch.setattr(cv, "_usdinr_now",
                        lambda today=None: (now, "2026-07-17", "test RBI reference rate"))
    return now


def test_t2_an_it_route_with_a_rupee_move_carries_e101_only_in_the_record_row(env, monkeypatch):
    _usd_move(monkeypatch, 6.0)
    cfg = state.cfg()
    closes = _tcs_closes()
    shown = cv.row_for("TCS.NS", cfg, None, closes=closes.copy())
    rec = cv.row_for("TCS.NS", cfg, None, closes=closes.copy(), tested_rule=True)
    assert rec["route"]["sector"].startswith("IT services")
    assert rec["tested_rule"] is True and "tested_rule" not in shown
    assert rec["assumptions"]["live_inputs"]["usdinr"] is True
    assert shown["assumptions"]["live_inputs"]["usdinr"] is False
    # the rupee row lowers the costs → the base EPS and the range move
    assert rec["scenarios"]["eps"]["base"] != pytest.approx(shown["scenarios"]["eps"]["base"])
    assert rec["range"]["base"] != pytest.approx(shown["range"]["base"])
    # the display row still says E101 is replaced; the record row does not
    assert shown["fx"]["e101_replaced"] is True and rec["fx"]["e101_replaced"] is False


def test_t2_with_no_move_and_no_selection_date_in_play_the_two_rows_agree(env, monkeypatch):
    """The flag changes exactly two things: E101 on IT routes and where the
    bridge stops. With USD/INR flat and the stored index ending before the
    selection date (so both read to the same close), the rows are equal."""
    _usd_move(monkeypatch, 0.0)
    real = cv._index_series

    def short_index(field):
        s = real(field)
        return None if s is None else s[s.index <= "2026-05-08"].copy()
    monkeypatch.setattr(cv, "_index_series", short_index)
    cfg = state.cfg()
    closes = _tcs_closes()
    shown = cv.row_for("TCS.NS", cfg, None, closes=closes.copy())
    rec = cv.row_for("TCS.NS", cfg, None, closes=closes.copy(), tested_rule=True)
    assert shown["macro_inputs"]["index_end_is_selection"] is False
    assert shown["macro_inputs"]["index_end_date"] == rec["macro_inputs"]["index_end_date"]
    assert rec["range"] == shown["range"]
    assert rec["scenarios"] == shown["scenarios"]
    assert rec["verdict"] == shown["verdict"]


# ---------------------------------------- T3 the study's sector index map

def _row(ticker: str, tested: bool = True) -> dict:
    out = {"ticker": ticker, "version": "2026-10-21", "price": 1000.0,
           "price_date": "2026-10-20", "drafted": False, "price_source": "store",
           "route": {"method": "pe", "family": "earnings"},
           "range": {"status": "OK", "low": 900.0, "base": 1200.0, "high": 1400.0,
                     "family": "earnings"},
           "verdict": {"verdict": "attractive"}}
    if tested:
        out["tested_rule"] = True
    return out


def _remapped() -> list:
    with open(ROOT / "data" / "remapped_names.csv") as f:
        return [r["ticker"] for r in csv.DictReader(f)]


def test_t3_the_record_reads_the_studys_map_never_the_remap():
    cfg = state.cfg()
    sm = dict(cfg.sector_map)
    # a name with no app label that the batch-3 remap moved to Nifty IT
    assert "CYIENT.NS" not in sm and "CYIENT.NS" in _remapped()
    assert vv.bench_for("CYIENT.NS", cfg)[0] == "IT"
    r = feed.record_from_row("CYIENT.NS", _row("CYIENT.NS"), None, None, cfg)
    assert r["sector_index"] == VE.MARKET_SECTOR == "Nifty 50"
    assert vv.bench_for_tested("CYIENT.NS", cfg)[0] is None
    # an app label that got its index the same day (Chemicals) stays on Nifty 50
    assert sm.get("SRF.NS") == "Chemicals" and vv.bench_for("SRF.NS", cfg)[0] == "Chemicals"
    assert feed.record_from_row("SRF.NS", _row("SRF.NS"), None, None, cfg)["sector_index"] \
        == "Nifty 50"
    # the override and an indexed app label read the same in both maps
    for t, idx in (("DLF.NS", "Realty"), ("TCS.NS", "IT")):
        assert vv.bench_for(t, cfg)[0] == idx
        assert feed.record_from_row(t, _row(t), None, None, cfg)["sector_index"] == idx
    # the engine's re-mark reads an unmapped record on the Nifty
    assert VE.leg_for(r["sector_index"]) == ("nifty", None)


def test_t3_bench_for_tested_is_the_studys_map_for_every_name():
    cfg = state.cfg()
    sm = dict(cfg.sector_map)

    def study(t):
        if t in STUDY_OVERRIDE:
            return STUDY_OVERRIDE[t]
        return STUDY_LABEL_INDEX.get(sm.get(t, ""))
    names = set(sm) | set(STUDY_OVERRIDE) | set(_remapped())
    moved = 0
    for t in sorted(names):
        assert vv.bench_for_tested(t, cfg)[0] == study(t), t
        moved += vv.bench_for(t, cfg)[0] != study(t)
    assert moved >= 132          # the remap really differs — the pin is not vacuous


# ----------------------------------------- T4 the precompute asks for it

def test_t4_the_precompute_builds_every_record_under_the_tested_rule(env, monkeypatch):
    seen: list = []

    def row_for(t, cfg, p, **kw):
        seen.append(dict(kw))
        out = _row(t, tested=bool(kw.get("tested_rule")))
        return out
    monkeypatch.setattr(cv, "row_for", row_for)
    monkeypatch.setattr(feed, "precompute_scope", lambda p, cap: (["TCS.NS", "CYIENT.NS"], []))
    monkeypatch.setattr(cv, "_prefetch_closes", lambda names: {})
    monkeypatch.setattr(cv, "_zip_close_series", lambda t: None)
    res = SCH.run_valuation_precompute("test")
    assert res["status"] == "ok"
    assert [k.get("tested_rule") for k in seen] == [True, True]
    rows = env[feed.DOC_ID]["rows"]
    for t in ("TCS.NS", "CYIENT.NS"):
        assert rows[t]["tested_rule"] is True
        assert rows[t]["rule"] == feed.RECORD_RULE == "tested-2026-10-21"
        assert "valued as the pre-registered study valued it" in rows[t]["note"]
    assert rows["CYIENT.NS"]["sector_index"] == "Nifty 50"


def test_t4_a_row_built_without_the_rule_says_so():
    r = feed.record_from_row("TCS.NS", _row("TCS.NS", tested=False), None, None, state.cfg())
    assert r["tested_rule"] is False and r["rule"] == "tested-2026-10-21"
    assert "NOT valued by the tested rule" in r["note"]


# --------------------------------------- T5 the display row is unchanged

def test_t5_the_display_row_still_ends_the_bridge_at_the_selection_date(env):
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    m = rec["macro_inputs"]
    assert m["index_end_date"] == "2026-05-15" and m["index_end_is_selection"] is True
    assert rec["fx"] == {"applies": False, "e101_replaced": True}
    assert rec["assumptions"]["live_inputs"]["usdinr"] is False
    assert "tested_rule" not in rec


def test_t5_the_record_row_reads_the_bridge_to_the_newest_close(env):
    t = cv.row_for("TCS.NS", state.cfg(), None, tested_rule=True)
    nifty = cv._index_series("nifty_close").dropna()
    assert t["macro_inputs"]["index_end_date"] == nifty.index[-1].date().isoformat()
    assert t["macro_inputs"]["index_end_is_selection"] is False
