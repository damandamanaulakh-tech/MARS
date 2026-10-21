"""Review of the trade price audit + correction (owner order 2026-10-21).

Each test pins a defect the review found in the first build:
- a restated trade with a fractional share moved cash by frac × exit on top
  of the net delta (the cash and the record's net then disagree);
- a flagged trade kept its in-range side's stored price, while the order
  was "BOTH sides, even if only one was flagged";
- a forced second correction of an open position journaled a second
  TRADE_PRICE_CORRECTED with nothing changed;
- the redirect carried raw text, so a reason with '&' or '#' was cut off;
- the cached result was keyed on counts + cash only, so a correction or an
  ex-date adjustment that left both alone kept showing the old verdict;
- an archived copy of a live trade (a save that died mid-trim) was listed
  twice; every minute fetch was cached as ~150 KB of dicts; a failed NSE
  call was asked twice per name; the POST did the whole audit on the event
  loop, with nothing stopping a double tap.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.costs import slippage_price, trade_costs
from trading.paper import ClosedTrade, PaperPosition
from web import auth as auth_mod, db, state, trade_audit as ta
from web.main import app

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[2]
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"


def _plain_daily(low=98.0, high=104.0, close=101.0):
    return [{"date": d, "open": 100.0, "high": high, "low": low, "close": close}
            for d in ("2026-10-01", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08")]


def _minute(date_iso, prices: dict):
    out = []
    for hhmm, c in prices.items():
        h, m = hhmm.split(":")
        ts = datetime.fromisoformat(f"{date_iso}T{h}:{m}:00").replace(tzinfo=IST)
        out.append({"ts": ts, "open": c, "high": c, "low": c, "close": c})
    return out


def _trade(ticker="ABC.NS", qty=100, entry=100.5, exit_=103.0, reason="TARGET",
           entry_dt=datetime(2026, 10, 1, 4, 5), exit_dt=datetime(2026, 10, 8, 5, 0), costs=10.0):
    gross = (exit_ - entry) * qty
    return ClosedTrade(ticker=ticker, qty=qty, entry_date=entry_dt, exit_date=exit_dt,
                       entry_price=entry, exit_price=exit_, gross_pnl=gross, costs=costs,
                       net_pnl=gross - costs, reason=reason, sector="IT", entry_score=70.0,
                       reasoning="fixture")


@pytest.fixture(autouse=True)
def _clean():
    ta.clear_caches()
    yield
    ta.clear_caches()


# ----------------------------------------------------------- pure maths --
def test_a_restated_fraction_moves_cash_by_exactly_the_net_delta(tmp_path):
    """Bonus 1:2 (× 1.5) on 7 shares: 10 whole shares + half a share. The
    half is returned at its cost, as the ex-date adjuster does — the cash
    change is new net − old net, nothing on top."""
    cfg = Config(state_dir=str(tmp_path))
    daily = [{"date": "2026-10-01", "open": 66, "high": 68, "low": 65, "close": 67},
             {"date": "2026-10-08", "open": 70, "high": 71, "low": 69, "close": 70}]
    t = _trade(ticker="BON.NS", qty=7, entry=100.0, exit_=70.0, reason="STOP_LOSS")
    ca_rows = [{"symbol": "BON", "exDate": "05-Oct-2026", "subject": "Bonus 1:2"}]
    r = ta.audit_trade(t, lambda *a: daily, lambda *a: None, ca_rows, cfg=cfg)
    rs = r["restate"]
    assert rs["ratio"] == 1.5 and rs["qty"] == 10 and rs["frac_shares"] == 0.5
    assert abs(r["delta_cash"] - (r["corrected"]["net"] - t.net_pnl)) < 1e-9
    # the record and the cash agree: half a share returned at cost
    assert abs(rs["frac_cash"] - 0.5 * (100.0 / 1.5)) < 1e-9


def test_a_flagged_trade_is_rewritten_on_both_sides(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(entry=250.0, exit_=103.0)          # buy impossible, sell in range
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0}),
            "2026-10-08": _minute("2026-10-08", {"10:30": 102.6})}
    r = ta.audit_trade(t, lambda *a: _plain_daily(),
                       lambda tk, d, i: mins.get(d) if i == 1 else None, [], cfg=cfg)
    assert r["buy"]["flag"] == ta.OUT_OF_RANGE and r["sell"]["flag"] == ta.OK
    assert abs(r["corrected"]["entry"] - slippage_price(100.0, "BUY", cfg)) < 1e-9
    assert abs(r["corrected"]["exit"] - slippage_price(102.6, "SELL", cfg)) < 1e-9
    assert abs(r["corrected"]["costs"] - trade_costs(100, r["corrected"]["entry"],
                                                     r["corrected"]["exit"], cfg)) < 1e-9


def test_an_in_range_side_with_only_a_daily_close_keeps_its_price(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(entry=250.0, exit_=103.0)
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0})}   # none for the sell day
    r = ta.audit_trade(t, lambda *a: _plain_daily(),
                       lambda tk, d, i: mins.get(d) if i == 1 else None, [], cfg=cfg)
    assert r["sell"]["flag"] == ta.OK and r["corrected"]["exit"] == 103.0


def test_an_unflagged_trade_stays_as_stored_and_force_rewrites_it(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade()
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.4}),
            "2026-10-08": _minute("2026-10-08", {"10:30": 103.1})}
    r = ta.audit_trade(t, lambda *a: _plain_daily(),
                       lambda tk, d, i: mins.get(d) if i == 1 else None, [], cfg=cfg)
    assert not r["flagged"] and r["delta_cash"] == pytest.approx(r["corrected"]["net"] - t.net_pnl)
    assert r["corrected"]["entry"] == 100.5 and r["corrected"]["exit"] == 103.0
    assert abs(r["corrected_force"]["entry"] - slippage_price(100.4, "BUY", cfg)) < 1e-9


def test_a_trade_closed_before_a_later_split_is_not_flagged(tmp_path):
    """Both legs before the ex-date: the candles are back-adjusted for both,
    the stored prices are right on their own days and on one basis — not a
    fault, nothing to correct (the first build flagged it and offered a
    Correct button that could only be refused)."""
    cfg = Config(state_dir=str(tmp_path))
    daily = [{"date": "2026-09-25", "open": 164.2, "high": 166.5, "low": 161.0, "close": 162.3},
             {"date": "2026-10-01", "open": 165.5, "high": 167.4, "low": 159.15, "close": 161.0}]
    t = _trade(ticker="BLSE.NS", entry=324.0, exit_=322.0, reason="SCORE_DECAY",
               entry_dt=datetime(2026, 9, 25, 4, 0), exit_dt=datetime(2026, 10, 1, 5, 0))
    r = ta.audit_trade(t, lambda *a: daily, lambda *a: None,
                       [{"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": SPLIT}], cfg=cfg)
    assert r["buy"]["flag"] == ta.OK and r["sell"]["flag"] == ta.OK
    assert not r["flagged"] and r["restate"] is None and r["delta_cash"] == pytest.approx(0.0)
    assert "one basis" in r["buy"]["note"]


def test_the_blse_trade_corrects_to_about_minus_one_percent(tmp_path):
    """The owner's case on the real BLSE candles (fetched 2026-10-21 under
    the new key INE0NLT01028): a −50% booked loss is a ~−1.6% move."""
    cfg = Config(state_dir=str(tmp_path))
    daily = [{"date": "2026-10-01", "open": 165.5, "high": 167.4, "low": 159.15, "close": 161.0},
             {"date": "2026-10-07", "open": 158.3, "high": 160.75, "low": 156.5, "close": 158.2}]
    qty, e, x = 468, 320.16, 157.47
    c0 = trade_costs(qty, e, x, cfg)
    t = ClosedTrade(ticker="BLSE.NS", qty=qty, entry_date=datetime(2026, 10, 1, 4, 5),
                    exit_date=datetime(2026, 10, 7, 5, 0), entry_price=e, exit_price=x,
                    gross_pnl=(x - e) * qty, costs=c0, net_pnl=(x - e) * qty - c0,
                    reason="STOP_LOSS", sector="IT", entry_score=70.0, reasoning="replay")
    r = ta.audit_trade(t, lambda *a: daily, lambda *a: None,
                       [{"symbol": "BLSE", "exDate": "06-Oct-2026", "subject": SPLIT}], cfg=cfg)
    c = r["corrected"]
    assert r["restate"]["qty"] == 936 and abs(c["entry"] - 160.08) < 1e-6 and c["exit"] == x
    booked = t.net_pnl / (qty * e)
    real = c["net"] / (936 * c["entry"])
    assert -0.52 < booked < -0.49
    assert -0.025 < real < -0.010
    assert r["delta_cash"] == pytest.approx(c["net"] - t.net_pnl)


# ------------------------------------------------------- audit_all cache --
class _Book:
    def __init__(self, trades=(), positions=()):
        self.trades, self.positions = list(trades), {p.ticker: p for p in positions}
        self.cash, self.decision_journal = 1_000_000.0, []


def _fetchers(calls=None):
    def fd(t, a, b):
        if calls is not None:
            calls.append(t)
        return _plain_daily()
    return (fd, lambda *a: None, lambda s: [], lambda t: "")


def test_a_book_change_with_the_same_counts_and_cash_is_re_checked(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    book = _Book(trades=[_trade(entry=250.0)])
    r1 = ta.run_audit(book, [], cfg=cfg, fetchers=_fetchers())
    assert r1["rows"][0]["flagged"]
    book.trades[0].entry_price = 100.6             # e.g. corrected elsewhere, cash untouched
    r2 = ta.run_audit(book, [], cfg=cfg, fetchers=_fetchers())
    assert r2 is not r1 and not r2["rows"][0]["flagged"]


def test_an_archived_copy_of_a_live_trade_is_listed_once(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade()
    book = _Book(trades=[t])
    res = ta.audit_all(book, [t.to_dict()], *_fetchers()[:2], lambda s: [], cfg=cfg, budget=0)
    assert res["summary"]["total"] == 1 and res["rows"][0]["archived"] is False


def test_minute_candles_are_cached_compact_and_pick_the_same_bar(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    mins = _minute("2026-10-01", {"09:33": 100.2, "09:34": 100.4, "09:36": 101.9})
    book = _Book(trades=[_trade()])
    res = ta.audit_all(book, [], lambda *a: _plain_daily(),
                       lambda tk, d, i: mins if (i == 1 and d == "2026-10-01") else None,
                       lambda s: [], cfg=cfg, budget=0)
    assert res["rows"][0]["buy"]["minute_price"] == 100.4
    cached = [v for k, v in ta._CANDLES.items() if k[0] == "m"]
    assert cached and all(isinstance(v[1], ta._Bars) for v in cached)
    fill = datetime(2026, 10, 1, 9, 35, tzinfo=IST)
    assert ta.minute_price(ta._Bars(mins), fill, 1) == ta.minute_price(mins, fill, 1)


def test_a_failed_nse_call_is_asked_once_per_name(monkeypatch):
    import trading.corporate_actions as tca
    calls = []
    monkeypatch.setattr(tca, "fetch_nse_actions", lambda sym, **k: calls.append(sym))
    ta.ca_rows_for("BLSE")
    ta.ca_complete_for("BLSE")
    ta.ca_rows_for("BLSE.NS")
    assert calls == ["BLSE"]


def test_a_running_rotation_gets_a_note_not_a_fetch(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    calls = []
    res = ta.run_audit(_Book(trades=[_trade()]), [], cfg=cfg, fetchers=_fetchers(calls),
                       busy=lambda: True)
    assert res.get("busy") and "rotation" in res["error"] and calls == []


# -------------------------------------------------------------- the page --
def _owner():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


@pytest.fixture()
def book(monkeypatch):
    p = state.portfolio()
    saved = (list(p.trades), dict(p.positions), p.cash, list(p.decision_journal))
    p.trades = []
    p.positions = {}
    p.cash = 1_000_000.0
    monkeypatch.setattr(db, "count_pre_mars", lambda coll: 0)
    monkeypatch.setattr(db, "list_trades", lambda limit=0, include_pre_mars=False, with_ids=False: [])
    try:
        yield p
    finally:
        p.trades, p.positions, p.cash, p.decision_journal = saved
        ta.clear_caches()


def test_a_forced_second_correction_of_an_open_position_journals_nothing(book, monkeypatch):
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0})}
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: (
        lambda *a: _plain_daily(), lambda tk, d, i: mins.get(d) if i == 1 else None,
        lambda s: [], lambda t: ""))
    pos = PaperPosition(ticker="POSX.NS", qty=10, entry_price=250.0,
                        entry_date=datetime(2026, 10, 1, 4, 5), sector="IT", peak_price=250.0,
                        entry_score=70.0, reasoning="f")
    book.positions = {pos.ticker: pos}
    c = _owner()
    rid = ta.position_id(pos)
    r1 = c.post("/trades/correct", data={"id": rid}, follow_redirects=False)
    assert "corrected=1" in r1.headers["location"]
    cash1 = book.cash
    r2 = c.post("/trades/correct", data={"id": rid, "force": "1"}, follow_redirects=False)
    q = parse_qs(urlparse(r2.headers["location"]).query)
    assert q["corrected"] == ["0"] and "nothing to change" in q["refused"][0]
    assert book.cash == cash1
    assert sum(1 for e in book.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED") == 1


def test_a_journaled_id_is_refused_without_force(book, monkeypatch):
    t = _trade(ticker="JRN.NS", entry=250.0)
    book.trades = [t]
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: _fetchers())
    rid = ta.trade_id(t)
    book.decision_journal.append({"ts": "2026-10-20T05:00:00", "action": "TRADE_PRICE_CORRECTED",
                                  "id": rid})
    r = _owner().post("/trades/correct", data={"id": rid}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["corrected"] == ["0"] and "already corrected" in q["refused"][0]


def test_the_redirect_keeps_a_message_with_ampersand_and_hash(book, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("a & b #c")
    monkeypatch.setattr(ta, "run_audit", boom)
    r = _owner().post("/trades/correct", data={"all_flagged": "1"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["audit_error"] == ["RuntimeError: a & b #c"]


def test_the_post_runs_off_the_event_loop_under_the_apply_lock():
    src = "\n".join(ln for ln in (ROOT / "web" / "routes" / "positions.py").read_text().splitlines()
                    if not ln.strip().startswith("#"))
    post = src[src.index("async def correct_trade_prices"):src.index("def _correct_trades")]
    assert "run_in_threadpool(_correct_trades" in post
    assert "apply_corrections(" not in post and "run_audit(" not in post
    work = src[src.index("def _correct_trades"):]
    work = work[:work.index("\n@router")]
    assert re.search(r"with trade_audit\.APPLY_LOCK:", work)
    assert work.index("APPLY_LOCK") < work.index("_run_trade_audit(") < work.index("p.save()")


def test_a_busy_audit_page_says_why_and_not_that_the_book_is_empty(book, monkeypatch):
    from web import scheduler
    book.trades = [_trade(ticker="BUSY.NS")]
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: pytest.fail("fetched while busy"))
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        body = _owner().get("/trades?audit=1").text
    finally:
        scheduler._ROTATION_LOCK.release()
    assert "trade_audit_error_marker" in body and "rotation is running" in body
    assert "No closed trades or open positions to check" not in body
