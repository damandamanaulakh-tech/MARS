"""Trade price audit + owner-tapped correction (owner order 2026-10-21:
"BELS ... never go beyond 200, u r showing 49% loss, fix this with buy and
sell real price at that time, and do check all other").

Pure audit with fake fetchers (no network), the /trades?audit=1 page for a
mixed book (owner only; a viewer sees no audit and 403s on POST), the
correction moving cash by exactly the net delta and journaling once, the
archived-row path through the db helper (mocked), the time budget, and the
BLSE rule: a split between buy and sell restates the buy, keeps the sell.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.paper import ClosedTrade, PaperPosition
from web import auth as auth_mod, db, state, trade_audit as ta
from web.main import app

IST = ZoneInfo("Asia/Kolkata")
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"
BLSE_CA = [{"symbol": "BLSE", "exDate": "06-Oct-2026", "isin": "INE0NLT01010", "subject": SPLIT}]


def _daily():
    rows = [("2026-09-30", 161.5, 166.0, 160.55, 164.3), ("2026-10-01", 165.5, 167.4, 159.15, 161.0),
            ("2026-10-05", 162.5, 163.9, 158.0, 159.6), ("2026-10-06", 163.7, 164.0, 160.05, 160.9),
            ("2026-10-07", 158.3, 160.75, 156.5, 158.2), ("2026-10-08", 156.15, 160.8, 155.2, 157.55)]
    return [{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows]


def _plain_daily(low=98.0, high=104.0, close=101.0):
    return [{"date": d, "open": 100.0, "high": high, "low": low, "close": close}
            for d in ("2026-10-01", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08")]


def _minute(date_iso, prices: dict):
    """{HH:MM: close} → 1-min candles (IST, bar start)."""
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


# ------------------------------------------------------------ pure audit --
def test_in_range_fill_reads_ok_and_the_minute_candle_is_picked_by_ist_time(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade()                                   # 04:05 UTC = 09:35 IST
    mins = {"2026-10-01": _minute("2026-10-01", {"09:33": 100.2, "09:34": 100.4, "09:36": 101.9}),
            "2026-10-08": _minute("2026-10-08", {"10:29": 102.8, "10:30": 103.1})}
    r = ta.audit_trade(t, lambda *a: _plain_daily(), lambda tk, d, i: mins.get(d) if i == 1 else None,
                       [], cfg=cfg)
    assert r["buy"]["flag"] == ta.OK and r["sell"]["flag"] == ta.OK
    assert r["flagged"] is False
    # the bar at/just before the fill minute: 09:34 for a 09:35 fill
    assert r["buy"]["minute_price"] == 100.4 and "09:34" in r["buy"]["real_source"]
    assert r["buy"]["time_ist"] == "09:35"
    # 05:00 UTC = 10:30 IST → the 10:30 bar
    assert r["sell"]["minute_price"] == 103.1
    # a right fill keeps its stored price; nothing to correct
    assert r["corrected"]["entry"] == 100.5 and r["corrected"]["exit"] == 103.0
    assert abs(r["delta_cash"] - (r["corrected"]["net"] - t.net_pnl)) < 1e-9


def test_out_of_range_fill_is_flagged_and_corrected_through_trade_costs(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(entry=250.0)                        # the day never traded above 104
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0})}
    r = ta.audit_trade(t, lambda *a: _plain_daily(), lambda tk, d, i: mins.get(d) if i == 1 else None,
                       [], cfg=cfg)
    assert r["buy"]["flag"] == ta.OUT_OF_RANGE and r["flagged"]
    assert r["buy"]["ratio_hint"] is None and r["needs_ca_record"] is False
    from trading.costs import slippage_price, trade_costs
    fill = slippage_price(100.0, "BUY", cfg)
    assert abs(r["corrected"]["entry"] - fill) < 1e-9
    assert abs(r["corrected"]["costs"] - trade_costs(100, fill, 103.0, cfg)) < 1e-9
    assert abs(r["delta_cash"] - (r["corrected"]["net"] - t.net_pnl)) < 1e-9
    assert r["delta_cash"] > 0


def test_no_candle_served_is_its_own_verdict_never_a_guess(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    r = ta.audit_trade(_trade(), lambda *a: None, lambda *a: None, [], cfg=cfg)
    assert r["buy"]["flag"] == ta.NO_CANDLE and r["no_candle"] and not r["flagged"]
    assert r["buy"]["minute_price"] is None
    assert r["corrected"]["entry"] == 100.5          # stored stands


def test_the_blse_rule_restates_the_buy_and_keeps_the_sell(tmp_path):
    """Split 10→5 ex 2026-10-06 between a buy at ~320 and a stop-out at
    ~157: entry ÷ 2, qty × 2, sell as booked — a booked −49% becomes the
    real ~−2%. Cash delta = new net − old net."""
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(ticker="BLSE.NS", entry=320.16, exit_=157.47, reason="STOP_LOSS")
    r = ta.audit_trade(t, lambda *a: _daily(), lambda *a: None, BLSE_CA, cfg=cfg)
    assert r["buy"]["flag"] == ta.CA_UNADJUSTED and r["buy"]["factor"] == 2.0
    assert r["sell"]["flag"] == ta.OK
    assert "CA_BETWEEN" in r["flags"] and r["flagged"]
    assert r["ca_between"] == [f"{SPLIT} ex 2026-10-06"]
    rs = r["restate"]
    assert rs["ratio"] == 2.0 and rs["qty"] == 200 and abs(rs["entry"] - 160.08) < 1e-6
    assert r["corrected"]["exit"] == 157.47 and r["corrected"]["qty"] == 200
    from trading.costs import trade_costs
    exp_gross = (157.47 - 160.08) * 200
    exp_costs = trade_costs(200, 160.08, 157.47, cfg)
    assert abs(r["corrected"]["gross"] - exp_gross) < 1e-6
    assert abs(r["corrected"]["net"] - (exp_gross - exp_costs)) < 1e-6
    assert abs(r["delta_cash"] - (r["corrected"]["net"] - t.net_pnl)) < 1e-9
    assert r["corrected"]["net"] > -700 and t.net_pnl < -16000


def test_a_split_with_no_reachable_record_is_a_hint_never_a_correction(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    t = _trade(ticker="BLSE.NS", entry=320.16, exit_=157.47, reason="STOP_LOSS")
    r = ta.audit_trade(t, lambda *a: _daily(), lambda *a: None, [], cfg=cfg, ca_complete=False)
    assert r["buy"]["flag"] == ta.OUT_OF_RANGE
    assert r["buy"]["ratio_hint"] == 0.5 and r["buy"]["needs_ca_record"]
    assert "2-for-1 split" in r["buy"]["note"] and "DATA_NEEDED" in r["buy"]["note"]
    assert r["needs_ca_record"] and r["corrected"] is None and r["delta_cash"] == 0.0
    # NSE answered and carries no split: the same price is simply a wrong fill
    r2 = ta.audit_trade(t, lambda *a: _daily(), lambda *a: None, [], cfg=cfg, ca_complete=True)
    assert r2["buy"]["flag"] == ta.OUT_OF_RANGE and not r2["needs_ca_record"]
    assert r2["corrected"] is not None and r2["buy"]["ratio_hint"] is None


def test_open_position_with_a_pending_split_is_restated_and_an_absorbed_one_reads_ok(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    pos = PaperPosition(ticker="BLSE.NS", qty=100, entry_price=320.16,
                        entry_date=datetime(2026, 10, 1, 4, 0), sector="IT", peak_price=330.0,
                        entry_score=70.0, reasoning="f", stop_price=304.15, target_price=384.19)
    r = ta.audit_position(pos, lambda *a: _daily(), lambda *a: None, BLSE_CA, cfg=cfg)
    assert r["buy"]["flag"] == ta.CA_UNADJUSTED and "CA_BETWEEN" in r["flags"]
    assert r["restate"]["qty"] == 200 and abs(r["restate"]["entry"] - 160.08) < 1e-6
    assert r["delta_cash"] == 0.0                   # cost-neutral restatement
    pos.ca_applied = ["BLSE|2026-10-06"]
    pos.entry_price, pos.qty = 160.08, 200
    r2 = ta.audit_position(pos, lambda *a: _daily(), lambda *a: None, BLSE_CA, cfg=cfg)
    assert r2["buy"]["flag"] == ta.OK and not r2["flagged"] and r2["restate"] is None


def test_journal_source_names_the_fill_source_within_two_minutes():
    j = [{"ts": "2026-10-01T04:04:30", "action": "BUY", "ticker": "ABC.NS", "fill_source": "prev_close"},
         {"ts": "2026-10-01T04:30:00", "action": "BUY", "ticker": "ABC.NS", "fill_source": "live_quote"},
         {"ts": "2026-10-08T05:00:10", "action": "OWNER_SELL", "ticker": "ABC.NS",
          "price_source": "owner_typed"},
         {"ts": "2026-10-08T05:00:20", "action": "OWNER_BATCH_BUY", "opened": [{"ticker": "ABC.NS"}]}]
    assert ta.journal_source(j, "ABC.NS", datetime(2026, 10, 1, 4, 5)) == "BUY: prev_close"
    assert ta.journal_source(j, "ABC.NS", datetime(2026, 10, 8, 5, 0)).startswith("OWNER_SELL: owner_typed")
    assert ta.journal_source(j, "XYZ.NS", datetime(2026, 10, 1, 4, 5)) == ""


# ----------------------------------------------------- audit_all + budget --
class _Book:
    def __init__(self, trades=(), positions=()):
        self.trades, self.positions = list(trades), {p.ticker: p for p in positions}
        self.cash, self.decision_journal = 1_000_000.0, []


def test_audit_all_checks_live_and_archived_rows_and_stops_on_the_budget(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path))
    book = _Book(trades=[_trade(ticker="A.NS"), _trade(ticker="B.NS", entry=250.0)])
    arch = [{**_trade(ticker="C.NS").to_dict(), "pre_mars": True, "_id": "x"}]
    res = ta.audit_all(book, arch, lambda *a: _plain_daily(), lambda *a: None, lambda s: [],
                       cfg=cfg, budget=0)
    assert res["summary"]["total"] == 3 and res["summary"]["checked"] == 3
    assert res["summary"]["outside"] == 1 and res["summary"]["flagged_rows"] == 1
    assert not res["stopped"]
    by = {r["ticker"]: r for r in res["rows"]}
    assert by["C.NS"]["pre_mars"] and by["C.NS"]["archived"] and not by["A.NS"]["archived"]
    # a clock that is already past the budget: rows print NOT CHECKED
    ta.clear_caches()
    t = [0.0]
    monkeypatch.setattr(ta.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 100), t[0])[1])
    res2 = ta.audit_all(book, arch, lambda *a: _plain_daily(), lambda *a: None, lambda s: [],
                        cfg=cfg, budget=1)
    assert res2["stopped"] and res2["summary"]["unchecked"] == 3
    assert all(r["note"] == "not checked this open — tap again" for r in res2["rows"])


def test_instrument_keys_prefer_the_live_master_then_the_committed_file(monkeypatch):
    import trading.brokers.upstox_instruments as ui
    monkeypatch.setattr(ui, "to_instrument_key", lambda t: "NSE_EQ|NEW" if t == "BLSE.NS" else None)
    monkeypatch.setattr(ta, "_IKEYS", {"BLSE.NS": "NSE_EQ|OLD"})
    assert ta.instrument_keys("BLSE") == ["NSE_EQ|NEW", "NSE_EQ|OLD"]
    calls = []

    def fake_get(url):
        calls.append(url)
        return None if "OLD" not in url else {"data": {"candles": [["2026-10-01T00:00:00+05:30", 1, 2, 0.5, 1.5, 9, 0]]}}
    monkeypatch.setattr(ta, "_get_json", fake_get)
    out = ta.fetch_daily_upstox("BLSE.NS", "2026-09-25", "2026-10-02")
    assert out and out[0]["date"] == "2026-10-01"
    assert len(calls) == 2 and "NEW" in calls[0] and "OLD" in calls[1]


# ------------------------------------------------------------ the page --
def _owner():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _viewer(monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "viewer-test-pw", "name": "Viewer"})
    c = TestClient(app)
    r = c.post("/login", data={"email": "viewer@sourceborn.in", "password": "viewer-test-pw"})
    assert r.status_code == 200
    return c


def _fake_fetchers():
    mins = {"2026-10-01": _minute("2026-10-01", {"09:35": 100.0})}

    def fd(ticker, a, b):
        return _daily() if ticker.startswith("BLSE") else _plain_daily()

    def fm(ticker, d, i):
        return mins.get(d) if (i == 1 and not ticker.startswith("BLSE")) else None

    def car(sym):
        return BLSE_CA if sym == "BLSE" else []
    return fd, fm, car, (lambda t: "")


@pytest.fixture()
def mixed_book(monkeypatch):
    p = state.portfolio()
    saved = (list(p.trades), dict(p.positions), p.cash, list(p.decision_journal))
    p.trades = [_trade(ticker="GOOD.NS"), _trade(ticker="BAD.NS", entry=250.0),
                _trade(ticker="BLSE.NS", entry=320.16, exit_=157.47, reason="STOP_LOSS")]
    pos = PaperPosition(ticker="HELD.NS", qty=10, entry_price=0.0, entry_date=datetime(2026, 10, 1),
                        sector="IT", peak_price=0.0, entry_score=0.0, reasoning="owner buy")
    p.positions = {pos.ticker: pos}
    p.cash = 1_000_000.0
    monkeypatch.setattr(ta, "upstox_fetchers", _fake_fetchers)
    monkeypatch.setattr(db, "count_pre_mars", lambda coll: 0)
    monkeypatch.setattr(db, "list_trades", lambda limit=0, include_pre_mars=False, with_ids=False: [])
    monkeypatch.setattr(db, "update_trade_prices", lambda rid, fields: False)
    ta.clear_caches()
    try:
        yield p
    finally:
        p.trades, p.positions, p.cash, p.decision_journal = saved
        ta.clear_caches()


def test_owner_sees_the_audit_table_a_viewer_does_not(mixed_book, monkeypatch):
    body = _owner().get("/trades?audit=1").text
    assert "trade_audit_marker" in body and "trade_audit_summary_marker" in body
    # each name must be an AUDIT row (the ticker alone also prints in the
    # closed-trades table below — a weak check would pass without the audit)
    for t in ("GOOD.NS", "BAD.NS", "BLSE.NS"):
        assert f'data-audit-row="{t}|' in body
    assert 'data-audit-row="POS|HELD.NS|' in body
    assert "OUT_OF_RANGE" in body and "CA_UNADJUSTED" in body
    assert 'data-audit-restate="BLSE.NS"' in body and "entry ÷ 2, qty × 2" in body
    assert 'data-correct-one="BAD.NS"' in body and 'data-correct-one="BLSE.NS"' in body
    assert 'data-correct-one="GOOD.NS"' not in body
    assert "date only" in body                     # the owner-buy stamp carries no time
    plain = _owner().get("/trades").text
    assert "trade_audit_marker" not in plain and "trade_audit_link_marker" in plain
    v = _viewer(monkeypatch)
    vb = v.get("/trades?audit=1").text
    assert "trade_audit_marker" not in vb and "trade_audit_link_marker" not in vb
    assert v.post("/trades/correct", data={"all_flagged": "1"}).status_code == 403


def test_correction_moves_cash_by_the_net_delta_and_journals_once(mixed_book):
    p = mixed_book
    c = _owner()
    res = ta.run_audit(p, [], cfg=state.cfg())
    bad = next(r for r in res["rows"] if r["ticker"] == "BAD.NS")
    blse = next(r for r in res["rows"] if r["ticker"] == "BLSE.NS")
    cash0, n0 = p.cash, len(p.decision_journal)
    r = c.post("/trades/correct", data={"id": bad["id"]}, follow_redirects=False)
    assert r.status_code == 303 and "corrected=1" in r.headers["location"]
    assert abs(p.cash - cash0 - bad["delta_cash"]) < 1e-6
    lines = [e for e in p.decision_journal[n0:] if e["action"] == "TRADE_PRICE_CORRECTED"]
    assert len(lines) == 1 and lines[0]["ticker"] == "BAD.NS" and lines[0]["owner"] is True
    t = next(t for t in p.trades if t.ticker == "BAD.NS")
    assert abs(t.entry_price - bad["corrected"]["entry"]) < 1e-9
    assert abs(t.net_pnl - bad["corrected"]["net"]) < 1e-9
    # a second tap on the same (now right) trade is refused — not flagged any more
    r2 = c.post("/trades/correct", data={"id": bad["id"]}, follow_redirects=False)
    assert "refused=" in r2.headers["location"]
    assert sum(1 for e in p.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED") == 1
    # an unflagged id is refused unless force=1
    good = next(r for r in res["rows"] if r["ticker"] == "GOOD.NS")
    r3 = c.post("/trades/correct", data={"id": good["id"]}, follow_redirects=False)
    assert "refused=GOOD" in r3.headers["location"]
    # the BLSE restatement through the page: qty doubled, sell kept, cash by the delta
    cash1 = p.cash
    r4 = c.post("/trades/correct", data={"id": blse["id"]}, follow_redirects=False)
    assert "corrected=1" in r4.headers["location"]
    tb = next(t for t in p.trades if t.ticker == "BLSE.NS")
    assert tb.qty == 200 and abs(tb.entry_price - 160.08) < 1e-6 and tb.exit_price == 157.47
    assert abs(p.cash - cash1 - blse["delta_cash"]) < 1e-6
    line = [e for e in p.decision_journal if e["action"] == "TRADE_PRICE_CORRECTED"][-1]
    assert line["corporate_action"] == [f"{SPLIT} ex 2026-10-06"] and line["ratio"] == 2.0
    assert line["before"]["qty"] == 100 and line["after"]["qty"] == 200
    page = c.get("/trades?audit=1").text
    assert "trade_audit_done_marker" not in page       # the redirect carried it, the re-open does not
    assert 'data-correct-one="BLSE.NS"' not in page    # right now


def test_archived_row_is_updated_through_the_db_helper_or_refused(mixed_book, monkeypatch):
    p = mixed_book
    arch = {**_trade(ticker="OLD.NS", entry=250.0).to_dict(), "pre_mars": True}
    monkeypatch.setattr(db, "list_trades", lambda limit=0, include_pre_mars=False, with_ids=False: [arch])
    calls = []
    monkeypatch.setattr(db, "update_trade_prices", lambda rid, fields: (calls.append((rid, fields)), True)[1])
    ta.clear_caches()
    c = _owner()
    res = ta.run_audit(p, [arch], cfg=state.cfg())
    old = next(r for r in res["rows"] if r["ticker"] == "OLD.NS")
    assert old["archived"] and old["flagged"]
    cash0 = p.cash
    r = c.post("/trades/correct", data={"id": old["id"]}, follow_redirects=False)
    assert "corrected=1" in r.headers["location"]
    assert calls and calls[0][0] == old["id"] and set(calls[0][1]) >= {"entry_price", "net_pnl", "qty"}
    assert abs(p.cash - cash0 - old["delta_cash"]) < 1e-6
    # Mongo refusing the write = no cash moved, refused named
    monkeypatch.setattr(db, "update_trade_prices", lambda rid, fields: False)
    ta.clear_caches()
    p.cash = cash0
    arch2 = {**_trade(ticker="OLD2.NS", entry=250.0).to_dict(), "pre_mars": True}
    monkeypatch.setattr(db, "list_trades", lambda limit=0, include_pre_mars=False, with_ids=False: [arch2])
    res2 = ta.run_audit(p, [arch2], cfg=state.cfg())
    old2 = next(r for r in res2["rows"] if r["ticker"] == "OLD2.NS")
    r2 = c.post("/trades/correct", data={"id": old2["id"]}, follow_redirects=False)
    assert "refused=OLD2" in r2.headers["location"] and p.cash == cash0


def test_budget_stop_prints_the_note_on_the_page(mixed_book, monkeypatch):
    monkeypatch.setenv("SB_TRADE_AUDIT_BUDGET_S", "0.000001")
    t = [0.0]
    monkeypatch.setattr(ta.time, "monotonic", lambda: (t.__setitem__(0, t[0] + 5), t[0])[1])
    body = _owner().get("/trades?audit=1").text
    assert "trade_audit_budget_marker" in body and "not checked this open" in body


def test_db_update_trade_prices_filters_fields_and_fails_closed(monkeypatch):
    class _Res:
        matched_count = 1

    class _Trades:
        def update_one(self, q, u):
            _Trades.last = (q, u)
            return _Res()

    class _DB:
        trades = _Trades()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", _DB())
    assert db.update_trade_prices("id1", {"entry_price": 1.5, "qty": 200, "ticker": "X"})
    assert _Trades.last[1]["$set"] == {"entry_price": 1.5, "qty": 200}
    assert db.update_trade_prices("id1", {"ticker": "X"}) is False
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.update_trade_prices("id1", {"entry_price": 1.0}) is False
