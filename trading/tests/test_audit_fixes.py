"""Tests for the P0/P1/P2 audit-fix batch (2026-07).

Covers: hold-mode decay exit, sector capital cap, NSE trading-day guard,
PAPER_ONLY lock, drawdown-ladder crash brake, and walk-forward validation
on synthetic data (no network).
"""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.exits import check_exit
from trading.paper import PaperPosition
from trading.risk import RiskState, can_open


def _cfg(**over):
    c = Config()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _pos(ticker="X.NS", entry=100.0, qty=100, sector="IT"):
    return PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry, entry_date=datetime(2026, 1, 1),
        sector=sector, peak_price=entry, entry_score=75.0, reasoning="test",
    )


# ---- P0/B1: hold mode keeps the score-decay risk exit ----

def test_hold_mode_no_profit_exits_but_decay_fires():
    cfg = _cfg(disable_exits=True, stop_loss_pct=-0.05, take_profit_pct=0.10,
               trailing_stop_pct=-0.08, score_decay_exit=45.0, max_hold_days=1)
    pos = _pos(entry=100.0)
    # -50% crash: stop would fire normally — hold mode ignores it.
    assert not check_exit(pos, 50.0, 80.0, cfg, datetime(2026, 6, 1))[0]
    # +50%: target ignored too.
    assert not check_exit(pos, 150.0, 80.0, cfg, datetime(2026, 6, 1))[0]
    # Momentum collapse below the floor: the risk exit STILL fires.
    should, reason = check_exit(pos, 120.0, 30.0, cfg, datetime(2026, 6, 1))
    assert should and reason == "SCORE_DECAY"


# ---- P2: sector capital cap ----

def test_sector_capital_cap_shrinks_then_blocks():
    cfg = _cfg(starting_capital=10_00_000, cash_reserve_pct=0.0,
               max_position_pct=0.50, max_positions=99, max_sector_positions=99,
               max_portfolio_heat_pct=10.0, max_sector_exposure_pct=0.25,
               min_position_value=0.0)   # F7 floor off: isolate the sector cap
    cfg.sector_map = {"A.NS": "IT", "B.NS": "IT", "NEW.NS": "IT"}
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    positions = {
        "A.NS": _pos(ticker="A.NS", qty=1200),
        "B.NS": _pos(ticker="B.NS", qty=1200),
    }  # IT notional 2,40,000 of a 2,50,000 cap -> 10,000 budget left
    ok, _, qty = can_open(risk, cash=10_00_000, current_equity=10_00_000,
                          open_positions=positions, ticker="NEW.NS",
                          fill_price=100.0, cfg=cfg)
    assert ok and qty == 100  # shrunk to the sector budget
    positions["C.NS"] = _pos(ticker="C.NS", qty=100)
    cfg.sector_map["C.NS"] = "IT"  # cap now exactly full
    ok, reason, _ = can_open(risk, cash=10_00_000, current_equity=10_00_000,
                             open_positions=positions, ticker="NEW.NS",
                             fill_price=100.0, cfg=cfg)
    assert not ok and reason == "sector_exposure_cap"


def test_unknown_sector_exempt_from_capital_cap():
    cfg = _cfg(starting_capital=10_00_000, cash_reserve_pct=0.20,
               max_position_pct=0.10, max_positions=99, max_sector_positions=99,
               max_sector_exposure_pct=0.25)
    cfg.sector_map = {}
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    positions = {f"S{i}.NS": _pos(ticker=f"S{i}.NS", qty=800) for i in range(4)}
    ok, _, qty = can_open(risk, cash=10_00_000, current_equity=10_00_000,
                          open_positions=positions, ticker="NEW.NS",
                          fill_price=100.0, cfg=cfg)
    assert ok and qty > 0  # 3,20,000 "Unknown" notional does not trip the cap


# ---- P2: NSE trading-day guard ----

def test_is_trading_day():
    from trading.calendars import is_trading_day
    assert not is_trading_day(date(2026, 7, 11))   # Saturday
    assert not is_trading_day(date(2026, 1, 26))   # Republic Day
    assert is_trading_day(date(2026, 7, 8))        # normal Wednesday


# ---- P2/D3: PAPER_ONLY hard lock ----

