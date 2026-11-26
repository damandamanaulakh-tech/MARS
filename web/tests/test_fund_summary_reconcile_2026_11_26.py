"""Fund summary: closed trades are added and the totals match.

Owner order 2026-11-26: "its not adding the margin released in the total, it
should add the amount, and must match with each total".

Before this the fund block showed only OPEN positions. A book that had closed
trades read Capital 5.00 Cr, Allocated X, Cash Y with nothing explaining why
X + Y was not 5.00 Cr — the result of every closed trade was invisible.

Display only: `fund_summary` reads the book, it changes nothing in it.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from trading import category_budget as cb
from trading.config import Config
from trading.paper import ClosedTrade, PaperPosition, Portfolio
from web import market_panels as mp, state
from web.tests.test_dashboard_mars_2026_10_12 import _render


def _text(html: str) -> str:
    """What the owner reads: tags removed, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _pos(ticker, qty, entry, mode):
    return PaperPosition(ticker=ticker, qty=qty, entry_price=entry,
                         entry_date=datetime(2026, 11, 20, 4, 0), sector="IT",
                         peak_price=entry, entry_score=70.0, reasoning="fixture", mode=mode)


def _trade(ticker, qty, entry, net, *, mode="", when=None, reasoning="fixture", tags=None):
    when = when or datetime.utcnow()
    return ClosedTrade(ticker=ticker, qty=qty, entry_date=when - timedelta(hours=2), exit_date=when,
                       entry_price=entry, exit_price=entry + net / qty, gross_pnl=net, costs=0.0,
                       net_pnl=net, reason="TAKE_PROFIT" if net >= 0 else "STOP_LOSS", sector="IT",
                       entry_score=70.0, reasoning=reasoning, mode=mode, tags=list(tags or []))


def _book(tmp_path):
    """One open swing position, three listed closed trades, an entry charge
    already paid on the open position and Rs 2,000 booked by older trades
    that are no longer in the live list (archived)."""
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions = {"OPEN.NS": _pos("OPEN.NS", 100, 1000.0, "swing")}
    yesterday = datetime.utcnow() - timedelta(days=2)
    p.trades = [
        _trade("DAY.NS", 10, 500.0, -1_000.0, mode="intraday"),                 # closed today
        _trade("MOM.NS", 20, 800.0, 4_000.0, mode="nifty200_momentum", when=yesterday),
        _trade("OLD.NS", 5, 300.0, 500.0, mode="", reasoning="composite 71", when=yesterday),
    ]
    listed, entry_charge, archived = 3_500.0, 50.0, 2_000.0
    p.cash = cfg.starting_capital - 100_000.0 - entry_charge + listed + archived
    return cfg, p, {"OPEN.NS": 1100.0}


def test_each_segment_shows_what_it_booked_and_what_came_back_today(tmp_path):
    cfg, p, prices = _book(tmp_path)
    rows = {r["category"]: r for r in mp.fund_summary(p, cfg, prices, live_count=1)["rows"]}
    intraday, momentum, swing = rows["intraday"], rows[cb.MOMENTUM], rows["swing"]
    assert (intraday["booked"], intraday["closed"]) == (-1_000.0, 1)
    assert (intraday["released_today"], intraday["closed_today"]) == (5_000.0, 1)
    assert (momentum["booked"], momentum["closed"]) == (4_000.0, 1)
    assert (momentum["released_today"], momentum["closed_today"]) == (0.0, 0)   # closed two days ago
    assert (swing["booked"], swing["closed"], swing["open"]) == (0.0, 0, 1)
    assert rows["positional"]["closed"] == 0 and rows[cb.OCEAN]["closed"] == 0


def test_the_two_lines_end_on_the_same_rupee(tmp_path):
    cfg, p, prices = _book(tmp_path)
    f = mp.fund_summary(p, cfg, prices, live_count=1)
    rc = f["reconcile"]
    equity = p.equity(prices)
    assert rc["capital"] == cfg.starting_capital
    assert rc["unrealised"] == 10_000.0 == f["pnl"]
    # everything that is not open P&L: 3,500 listed + 2,000 archived - 50 charge
    assert rc["realised"] == pytest.approx(5_450.0)
    assert rc["capital"] + rc["realised"] + rc["unrealised"] == pytest.approx(equity)
    assert rc["cash"] + rc["used_now"] == pytest.approx(equity)
    assert rc["equity"] == equity and rc["balances"] is True
    # the listed trades are shown beside it and the gap is named, not hidden
    assert (rc["listed_booked"], rc["listed_closed"]) == (3_500.0, 3)
    assert rc["other"] == pytest.approx(1_950.0)
    assert (rc["released_today"], rc["booked_today"], rc["closed_today"]) == (5_000.0, -1_000.0, 1)
    assert rc["total_result"] == pytest.approx(15_450.0)
    # a listed trade whose record names no segment is counted once, on its own
    assert (rc["unplaced"]["closed"], rc["unplaced"]["booked"]) == (1, 500.0)


