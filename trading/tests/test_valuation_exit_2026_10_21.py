"""Item 19 — the valuation target as the EXIT target (owner order
2026-10-21, card answers "Wire A3" + "Exit as tested").

The rule was measured BEFORE wiring (pre-registered; TEST half C0 k x ATR
+5.46% / DD -4.11 vs A3 +5.68% / -4.18, 33% coverage). These tests pin the
live rule field for field against the study's harness:

  * judged = verdict attractive/watch AND range OK, base > fill x 1.05
    (records older than 35 days are not used — ASSUMED, the study used the
    latest month-end valuation before entry);
  * re-mark T_m = T_entry x (1 + beta x Sensex move) x (1 + (sector move -
    Sensex move)), beta None = 1; a re-marked target at or below the
    month-end close exits (VAL_TARGET_DOWN);
  * A3: a held k x ATR position whose record qualifies against its ENTRY
    price is re-targeted ONCE;
  * the engine: BUY, re-target, monthly re-mark once a month, the exit at
    the next in-session pass (only journaled outside hours), the owner's
    hand-typed target winning, the boot retag keeping a valuation target,
    persistence through to_dict / from_dict, the switch.

Live shapes modelled: Portfolio.positions is a dict keyed by ticker;
legacy positions carry a blank mode, a date-only entry_date and no
val_target.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading import valuation_exit as VE
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

MOM = "nifty200_momentum"


# ------------------------------------------------------------- fixtures

def _rec(base=130.0, verdict="attractive", asof="2026-10-19", status="OK",
         sector="IT", beta=1.2, **kw):
    r = {"ticker": "TCS.NS", "asof": asof, "verdict": verdict,
         "range_status": status, "base_target": base,
         "low": None if base is None else base * 0.8,
         "high": None if base is None else base * 1.2,
         "family": "earnings", "method": "pe",
         "sector_index": sector, "beta": beta, "version": "2026-10-14",
         "drafted": False, "note": "test"}
    r.update(kw)
    return r


class _Levels:
    """A level reader with the feed's contract: close on or before `when`,
    None while the series has not reached that day; when=None = latest."""

    def __init__(self, series: dict):
        self.series = series            # {(kind, name): pd.Series}
        self.calls = []

    def __call__(self, kind, name=None, when=None):
        self.calls.append((kind, name, when))
        s = self.series.get((kind, name))
        if s is None or not len(s):
            return None
        if when is None:
            return float(s.iloc[-1])
        ts = pd.Timestamp(when)
        if s.index[-1] < ts:
            return None
        part = s[s.index <= ts]
        return float(part.iloc[-1]) if len(part) else None


def _ser(points: dict) -> pd.Series:
    return pd.Series({pd.Timestamp(k): float(v) for k, v in points.items()}).sort_index()


@pytest.fixture
def readers(monkeypatch):
    """Attach fake readers (restored after the test)."""
    store = {"records": {}}
    lv = _Levels({})
    monkeypatch.setattr(VE, "_RECORD_READER",
                        lambda tickers: {t: store["records"][t] for t in tickers
                                         if t in store["records"]})
    monkeypatch.setattr(VE, "_LEVEL_READER", lv)
    store["levels"] = lv
    return store


def _bars(atr_pct=0.025, n=80, price=100.0, end="2026-10-19"):
    idx = pd.bdate_range(end=end, periods=n)
    c = np.full(n, price)
    half = price * atr_pct / 2.0
    return pd.DataFrame({"Open": c, "High": c + half, "Low": c - half,
                         "Close": c, "Volume": np.full(n, 1e6)}, index=idx)


def _engine(tmp_path, mode=MOM):
    cfg = Config(state_dir=str(tmp_path), mode=mode)
    cfg.apply_mode_preset()
    return TradingEngine(cfg, Portfolio.fresh(cfg))


def _pick(ticker="TCS.NS", df=None):
    return {"ticker": ticker, "score": 70.0, "reasoning": "fixture",
            "history": df if df is not None else _bars(), "components": {}}


NOW_BUY = datetime(2026, 10, 21, 4, 0)       # 09:30 IST, Wed 2026-10-21


def _journal(e, action):
    return [x for x in e.portfolio.decision_journal if x.get("action") == action]


# ------------------------------------------------------------- the rule

def test_the_measured_constants_are_the_studys():
    assert VE.VALUATION_EXIT_MARGIN == 1.05
    assert VE.JUDGED == ("attractive", "watch")
    assert VE.RECORD_MAX_AGE_DAYS == 35
    assert Config().valuation_exit_enabled is True


def test_a_judged_record_above_the_margin_sets_the_base():
    t, d = VE.target_from_record(_rec(base=130.0), 100.0, date(2026, 10, 21))
    assert t == 130.0 and d["reason"] == "judged"
    assert d["asof"] == "2026-10-19" and d["verdict"] == "attractive"
    assert d["upside_pct"] == pytest.approx(30.0)
    t, d = VE.target_from_record(_rec(base=110.0, verdict="watch"), 100.0,
                                 date(2026, 10, 21))
    assert t == 110.0 and d["verdict"] == "watch"


def test_the_margin_is_strictly_above_fill_times_1_05():
    t, d = VE.target_from_record(_rec(base=105.0), 100.0, date(2026, 10, 21))
    assert t is None and d["reason"] == "below_margin"
    t, _ = VE.target_from_record(_rec(base=105.01), 100.0, date(2026, 10, 21))
    assert t == 105.01


def test_stale_unjudged_disabled_and_missing_records_never_set_a_target():
    today = date(2026, 10, 21)
    assert VE.target_from_record(None, 100.0, today)[1]["reason"] == "no_record"
    assert VE.target_from_record(_rec(), 100.0, today, enabled=False)[1]["reason"] == "disabled"
    old = (today - timedelta(days=36)).isoformat()
    assert VE.target_from_record(_rec(asof=old), 100.0, today)[1]["reason"] == "stale:36"
    edge = (today - timedelta(days=35)).isoformat()
    assert VE.target_from_record(_rec(asof=edge), 100.0, today)[0] == 130.0
    for v in ("unattractive", "insufficient"):
        t, d = VE.target_from_record(_rec(verdict=v), 100.0, today)
        assert t is None and d["reason"] == f"not_judged:{v}"
    t, d = VE.target_from_record(_rec(status="DATA_NEEDED"), 100.0, today)
    assert t is None and d["reason"] == "not_judged:range_DATA_NEEDED"
    t, d = VE.target_from_record(_rec(base=None), 100.0, today)
    assert t is None and d["reason"] == "not_judged:no_base"
    t, d = VE.target_from_record(_rec(drafted=True), 100.0, today)
    assert t is None and d["reason"] == "drafted"


def test_the_remark_formula_reproduces_the_study():
    # T_entry 100, beta 1.2, Sensex +10%, sector +15% -> 100 x 1.12 x 1.05
    assert VE.remarked_target(100, 1000, 1100, 500, 575, 1.2) == pytest.approx(117.60)
    # beta None = 1 (labelled at the call site): 100 x 1.10 x 1.05
    assert VE.remarked_target(100, 1000, 1100, 500, 575, None) == pytest.approx(115.50)
    assert VE.remarked_target(100, 1000, None, 500, 575, 1.2) is None
    assert VE.remarked_target(100, 0, 1100, 500, 575, 1.2) is None


def test_val_target_down_is_target_at_or_below_the_month_end_close():
    assert VE.target_down(99.0, 100.0) and VE.target_down(100.0, 100.0)
    assert not VE.target_down(100.01, 100.0)
    assert not VE.target_down(None, 100.0) and not VE.target_down(99.0, None)


def test_a_month_closes_once_and_its_end_is_the_last_nse_trading_day():
    assert VE.month_closed(date(2026, 11, 2), None) == "2026-10"
    assert VE.month_closed(date(2026, 11, 2), "2026-10") is None
    assert VE.month_closed(date(2026, 11, 2), "2026-09") == "2026-10"
    assert VE.month_closed(date(2027, 1, 4), "2026-11") == "2026-12"
    assert VE.month_end("2026-10") == date(2026, 10, 30)     # 31st is a Saturday
    assert VE.month_end("2026-03") == date(2026, 3, 30)      # 31st Mahavir Jayanti
    assert VE.month_end("bad") is None


# ------------------------------------------------------------ BUY (A1)

def test_a_judged_buy_takes_the_valuation_base_and_the_journal_names_it(tmp_path, readers):
    readers["records"]["TCS.NS"] = _rec(base=130.0)
    readers["levels"].series = {("sensex", None): _ser({"2026-10-20": 81000}),
                                ("sector", "IT"): _ser({"2026-10-20": 36000})}
    e = _engine(tmp_path)
    e._open(_pick(), 100, 100.0, NOW_BUY)
    pos = e.portfolio.positions["TCS.NS"]
    assert pos.target_price == 130.0
    vt = pos.val_target
    assert vt["source"] == "valuation" and vt["base"] == 130.0 and vt["t_entry"] == 130.0
    assert vt["asof"] == "2026-10-19" and vt["verdict"] == "attractive"
    assert vt["sensex_entry"] == 81000.0 and vt["sector_entry"] == 36000.0
    assert vt["sector_index"] == "IT" and vt["beta"] == 1.2
    assert vt["entry_fill_date"] == "2026-10-21" == vt["anchor_date"]
    assert vt["remarks"] == [] and vt["exit_due"] is False
    # what k x ATR would have been: 8 x 2.5% = +20%
    assert vt["k_atr_target"] == pytest.approx(120.0)
    buy = _journal(e, "BUY")[-1]["target"]
    assert buy["price"] == 130.0 and buy["source"] == "valuation"
    assert buy["base"] == 130.0 and buy["asof"] == "2026-10-19"
    assert buy["verdict"] == "attractive" and buy["family"] == "earnings"
    assert buy["k_atr_target"] == pytest.approx(120.0)


@pytest.mark.parametrize("rec,reason", [
    (_rec(verdict="unattractive"), "not_judged:unattractive"),
    (_rec(asof="2026-09-01"), "stale:50"),
    (_rec(base=104.0), "below_margin"),
])
def test_an_unqualified_record_keeps_k_atr_with_the_reason(tmp_path, readers, rec, reason):
    readers["records"]["TCS.NS"] = rec
    e = _engine(tmp_path)
    e._open(_pick(), 100, 100.0, NOW_BUY)
    pos = e.portfolio.positions["TCS.NS"]
    assert pos.target_price == pytest.approx(120.0)            # k x ATR as before
    assert pos.val_target == {"source": "k_atr", "reason": reason,
                              "k_atr_target": pytest.approx(120.0)}
    buy = _journal(e, "BUY")[-1]["target"]
    assert buy["source"] == "atr" and buy["valuation"] == reason


def test_no_reader_and_the_switch_off_keep_k_atr(tmp_path, monkeypatch, readers):
    readers["records"]["TCS.NS"] = _rec(base=130.0)
    e = _engine(tmp_path)
    e.cfg.valuation_exit_enabled = False
    e._open(_pick(), 100, 100.0, NOW_BUY)
    assert e.portfolio.positions["TCS.NS"].target_price == pytest.approx(120.0)
    assert e.portfolio.positions["TCS.NS"].val_target["reason"] == "disabled"
    monkeypatch.setattr(VE, "_LEVEL_READER", None)
    e2 = _engine(tmp_path / "b")
    e2._open(_pick(), 100, 100.0, NOW_BUY)
    assert e2.portfolio.positions["TCS.NS"].target_price == pytest.approx(120.0)
    assert e2.portfolio.positions["TCS.NS"].val_target["reason"] == "no_reader"


def test_an_intraday_buy_is_never_valuation_targeted(tmp_path, readers):
    readers["records"]["TCS.NS"] = _rec(base=130.0)
    e = _engine(tmp_path, mode="intraday")
    e._open(_pick(), 100, 100.0, NOW_BUY)
    assert e.portfolio.positions["TCS.NS"].val_target["reason"] == "intraday"
    assert e.portfolio.positions["TCS.NS"].target_price < 130.0


def test_the_owners_hand_typed_target_wins_and_is_never_moved(tmp_path, readers):
    from trading.orders import apply_custom_levels
    readers["records"]["TCS.NS"] = _rec(base=130.0)
    e = _engine(tmp_path)
    e._open(_pick(), 100, 100.0, NOW_BUY)
    pos = e.portfolio.positions["TCS.NS"]
    apply_custom_levels(pos, None, 12)
    assert pos.target_price == 112.0
    assert pos.val_target["source"] == "owner" and pos.val_target["replaced"] == "valuation"
    readers["levels"].series = {("sensex", None): _ser({"2026-10-21": 100, "2026-10-30": 50})}
    e._valuation_pass({"TCS.NS": 100.0}, {}, datetime(2026, 11, 2, 4, 0))
    assert pos.target_price == 112.0
    assert not _journal(e, "VALUATION_REMARK") and not _journal(e, "VALUATION_RETARGET")


# --------------------------------------------------------- A3 re-target

def _legacy(ticker="INFY.NS", entry=100.0, target=120.0, mode="", **kw):
    """An open position as the live book holds it: blank legacy mode,
    a date-only entry_date, no val_target."""
    d = {"ticker": ticker, "qty": 100, "entry_price": entry,
         "entry_date": "2026-09-01", "sector": "IT", "peak_price": entry,
         "entry_score": 70.0, "reasoning": "old buy", "mode": mode,
         "stop_price": entry * 0.95, "target_price": target}
    d.update(kw)
    return PaperPosition.from_dict(d)


def test_a3_retargets_a_held_k_atr_position_exactly_once(tmp_path, readers):
    readers["records"]["INFY.NS"] = _rec(base=150.0, ticker="INFY.NS")
    readers["levels"].series = {("sensex", None): _ser({"2026-10-20": 81000}),
                                ("sector", "IT"): _ser({"2026-10-20": 36000})}
    e = _engine(tmp_path)
    e.portfolio.positions["INFY.NS"] = _legacy()
    assert e.portfolio.positions["INFY.NS"].val_target == {}
    e._valuation_pass({"INFY.NS": 118.0}, {}, NOW_BUY)
    pos = e.portfolio.positions["INFY.NS"]
    assert pos.target_price == 150.0
    vt = pos.val_target
    assert vt["source"] == "retarget" and vt["retargeted"] is True
    assert vt["old_target"] == 120.0 and vt["t_entry"] == 150.0
    assert vt["anchor_date"] == "2026-10-21" == vt["retarget_date"]
    assert vt["entry_fill_date"] == "2026-09-01"
    j = _journal(e, "VALUATION_RETARGET")
    assert len(j) == 1 and j[0]["old_target"] == 120.0 and j[0]["new_target"] == 150.0
    assert j[0]["verdict"] == "attractive" and j[0]["asof"] == "2026-10-19"
    # a later, higher record never re-targets it again
    readers["records"]["INFY.NS"] = _rec(base=200.0, ticker="INFY.NS")
    e._valuation_pass({"INFY.NS": 118.0}, {}, NOW_BUY + timedelta(minutes=30))
    assert pos.target_price == 150.0 and len(_journal(e, "VALUATION_RETARGET")) == 1


def test_a3_measures_the_margin_against_the_entry_price(tmp_path, readers):
    # base 104 < entry 100 x 1.05: not re-targeted even with the price at 90
    readers["records"]["INFY.NS"] = _rec(base=104.0, ticker="INFY.NS")
    e = _engine(tmp_path)
    e.portfolio.positions["INFY.NS"] = _legacy()
    e._valuation_pass({"INFY.NS": 90.0}, {}, NOW_BUY)
    assert e.portfolio.positions["INFY.NS"].target_price == 120.0
    assert not _journal(e, "VALUATION_RETARGET")


def test_a3_leaves_hand_set_and_intraday_positions_alone(tmp_path, readers):
    readers["records"]["INFY.NS"] = _rec(base=150.0, ticker="INFY.NS")
    readers["records"]["WIPRO.NS"] = _rec(base=150.0, ticker="WIPRO.NS")
    e = _engine(tmp_path)
    e.portfolio.positions["INFY.NS"] = _legacy(custom_levels=True)
    e.portfolio.positions["WIPRO.NS"] = _legacy("WIPRO.NS", mode="intraday")
    # priced inside the envelope, so only the exclusions can stop it
    e._valuation_pass({"INFY.NS": 118.0, "WIPRO.NS": 118.0}, {}, NOW_BUY)
    assert e.portfolio.positions["INFY.NS"].target_price == 120.0
    assert e.portfolio.positions["WIPRO.NS"].target_price == 120.0
    assert not _journal(e, "VALUATION_RETARGET")


def test_the_switch_off_retargets_and_remarks_nothing(tmp_path, readers):
    readers["records"]["INFY.NS"] = _rec(base=150.0, ticker="INFY.NS")
    e = _engine(tmp_path)
    e.cfg.valuation_exit_enabled = False
    e.portfolio.positions["INFY.NS"] = _legacy()
    held = _val_pos()
    e.portfolio.positions[held.ticker] = held
    e._valuation_pass({"INFY.NS": 118.0, held.ticker: 101.0}, _hist(100.0),
                      datetime(2026, 11, 2, 4, 0))
    assert e.portfolio.positions["INFY.NS"].target_price == 120.0
    assert held.target_price == 120.0 and held.val_target["remarks"] == []


# ------------------------------------------------------ A2 monthly re-mark

def _val_pos(ticker="TCS.NS", t_entry=120.0, anchor="2026-10-05", beta=1.2,
             sector="IT", sensex_entry=990.0, sector_entry=490.0):
    pos = _legacy(ticker, entry=100.0, target=t_entry, mode=MOM)
    pos.val_target = {"source": "valuation", "base": t_entry, "asof": "2026-10-02",
                      "verdict": "attractive", "family": "earnings",
                      "t_entry": t_entry, "k_atr_target": 120.0,
                      "sensex_entry": sensex_entry, "sector_entry": sector_entry,
                      "sector_index": sector, "beta": beta,
                      "entry_fill_date": anchor, "anchor_date": anchor,
                      "last_remark_month": None, "remarks": [], "exit_due": False}
    return pos


def _month_levels():
    return {("sensex", None): _ser({"2026-10-05": 1000, "2026-10-30": 1100,
                                    "2026-11-02": 1105}),
            ("sector", "IT"): _ser({"2026-10-05": 500, "2026-10-30": 575,
                                    "2026-11-02": 580}),
            ("nifty", None): _ser({"2026-10-05": 200, "2026-10-30": 210,
                                   "2026-11-02": 211})}


def _hist(close_at_me: float, end="2026-11-02"):
    idx = pd.bdate_range(end=end, periods=30)
    c = pd.Series(100.0, index=idx)
    c[pd.Timestamp("2026-10-30")] = close_at_me
    return {"TCS.NS": pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c,
                                    "Volume": 1e6})}


NOW_REMARK = datetime(2026, 11, 2, 4, 0)      # 09:30 IST, first trading day of November


def test_a_closed_month_remarks_once_with_the_anchor_days_closes(tmp_path, readers):
    readers["levels"].series = _month_levels()
    e = _engine(tmp_path)
    pos = _val_pos()
    e.portfolio.positions["TCS.NS"] = pos
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK)
    # 120 x (1 + 1.2 x 10%) x (1 + (15% - 10%)) = 141.12 — measured from the
    # ANCHOR day's close (1000 / 500), not the BUY-time reading (990 / 490)
    assert pos.target_price == pytest.approx(141.12)
    vt = pos.val_target
    assert vt["last_remark_month"] == "2026-10" and vt["exit_due"] is False
    assert len(vt["remarks"]) == 1
    r = vt["remarks"][0]
    assert r["month"] == "2026-10" and r["month_end"] == "2026-10-30"
    assert r["sensex_move"] == pytest.approx(0.10) and r["sector_move"] == pytest.approx(0.15)
    assert r["target"] == pytest.approx(141.12) and r["close"] == 100.0
    assert r["sector_leg"] == "IT"
    j = _journal(e, "VALUATION_REMARK")
    assert len(j) == 1 and j[0]["new_target"] == pytest.approx(141.12)
    assert j[0]["close_source"] == "history" and j[0]["exit_due"] is False
    # the same month never re-marks twice
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK + timedelta(hours=1))
    assert len(_journal(e, "VALUATION_REMARK")) == 1
    assert pos.target_price == pytest.approx(141.12)
    # and it survives the JSON round trip (Mongo mirror: web test)
    back = PaperPosition.from_dict(pos.to_dict())
    assert back.val_target == vt and back.target_price == pytest.approx(141.12)


def test_beta_none_reads_one_and_says_so(tmp_path, readers):
    readers["levels"].series = _month_levels()
    e = _engine(tmp_path)
    pos = _val_pos(beta=None)
    e.portfolio.positions["TCS.NS"] = pos
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK)
    assert pos.target_price == pytest.approx(120 * 1.10 * 1.05)
    j = _journal(e, "VALUATION_REMARK")[-1]
    assert j["beta"] == 1.0 and j["beta_source"] == "fallback_1"


def test_a_remark_at_or_below_the_month_end_close_flags_val_target_down(tmp_path, readers):
    lv = _month_levels()
    lv[("sensex", None)] = _ser({"2026-10-05": 1000, "2026-10-30": 800, "2026-11-02": 790})
    lv[("sector", "IT")] = _ser({"2026-10-05": 500, "2026-10-30": 400, "2026-11-02": 398})
    readers["levels"].series = lv
    e = _engine(tmp_path)
    pos = _val_pos()
    e.portfolio.positions["TCS.NS"] = pos
    # 120 x (1 - 1.2 x 20%) x (1 + 0) = 91.2 <= month-end close 95
    e._valuation_pass({"TCS.NS": 96.0}, _hist(95.0), NOW_REMARK)
    assert pos.target_price == pytest.approx(91.2)
    assert pos.val_target["exit_due"] is True
    j = _journal(e, "VALUATION_REMARK")[-1]
    assert j["exit_due"] is True and j["close"] == 95.0


def test_a_position_opened_after_the_month_end_is_not_remarked(tmp_path, readers):
    readers["levels"].series = _month_levels()
    e = _engine(tmp_path)
    pos = _val_pos(anchor="2026-10-30")
    e.portfolio.positions["TCS.NS"] = pos
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK)
    assert pos.target_price == 120.0 and pos.val_target["remarks"] == []
    assert pos.val_target["last_remark_month"] == "2026-10"
    assert not _journal(e, "VALUATION_REMARK")


def test_a_month_end_close_not_stored_yet_waits_and_retries(tmp_path, readers):
    lv = _month_levels()
    lv[("sensex", None)] = _ser({"2026-10-05": 1000, "2026-10-29": 1090})
    readers["levels"].series = lv
    e = _engine(tmp_path)
    pos = _val_pos()
    e.portfolio.positions["TCS.NS"] = pos
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK)
    assert pos.target_price == 120.0 and pos.val_target["last_remark_month"] is None
    assert len(_journal(e, "VALUATION_REMARK_WAITING")) == 1
    lv[("sensex", None)] = _ser({"2026-10-05": 1000, "2026-10-30": 1100})
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK + timedelta(hours=1))
    assert pos.target_price == pytest.approx(141.12)


def test_a_sector_index_not_stored_falls_back_to_nifty_and_says_so(tmp_path, readers):
    lv = _month_levels()
    del lv[("sector", "IT")]
    readers["levels"].series = lv
    e = _engine(tmp_path)
    pos = _val_pos()
    e.portfolio.positions["TCS.NS"] = pos
    e._valuation_pass({"TCS.NS": 101.0}, _hist(100.0), NOW_REMARK)
    # Nifty +5%: 120 x 1.12 x (1 + 0.05 - 0.10)
    assert pos.target_price == pytest.approx(120 * 1.12 * 0.95)
    assert "Nifty 50" in pos.val_target["remarks"][0]["sector_leg"]


# ------------------------------------------------ the VAL_TARGET_DOWN exit

def _refresh_engine(tmp_path, monkeypatch, readers, price=110.0):
    e = _engine(tmp_path)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    rng = np.random.default_rng(3)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 300))
    close = close * (price / close[-1])
    frame = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                          "Close": close, "Volume": np.full(300, 5e6)}, index=idx)
    frames = {"TCS.NS": frame, e.cfg.benchmark: frame.copy()}
    monkeypatch.setattr("trading.engine.fetch_daily",
                        lambda tickers, period="6mo": {t: frames[t].copy()
                                                       for t in ([tickers] if isinstance(tickers, str) else tickers)
                                                       if t in frames})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None: {t: price for t in tickers if t == "TCS.NS"})
    e.cfg.danger_layer_enabled = False
    pos = _val_pos(t_entry=200.0)
    # dated from today: a fixed entry date turns into a MAX_HOLD exit later
    pos.entry_date = datetime.utcnow() - timedelta(days=30)
    pos.stop_price = 80.0
    pos.target_price = 200.0
    ist_today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    pos.val_target["last_remark_month"] = VE.month_closed(ist_today, None)
    e.portfolio.positions["TCS.NS"] = pos
    e.portfolio.cash -= pos.qty * pos.entry_price
    return e, pos


def test_val_target_down_fires_at_the_next_in_session_pass(tmp_path, monkeypatch, readers):
    e, pos = _refresh_engine(tmp_path, monkeypatch, readers)
    e.refresh()
    assert "TCS.NS" in e.portfolio.positions          # nothing due yet
    pos.val_target["exit_due"] = True
    e.refresh()
    assert "TCS.NS" not in e.portfolio.positions
    assert e.portfolio.trades[-1].reason == "VAL_TARGET_DOWN"
    assert e.portfolio.trades[-1].exit_price == pytest.approx(110.0, rel=0.01)


def test_val_target_down_only_journals_while_the_market_is_closed(tmp_path, monkeypatch, readers):
    e, pos = _refresh_engine(tmp_path, monkeypatch, readers)
    pos.val_target["exit_due"] = True
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: False)
    e.refresh()
    assert "TCS.NS" in e.portfolio.positions
    due = [x for x in _journal(e, "EXIT_DUE_MARKET_CLOSED") if x.get("ticker") == "TCS.NS"]
    assert due and due[-1]["rule"] == "VAL_TARGET_DOWN"


def test_the_stop_keeps_its_priority_over_val_target_down(tmp_path, monkeypatch, readers):
    e, pos = _refresh_engine(tmp_path, monkeypatch, readers, price=79.0)
    pos.val_target["exit_due"] = True
    e.refresh()
    assert e.portfolio.trades[-1].reason == "STOP_LOSS"


def test_the_switch_off_does_not_fire_a_pending_val_target_down(tmp_path, monkeypatch, readers):
    e, pos = _refresh_engine(tmp_path, monkeypatch, readers)
    pos.val_target["exit_due"] = True
    e.cfg.valuation_exit_enabled = False
    e.refresh()
    assert "TCS.NS" in e.portfolio.positions
    assert pos.target_price == 200.0                  # the held target is kept


# ------------------------------------------------------ retag, persistence

def test_the_boot_retag_keeps_a_valuation_target_even_on_a_blank_tag(tmp_path):
    from trading.exits import retag_positions_per_mode
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    vpos = _legacy("TCS.NS", target=150.0)               # blank legacy mode
    vpos.val_target = {"source": "retarget", "base": 150.0}
    kpos = _legacy("INFY.NS", target=150.0)
    kpos.val_target = {"source": "k_atr", "reason": "no_record"}
    mpos = _legacy("SBIN.NS", target=150.0, mode=MOM)
    mpos.val_target = {"source": "valuation", "base": 150.0}
    p.positions = {"TCS.NS": vpos, "INFY.NS": kpos, "SBIN.NS": mpos}
    retag_positions_per_mode(p)
    assert vpos.target_price == 150.0 and mpos.target_price == 150.0
    assert kpos.target_price == pytest.approx(120.0)     # the preset, as before


def test_a_legacy_position_dict_reads_an_empty_val_target():
    pos = _legacy()
    assert pos.val_target == {}
    d = pos.to_dict()
    assert d["val_target"] == {}
    d.pop("val_target")
    assert PaperPosition.from_dict(d).val_target == {}


def test_the_backtest_says_it_does_not_model_the_valuation_exit():
    """Read from the string literals of run_backtest's warning list — a
    comment saying it would not count."""
    import ast
    import inspect
    import textwrap
    from trading import backtest
    tree = ast.parse(textwrap.dedent(inspect.getsource(backtest.run_backtest)))
    texts = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    hit = [t for t in texts if "The valuation exit target is not modelled" in t]
    assert hit and "LIVE-GATE GAP" in hit[0]


def test_a_val_target_down_exit_counts_in_the_momentum_record():
    """The measured-Kelly check and the loss halt read it as a strategy
    exit — an unknown reason would drop it from the record silently."""
    from trading.measured_kelly import classify
    assert classify("VAL_TARGET_DOWN") == "strategy"


# ------------------------------------------- review fixes (2026-10-21)

def test_buy_and_hold_never_fires_val_target_down(tmp_path, monkeypatch, readers):
    """disable_exits switches every target exit off; VAL_TARGET_DOWN is a
    target exit (the first cut fired it anyway)."""
    e, pos = _refresh_engine(tmp_path, monkeypatch, readers)
    pos.tags = ["owner_pick"]                  # no score-decay sale either
    pos.val_target["exit_due"] = True
    e.cfg.disable_exits = True
    e.refresh()
    assert "TCS.NS" in e.portfolio.positions
    assert not any(t.reason == "VAL_TARGET_DOWN" for t in e.portfolio.trades)


def test_one_bad_position_never_stops_the_other_remarks(tmp_path, readers):
    readers["levels"].series = _month_levels()
    e = _engine(tmp_path)
    bad = _val_pos("BAD.NS")
    bad.val_target["remarks"] = 5               # corrupt: not a list
    good = _val_pos("TCS.NS")
    e.portfolio.positions = {"BAD.NS": bad, "TCS.NS": good}
    hist = _hist(100.0)
    hist["BAD.NS"] = hist["TCS.NS"].copy()
    e._valuation_pass({"TCS.NS": 101.0, "BAD.NS": 101.0}, hist, NOW_REMARK)
    assert good.target_price == pytest.approx(141.12)
    assert good.val_target["last_remark_month"] == "2026-10"
    assert bad.val_target["last_remark_month"] is None      # retried next pass
    assert bad.target_price == 120.0                         # left untouched
    assert [j["ticker"] for j in _journal(e, "VALUATION_PASS_FAILED")] == ["BAD.NS"]


def test_one_bad_record_never_stops_the_other_retargets(tmp_path, readers):
    readers["records"]["BAD.NS"] = ["not", "a", "record"]
    readers["records"]["INFY.NS"] = _rec(base=150.0, ticker="INFY.NS")
    e = _engine(tmp_path)
    e.portfolio.positions = {"BAD.NS": _legacy("BAD.NS"), "INFY.NS": _legacy()}
    e._valuation_pass({"BAD.NS": 118.0, "INFY.NS": 118.0}, {}, NOW_BUY)
    assert e.portfolio.positions["INFY.NS"].target_price == 150.0
    assert e.portfolio.positions["BAD.NS"].target_price == 120.0
    assert e.portfolio.positions["BAD.NS"].val_target == {}
    assert len(_journal(e, "VALUATION_RETARGET")) == 1


# --------------------------------- skeptic review (2026-10-21): the envelope

def test_the_retarget_ceiling_mirrors_the_assess_watch_band():
    """valuation_exit may not import the pipeline, so the 1.10 is a copy:
    pinned to assess's default unattractive premium here."""
    from trading.valuation import assess
    prem = assess.ASSUMED["unattractive_premium_pct"]["value"]
    assert VE.RETARGET_PRICE_CEILING == pytest.approx(1 + prem / 100.0)


