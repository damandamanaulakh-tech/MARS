"""Web side of the owner's 50-name universe + the "prices not live" root fix.

Pins: the Settings box normalises and validates the weekly list (a typo is
REFUSED with the name shown, never silently dropped); the daily top-up runs
for exactly the custom names with the daily freshness window; and the
history client is attached WITHOUT a login token, so a login-less morning
no longer sends every candle fetch to Yahoo's 429 wall.
"""
from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _post_settings(c, **extra):
    return c.post("/settings", data=extra, follow_redirects=False)


def _restore(old):
    cfg = state.cfg()
    cfg.custom_universe = old


def test_settings_normalises_case_suffix_and_separators():
    cfg = state.cfg()
    old = cfg.custom_universe
    try:
        r = _post_settings(_client(),
                           custom_universe="tcs, reliance\nhfcl HFCL")
        assert r.status_code == 303 and "saved=ok" in r.headers["location"]
        assert tuple(cfg.custom_universe) == (
            "TCS.NS", "RELIANCE.NS", "HFCL.NS")      # deduped, .NS added
    finally:
        _restore(old)


def test_settings_refuses_an_unknown_name_and_changes_nothing():
    """A typo must never silently shrink the money universe."""
    cfg = state.cfg()
    old = cfg.custom_universe
    try:
        r = _post_settings(_client(),
                           custom_universe="TCS, NOTAREALSTOCK")
        assert "saved=universe_unknown" in r.headers["location"]
        assert "NOTAREALSTOCK" in r.headers["location"]
        assert tuple(cfg.custom_universe) == tuple(old)   # untouched
    finally:
        _restore(old)


def test_settings_blank_restores_the_rotating_bucket():
    cfg = state.cfg()
    old = cfg.custom_universe
    try:
        r = _post_settings(_client(), custom_universe="   ")
        assert "saved=ok" in r.headers["location"]
        assert tuple(cfg.custom_universe) == ()
    finally:
        _restore(old)


def test_settings_page_shows_the_editable_list():
    html = _client().get("/settings").text
    assert "custom_universe_marker" in html
    assert "Custom Universe (50 names" in html
    assert "Blank the box to go back" in html


def test_daily_topup_covers_the_custom_names_with_daily_freshness(monkeypatch):
    from web import db, scheduler
    seen = {}

    def fake_topup(broker, keys, load_doc, save_doc, today,
                   fresh_within_days=4):
        seen["names"] = sorted(keys)
        seen["fresh"] = fresh_within_days
        return {"ok": len(keys), "skipped_fresh": 0, "failed": 0,
                "total": len(keys)}
    monkeypatch.setattr("trading.history_store.top_up_history", fake_topup)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    # instrument resolution is not under test (and other tests poison its
    # cache); identity-map it so the job's own wiring is what's measured
    monkeypatch.setattr("trading.brokers.upstox_instruments.to_instrument_keys",
                        lambda names: {t: f"KEY|{t}" for t in names})
    # the tokenless history client stands in when engine.broker is None —
    # exactly the login-less morning this job must survive
    from trading import data as trading_data
    old_hist = trading_data.history_broker()
    trading_data.set_history_broker(object())
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = ("TCS.NS", "HFCL.NS")
    try:
        scheduler._daily_universe_topup()
        assert seen["names"] == ["HFCL.NS", "TCS.NS"]
        assert seen["fresh"] == 1               # daily window, not weekly
    finally:
        _restore(old)
        trading_data.set_history_broker(old_hist)


def test_daily_topup_is_a_noop_when_no_custom_list(monkeypatch):
    from web import scheduler
    called = []
    monkeypatch.setattr("trading.history_store.top_up_history",
                        lambda *a, **k: called.append(1))
    cfg = state.cfg()
    old = cfg.custom_universe
    cfg.custom_universe = ()
    try:
        scheduler._daily_universe_topup()
        assert not called      # rotating mode keeps its weekly cadence
    finally:
        _restore(old)


def test_history_client_attaches_without_a_login_token(monkeypatch):
    """THE ROOT of "prices are still not live" (owner, 2026-08-20): before
    the day's login there was NO Upstox client at all, so every historical
    fetch fell to yfinance, which Yahoo 429-blocks on Render. The history
    client must now exist token or no token; live quotes (engine.broker)
    still honestly wait for the login."""
    from trading import data as trading_data
    from trading.brokers.upstox import UpstoxClient
    from web import db

    monkeypatch.setattr(db, "load_upstox_token", lambda: None)
    monkeypatch.setattr(UpstoxClient, "from_env",
                        classmethod(lambda cls: UpstoxClient(
                            api_key="k", api_secret="s", redirect_uri="r")))
    old_hist = trading_data.history_broker()
    eng = state.engine()
    old_broker = eng.broker
    try:
        trading_data.set_history_broker(None)
        eng.broker = None
        state._attach_broker_if_persisted()
        assert trading_data.history_broker() is not None      # candles work
        assert trading_data.history_broker().access_token is None
        assert eng.broker is None                # live quotes wait for login
    finally:
        trading_data.set_history_broker(old_hist)
        eng.broker = old_broker


def test_disconnect_keeps_the_history_client(monkeypatch):
    """Logout clears live quotes, not history — the candle endpoint is
    public and a dead bearer is ignored (verified live 2026-08-20)."""
    import inspect

    from web import state as st
    src = inspect.getsource(st.refresh_broker_attachment)
    assert "set_history_broker(None)" not in src
