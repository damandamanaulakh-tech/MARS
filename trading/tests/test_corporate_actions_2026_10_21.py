"""Corporate actions on held names (owner order 2026-10-21, the BLSE split).

BLSE split 10 → 5 on 2026-10-06 while held from ~Rs 320; nothing adjusted
the position, the quote halved, the −5% stop fired and a −49% loss was
booked on a −1% move. Owner: "Yes — adjust held positions on the ex-date".
These tests pin the parser, the pending rule, the restatement, the engine
hook, persistence and the boundary.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading import corporate_actions as ca
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

ROOT = Path(__file__).resolve().parents[2]
SPLIT_SUBJECT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"


def _blse_actions():
    return [{"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "split", "ratio": 2.0,
             "subject": SPLIT_SUBJECT, "isin": "INE0NLT01010", "source": "test"}]


def _pos(qty=101, entry=320.16, when=datetime(2026, 10, 1, 4, 0), **kw):
    d = dict(ticker="BLSE.NS", qty=qty, entry_price=entry, entry_date=when, sector="IT",
             peak_price=330.0, entry_score=70.0, reasoning="fixture",
             stop_price=304.15, target_price=384.19, mode="nifty200_momentum")
    d.update(kw)
    return PaperPosition(**d)


# ---------------------------------------------------------------- parser --
@pytest.mark.parametrize("subject, kind, ratio", [
    (SPLIT_SUBJECT, "split", 2.0),
    ("Fv Split Rs.10 To Re.1", "split", 10.0),
    ("Face Value Split From Rs 10 To Rs 2", "split", 5.0),
    ("Bonus 1:1", "bonus", 2.0),
    ("Bonus 1:2", "bonus", 1.5),
    ("Bonus 3:1", "bonus", 4.0),
    ("Consolidation Of Equity Shares From Re 1 Per Share To Rs 10 Per Share", "consolidation", 0.1),
    ("Interim Div - Rs 6/- Per Share + Face Value Split (Sub-Division) - From Rs 10/- "
     "Per Share To Re 1/- Per Share", "split", 10.0),
])
def test_parser_reads_the_nse_subjects(subject, kind, ratio):
    p = ca.parse_subject(subject)
    assert p["kind"] == kind
    assert abs(p["ratio"] - ratio) < 1e-9


def test_parser_never_invents_a_ratio():
    assert ca.parse_subject("Rights 1:4 @ Premium Rs 10") == {
        "kind": "rights", "ratio": None, "detail": "Rights 1:4 @ Premium Rs 10"}
    assert ca.parse_subject("Dividend - Re 0.50 Per Share")["kind"] == "other"
    assert ca.parse_subject("Bonus Debentures 1:1")["ratio"] is None
    assert ca.parse_subject("Face Value Split")["ratio"] is None     # no from/to


def test_parse_rows_takes_the_nse_api_shape_and_the_committed_file_shape():
    api_row = {"bcEndDate": "-", "comp": "BLS E-Services Limited", "exDate": "06-Oct-2026",
               "faceVal": "5", "isin": "INE0NLT01010", "recDate": "06-Oct-2026",
               "series": "EQ", "subject": SPLIT_SUBJECT, "symbol": "BLSE"}
    div_row = {**api_row, "exDate": "08-Sep-2026", "subject": "Dividend - Re 0.50 Per Share"}
    rows = ca.parse_rows([api_row, div_row, api_row], source="nse_api")
    assert len(rows) == 1                       # dividend dropped, duplicate deduped
    assert rows[0]["ex_date"] == "2026-10-06" and rows[0]["ratio"] == 2.0
    assert rows[0]["source"] == "nse_api" and ca.ca_key(rows[0]) == "BLSE|2026-10-06"


def test_parser_agrees_with_the_valuation_parser_on_the_committed_file():
    """One parser for the app: every split/bonus the display chain reads, the
    engine reads the same way (the three known extras are rows the
    valuation parser leaves as 'other': a sub-division without the word
    'split' and two consolidations)."""
    from trading.valuation.shares import parse_corporate_action, share_factor
    idx = ca._file_index()
    assert len(idx) > 500
    disagree = []
    for sym, rows in idx.items():
        for r in rows:
            mine = ca.parse_subject(r["subject"])
            theirs = share_factor(parse_corporate_action(r["subject"]))
            if mine["kind"] in ("rights", "other"):
                continue
            if theirs != 1.0 and abs(theirs - mine["ratio"]) > 1e-9:
                disagree.append((sym, r["subject"]))
    assert disagree == []


def test_committed_rows_carry_blse_history_but_not_the_october_split():
    """The committed file ends before 2026-10-06 — the live fetch / store
    is the only source of the BLSE split. Said so the audit never claims
    the file alone covers it."""
    rows = ca.committed_rows("BLSE.NS")
    assert all("split" not in (r["subject"] or "").lower() for r in rows)


# --------------------------------------------------------- pending rule --
def test_pending_applies_on_the_ex_date_and_within_the_lookback():
    pos = _pos()
    acts = _blse_actions()
    assert len(ca.pending_for_position(pos, acts, date(2026, 10, 6))) == 1
    # 5 sessions back from Mon 10-12: 12, 9, 8, 7, 6 → still pending
    assert len(ca.pending_for_position(pos, acts, date(2026, 10, 12))) == 1
    # 5 sessions back from Wed 10-14: 14, 13, 12, 9, 8 → 10-06 outside
    assert ca.pending_for_position(pos, acts, date(2026, 10, 14)) == []


def test_pending_skips_a_future_ex_date_a_buy_after_it_and_an_applied_one():
    acts = _blse_actions()
    assert ca.pending_for_position(_pos(), acts, date(2026, 10, 5)) == []
    bought_after = _pos(entry=160.0, when=datetime(2026, 10, 7, 4, 0))
    assert ca.pending_for_position(bought_after, acts, date(2026, 10, 7)) == []
    done = _pos(ca_applied=["BLSE|2026-10-06"])
    assert ca.pending_for_position(done, acts, date(2026, 10, 6)) == []


def test_pending_never_applies_a_rights_issue():
    rights = [{"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "rights", "ratio": None,
               "subject": "Rights 1:4", "isin": "", "source": "test"}]
    assert ca.pending_for_position(_pos(), rights, date(2026, 10, 6)) == []


# ---------------------------------------------------------- restatement --
def test_apply_to_position_restates_qty_entry_and_levels_and_never_sells():
    pos = _pos(qty=101, custom_levels=True)
    body = ca.apply_to_position(pos, _blse_actions()[0])
    assert pos.qty == 202
    assert abs(pos.entry_price - 160.08) < 1e-6
    assert abs(pos.stop_price - 152.075) < 1e-6
    assert abs(pos.target_price - 192.095) < 1e-6
    assert abs(pos.peak_price - 165.0) < 1e-6
    assert pos.custom_levels is True
    assert pos.ca_applied == ["BLSE|2026-10-06"]
    assert body["before"]["qty"] == 101 and body["after"]["qty"] == 202
    assert body["cash_credit"] == 0.0 and body["remainder_shares"] == 0.0


def test_fractional_remainder_is_credited_cost_neutral():
    pos = _pos(qty=7, entry=300.0, stop_price=0.0, target_price=0.0)
    bonus = {"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "bonus", "ratio": 1.5,
             "subject": "Bonus 1:2", "isin": "", "source": "test"}
    body = ca.apply_to_position(pos, bonus)
    assert pos.qty == 10                                   # floor(10.5)
    assert abs(pos.entry_price - 200.0) < 1e-9
    assert pos.stop_price == 0.0 and pos.target_price == 0.0   # unset stays unset
    # 0.5 share × Rs 200 = Rs 100: cost basis unchanged (7 × 300 = 10 × 200 + 100)
    assert abs(body["cash_credit"] - 100.0) < 1e-9
    assert abs(body["remainder_shares"] - 0.5) < 1e-9


# --------------------------------------------------------------- engine --
@pytest.fixture()
def eng(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = 1_000_000.0
    return TradingEngine(cfg, p)


def test_engine_applies_the_split_on_the_ex_date_journals_and_sells_nothing(eng):
    p = eng.portfolio
    pos = _pos()
    p.positions = {pos.ticker: pos}
    cash0 = p.cash
    lookup = lambda s: _blse_actions() if s == "BLSE" else []    # noqa: E731
    applied = eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lookup)
    assert len(applied) == 1
    assert pos.qty == 202 and abs(pos.entry_price - 160.08) < 1e-6
    assert p.trades == [] and "BLSE.NS" in p.positions
    assert p.cash == cash0
    line = [e for e in p.decision_journal if e["action"] == "CORPORATE_ACTION_APPLIED"]
    assert len(line) == 1
    assert line[0]["ticker"] == "BLSE.NS" and line[0]["ratio"] == 2.0
    assert line[0]["before"]["qty"] == 101 and line[0]["after"]["qty"] == 202
    # idempotent: a second run on the same day does nothing
    assert eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lookup) == []
    assert pos.qty == 202
    assert sum(1 for e in p.decision_journal if e["action"] == "CORPORATE_ACTION_APPLIED") == 1


def test_the_exit_pass_after_the_adjustment_sees_no_minus_fifty(eng, monkeypatch):
    """The BLSE fault itself: the halved quote against the halved entry is a
    HOLD, not a stop-out."""
    p = eng.portfolio
    pos = _pos()
    p.positions = {pos.ticker: pos}
    eng.apply_corporate_actions(today=date(2026, 10, 6),
                                actions_for=lambda s: _blse_actions())

    def frame():
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
        close = np.linspace(150.0, 158.0, 300)
        return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                             "Close": close, "Volume": np.full(300, 5e6)}, index=idx)
    monkeypatch.setattr("trading.engine.fetch_daily",
                        lambda tickers, period="6mo": {t: frame() for t in
                                                       ([tickers] if isinstance(tickers, str)
                                                        else tickers)})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None: {t: 158.0 for t in tickers})
    eng.refresh()
    assert "BLSE.NS" in p.positions
    assert p.trades == []
    assert not any(e["action"] == "SELL" for e in p.decision_journal)


def test_engine_skips_an_unreadable_name_and_keeps_going(eng):
    p = eng.portfolio
    a = _pos()
    b = _pos(ticker="OTHER.NS")
    p.positions = {a.ticker: a, b.ticker: b}

    def lookup(sym):
        if sym == "BLSE":
            raise RuntimeError("store down")
        return [{**_blse_actions()[0], "symbol": "OTHER"}]
    applied = eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lookup)
    assert [x["ticker"] for x in applied] == ["OTHER.NS"]
    assert a.qty == 101 and b.qty == 202


def test_a_rights_issue_is_named_not_applied(eng):
    p = eng.portfolio
    pos = _pos()
    p.positions = {pos.ticker: pos}
    rights = [{"symbol": "BLSE", "ex_date": "2026-10-06", "kind": "rights", "ratio": None,
               "subject": "Rights 1:4 @ Premium Rs 10", "isin": "", "source": "test"}]
    assert eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lambda s: rights) == []
    assert pos.qty == 101
    assert any(e["action"] == "CORPORATE_ACTION_UNAPPLIED" for e in p.decision_journal)


# ---------------------------------------------------------- persistence --
def test_ca_applied_persists_through_json_and_the_mongo_mirror_shape():
    pos = _pos(ca_applied=["BLSE|2026-10-06"])
    d = pos.to_dict()
    assert d["ca_applied"] == ["BLSE|2026-10-06"]
    assert PaperPosition.from_dict(d).ca_applied == ["BLSE|2026-10-06"]
    legacy = {k: v for k, v in d.items() if k != "ca_applied"}
    assert PaperPosition.from_dict(legacy).ca_applied == []
    # the Mongo mirror and hydrate both go through to_dict/from_dict (web/state)
    src = (ROOT / "web" / "state.py").read_text()
    assert "PaperPosition.from_dict(v)" in src and "v.to_dict() for k, v in self.positions" in src


def test_portfolio_save_and_load_round_trip_keeps_ca_applied(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    pos = _pos()
    ca.apply_to_position(pos, _blse_actions()[0])
    p.positions = {pos.ticker: pos}
    p.save()
    back = Portfolio.load_or_fresh(cfg)
    assert back.positions["BLSE.NS"].ca_applied == ["BLSE|2026-10-06"]
    assert back.positions["BLSE.NS"].qty == 202


# ------------------------------------------------------------- boundary --
def _code(path: Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.strip().startswith("#")]
    return "\n".join(lines)


def test_corporate_actions_imports_nothing_from_web_or_the_valuation_chain():
    src = _code(ROOT / "trading" / "corporate_actions.py")
    assert not re.search(r"^\s*(from|import)\s+web\b", src, re.M)
    assert "valuation" not in src
    assert "from web" not in src and "import web" not in src


def test_engine_reaches_the_adjuster_but_never_the_network_fetch():
    src = _code(ROOT / "trading" / "engine.py")
    assert "corporate_actions" in src
    assert "fetch_nse_actions" not in src
    # Item 19 (owner order 2026-10-21) lets the engine read trading/valuation_exit
    # (pure, injected readers); the valuation PIPELINE package stays out — the
    # authoritative pin is test_valuation_boundary_2026_10_14.
    assert "from .valuation import" not in src and "from .valuation." not in src
    assert "trading.valuation." not in src and "trading.valuation import" not in src


def test_store_reader_is_the_injection_seam():
    ca.set_store_reader(lambda syms: [{"symbol": "BLSE", "exDate": "06-Oct-2026",
                                       "subject": SPLIT_SUBJECT, "isin": ""}])
    try:
        acts = ca.actions_for("BLSE.NS")
        assert any(a["ex_date"] == "2026-10-06" and a["ratio"] == 2.0 for a in acts)
        ca.set_store_reader(lambda syms: (_ for _ in ()).throw(RuntimeError("down")))
        assert all(a["ex_date"] != "2026-10-06" for a in ca.actions_for("BLSE.NS"))
    finally:
        ca.set_store_reader(None)


def test_fetch_nse_actions_uses_the_injected_getter_and_fails_closed():
    rows = ca.fetch_nse_actions("BLSE.NS", get=lambda url: [{"symbol": "BLSE", "exDate": "06-Oct-2026",
                                                             "subject": SPLIT_SUBJECT}])
    assert rows and rows[0]["source"] == "nse_api"
    assert ca.fetch_nse_actions("BLSE", get=lambda url: {"not": "a list"}) is None
    assert ca.fetch_nse_actions("BLSE", get=lambda url: (_ for _ in ()).throw(OSError("x"))) is None
