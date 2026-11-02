"""AUD-001: incomplete cuts keep their original obligation across quote gaps."""
from datetime import datetime, timedelta
import copy

import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

NOW = datetime(2026, 11, 2, 6)


def book(tmp_path, count=4, drawdown=.12):
    cfg = Config(state_dir=str(tmp_path), universe=[], sector_map={})
    p = Portfolio(cfg=cfg, cash=100_000.)
    for i in range(count):
        t = f"H{i}.NS"
        p.positions[t] = PaperPosition(
            ticker=t, qty=10, entry_price=100., entry_date=NOW-timedelta(days=1),
            sector="Test", peak_price=100., entry_score=75., reasoning="fixture")
    p.dd_peak_equity = p.equity({}) / (1-drawdown)
    return TradingEngine(cfg, p)


def cut(e, quotes, allowed=True):
    return e._update_drawdown(quotes, NOW, cut_allowed=allowed,
                              marks={t: 100. for t in e.portfolio.positions})


@pytest.mark.parametrize("count,dd,required", [(2,.25,2),(4,.12,2),(5,.17,4)])
def test_partial_quotes_complete_original_cut_without_recut(tmp_path, count, dd, required):
    e = book(tmp_path, count, dd)
    assert cut(e, {"H0.NS": 100.}) == ["H0.NS"]
    assert e.portfolio.dd_cut_level == "normal"
    assert e.portfolio.dd_cut_pending["remaining_count"] == required-1
    for i in range(1, count):
        cut(e, {f"H{i}.NS": 100.})
    assert len(e.portfolio.trades) == required
    assert len(e.portfolio.positions) == count-required
    assert not e.portfolio.dd_cut_pending
    cut(e, {t: 100. for t in e.portfolio.positions})
    assert len(e.portfolio.trades) == required


def test_partial_cut_survives_json_restart(tmp_path):
    e = book(tmp_path)
    cut(e, {"H0.NS": 100.})
    e.portfolio.save()
    restored = Portfolio.load_or_fresh(e.cfg)
    fresh = TradingEngine(e.cfg, restored)
    assert cut(fresh, {"H1.NS": 100.}) == ["H1.NS"]
    assert len(restored.positions) == 2
    assert restored.dd_cut_level == "cut_half"


def test_no_quotes_retain_original_count_and_halt_after_market_recovers(tmp_path):
    e = book(tmp_path)
    cut(e, {})
    assert e.portfolio.dd_cut_pending["remaining_count"] == 2
    e.portfolio.dd_peak_equity = e.portfolio.equity({})
    cut(e, {}, allowed=False)
    assert e.drawdown_gov.halt_new_entries
    assert "pending" in e.drawdown_gov.reason.lower()
    assert cut(e, {"H0.NS": 100., "H1.NS": 100.}) == ["H0.NS", "H1.NS"]
    assert not e.portfolio.dd_cut_pending


def test_mark_only_pass_does_not_fill_pending_cut(tmp_path):
    e = book(tmp_path)
    cut(e, {"H0.NS": 100.})
    pending = copy.deepcopy(e.portfolio.dd_cut_pending)
    assert cut(e, {"H1.NS": 100.}, allowed=False) == []
    assert e.portfolio.dd_cut_pending == pending
    assert cut(e, {"H1.NS": 100.}) == ["H1.NS"]


@pytest.mark.parametrize("bad", [0., -1., float("nan"), float("inf"), -float("inf")])
def test_invalid_quote_never_fills_or_completes_pending_cut(tmp_path, bad):
    e = book(tmp_path, 2, .25)
    cut(e, {"H0.NS": 100.})
    cash = e.portfolio.cash
    assert cut(e, {"H1.NS": bad}) == []
    assert e.portfolio.cash == cash
    assert e.portfolio.dd_cut_pending["remaining_count"] == 1
    assert cut(e, {"H1.NS": 100.}) == ["H1.NS"]


def test_deeper_level_supersedes_pending_cut_and_closes_once(tmp_path):
    e = book(tmp_path, 5, .12)
    cut(e, {"H0.NS": 100.})
    e.portfolio.dd_peak_equity = e.portfolio.equity({}) / .75
    cut(e, {"H1.NS": 100.})
    assert e.portfolio.dd_cut_pending["level"] == "full_halt"
    assert e.portfolio.dd_cut_pending["remaining_count"] == 3
    cut(e, {t: 100. for t in e.portfolio.positions})
    assert len(e.portfolio.trades) == 5
    assert not e.portfolio.positions
    assert not e.portfolio.dd_cut_pending


def test_other_exit_satisfies_remaining_cut_without_extra_sale(tmp_path):
    e = book(tmp_path)
    cut(e, {"H0.NS": 100.})
    e._close("H1.NS", 100., "STOP_LOSS", NOW)
    assert cut(e, {"H2.NS": 100.}) == []
    assert len(e.portfolio.trades) == 2
    assert e.portfolio.dd_cut_level == "cut_half"
    assert not e.portfolio.dd_cut_pending


def test_reopened_ticker_does_not_inherit_old_liquidation(tmp_path):
    e = book(tmp_path)
    cut(e, {"H0.NS": 100.})
    old = copy.deepcopy(e.portfolio.positions["H1.NS"])
    e._close("H1.NS", 100., "OWNER", NOW)
    old.entry_date = NOW
    e.portfolio.positions["H1.NS"] = old
    assert cut(e, {"H1.NS": 100.}) == []
    assert e.portfolio.positions["H1.NS"].entry_date == NOW
    assert not e.portfolio.dd_cut_pending


def test_pending_pass_ranks_available_prices_and_never_uses_marks_to_fill(tmp_path):
    e = book(tmp_path)
    cut(e, {})
    assert cut(e, {"H0.NS": 99., "H1.NS": 98., "H2.NS": 97.}) == ["H2.NS", "H1.NS"]
    assert set(e.portfolio.positions) == {"H0.NS", "H3.NS"}


def test_legacy_snapshot_loads_without_pending_obligation(tmp_path):
    e = book(tmp_path)
    e.portfolio.save()
    import json
    path = tmp_path / "portfolio.json"
    data = json.loads(path.read_text())
    data.pop("dd_cut_pending", None)
    path.write_text(json.dumps(data))
    assert Portfolio.load_or_fresh(e.cfg).dd_cut_pending == {}