def test_a_book_with_no_closed_trade_and_no_stored_base_still_balances(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.capital_base = 0.0                      # a book saved before the base was stored
    p.positions = {"OPEN.NS": _pos("OPEN.NS", 100, 1000.0, "swing")}
    p.cash = cfg.starting_capital - 100_000.0
    rc = mp.fund_summary(p, cfg, {"OPEN.NS": 900.0}, live_count=1)["reconcile"]
    assert rc["capital"] == cfg.starting_capital
    assert (rc["realised"], rc["unrealised"]) == (pytest.approx(0.0), -10_000.0)
    assert rc["listed_closed"] == 0 and rc["closed_today"] == 0 and rc["balances"] is True


def test_a_capital_change_in_settings_is_not_read_as_profit(tmp_path):
    cfg, p, prices = _book(tmp_path)
    before = mp.fund_summary(p, cfg, prices, 1)["reconcile"]["realised"]
    cfg.starting_capital += 10_000_000.0       # owner raises the paper capital
    p.sync_capital(cfg)
    rc = mp.fund_summary(p, cfg, prices, 1)["reconcile"]
    assert rc["capital"] == cfg.starting_capital
    assert rc["realised"] == pytest.approx(before) and rc["balances"] is True


@pytest.mark.parametrize("damage", ["no_exit_date", "text_exit_date", "no_net", "text_qty"])
def test_an_odd_stored_trade_never_breaks_the_block(tmp_path, damage):
    cfg, p, prices = _book(tmp_path)
    odd = _trade("ODD.NS", 10, 100.0, 250.0, mode="swing")
    if damage == "no_exit_date":
        odd.exit_date = None
    elif damage == "text_exit_date":
        odd.exit_date = "2026-11-26T05:00:00"
    elif damage == "no_net":
        odd.net_pnl = None
    else:
        odd.qty = "ten"
    p.trades.append(odd)
    f = mp.fund_summary(p, cfg, prices, live_count=1)
    assert f["reconcile"]["balances"] is True
    swing = {r["category"]: r for r in f["rows"]}["swing"]
    assert swing["closed"] == (0 if damage == "text_qty" else 1)


def test_the_dashboard_prints_both_lines_and_the_segment_columns(tmp_path):
    cfg, book, prices = _book(tmp_path)
    p = state.portfolio()
    saved = (dict(p.positions), list(p.trades), p.cash, p.capital_base)
    try:
        p.positions, p.trades = book.positions, book.trades
        p.capital_base = state.cfg().starting_capital
        p.cash = p.capital_base - 100_000.0 - 50.0 + 3_500.0 + 2_000.0
        html = " ".join(_render(marks=(prices, True)).split())
    finally:
        p.positions, p.trades, p.cash, p.capital_base = saved
    page = _text(html)
    assert "fund_reconcile_marker" in html
    assert "Does it add up?" in page and "yes — both lines end on the same rupee" in page
    assert "NO — the two lines differ" not in page
    assert "+ booked ₹5,450" in page and "+ open P&amp;L ₹10,000" in page
    assert "Closed trades listed in the book: 3, net +₹3,500" in page
    assert "the other +₹1,950 is older trades no longer listed here" in page
    assert "₹5,000 released by 1 close(s), booked −₹1,000" in page
    assert "1 listed trade(s), net +₹500 , carry no segment" in page
    assert 'data-fund-released="intraday">₹5,000' in html
    assert 'data-fund-booked="intraday">−₹1,000' in html
    assert "Released today" in page and "Booked P&amp;L" in page
    # a sign never separates from its rupee amount when the line wraps
    assert '<span class="whitespace-nowrap">+₹3,500</span>' in html


def test_an_empty_book_says_nothing_closed_and_still_renders():
    p = state.portfolio()
    saved = (dict(p.positions), list(p.trades), p.cash, p.capital_base)
    try:
        p.positions, p.trades = {}, []
        p.capital_base = state.cfg().starting_capital
        p.cash = p.capital_base
        html = _render()
    finally:
        p.positions, p.trades, p.cash, p.capital_base = saved
    page = _text(html)
    assert "fund_reconcile_marker" in html and "no position closed" in page
    assert "Closed trades listed in the book: 0, net +₹0 ." in page