def test_paper_only_lock_blocks_live_orders(monkeypatch):
    from trading.brokers.upstox import assert_paper_trading
    monkeypatch.delenv("PAPER_ONLY", raising=False)
    with pytest.raises(RuntimeError):
        assert_paper_trading()          # locked by default
    monkeypatch.setenv("PAPER_ONLY", "0")
    assert_paper_trading()              # explicit opt-out only


# ---- P1/F3: drawdown-ladder crash brake ----

def test_drawdown_governor_forced_cut_levels():
    from trading.risk_v2 import DrawdownGovernor
    gov = DrawdownGovernor()
    gov.update(100.0)
    gov.update(89.0)   # -11% from peak -> cut_half
    assert gov.forced_cut == 0.50
    gov.update(79.0)   # -21% -> full halt
    assert gov.forced_cut == 1.0


# ---- P1/A1: walk-forward on synthetic data (no network) ----

def _synth(seed, n=900, drift=0.0007):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    px = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    return pd.DataFrame({"Open": px, "High": px * 1.01, "Low": px * 0.99,
                         "Close": px, "Volume": 1_000_000.0}, index=idx)


def test_walk_forward_synthetic_two_windows():
    from trading.backtest import walk_forward
    cfg = Config()
    cfg.mode = "nifty200_momentum"
    cfg.apply_mode_preset()
    cfg.disable_exits = False
    uni = [f"S{i}.NS" for i in range(6)]
    cfg.universe = uni
    cfg.sector_map = {}
    hist = {t: _synth(i) for i, t in enumerate(uni)}
    hist["^NSEI"] = _synth(99)
    windows, summary = walk_forward(
        cfg, "2023-06-01", "2025-06-01", window_years=1,
        universe=uni, benchmark="^NSEI", preloaded_history=hist,
    )
    assert len(windows) == 2
    assert summary["windows"] == 2
    for w in windows:
        assert "error" not in w, w
        assert "total_return_pct" in w and "alpha_pct" in w


# ---- Render 512MB: journal RAM cap ----

def test_save_caps_journal_in_ram(tmp_path):
    from trading.paper import Portfolio, JOURNAL_RAM_CAP
    cfg = _cfg(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.decision_journal = [{"ts": str(i), "action": "HOLD"} for i in range(JOURNAL_RAM_CAP + 1200)]
    p.scan_history = [{"i": i} for i in range(900)]
    p.save()
    assert len(p.decision_journal) == JOURNAL_RAM_CAP     # bounded
    assert p.decision_journal[-1]["ts"] == str(JOURNAL_RAM_CAP + 1199)  # newest kept
    assert len(p.scan_history) == 500


# ---- Round-1 adoption: committed datasets are loadable ----

def test_committed_upstox_universe_loads():
    import os
    from trading.universes import load_universe_from_csv
    path = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                        "upstox_nse_equities.csv")
    tickers = load_universe_from_csv(path, limit=None)
    assert len(tickers) > 2000                      # full EQ-series master
    assert all(t.endswith(".NS") for t in tickers)
    assert "RELIANCE.NS" in tickers and "TCS.NS" in tickers
    assert len(tickers) == len(set(tickers))        # no dupes


def test_committed_fii_dii_csv_parses():
    import csv, os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                        "fii_dii_cash.csv")
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 100
    for r in rows[:5]:
        float(r["fii_cash_net"]); float(r["dii_cash_net"])
        assert r["date"].startswith("2024-")


