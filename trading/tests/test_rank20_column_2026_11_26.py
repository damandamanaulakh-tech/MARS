"""20-session rank — owner decision 2026-11-26: "20-session top-10 rank -
Live Rank Rule", approved as a COLUMN, not a buy rule.

The measured record of buying on it is negative in both halves
(research/ash_formula_study/), so it is shown and never traded on.
"""
import ast
import pathlib

import pytest

from trading import multi_formula as mf

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _rows(values):
    return [{"ticker": f"T{i}", "multi": ({"mom20": v} if v != "noview" else None)}
            for i, v in enumerate(values)]


def test_highest_twenty_session_return_is_rank_one_and_the_top_ten_are_marked():
    rows = _rows([0.01 * i for i in range(25)])
    assert mf.rank_mom20(rows) == 25
    by = {r["ticker"]: r["multi"]["rank20"] for r in rows}
    assert by["T24"] == {"rank": 1, "of": 25, "top": True}
    assert by["T15"] == {"rank": 10, "of": 25, "top": True}
    assert by["T14"] == {"rank": 11, "of": 25, "top": False}
    assert by["T0"] == {"rank": 25, "of": 25, "top": False}
    assert mf.RANK_TOP == 10


def test_a_stock_with_no_twenty_session_return_is_unranked_not_last():
    rows = _rows([0.05, None, "x", float("nan"), -0.02, "noview"])
    assert mf.rank_mom20(rows) == 2
    assert rows[0]["multi"]["rank20"] == {"rank": 1, "of": 2, "top": True}
    assert rows[4]["multi"]["rank20"] == {"rank": 2, "of": 2, "top": True}
    for i in (1, 2, 3):
        assert rows[i]["multi"]["rank20"] is None
    assert rows[5]["multi"] is None                    # no view: nothing invented
    assert mf.rank_mom20([]) == 0 and mf.rank_mom20(None) == 0


def test_the_scan_stores_the_rank_on_the_view_the_pages_already_read(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    e.scan()
    pick = e.portfolio.last_scan_picks[0]
    assert pick["ticker"] == "UPP.NS"
    assert pick["multi"]["rank20"] == {"rank": 1, "of": 1, "top": True}
    assert e.portfolio.scan_matrix["rows"]["UPP.NS"]["multi"]["rank20"]["rank"] == 1
    # the round trip through the saved book keeps it
    from trading.paper import Portfolio
    again = Portfolio.load_or_fresh(e.cfg)
    assert again.last_scan_picks[0]["multi"]["rank20"]["rank"] == 1


def test_a_failed_rank_costs_the_column_never_the_scan(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    monkeypatch.setattr("trading.multi_formula.rank_mom20", lambda rows: 1 / 0)
    result = e.scan()
    assert [r["ticker"] for r in result.ranked] == ["UPP.NS"]
    assert "rank20" not in (e.portfolio.last_scan_picks[0]["multi"] or {})


def test_no_buy_sell_or_size_rule_reads_the_rank():
    for name in ("engine", "risk", "orders", "danger", "sizing", "exits", "scoring", "timing", "targets"):
        tree = ast.parse((ROOT / "trading" / f"{name}.py").read_text())
        reads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and n.value == "rank20"]
        assert not reads, f"trading/{name}.py reads the display-only 20-session rank"
    # and the owner's switchable three-formula filter does not use it either
    assert mf.gate_blocks({"rows": {}, "rank20": {"rank": 999, "of": 999, "top": False}}) is None