def test_a3_never_retargets_outside_the_same_day_judged_envelope(tmp_path, readers):
    """The study re-targeted on a verdict valued at THAT close: every
    same-day sale there sold at most 9.3% above base (TEST 12 of 72, TRAIN
    7 of 61). A record from an earlier close says nothing about a price
    above base x 1.10 or the range high — that would read unattractive."""
    readers["records"]["INFY.NS"] = _rec(base=150.0, ticker="INFY.NS")   # high 180
    e = _engine(tmp_path)
    e.portfolio.positions["INFY.NS"] = _legacy(target=200.0)
    e._valuation_pass({"INFY.NS": 166.0}, {}, NOW_BUY)                   # > 165
    assert e.portfolio.positions["INFY.NS"].target_price == 200.0
    assert e.portfolio.positions["INFY.NS"].val_target == {}
    assert not _journal(e, "VALUATION_RETARGET")
    # back inside the envelope on a later pass: re-targeted, and the journal
    # says it takes profit on that pass
    e._valuation_pass({"INFY.NS": 160.0}, {}, NOW_BUY + timedelta(minutes=30))
    assert e.portfolio.positions["INFY.NS"].target_price == 150.0
    j = _journal(e, "VALUATION_RETARGET")
    assert len(j) == 1 and j[0]["price"] == 160.0 and "takes profit" in j[0]["why"]
    # the range high binds when it sits under base x 1.10
    readers["records"]["WIPRO.NS"] = _rec(base=150.0, ticker="WIPRO.NS", high=155.0)
    e.portfolio.positions["WIPRO.NS"] = _legacy("WIPRO.NS", target=200.0)
    e._valuation_pass({"WIPRO.NS": 158.0}, {}, NOW_BUY)
    assert e.portfolio.positions["WIPRO.NS"].target_price == 200.0
    e._valuation_pass({"WIPRO.NS": 155.0}, {}, NOW_BUY + timedelta(hours=1))
    assert e.portfolio.positions["WIPRO.NS"].target_price == 150.0


