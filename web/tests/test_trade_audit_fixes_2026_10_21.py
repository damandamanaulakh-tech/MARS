"""Review fixes to the trade price audit (owner order 2026-10-21, BLSE).

Each test fails on the build before the review:
- a row whose minute fetch the time budget skipped was shown as CHECKED,
  its real price a daily close labelled "no minute candles served" (and a
  correction would have been priced off it);
- a restated ARCHIVED trade (qty × ratio) lost its row id — the id was
  rebuilt from the new qty while the Mongo _id keeps the old one — so a
  second correction could never reach it;
- a restated LIVE trade's id moved with its qty and the journal kept only
  the old one, so "already corrected" never fired for it;
- a stale audit row could restate a position the ex-date adjuster had
  already restated (a second TRADE_PRICE_CORRECTED, a doubled ca_applied);
- the correction did not wait for the book (an exit pass or scan holding
  the mode lock), unlike every other owner tap (audit S-6);
- the "Why" column printed the owner BUY button's borrowed fill_source as
  its own, missed limit fills (OWNER_ORDER_FILLED) and printed a bare
  "SELL" for an engine exit that journals no quote source;
- the history re-fetch read the instrument lookup (which can download the
  Upstox master) even with no client to fetch with;
- a refused open position was named "POS" instead of its ticker.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from trading import corporate_actions as tca
from trading.config import Config
from trading.paper import ClosedTrade, PaperPosition
from web import auth as auth_mod, db, scheduler, state, trade_audit as ta
from web.main import app

IST = ZoneInfo("Asia/Kolkata")
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
BLSE_CA = [{"symbol": "BLSE", "exDate": "06-Oct-2026", "isin": "INE0NLT01010", "subject": SPLIT}]


def _blse_daily():
    rows = [("2026-09-30", 161.5, 166.0, 160.55, 164.3), ("2026-10-01", 165.5, 167.4, 159.15, 161.0),
            ("2026-10-05", 162.5, 163.9, 158.0, 159.6), ("2026-10-06", 163.7, 164.0, 160.05, 160.9),
            ("2026-10-07", 158.3, 160.75, 156.5, 158.2), ("2026-10-08", 156.15, 160.8, 155.2, 157.55)]
    return [{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows]


def _plain_daily():
    return [{"date": d, "open": 100.0, "high": 104.0, "low": 98.0, "close": 101.0}
            for d in ("2026-10-01", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08")]


def _minute(date_iso, prices: dict):
    out = []
    for hhmm, c in prices.items():
        h, m = hhmm.split(":")
        ts = datetime.fromisoformat(f"{date_iso}T{h}:{m}:00").replace(tzinfo=IST)
        out.append({"ts": ts, "open": c, "high": c, "low": c, "close": c})
    return out


def _trade(ticker="ABC.NS", qty=100, entry=100.5, exit_=103.0, reason="TARGET",
           entry_dt=datetime(2026, 10, 1, 4, 5), exit_dt=datetime(2026, 10, 7, 5, 0), costs=10.0):
    gross = (exit_ - entry) * qty
    return ClosedTrade(ticker=ticker, qty=qty, entry_date=entry_dt, exit_date=exit_dt,
                       entry_price=entry, exit_price=exit_, gross_pnl=gross, costs=costs,
                       net_pnl=gross - costs, reason=reason, sector="IT", entry_score=70.0,
                       reasoning="fixture")


class _Book:
    def __init__(self, trades=(), positions=()):
        self.trades, self.positions = list(trades), {p.ticker: p for p in positions}
        self.cash, self.decision_journal = 1_000_000.0, []


@pytest.fixture(autouse=True)
def _clean():
    ta.clear_caches()
    yield
    ta.clear_caches()


def _owner():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


@pytest.fixture()
def book(monkeypatch):
    p = state.portfolio()
    saved = (list(p.trades), dict(p.positions), p.cash, list(p.decision_journal))
    p.trades, p.positions, p.cash = [], {}, 1_000_000.0
    monkeypatch.setattr(db, "count_pre_mars", lambda coll: 0)
    monkeypatch.setattr(db, "list_trades", lambda limit=0, include_pre_mars=False, with_ids=False: [])
    try:
        yield p
    finally:
        p.trades, p.positions, p.cash, p.decision_journal = saved
        ta.clear_caches()


def _q(resp):
    return parse_qs(urlparse(resp.headers["location"]).query)


# ------------------------------------------------------------- the budget --
def test_a_row_the_budget_cut_before_its_minute_fetch_is_not_checked(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path))
    book = _Book(trades=[_trade(entry=250.0)])        # flagged if it were checked
    clock = [0.0]
    monkeypatch.setattr(ta.time, "monotonic", lambda: (clock.__setitem__(0, clock[0] + 100),
                                                      clock[0])[1])
    asked = []
    res = ta.audit_all(book, [], lambda *a: _plain_daily(),
                       lambda *a: (asked.append(a), _minute("2026-10-01", {"09:35": 100.0}))[1],
                       lambda s: [], cfg=cfg, budget=150)
    assert asked == []                                 # the minute fetch was cut
    row = res["rows"][0]
    assert row["checked"] is False and row["note"] == "not checked this open — tap again"
    assert row["corrected"] is None and res["stopped"] and res["summary"]["unchecked"] == 1
    assert res["summary"]["cash_delta"] == 0


# ---------------------------------------------------------- archive id --
def _fake_archive(monkeypatch, docs: dict):
    """A Mongo `trades` stand-in: list_trades returns the stored docs (with
    _id when asked), update_trade_prices $sets fields by _id."""
    def list_trades(limit=0, include_pre_mars=False, with_ids=False):
        out = []
        for d in docs.values():
            row = dict(d)
            if not with_ids:
                row.pop("_id", None)
            out.append(row)
        return out
    calls = []

    def update(rid, fields):
        calls.append((rid, dict(fields)))
        if rid not in docs:
            return False
        docs[rid].update(fields)
        return True
    monkeypatch.setattr(db, "list_trades", list_trades)
    monkeypatch.setattr(db, "update_trade_prices", update)
    return calls


def test_a_restated_archived_trade_keeps_its_archive_id_and_is_never_divided_twice(
        book, monkeypatch):
    t = _trade(ticker="BLSE.NS", qty=100, entry=320.16, exit_=157.47, reason="STOP_LOSS")
    aid = db._trade_key(t.to_dict())
    docs = {aid: {**t.to_dict(), "_id": aid, "pre_mars": False}}
    calls = _fake_archive(monkeypatch, docs)
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 160.5}),
            "2026-10-07": _minute("2026-10-07", {"10:30": 157.6})}
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: (
        lambda *a: _blse_daily(), lambda tk, d, i: mins.get(d) if i == 1 else None,
        lambda s: BLSE_CA if s == "BLSE" else [], lambda tk: ""))
    c = _owner()
    r1 = c.post("/trades/correct", data={"id": aid}, follow_redirects=False)
    assert _q(r1)["corrected"] == ["1"]
    assert calls[-1][0] == aid and docs[aid]["qty"] == 200
    assert abs(docs[aid]["entry_price"] - 160.08) < 1e-6
    line = [e for e in book.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED"][-1]
    assert line["id"] == aid and line["new_id"] == aid
    # the re-read finds the restated row under the SAME id, now on one basis
    res = ta.run_audit(book, list(docs.values()), cfg=state.cfg())
    row = next(r for r in res["rows"] if r["ticker"] == "BLSE.NS")
    assert row["id"] == aid and not row["flagged"] and row["restate"] is None
    # a forced second tap reprices to the minute candles — it never divides again
    r2 = c.post("/trades/correct", data={"id": aid, "force": "1"}, follow_redirects=False)
    assert _q(r2)["corrected"] == ["1"]
    assert calls[-1][0] == aid and docs[aid]["qty"] == 200
    assert 159.0 < docs[aid]["entry_price"] < 162.0


def test_a_restated_live_trade_journals_its_new_id_and_refuses_a_retap(book, monkeypatch):
    t = _trade(ticker="BLSE.NS", qty=100, entry=320.16, exit_=157.47, reason="STOP_LOSS")
    book.trades = [t]
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: (
        lambda *a: _blse_daily(), lambda *a: None,
        lambda s: BLSE_CA if s == "BLSE" else [], lambda tk: ""))
    old_id = ta.trade_id(t)
    c = _owner()
    assert _q(c.post("/trades/correct", data={"id": old_id}, follow_redirects=False))[
        "corrected"] == ["1"]
    assert t.qty == 200
    line = [e for e in book.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED"][-1]
    assert line["id"] == old_id and line["new_id"] == ta.trade_id(t) != old_id
    q = _q(c.post("/trades/correct", data={"id": ta.trade_id(t)}, follow_redirects=False))
    assert q["corrected"] == ["0"] and "already corrected" in q["refused"][0]
    assert t.qty == 200 and abs(t.entry_price - 160.08) < 1e-6


# ------------------------------------------------------- stale + locking --
def test_a_stale_row_never_restates_what_the_adjuster_already_restated(tmp_path):
    """A 1:100 bonus leaves 50 shares at 50 (floor 50.5), so the position id
    does not move: the stale audit row still finds it. The adjuster ran in
    between — the correction must refuse, not journal a second restatement."""
    cfg = Config(state_dir=str(tmp_path))
    pos = PaperPosition(ticker="TINYB.NS", qty=50, entry_price=101.0,
                        entry_date=datetime(2026, 10, 1, 4, 5), sector="IT", peak_price=101.0,
                        entry_score=70.0, reasoning="f", stop_price=96.0, target_price=120.0)
    book = _Book(positions=[pos])
    rows = [{"symbol": "TINYB", "exDate": "06-Oct-2026", "subject": "Bonus 1:100"}]
    res = ta.audit_all(book, [], lambda *a: _plain_daily(), lambda *a: None,
                       lambda s: rows, cfg=cfg, budget=0)
    row = res["rows"][0]
    assert row["restate"] and row["restate"]["ratio"] == pytest.approx(1.01)
    # the ex-date adjuster restates the position after the audit read
    act = tca.parse_rows([{**rows[0], "source": "nse_api"}])[0]
    body = tca.apply_to_position(pos, act)
    book.cash += body["cash_credit"]
    assert pos.qty == 50 and ta.position_id(pos) == row["id"]
    out = ta.apply_corrections(book, res, [row["id"]], cfg=cfg)
    assert out["done"] == [] and "changed since the audit read" in out["refused"][0]["why"]
    assert pos.ca_applied == ["TINYB|2026-10-06"]
    assert not [e for e in book.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED"]


def test_the_correction_waits_for_the_book_like_every_owner_tap(book, monkeypatch):
    t = _trade(ticker="LOCKD.NS", entry=250.0)
    book.trades = [t]
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0})}
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: (
        lambda *a: _plain_daily(), lambda tk, d, i: mins.get(d) if i == 1 else None,
        lambda s: [], lambda tk: ""))
    import trading.modes as modes
    monkeypatch.setattr(modes, "MODE_LOCK_WAIT_S", 0.2)
    cash0 = book.cash
    c = _owner()
    assert modes.MODE_LOCK.acquire(blocking=False)     # a scan pass holds the book
    try:
        r = c.post("/trades/correct", data={"id": ta.trade_id(t)}, follow_redirects=False)
    finally:
        modes.MODE_LOCK.release()
    q = _q(r)
    assert "holds the book" in q["audit_error"][0]
    assert t.entry_price == 250.0 and book.cash == cash0
    assert not [e for e in book.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED"]
    # with the book free the same tap goes through
    assert _q(c.post("/trades/correct", data={"id": ta.trade_id(t)},
                     follow_redirects=False))["corrected"] == ["1"]


def test_a_refused_open_position_is_named_by_its_ticker(book, monkeypatch):
    monkeypatch.setattr(ta, "upstox_fetchers", lambda: (
        lambda *a: _plain_daily(), lambda *a: None, lambda s: [], lambda tk: ""))
    pos = PaperPosition(ticker="POSX.NS", qty=10, entry_price=101.0,
                        entry_date=datetime(2026, 10, 1, 4, 5), sector="IT", peak_price=101.0,
                        entry_score=70.0, reasoning="f")
    book.positions = {pos.ticker: pos}
    q = _q(_owner().post("/trades/correct", data={"id": ta.position_id(pos)},
                         follow_redirects=False))
    assert q["refused"][0].startswith("POSX.NS: not flagged")


# ------------------------------------------------------------ why column --
def test_the_why_column_never_repeats_a_borrowed_or_missing_source():
    j = [{"ts": "2026-10-01T04:05:00", "action": "BUY", "ticker": "ABC.NS",
          "fill_source": "prev_close", "why": "OWNER BUY CALL — user pushed this YES pick"},
         {"ts": "2026-10-02T05:00:00", "action": "BUY", "ticker": "LIM.NS",
          "fill_source": "limit_order", "why": "OWNER LIMIT ORDER x filled"},
         {"ts": "2026-10-02T05:00:01", "action": "OWNER_ORDER_FILLED", "ticker": "LIM.NS"},
         {"ts": "2026-10-03T06:00:00", "action": "SELL", "ticker": "ENG.NS",
          "exit_reason": "STOP_LOSS"}]
    own = ta.journal_source(j, "ABC.NS", datetime(2026, 10, 1, 4, 5))
    assert own.startswith("OWNER BUY button") and "not this fill's" in own
    lim = ta.journal_source(j, "LIM.NS", datetime(2026, 10, 2, 5, 0))
    assert "OWNER_ORDER_FILLED: limit order" in lim and "BUY: limit_order" in lim
    assert ta.journal_source(j, "ENG.NS", datetime(2026, 10, 3, 6, 0)) == \
        "SELL: quote source not journaled"


# ------------------------------------------------- refetch + db helpers --
def test_the_history_refetch_reads_no_lookup_without_a_client(monkeypatch):
    import trading.brokers.upstox_instruments as ui
    asked = []
    monkeypatch.setattr(ui, "to_instrument_key", lambda t: (asked.append(t), "NSE_EQ|X")[1])
    monkeypatch.setattr("trading.data.history_broker", lambda: None)
    monkeypatch.setattr(state.engine(), "broker", None)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    try:
        assert scheduler._refetch_history("NOCLIENT.NS", "test") is False
        assert asked == []
        assert any(e.get("action") == "HISTORY_REFETCH_FAILED" and "no Upstox client" in e["err"]
                   for e in p.decision_journal[n0:])
    finally:
        del p.decision_journal[n0:]


def test_list_trades_keeps_the_archive_id_only_when_asked(monkeypatch):
    seen = []

    class _Cur(list):
        def sort(self, *a):
            return self

        def limit(self, n):
            return self

    class _Trades:
        def find(self, q, proj=None):
            seen.append(proj)
            return _Cur([{"_id": "T|a|b|1|X", "ticker": "T"}])

    class _DB:
        trades = _Trades()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", _DB())
    db.list_trades(include_pre_mars=True)
    db.list_trades(include_pre_mars=True, with_ids=True)
    assert seen == [{"_id": 0}, None]


# ------------------------------------------------- risk baselines move --
def test_a_large_negative_correction_never_trips_the_kill_switch_or_the_ladder(tmp_path):
    """An owner-typed exit Rs 15 lakh too high (3% of Rs 5 Cr), corrected:
    the book's cash falls by the same Rs 15 lakh. That is past P&L restated,
    not a loss today — the -2% daily halt (which SELLS every quoted
    position) and the drawdown peak must move with it."""
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = 50_000_000.0
    # the owner typed 250 for a stock that traded 98-104 that day
    t = _trade(ticker="TYPO.NS", qty=10_000, entry=100.0, exit_=250.0, reason="OWNER_SELL",
               costs=0.0)
    p.trades = [t]
    eng = TradingEngine(cfg, p)
    now = datetime.utcnow()
    eng._roll_risk_day(p.equity({}), now)            # today's baseline = the book now
    p.risk_day_equity = p.equity({})
    p.dd_peak_equity = p.equity({})
    mins = {"2026-10-07": _minute("2026-10-07", {"10:30": 101.0})}
    res = ta.audit_all(p, [], lambda *a: _plain_daily(),
                       lambda tk, d, i: mins.get(d) if i == 1 else None,
                       lambda s: [], cfg=cfg, budget=0)
    row = res["rows"][0]
    assert row["sell"]["flag"] == ta.OUT_OF_RANGE and row["delta_cash"] < -1_400_000
    out = ta.apply_corrections(p, res, [row["id"]], cfg=cfg, now=now)
    assert len(out["done"]) == 1
    eq = p.equity({})
    eng._roll_risk_day(eq, now)                      # what the next pass does first
    assert eng.risk.check_kill_switch(eq) is False
    assert eng.risk.daily_pnl_pct(eq) == pytest.approx(0.0, abs=1e-9)
    assert p.dd_peak_equity == pytest.approx(eq)
    line = [e for e in p.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED"][-1]
    assert line["baselines_moved"]["risk_day_equity"] == pytest.approx(eq, abs=0.01)


# --------------------------------------------------------- NSE breaker --
def test_a_blocked_nse_stops_being_asked_after_three_failures(monkeypatch):
    """From a cloud box NSE can refuse or hang (≤ 15 s a call): the audit's
    60 s budget must not drain one name at a time."""
    calls = []

    def fetch(sym, **k):
        calls.append((sym, k.get("timeout")))
        return None
    monkeypatch.setattr(tca, "fetch_nse_actions", fetch)
    for s in ("A1", "A2", "A3", "A4", "A5"):
        assert ta.ca_complete_for(s) is False
    assert [c[0] for c in calls] == ["A1", "A2", "A3"]
    assert all(c[1] is not None and c[1] <= 6.0 for c in calls)
    assert ta.ca_rows_for("A6") == [] or all(r.get("source") != "nse_api"
                                             for r in ta.ca_rows_for("A6"))
    # NSE answering again (a later window) resets the count
    ta.clear_caches()
    monkeypatch.setattr(tca, "fetch_nse_actions", lambda sym, **k: [])
    assert ta.ca_complete_for("B1") is True


# --------------------------------------------- flags with no test before --
def test_a_buy_on_the_ex_date_at_the_stale_pre_split_close_gets_the_records_ratio(tmp_path):
    """The scan's prev_close fallback on the ex-date morning books the
    pre-split close (~Rs 320) for a post-split share (~Rs 160). The CA is
    not between buy and sell (same day as the buy), so nothing is restated:
    the fill is simply wrong, the hint names the record's ratio, and the
    correction prices the buy at the real minute candle."""
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(ticker="BLSE.NS", entry=320.30, exit_=157.47, reason="STOP_LOSS",
               entry_dt=datetime(2026, 10, 6, 3, 50), exit_dt=datetime(2026, 10, 7, 5, 0))
    mins = {"2026-10-06": _minute("2026-10-06", {"09:20": 161.2})}
    r = ta.audit_trade(t, lambda *a: _blse_daily(),
                       lambda tk, d, i: mins.get(d) if i == 1 else None, BLSE_CA, cfg=cfg)
    assert r["buy"]["flag"] == ta.OUT_OF_RANGE and r["buy"]["factor"] == 1.0
    assert r["buy"]["ratio_hint"] == 0.5 and not r["needs_ca_record"]
    assert r["restate"] is None and r["ca_between"] == []
    from trading.costs import slippage_price
    assert r["corrected"]["entry"] == pytest.approx(slippage_price(161.2, "BUY", cfg))
    assert r["corrected"]["qty"] == 100 and r["delta_cash"] > 15_000


def test_an_out_of_range_fill_on_a_relisted_share_reads_old_isin(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(ticker="RELIST.NS", entry=250.0)
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0})}
    note = "ISIN changed: Upstox master lists INE000X01028, the universe file INE000X01010"
    r = ta.audit_trade(t, lambda *a: _plain_daily(),
                       lambda tk, d, i: mins.get(d) if i == 1 else None, [], cfg=cfg,
                       isin_note=note, ca_complete=True)
    assert r["buy"]["flag"] == ta.OLD_ISIN and r["flagged"] and "OLD_ISIN" in r["flags"]
    assert note in r["buy"]["note"] and r["corrected"]["entry"] < 101.0


def test_an_owner_restated_position_is_never_restated_again_by_the_adjuster(tmp_path):
    """The audit's restatement records the action under the adjuster's own
    key, so the next 09:20 / exit pass skips it (qty stays doubled once)."""
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    pos = PaperPosition(ticker="BLSE.NS", qty=100, entry_price=320.16,
                        entry_date=datetime(2026, 10, 1, 4, 5), sector="IT", peak_price=330.0,
                        entry_score=70.0, reasoning="f", stop_price=304.15, target_price=384.19)
    p.positions = {pos.ticker: pos}
    res = ta.audit_all(p, [], lambda *a: _blse_daily(), lambda *a: None,
                       lambda s: BLSE_CA, cfg=cfg, budget=0)
    row = res["rows"][0]
    out = ta.apply_corrections(p, res, [row["id"]], cfg=cfg)
    assert len(out["done"]) == 1 and pos.qty == 200 and pos.ca_applied == ["BLSE|2026-10-06"]
    acts = tca.parse_rows([{**BLSE_CA[0], "source": "nse_api"}])
    from datetime import date
    from trading import engine as eng_mod
    before = set(eng_mod.CA_REFETCH_DUE)
    try:
        applied = TradingEngine(cfg, p).apply_corporate_actions(
            today=date(2026, 10, 7), actions_for=lambda s: acts,
            now=datetime(2026, 10, 7, 5, 0))
    finally:
        eng_mod.CA_REFETCH_DUE.clear()
        eng_mod.CA_REFETCH_DUE.update(before)
    assert applied == [] and pos.qty == 200 and abs(pos.entry_price - 160.08) < 1e-6