def test_save_caps_equity_history_in_ram(tmp_path):
    from trading.paper import Portfolio, EQUITY_HISTORY_RAM_CAP
    cfg = _cfg(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.equity_history = [(str(i), float(i)) for i in range(EQUITY_HISTORY_RAM_CAP + 700)]
    p.save()
    assert len(p.equity_history) == EQUITY_HISTORY_RAM_CAP
    assert p.equity_history[-1][1] == float(EQUITY_HISTORY_RAM_CAP + 699)


def test_yf_thread_pool_is_capped():
    from trading.data import YF_THREADS
    assert isinstance(YF_THREADS, int) and 1 <= YF_THREADS <= 8


# ---- Upstox-first history routing (Yahoo 429-blocks cloud IPs) ----

class _StubBroker:
    def __init__(self):
        self.calls = []

    def get_daily_candles(self, instrument_key, from_date, to_date):
        self.calls.append(instrument_key)
        idx = pd.bdate_range("2025-01-01", periods=300)
        px = np.linspace(100, 150, 300).astype("float32")
        return pd.DataFrame({"Open": px, "High": px, "Low": px,
                             "Close": px, "Volume": px * 1000}, index=idx)


def test_fetch_daily_prefers_upstox_no_network(monkeypatch):
    from trading import data as d
    stub = _StubBroker()
    d.set_history_broker(stub)
    try:
        # yfinance must NOT be hit when Upstox serves everything
        monkeypatch.setattr(d.yf, "download",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("yf hit")))
        out = d.fetch_daily(["RELIANCE.NS", "^NSEI"], period="2y")
        assert set(out) == {"RELIANCE.NS", "^NSEI"}
        assert "NSE_INDEX|Nifty 50" in stub.calls          # index key mapped
        assert any(k.startswith("NSE_EQ|") for k in stub.calls)  # equity key from CSV
        assert str(out["RELIANCE.NS"]["Close"].dtype) == "float32"
    finally:
        d.set_history_broker(None)


def test_fetch_daily_range_upstox_then_fallback(monkeypatch):
    from trading import data as d

    class _NoneBroker:
        def get_daily_candles(self, *a):
            return None  # Upstox fails -> must fall back to yfinance

    d.set_history_broker(_NoneBroker())
    try:
        called = {}
        def fake_dl(tickers, **k):
            called["tickers"] = list(tickers)
            return pd.DataFrame()  # empty: parse yields nothing, no crash
        monkeypatch.setattr(d.yf, "download", fake_dl)
        out = d.fetch_daily_range(["RELIANCE.NS"], "2025-01-01", "2025-06-01")
        assert out == {} and called["tickers"] == ["RELIANCE.NS"]
    finally:
        d.set_history_broker(None)


def test_instrument_key_mapping():
    from trading.data import _instrument_key
    ik = _instrument_key("RELIANCE.NS")
    assert ik and ik.startswith("NSE_EQ|INE")
    assert _instrument_key("^NSEI") == "NSE_INDEX|Nifty 50"
    assert _instrument_key("NOSUCHTICKER.NS") is None


# ---- 200-DMA regime fix + OpenAI sentiment provider (2026-07-11) ----

def test_regime_gets_a_real_200dma(monkeypatch):
    import trading.data as tdata
    from trading.regime import fetch_regime
    idx = pd.bdate_range("2025-06-01", periods=300)
    nifty = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                          "Close": np.linspace(100, 130, 300),
                          "Volume": 1e6}, index=idx)
    vix = pd.DataFrame({"Close": np.full(300, 13.0)}, index=idx)
    captured = {}
    def fake_fetch(tickers, period="6mo"):
        captured["period"] = period
        return {"^NSEI": nifty, "^INDIAVIX": vix}
    monkeypatch.setattr(tdata, "fetch_daily", fake_fetch)
    reading = fetch_regime()
    assert captured["period"] == "14mo"          # enough bars for 200-DMA
    assert reading.nifty_200dma is not None      # was ALWAYS None before fix
    assert reading.nifty_200dma > 0


def test_openai_sentiment_provider(monkeypatch):
    from trading.signals import news as news_mod
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "0.6\nStrong results beat"}}]}
    monkeypatch.setattr(news_mod.httpx, "post", lambda *a, **k: _Resp())
    got = news_mod._score_with_openai("TCS.NS", ["TCS beats estimates"])
    assert got == (0.6, "Strong results beat")
    # no keys at all -> scorer returns None (fetch_sentiment maps to neutral 50)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert news_mod._score_with_openai("TCS.NS", ["x"]) is None


def test_festival_calendar_v11():
    from trading.calendars import festival_within, festival_context
    assert festival_within(days=5, today=date(2026, 11, 7)) in ("Diwali", "Dhanteras")
    assert festival_within(days=5, today=date(2026, 4, 19)) == "Akshaya Tritiya"
    assert festival_within(days=5, today=date(2026, 7, 15)) is None
    ctx = festival_context(days=5, today=date(2026, 11, 6))
    assert ctx and "Gold" in ctx                       # Dhanteras sector hint
    assert festival_context(today=date(2026, 11, 20))  # wedding season window
