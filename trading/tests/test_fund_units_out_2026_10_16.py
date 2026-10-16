"""Fund units out of the scan, the weekly rebuild and breadth (2026-10-16).

Owner: "new position are not added, its not using the all 5 trade
segements as well" -> "relook n fix". Measured on the 10-15 close with the
real engine: liquid funds (ISIN INF...) saturate the vol-adjusted momentum
score, take Rs 1.5 Cr of momentum's Rs 2 Cr share before any company is
bought, can never reach a stop or a target, and always vote "above the
200-DMA" in the breadth reading the entry gate reads.

Pinned: the ISIN decides (INF = fund unit, INE = company); an unreadable
file drops nothing; held fund units keep their exits; every category's scan
drops them; the weekly rebuild never ranks or carries them; neither breadth
reading counts them.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from trading.breadth import store_breadth
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from trading.universe_select import select_liquid_universe
from trading.universes import drop_fund_units, fund_unit_tickers, is_fund_unit


def _csv(tmp_path, rows):
    p = tmp_path / "universe.csv"
    lines = ["ticker,trading_symbol,name,isin,instrument_key,mis_allowed"]
    for t, isin in rows:
        lines.append(f"{t},{t[:-3]},{t} LTD,{isin},NSE_EQ|{isin},1")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_the_committed_file_marks_liquid_funds_and_etfs_not_companies():
    funds = fund_unit_tickers()
    assert len(funds) > 300            # 351 on 2026-10-15
    for t in ("LIQUIDCASE.NS", "LIQUID1.NS", "CASHIETF.NS", "GILT5YBEES.NS",
              "NIFTYBEES.NS", "LIQUIDBEES.NS"):
        assert t in funds, t
    for t in ("TCS.NS", "RELIANCE.NS", "HAL.NS", "SBIN.NS"):
        assert t not in funds, t


def test_the_isin_decides_and_a_missing_file_drops_nothing(tmp_path):
    path = _csv(tmp_path, [("FUNDX.NS", "INF000X01011"),
                           ("COMPANY.NS", "INE000C01011")])
    assert fund_unit_tickers(path) == {"FUNDX.NS"}
    assert is_fund_unit("FUNDX", path) and not is_fund_unit("COMPANY", path)
    gone = str(tmp_path / "nope.csv")
    assert fund_unit_tickers(gone) == frozenset()
    assert drop_fund_units(["FUNDX.NS", "COMPANY.NS"], path=gone) == [
        "FUNDX.NS", "COMPANY.NS"]


def test_held_fund_units_stay_for_their_exits(tmp_path):
    path = _csv(tmp_path, [("FUNDX.NS", "INF000X01011"),
                           ("FUNDY.NS", "INF000Y01011"),
                           ("COMPANY.NS", "INE000C01011")])
    out = drop_fund_units(["FUNDX.NS", "FUNDY.NS", "COMPANY.NS"],
                          held={"FUNDY.NS"}, path=path)
    assert out == ["FUNDY.NS", "COMPANY.NS"]


def _engine(mode, custom, held=()):
    cfg = Config(mode=mode)
    cfg.apply_mode_preset()
    cfg.custom_universe = tuple(custom)
    p = Portfolio.fresh(cfg)
    for t in held:
        from trading.paper import PaperPosition
        p.positions[t] = PaperPosition(
            ticker=t, qty=10, entry_price=100.0, entry_date=datetime(2026, 10, 1),
            stop_price=95.0, target_price=110.0, peak_price=100.0,
            entry_score=70.0, sector="Unknown", reasoning="test", mode=mode)
    return TradingEngine(cfg, p)


def test_every_category_scans_companies_only():
    """Momentum AND the four composite passes read the same custom list —
    the fund units leave all five, a held one stays."""
    custom = ["TCS.NS", "LIQUIDCASE.NS", "NIFTYBEES.NS", "SBIN.NS"]
    for mode in ("nifty200_momentum", "intraday", "short", "swing",
                 "positional"):
        e = _engine(mode, custom, held=("GILT5YBEES.NS",))
        uni, _period = e.scan_universe()
        assert "LIQUIDCASE.NS" not in uni and "NIFTYBEES.NS" not in uni, mode
        assert "TCS.NS" in uni and "SBIN.NS" in uni, mode
        assert "GILT5YBEES.NS" in uni, f"{mode}: a held fund lost its exits"


def _doc(t, close=100.0, vol=1_000_000, bars=40):
    return {"ticker": t, "candles": [["2026-10-01", close, close, close,
                                      close, vol] for _ in range(bars)]}


def test_the_weekly_rebuild_never_ranks_a_fund_unit_even_a_held_one():
    """A held fund unit is the heaviest turnover name here; with target 1 it
    would take the only stock slot if it were ranked. It is carried instead
    (its exits keep running), outside the ranked count."""
    res = select_liquid_universe(
        [_doc("BIGFUND.NS", vol=50_000_000), _doc("HELDFUND.NS", vol=90_000_000),
         _doc("CO.NS")],
        target=1, always_include=["HELDFUND.NS", "HELDCO.NS"],
        exclude={"BIGFUND.NS", "HELDFUND.NS"})
    assert [r["ticker"] for r in res["ranked"]] == ["CO.NS"]
    assert res["tickers"] == ["CO.NS", "HELDCO.NS", "HELDFUND.NS"]
    assert res["skipped_fund"] == 2 and res["short_by"] == 0
    assert "BIGFUND.NS" not in res["tickers"]


def _bdoc(t, rising, end="2026-10-15"):
    closes = list(range(100, 300)) if rising else list(range(300, 100, -1))
    return {"ticker": t, "end": end,
            "candles": [[end, c, c, c, c, 0.0] for c in closes]}


def test_store_breadth_does_not_count_fund_units():
    docs = ([_bdoc(f"F{i}.NS", True) for i in range(5)]
            + [_bdoc("UP.NS", True)]
            + [_bdoc(f"DN{i}.NS", False) for i in range(3)])
    with_funds = store_breadth(docs, min_sample=2)
    assert with_funds["pct"] == round(6 / 9, 4)
    clean = store_breadth(docs, min_sample=2,
                          exclude={f"F{i}.NS" for i in range(5)})
    assert clean["pct"] == 0.25 and clean["sample"] == 4
    assert clean["excluded_fund"] == 5


def test_the_scan_bucket_breadth_does_not_count_a_held_fund_unit():
    idx = pd.bdate_range("2025-10-01", periods=260)

    def frame(rising):
        c = np.linspace(100, 200, 260) if rising else np.linspace(200, 100, 260)
        return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c,
                             "Volume": 1e6}, index=idx)
    e = _engine("nifty200_momentum", [], held=("LIQUIDCASE.NS",))
    history = {"LIQUIDCASE.NS": frame(True), "NIFTYBEES.NS": frame(True),
               "UP.NS": frame(True), "DN.NS": frame(False),
               "DN2.NS": frame(False)}
    pct, sample = e._cache_breadth_today(history, datetime(2026, 10, 16, 4))
    assert sample == 3 and abs(pct - 1 / 3) < 1e-9


def test_the_buy_desk_never_offers_a_fund_unit_from_an_older_stored_list():
    from web.buy_desk import shortlist
    cfg = Config(mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    row = {"score": 95.0, "abs_mom": True, "liq": True, "timing": True,
           "outcome": "", "c": {}}
    p.momentum_shortlist = {"ts": "2026-10-16T03:50:00",
                            "mode": "nifty200_momentum", "score_floor": 62.0,
                            "rows": {"LIQUIDCASE.NS": dict(row),
                                     "TCS.NS": dict(row)}}
    names = [r["ticker"] for r in shortlist(p, cfg)["rows"]]
    assert "TCS.NS" in names and "LIQUIDCASE.NS" not in names