def test_a3_without_a_live_price_waits(tmp_path, readers):
    readers["records"]["INFY.NS"] = _rec(base=150.0, ticker="INFY.NS")
    e = _engine(tmp_path)
    e.portfolio.positions["INFY.NS"] = _legacy()
    e._valuation_pass({}, {}, NOW_BUY)
    assert e.portfolio.positions["INFY.NS"].target_price == 120.0
    assert not _journal(e, "VALUATION_RETARGET")
    e._valuation_pass({"INFY.NS": 118.0}, {}, NOW_BUY + timedelta(minutes=30))
    assert e.portfolio.positions["INFY.NS"].target_price == 150.0


def _legacy_refresh_engine(tmp_path, monkeypatch, price):
    """A refresh-ready engine holding ONE legacy k x ATR position (entry
    100, target 200, stop 80) at `price`."""
    e = _engine(tmp_path)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    close = np.linspace(80.0, price, 300)
    frame = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                          "Close": close, "Volume": np.full(300, 5e6)}, index=idx)
    frames = {"INFY.NS": frame, e.cfg.benchmark: frame.copy()}
    monkeypatch.setattr("trading.engine.fetch_daily",
                        lambda tickers, period="6mo": {t: frames[t].copy()
                                                       for t in ([tickers] if isinstance(tickers, str) else tickers)
                                                       if t in frames})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None: {t: price for t in tickers if t == "INFY.NS"})
    e.cfg.danger_layer_enabled = False
    pos = _legacy(target=200.0, mode=MOM, stop_price=80.0,
                  entry_date=(datetime.utcnow() - timedelta(days=30)).date().isoformat())
    e.portfolio.positions["INFY.NS"] = pos
    e.portfolio.cash -= pos.qty * pos.entry_price
    return e, pos


