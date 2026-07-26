"""Batch A+B (2026-07): positions P&L columns, dashboard cards, square-off."""

from datetime import datetime

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from trading.paper import PaperPosition


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _seed_position(ticker="TESTPOS.NS", mode="", qty=10, entry=100.0):
    p = state.portfolio()
    p.positions[ticker] = PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry,
        entry_date=datetime(2026, 7, 1), sector="IT", peak_price=entry,
        entry_score=70.0, reasoning="test seed", mode=mode,
        stop_price=entry * 0.88, target_price=entry * 1.30,
    )
    return p


def test_positions_page_shows_ltp_value_pnl_and_totals():
    p = _seed_position()
    try:
        r = _client().get("/positions")
        assert r.status_code == 200
        for label in ("Current ₹", "Value ₹", "P&amp;L", "To Target",
                      "To Stop Loss", "TOTAL", "DETAILS"):
            assert label in r.text, label
        # old always-visible columns are now inside the details row
        assert "Stop ₹" not in r.text          # renamed
    finally:
        p.positions.pop("TESTPOS.NS", None)


def test_dashboard_shows_picks_watchlist_triggers():
    p = state.portfolio()
    p.last_scan_picks = [{"ticker": "AAA.NS", "score": 88, "last_close": 100.0},
                         {"ticker": "BBB.NS", "score": 77, "last_close": 50.0}]
    p.last_scan_skipped = [{"ticker": "CCC.NS", "reason": "score_below_floor"}]
    r = _client().get("/")
    assert r.status_code == 200
    for label in ("Today's Picks", "Watchlist", "Engine Triggers Today",
                  "UNREALIZED P&amp;L", "AAA.NS", "score_below_floor"):
        assert label in r.text, label


def test_square_off_intraday_only_and_ignores_disable_exits():
    p = _seed_position("INTRA1.NS", mode="intraday", qty=5, entry=200.0)
    _seed_position("HOLD1.NS", mode="nifty200_momentum", qty=5, entry=300.0)
    cfg = state.cfg()
    old_disable = cfg.disable_exits
    cfg.disable_exits = True                 # buy-and-hold must NOT protect intraday
    try:
        closed = state.engine().square_off_intraday(prices={"INTRA1.NS": 210.0})
        assert closed == ["INTRA1.NS"]
        assert "INTRA1.NS" not in p.positions
        assert "HOLD1.NS" in p.positions      # non-intraday untouched
        assert any(t.ticker == "INTRA1.NS" and t.reason == "SQUARE_OFF"
                   for t in p.trades)
    finally:
        cfg.disable_exits = old_disable
        p.positions.pop("HOLD1.NS", None)
        p.trades = [t for t in p.trades if t.ticker != "INTRA1.NS"]


# ---- F9 / F13 / F17 (2026-07-12) ----

def test_closed_trades_capital_columns():
    from trading.paper import ClosedTrade
    from datetime import datetime as _dt
    p = state.portfolio()
    p.trades.append(ClosedTrade(
        ticker="ZTEST.NS", qty=100, entry_date=_dt(2026, 6, 1),
        exit_date=_dt(2026, 6, 10), entry_price=100.0, exit_price=110.0,
        gross_pnl=1000.0, costs=0.0, net_pnl=1000.0, reason="TAKE_PROFIT",
        sector="IT", entry_score=70.0, reasoning="t"))
    try:
        r = _client().get("/trades")
        assert r.status_code == 200
        for label in ("Capital Used", "% of Capital", "GROSS PROFIT",
                      "GROSS LOSS", "of capital"):
            assert label in r.text, label
    finally:
        p.trades = [t for t in p.trades if t.ticker != "ZTEST.NS"]


def test_settings_shows_percent_and_saves_fraction():
    c = _client()
    r = c.get("/settings")
    assert r.status_code == 200
    cfg = state.cfg()
    # max_position_pct stored as fraction (0.025) shown as 2.5
    shown = round(cfg.max_position_pct * 100, 4)
    assert str(shown) in r.text
    # save 3(%) -> stored 0.03
    old = cfg.max_position_pct
    from web.routes.settings_routes import EDITABLE_FIELDS
    form = {n: (round(getattr(cfg, n) * 100, 4) if n in
                {x[0] for x in EDITABLE_FIELDS if x[6]} else getattr(cfg, n))
            for n, *_ in EDITABLE_FIELDS if getattr(cfg, n, None) is not None}
    form["max_position_pct"] = 3
    form["mode"] = cfg.mode
    c.post("/settings", data=form)
    assert abs(state.cfg().max_position_pct - 0.03) < 1e-9
    state.cfg().max_position_pct = old


