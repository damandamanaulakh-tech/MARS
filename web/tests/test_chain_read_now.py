"""The owner's one-stock "read the chain now" button (order 2026-09-08:
"run the third table with my upstox login" -> "build 1").

Pinned here: without a login the page says so and nothing is stored; with
a login the chain is read, stored beside the daily snapshot, journaled and
shown on the stock page; a read-now on a day with no universe snapshot
must NOT make the 09:20 universe job think it already ran; a stock with no
options market is reported as such, not as a failure.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from web import auth as auth_mod, scheduler, state
from web.main import app

T = "TCS.NS"


def _strike(k, c_oi, p_oi, c_iv=None, p_iv=None, spot=None):
    row = {"strike_price": k,
           "call_options": {"market_data": {"oi": c_oi},
                            "option_greeks": {"iv": c_iv}},
           "put_options": {"market_data": {"oi": p_oi},
                           "option_greeks": {"iv": p_iv}}}
    if spot is not None:
        row["underlying_spot_price"] = spot
    return row


class _Broker:
    def __init__(self, connected=True, has_fo=True):
        self._c, self._fo = connected, has_fo

    def is_connected(self):
        return self._c

    def get_option_contracts(self, ik):
        return [{"expiry": "2099-12-30"}] if self._fo else None

    def get_option_chain(self, ik, expiry):
        return [_strike(95, 1000, 3000, 20.0, 26.0, spot=100.0),
                _strike(100, 2000, 2000, 18.0, 22.5)]


def _client():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _keys(monkeypatch):
    monkeypatch.setattr("trading.brokers.upstox_instruments.to_instrument_keys",
                        lambda ts: {t: "NSE_EQ|INE000TEST" for t in ts})


def test_without_a_login_the_page_says_so_and_stores_nothing(monkeypatch):
    p = state.portfolio()
    old = p.option_chain
    p.option_chain = {}
    monkeypatch.setattr(state.engine(), "broker", None)
    try:
        c = _client()
        r = c.post(f"/triggers/{T}/chain", follow_redirects=False)
        assert r.status_code == 303 and "chain=needs_login" in r.headers["location"]
        body = c.get(r.headers["location"]).text
        assert "chain_read_marker" in body
        assert "Needs your Upstox login" in body
        assert p.option_chain == {}
    finally:
        p.option_chain = old
        p.save()


def test_with_a_login_the_chain_is_read_stored_journaled_and_shown(monkeypatch):
    p = state.portfolio()
    old = p.option_chain
    p.option_chain = {}
    n0 = len(p.decision_journal)
    monkeypatch.setattr(state.engine(), "broker", _Broker())
    _keys(monkeypatch)
    try:
        c = _client()
        r = c.post(f"/triggers/{T}/chain", follow_redirects=False)
        assert "chain=ok" in r.headers["location"]
        row = p.option_chain["rows"][T]
        assert row["pcr_oi"] == 1.667 and row["iv_skew"] == 4.5   # 5000/3000, 22.5-18
        assert p.option_chain["scope"] == "single"
        assert "^NSEI" not in p.option_chain["rows"]            # one stock, not the index
        assert len(p.option_chain["history"][T]) == 1
        assert any(e.get("action") == "OPTION_CHAIN_READ" and e.get("ticker") == T
                   for e in p.decision_journal[n0:])
        body = c.get(r.headers["location"]).text
        assert "Chain read just now" in body
        assert "1.667" in body and "4.5" in body
        assert "1 of 5 days recorded" in body                    # pattern needs 5
        assert "never blocks a buy" in body
    finally:
        p.option_chain = old
        p.save()


def test_a_read_now_never_makes_the_daily_universe_job_skip(monkeypatch):
    """The daily job's once-per-day guard keys on today's date. A single-stock
    read stamps today too — without the scope check the 09:20 universe pass
    would silently not run on any day the owner tapped the button first."""
    p = state.portfolio()
    old = p.option_chain
    p.option_chain = {}
    monkeypatch.setattr(state.engine(), "broker", _Broker())
    _keys(monkeypatch)
    try:
        assert scheduler.read_option_chain_now(T, "test")["status"] == "ok"
        out = scheduler.run_option_chain_snapshot("test")
        assert "^NSEI" in out["rows"], "universe job skipped after a read-now"
        assert out["scope"] == "universe"
        # and a second read-now merges into today's universe snapshot
        assert scheduler.read_option_chain_now(T, "test")["status"] == "ok"
        assert p.option_chain["scope"] == "universe"
        assert "^NSEI" in p.option_chain["rows"] and T in p.option_chain["rows"]
    finally:
        p.option_chain = old
        p.save()


def test_no_options_market_is_reported_not_failed(monkeypatch):
    p = state.portfolio()
    old = p.option_chain
    p.option_chain = {}
    monkeypatch.setattr(state.engine(), "broker", _Broker(has_fo=False))
    _keys(monkeypatch)
    try:
        res = scheduler.read_option_chain_now(T, "test")
        assert res["status"] == "no_fo"
        assert p.option_chain == {}
        body = _client().get(f"/triggers/{T}?chain=no_fo").text
        assert "No options market for this stock" in body
    finally:
        p.option_chain = old
        p.save()


def test_read_now_is_owner_only_and_display_only():
    from pathlib import Path
    import trading.engine, trading.risk, trading.orders, trading.danger
    for mod in (trading.engine, trading.risk, trading.orders, trading.danger):
        src = Path(mod.__file__).read_text()
        assert "read_option_chain_now" not in src
    src = Path(scheduler.__file__).read_text()
    assert "def read_option_chain_now" in src