def test_the_first_in_session_pass_sells_a_retargeted_name_already_past_its_base(
        tmp_path, monkeypatch, readers):
    """The tested A3 rule, said plainly: base 106 > entry 100 x 1.05, price
    110 inside base x 1.10 -> re-targeted to 106 and sold (TAKE_PROFIT) on
    that same pass, at that pass's price."""
    readers["records"]["INFY.NS"] = _rec(base=106.0, ticker="INFY.NS",
                                         asof=(datetime.utcnow() + timedelta(hours=5, minutes=30)).date().isoformat())
    e, pos = _legacy_refresh_engine(tmp_path, monkeypatch, 110.0)
    e.refresh()
    assert "INFY.NS" not in e.portfolio.positions
    t = e.portfolio.trades[-1]
    assert t.reason == "TAKE_PROFIT" and t.exit_price == pytest.approx(110.0, rel=0.01)
    assert len(_journal(e, "VALUATION_RETARGET")) == 1


def test_a_closed_market_pass_never_retargets(tmp_path, monkeypatch, readers):
    """Set at night, the target would sell on the next morning's gap — a
    price the study never judged it on. The re-target waits for the
    in-session pass, where the target and the exit read one price."""
    readers["records"]["INFY.NS"] = _rec(base=106.0, ticker="INFY.NS",
                                         asof=(datetime.utcnow() + timedelta(hours=5, minutes=30)).date().isoformat())
    e, pos = _legacy_refresh_engine(tmp_path, monkeypatch, 110.0)
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: False)
    e.refresh()
    assert pos.target_price == 200.0 and pos.val_target == {}
    assert not _journal(e, "VALUATION_RETARGET")
    assert not [x for x in _journal(e, "EXIT_DUE_MARKET_CLOSED") if x.get("ticker") == "INFY.NS"]