def test_recent_candles_cached_no_crash(monkeypatch):
    import trading.data as tdata
    import pandas as pd, numpy as np
    idx = pd.bdate_range("2026-04-01", periods=50)
    df = pd.DataFrame({"Open": np.linspace(100, 120, 50),
                       "High": np.linspace(101, 121, 50),
                       "Low": np.linspace(99, 119, 50),
                       "Close": np.linspace(100, 120, 50),
                       "Volume": 1e6}, index=idx)
    monkeypatch.setattr(tdata, "fetch_daily", lambda t, period="4mo": {"^NSEI": df})
    state._CANDLE_CACHE["ts"] = 0.0
    out = state.recent_candles("^NSEI", bars=40)
    assert len(out) == 40 and set(out[0]) == {"t", "o", "h", "l", "c"}

    # Owner ask 2026-07-26: the dashboard's big chart is a SHARE, not NIFTY.
    # It is drawn from bars the scan already stored — no fetch on page load.
    p = state.portfolio()
    saved = list(p.last_scan_picks or [])
    p.last_scan_picks = [{
        "ticker": "TESTCO.NS", "score": 77.0, "reasoning": "", "last_close": 120.0,
        "timing_pass": True, "components": {},
        "candles": [[100 + i, 101 + i, 99 + i, 100.5 + i] for i in range(20)],
        "structures": {"hammer": True, "bearish_engulfing": False},
        "candle": "hammer", "opened": False,
    }]
    try:
        r = _client().get("/")
        assert "NIFTY 50 — Daily Candles" not in r.text     # index chart gone
        assert "TESTCO — Daily Candles" in r.text            # share chart shown
        assert "hammer" in r.text                            # structure chips
    finally:
        p.last_scan_picks = saved


# ---- F4 / F5 / F10 (2026-07-12) ----

def test_f10_strategy_hit_counts_render():
    p = state.portfolio()
    p.decision_journal.append({
        "ts": "2026-07-12T09:20:00", "action": "BUY", "ticker": "HITX.NS",
        "components": {"momentum_1m": 88.0, "volume_expansion": 40.0},
    })
    try:
        r = _client().get("/strategy")
        assert r.status_code == 200
        for label in ("Top-driver hits", "Positions by Sector", "Exits by Reason"):
            assert label in r.text, label
    finally:
        p.decision_journal[:] = [e for e in p.decision_journal
                                 if e.get("ticker") != "HITX.NS"]


def test_f5_watchlist_blocked_reason():
    p = state.portfolio()
    p.last_scan_picks = [{"ticker": "BLKD.NS", "score": 80, "last_close": 100.0}]
    p.last_scan_skipped = [{"ticker": "BLKD.NS", "reason": "reserve_or_cash_limit"}]
    p.positions.pop("BLKD.NS", None)
    r = _client().get("/")
    assert r.status_code == 200
    assert "Why not bought" in r.text
    assert "cash reserve" in r.text


def test_f4_decay_watch_flag():
    p = _seed_position("DECAY.NS", entry=100.0)
    cfg = state.cfg()
    floor = float(cfg.score_decay_exit)
    p.decision_journal.append({
        "ts": "2026-07-12T15:35:00", "action": "HOLD", "ticker": "DECAY.NS",
        "current_score": floor + 2,          # inside floor+5 -> watch
    })
    try:
        r = _client().get("/positions")
        assert r.status_code == 200
        assert "⚠ DECAY" in r.text
    finally:
        p.positions.pop("DECAY.NS", None)
        p.decision_journal[:] = [e for e in p.decision_journal
                                 if e.get("ticker") != "DECAY.NS"]


# ---- F6 rotation card + F8 scan 2.0 pages (2026-07-12) ----

def test_dashboard_rotation_card(monkeypatch):
    from trading.paper import PaperPosition
    from datetime import datetime as _dt
    p = state.portfolio()
    p.positions["ROT.NS"] = PaperPosition(
        ticker="ROT.NS", qty=100, entry_price=100.0, entry_date=_dt(2026, 6, 1),
        sector="IT", peak_price=119.0, entry_score=70.0, reasoning="t",
        target_price=120.0)
    p.last_scan_picks = [{"ticker": "FRESH.NS", "score": 90, "last_close": 50.0}]
    # force the live mark to 119 (99%+ progress) so rotation triggers
    monkeypatch.setattr(state, "marks", lambda t: ({"ROT.NS": 119.0}, True))
    try:
        r = _client().get("/")
        assert r.status_code == 200
        assert "Rotation Suggestions" in r.text and "FRESH.NS" in r.text
    finally:
        p.positions.pop("ROT.NS", None)


def test_scan_page_and_csv():
    p = state.portfolio()
    p.last_scan = "2026-07-12T09:20:00"
    p.last_scan_picks = [{"ticker": "SP.NS", "score": 80, "last_close": 100.0,
                          "candle": "breakout", "opened": False}]
    p.virtual_book = {"VV.NS": {"ticker": "VV.NS", "score": 75, "entry_px": 100.0,
                                "last_px": 110.0, "pnl_pct": 10.0, "status": "open",
                                "skip_reason": "reserve", "sector": "IT",
                                "target_px": 130.0, "stop_px": 88.0}}
    c = _client()
    r = c.get("/scan")
    assert r.status_code == 200
    assert "Shadow book" in r.text and "VV.NS" in r.text
    assert "by sector" in r.text
    csv = c.get("/scan.csv")
    assert csv.status_code == 200
    assert "VIRTUAL" in csv.text and "VV.NS" in csv.text
