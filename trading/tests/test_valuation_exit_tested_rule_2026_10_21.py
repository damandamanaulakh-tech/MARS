"""Batch-3 review (2026-10-21): pipeline.evaluate(e101_it_rupee=True) — the
rupee row E101 live for IT-services routes, the batch-2 branch the item-19
pre-registered study measured. The exit RECORD is built with it
(web/valuation_chain_view.row_for(tested_rule=True)); the §12 display keeps
batch 3 (E101 counted 0, the currency leg through EPS).

Pins (fail on 5f08aa3, where evaluate has no such keyword):
  * an IT-services route with a USD/INR move: E101 = ROUND((now / base − 1)
    × 100, 2) — the batch-2 formula — the base EPS and the range move, and
    live_inputs says the rate was fed; the default call keeps E101 at 0;
  * IT only: another route with the same move keeps E101 at 0;
  * an IT route missing a rate says "USDINR now/base — rupee leg counted 0"
    (the batch-2 line), and only under the flag;
  * the ASSUMED entry keeps its value [] and names the exit record's rule.
"""
from __future__ import annotations


from trading.config import Config
from trading.valuation import pipeline as P
from trading.valuation import scenarios as SC
from trading.valuation import snapshot as S

BASE_FX = S.usdinr_at("2026-03-31")
_REAL_THREE_CASES = SC.three_cases          # the spy always calls the real one


def _macro(now_pct: float = 6.0, with_now: bool = True):
    def mac(snap):
        out = {"sensex_start": 73000.0, "sensex_end": 76000.0,
               "nifty_start": 22100.0, "nifty_end": 23000.0,
               "rf_pct": 6.9, "beta": 0.9, "vix": 13.0,
               "usdinr_base": BASE_FX[0],
               "usdinr_now": round(BASE_FX[0] * (1 + now_pct / 100.0), 4) if with_now
               else None}
        return out
    return mac


def _peers(method, exclude=""):
    return {"n": 3, "pe_median": 24.0, "pb_median": 2.0, "ev_ebitda_median": 9.0,
            "ev_sales_median": 2.5, "source": "test peers"}


def _run(ticker: str, monkeypatch, **kw):
    seen: dict = {}

    def spy(base, drivers, market, assumptions=None, deltas=None):
        out = _REAL_THREE_CASES(base, drivers, market, assumptions=assumptions,
                                deltas=deltas)
        seen["cases"] = out
        seen["market"] = dict(market)
        return out
    monkeypatch.setattr(P.SC, "three_cases", spy)
    rec = P.evaluate(ticker, price=3000.0, price_date="2026-07-17",
                     sector_map=dict(Config().sector_map),
                     price_at=lambda when: 3000.0, peers=_peers,
                     macro=kw.pop("macro", _macro()), asof="2026-07-17", **kw)
    return rec, seen


def _e101(seen: dict) -> float:
    return seen["cases"]["base"]["cells"]["E101"]


def test_an_it_route_reads_e101_the_batch_2_way_only_under_the_flag(monkeypatch):
    assert BASE_FX, "the committed USD-INR file reaches the FY26 year end"
    shown, s0 = _run("TCS", monkeypatch)
    rec, s1 = _run("TCS", monkeypatch, e101_it_rupee=True)
    assert rec["route"]["sector"].startswith("IT services")
    now = round(BASE_FX[0] * 1.06, 4)
    # batch 3 (default): the row is counted 0
    assert _e101(s0) == 0.0 and s0["market"]["usdinr"] == s0["market"]["usdinr_base"]
    assert shown["assumptions"]["live_inputs"]["usdinr"] is False
    # the record's rule: the batch-2 branch — now / base at the year end
    assert (s1["market"]["usdinr"], s1["market"]["usdinr_base"]) == (now, BASE_FX[0])
    assert _e101(s1) == SC.xround((now / BASE_FX[0] - 1) * 100, 2) != 0.0
    assert rec["assumptions"]["live_inputs"]["usdinr"] is True
    # a weaker rupee lowers the costs (E114) → a higher EPS → a higher range
    assert s1["cases"]["base"]["cells"]["E114"] != 0.0
    assert rec["scenarios"]["eps"]["base"] > shown["scenarios"]["eps"]["base"]
    assert rec["range"]["base"] > shown["range"]["base"]
    # nothing else moved: the same route, snapshot and peers
    assert rec["route"] == shown["route"] and rec["snapshot"] == shown["snapshot"]


def test_the_rupee_row_stays_it_only_under_the_flag(monkeypatch):
    rec, s1 = _run("MARUTI", monkeypatch, e101_it_rupee=True)
    shown, s0 = _run("MARUTI", monkeypatch)
    assert not rec["route"]["sector"].startswith("IT services")
    assert _e101(s1) == 0.0 == _e101(s0)
    assert rec["assumptions"]["live_inputs"]["usdinr"] is False
    assert rec["range"]["base"] == shown["range"]["base"]


def test_a_missing_rate_on_an_it_route_is_said_only_under_the_flag(monkeypatch):
    rec, s1 = _run("TCS", monkeypatch, e101_it_rupee=True, macro=_macro(with_now=False))
    shown, _s0 = _run("TCS", monkeypatch, macro=_macro(with_now=False))
    line = "USDINR now/base — rupee leg counted 0"
    assert line in rec["scenarios"]["macro_data_needed"]
    assert line not in shown["scenarios"]["macro_data_needed"]
    assert _e101(s1) == 0.0
    assert rec["assumptions"]["live_inputs"]["usdinr"] is False


def test_the_assumed_entry_names_the_exit_records_rule():
    e = P.ASSUMED["currency_effect_routes"]
    assert e["value"] == []
    src = " ".join(e["source"].split())
    assert "the item-19 exit record is built with e101_it_rupee=True" in src
    assert "the rule the pre-registered study measured" in src


def test_live_inputs_reads_the_rate_the_batch_2_way_under_the_flag():
    it = {"sector": "IT services / ER&D"}
    both = {"usdinr_now": 95.0, "usdinr_base": 90.0}
    assert P.live_inputs(both, it, {}, True)["usdinr"] is False
    assert P.live_inputs(both, it, {}, True, e101_it_rupee=True)["usdinr"] is True
    assert P.live_inputs({"usdinr_now": 95.0}, it, {}, True,
                         e101_it_rupee=True)["usdinr"] is False
    assert P.live_inputs(both, {"sector": "Auto / auto components"}, {}, True,
                         e101_it_rupee=True)["usdinr"] is False