# ------------------- skeptic review (2026-10-21): the stock's own month-end close

def _hist_to(end: str, closes: dict = None) -> dict:
    """TCS.NS daily closes of 100 ending `end`, with `closes` overriding days."""
    idx = pd.bdate_range(end=end, periods=30)
    c = pd.Series(100.0, index=idx)
    for d, v in (closes or {}).items():
        c[pd.Timestamp(d)] = v
    c = c.sort_index()
    return {"TCS.NS": pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c,
                                    "Volume": 1e6})}


def _down_levels():
    lv = _month_levels()
    lv[("sensex", None)] = _ser({"2026-10-05": 1000, "2026-10-30": 800, "2026-11-02": 790})
    lv[("sector", "IT")] = _ser({"2026-10-05": 500, "2026-10-30": 400, "2026-11-02": 398})
    return lv


def test_the_remark_waits_for_the_stocks_own_month_end_close(tmp_path, readers):
    """The study compared T_m with the stock's month-end CLOSE. The Sensex
    close can be stored (15:35 EOD refresh) before the stock's (next
    morning's top-up). Judging on the morning price instead lost a tested
    exit here: T_m 91.2 <= the 10-30 close 95, but the 09:30 price is 90."""
    readers["levels"].series = _down_levels()
    e = _engine(tmp_path)
    pos = _val_pos()
    e.portfolio.positions["TCS.NS"] = pos
    e._valuation_pass({"TCS.NS": 90.0}, _hist_to("2026-10-29"), NOW_REMARK)
    assert pos.target_price == 120.0 and pos.val_target["last_remark_month"] is None
    assert not _journal(e, "VALUATION_REMARK")
    w = _journal(e, "VALUATION_REMARK_WAITING")
    assert len(w) == 1 and w[0]["ticker"] == "TCS.NS"
    # the top-up lands the 10-30 close: re-marked against it
    e._valuation_pass({"TCS.NS": 90.0}, _hist_to("2026-11-02", {"2026-10-30": 95.0}),
                      NOW_REMARK + timedelta(minutes=30))
    assert pos.target_price == pytest.approx(91.2)
    assert pos.val_target["exit_due"] is True
    j = _journal(e, "VALUATION_REMARK")
    assert len(j) == 1 and j[0]["close"] == 95.0 and j[0]["close_source"] == "history"


def test_past_the_wait_the_pass_price_is_used_and_named(tmp_path, readers):
    readers["levels"].series = _down_levels()
    e = _engine(tmp_path)
    pos = _val_pos()
    e.portfolio.positions["TCS.NS"] = pos
    late = NOW_REMARK + timedelta(days=VE.REMARK_CLOSE_WAIT_DAYS + 2)   # 11-09: 10 days past 10-30
    e._valuation_pass({"TCS.NS": 96.0}, _hist_to("2026-10-29"), late)
    assert pos.target_price == pytest.approx(91.2)
    j = _journal(e, "VALUATION_REMARK")
    assert len(j) == 1 and j[0]["close_source"] == "live_mark" and j[0]["close"] == 96.0
    assert pos.val_target["exit_due"] is True
