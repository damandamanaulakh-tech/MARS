"""Review fixes to the ex-date adjuster (owner order 2026-10-21, BLSE).

- one split + a same-day bonus from DIFFERENT sources (NSE carries the
  split, only the committed file the bonus): the dedupe kept the best
  source alone and dropped the bonus — it is per symbol|ex_date|kind now;
- a consolidation that leaves less than one whole share booked a qty-0
  position; it is named for a hand settlement instead;
- the boundary check read comment lines only — it now reads the imports.
"""
from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pytest

from trading import corporate_actions as ca
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

ROOT = Path(__file__).resolve().parents[2]
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"


def _pos(ticker="BLSE.NS", qty=101, entry=320.16, when=datetime(2026, 10, 1, 4, 0), **kw):
    d = dict(ticker=ticker, qty=qty, entry_price=entry, entry_date=when, sector="IT",
             peak_price=330.0, entry_score=70.0, reasoning="fixture",
             stop_price=304.15, target_price=384.19, mode="nifty200_momentum")
    d.update(kw)
    return PaperPosition(**d)


@pytest.fixture(autouse=True)
def _no_leaked_refetch_queue():
    """engine.CA_REFETCH_DUE is module-global; leave it as found."""
    from trading import engine as eng_mod
    before = set(eng_mod.CA_REFETCH_DUE)
    yield
    eng_mod.CA_REFETCH_DUE.clear()
    eng_mod.CA_REFETCH_DUE.update(before)


@pytest.fixture()
def eng(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = 1_000_000.0
    return TradingEngine(cfg, p)


def test_a_kind_only_the_committed_file_carries_still_counts():
    rows = [{"symbol": "Y", "exDate": "06-Oct-2026", "subject": SPLIT, "source": "nse_api"},
            {"symbol": "Y", "exDate": "06-Oct-2026", "subject": "Bonus 1:1",
             "source": "committed_file"},
            {"symbol": "Y", "exDate": "06-Oct-2026", "subject": "Fv Split Rs.10 To Rs.5",
             "source": "committed_file"}]
    acts = ca.parse_rows(rows)
    assert len(acts) == 1
    assert acts[0]["kind"] == "split+bonus" and acts[0]["ratio"] == 4.0


def test_the_same_kind_from_two_sources_is_still_one_action():
    rows = [{"symbol": "Y", "exDate": "06-Oct-2026", "subject": SPLIT, "source": "committed_file"},
            {"symbol": "Y", "exDate": "06-Oct-2026", "subject": "Fv Split Rs.10 To Rs.5",
             "source": "nse_api"}]
    acts = ca.parse_rows(rows)
    assert len(acts) == 1 and acts[0]["ratio"] == 2.0 and acts[0]["source"] == "nse_api"


def test_a_consolidation_below_one_share_is_named_not_applied(eng):
    p = eng.portfolio
    pos = _pos(ticker="TINY.NS", qty=5, entry=1500.0, stop_price=1425.0, target_price=1800.0)
    p.positions = {pos.ticker: pos}
    cons = [{"symbol": "TINY", "ex_date": "2026-10-06", "kind": "consolidation", "ratio": 0.1,
             "subject": "Consolidation Of Equity Shares From Re 1 To Rs 10", "isin": "",
             "source": "nse_api"}]
    cash0 = p.cash
    applied = eng.apply_corporate_actions(today=date(2026, 10, 6), actions_for=lambda s: cons,
                                          now=datetime(2026, 10, 6, 5, 0))
    assert applied == []
    assert pos.qty == 5 and pos.entry_price == 1500.0 and p.cash == cash0
    lines = [e for e in p.decision_journal if e["action"] == "CORPORATE_ACTION_UNAPPLIED"]
    assert len(lines) == 1 and "less than one whole share" in lines[0]["why"]
    # a holding big enough is restated as usual
    big = _pos(ticker="BIG.NS", qty=25, entry=1500.0)
    p.positions = {big.ticker: big}
    assert len(eng.apply_corporate_actions(
        today=date(2026, 10, 6), actions_for=lambda s: [{**cons[0], "symbol": "BIG"}],
        now=datetime(2026, 10, 6, 5, 0))) == 1
    assert big.qty == 2 and abs(big.entry_price - 15000.0) < 1e-6


def _imports(path: Path) -> list:
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(("." * node.level) + (node.module or ""))
    return out


def test_corporate_actions_imports_nothing_from_web_by_its_import_statements():
    mods = _imports(ROOT / "trading" / "corporate_actions.py")
    assert mods, "no imports read"
    assert not [m for m in mods if m == "web" or m.startswith("web.")]
    assert not [m for m in mods if "valuation" in m]
    # the engine reaches the module but never its network fetch
    eng_src = (ROOT / "trading" / "engine.py").read_text()
    tree = ast.parse(eng_src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "fetch_nse_actions" not in names and "apply_to_position" in names
