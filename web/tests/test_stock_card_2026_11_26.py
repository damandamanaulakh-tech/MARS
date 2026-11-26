"""The stock page shows the five segments, stops and targets.

Owner order 2026-11-26: "when u click the selected stock, it should show
target prices, and category of 5 momentum, intra etc".

Display only: built from stored bars, the book and the stored scan record.
The card's stop and target are the engine's own functions (trading.targets)
applied to the last stored close — a test compares them directly.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trading import targets as T
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from web import stock_card


def _bars(n=320, drift=0.0012, start=500.0, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-08-01", periods=n)
    close = start * np.cumprod(1 + drift + rng.normal(0, 0.012, n))
    return pd.DataFrame({"Open": close * 0.995, "High": close * 1.012, "Low": close * 0.988,
                         "Close": close, "Volume": np.full(n, 400_000.0)}, index=idx)


def _engine(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    return TradingEngine(cfg, p), cfg, p


def _card(tmp_path, df=None, **setup):
    e, cfg, p = _engine(tmp_path)
    for key, value in setup.items():
        setattr(p, key, value)
    return stock_card.build("ABC.NS", df, p, cfg, e.mode_cfg, today=date(2026, 11, 26)), e, cfg


def test_every_segment_has_its_own_mark_stop_and_target(tmp_path):
    df = _bars()
    card, e, cfg = _card(tmp_path, df)
    rows = {r["mode"]: r for r in card["segments"]}
    assert list(rows) == list(stock_card.SEGMENTS)
    assert {m: r["buy_mark"] for m, r in rows.items()} == {
        "nifty200_momentum": 62.0, "intraday": 72.0, "short": 70.0, "swing": 60.0, "positional": 68.0}
    last = float(df["Close"].iloc[-1])
    assert card["last_close"] == last and card["asof"] == df.index[-1].strftime("%d %b %Y")
    for mode in ("nifty200_momentum", "short", "swing", "positional"):
        mcfg = e.mode_cfg(mode)
        want_target, _ = T.target_price_for(mode, last, df, float(mcfg.take_profit_pct))
        want_stop, _ = T.stop_price_for(mode, last, df, float(mcfg.stop_loss_pct),
                                        enabled=bool(mcfg.atr_stop_enabled))
        assert rows[mode]["target_price"] == want_target, mode
        assert rows[mode]["stop_price"] == want_stop, mode
        assert rows[mode]["stop_price"] < last < rows[mode]["target_price"]
    # intraday is priced off 5-minute bars at entry: a rule, never a daily-bar price
    intraday = rows["intraday"]
    assert intraday["stop_price"] is None and intraday["target_price"] is None
    assert "5-minute bars" in intraday["rule"] and "15:25" in intraday["rule"]
    assert e.cfg.mode == "nifty200_momentum"            # the shared settings were not touched


def test_momentum_is_scored_from_stored_bars_and_the_others_only_when_stored(tmp_path):
    df = _bars()
    card, e, cfg = _card(tmp_path, df)
    rows = {r["mode"]: r for r in card["segments"]}
    from trading.scoring import score_for_mode
    want, _ = score_for_mode("nifty200_momentum", "ABC.NS", df, None, {}, e.mode_cfg("nifty200_momentum"), missing=None)
    assert rows["nifty200_momentum"]["score"] == pytest.approx(want)
    assert rows["nifty200_momentum"]["score_source"] == "computed now from the stored daily bars"
    for mode in ("intraday", "short", "swing", "positional"):
        assert rows[mode]["score"] is None and rows[mode]["verdict"] == "unknown"
        assert "not scored in the last stored pass" in rows[mode]["score_source"]


def test_stored_scores_win_and_each_is_read_against_its_own_mark(tmp_path):
    matrix = {"mode": "swing", "ts": "2026-11-26T09:20:00", "score_floor": 60.0,
              "rows": {"ABC.NS": {"score": 57.5, "outcome": "", "timing": True}}}
    shortlist = {"ts": "2026-11-26T03:55:00", "rows": {"ABC.NS": {"score": 71.2, "outcome": "opened"}}}
    card, _, _ = _card(tmp_path, _bars(), scan_matrix=matrix, momentum_shortlist=shortlist)
    rows = {r["mode"]: r for r in card["segments"]}
    assert (rows["nifty200_momentum"]["score"], rows["nifty200_momentum"]["verdict"]) == (71.2, "cleared")
    assert rows["nifty200_momentum"]["score_source"] == "stored by the last Momentum pass"
    assert (rows["swing"]["score"], rows["swing"]["verdict"]) == (57.5, "watch")       # 55 <= 57.5 < 60
    assert rows["positional"]["verdict"] == "unknown"
    assert card["last_scan"]["segment"] == "Swing" and card["last_scan"]["floor"] == 60.0


def test_a_score_under_55_reads_below_not_watch(tmp_path):
    matrix = {"mode": "short", "ts": "x", "score_floor": 70.0, "rows": {"ABC.NS": {"score": 41.0}}}
    card, _, _ = _card(tmp_path, _bars(), scan_matrix=matrix)
    short = {r["mode"]: r for r in card["segments"]}["short"]
    assert (short["verdict"], short["verdict_label"]) == ("below", "BELOW")


def test_a_held_stock_shows_its_own_segment_and_exit_levels(tmp_path):
    df = _bars()
    last = float(df["Close"].iloc[-1])
    pos = PaperPosition(ticker="ABC.NS", qty=40, entry_price=last / 1.05, entry_date=datetime(2026, 11, 20, 4, 0),
                        sector="IT", peak_price=last, entry_score=66.0, reasoning="fixture", mode="swing",
                        stop_price=round(last / 1.05 * 0.95, 2), target_price=round(last / 1.05 * 1.10, 2))
    card, _, _ = _card(tmp_path, df, positions={"ABC.NS": pos})
    held = card["held"]
    assert held["segment"] == "Swing" and held["qty"] == 40 and held["days"] == 6
    assert held["pnl_pct"] == pytest.approx(5.0) and held["to_target_pct"] == pytest.approx(50.0, abs=0.01)
    assert held["stop"] == pos.stop_price and held["target"] == pos.target_price


def test_no_stored_history_says_so_and_guesses_nothing(tmp_path):
    card, _, _ = _card(tmp_path, None)
    assert card["has_data"] is False and card["valuation"] is None
    assert all(r["stop_price"] is None and r["target_price"] is None for r in card["segments"])
    assert "nothing is guessed" in card["notes"][0]
    assert {r["mode"]: r["buy_mark"] for r in card["segments"]}["swing"] == 60.0   # rules still shown


def test_the_valuation_target_is_the_engines_own_rule(tmp_path, monkeypatch):
    from trading import valuation_exit as VE
    df = _bars()
    last = float(df["Close"].iloc[-1])
    seen = {}

    def target_from_record(rec, fill, today, enabled=True):
        seen.update(rec=rec, fill=fill)
        return round(fill * 1.30, 2), {"reason": ""}
    monkeypatch.setattr(VE, "ready", lambda: True)
    monkeypatch.setattr(VE, "records_for", lambda names: {"ABC.NS": {"asof": "2026-11-20", "verdict": "attractive"}})
    monkeypatch.setattr(VE, "target_from_record", target_from_record)
    card, _, _ = _card(tmp_path, df)
    v = card["valuation"]
    assert v["target"] == round(last * 1.30, 2) and v["upside_pct"] == pytest.approx(30.0, abs=0.01)
    assert seen["fill"] == last and seen["rec"]["verdict"] == "attractive"
    # and when the rule gives none, the reason is in plain words
    monkeypatch.setattr(VE, "target_from_record", lambda *a, **k: (None, {"reason": "not_judged:insufficient"}))
    v = _card(tmp_path, df)[0]["valuation"]
    assert v["target"] is None and "could not judge this stock (insufficient)" in v["text"]
    monkeypatch.setattr(VE, "ready", lambda: False)
    assert "no stored valuation record" in _card(tmp_path, df)[0]["valuation"]["text"]


def test_the_card_never_calls_a_feed_or_the_composite_scorer(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("a page view must not fetch or score through a feed")
    monkeypatch.setattr("trading.scoring.compute_score", forbidden)
    monkeypatch.setattr("trading.data.fetch_daily", forbidden)
    monkeypatch.setattr("trading.signals.news.fetch_sentiment", forbidden)
    card, _, _ = _card(tmp_path, _bars())
    assert len(card["segments"]) == 5


# ----------------------------------------------------------------- the page

def _page(df, monkeypatch, tmp_path, **setup):
    from fastapi.testclient import TestClient
    from web import auth, state
    from web.main import app
    e, cfg, p = _engine(tmp_path)
    for key, value in setup.items():
        setattr(p, key, value)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(auth, "current_user", lambda r: {"role": "owner", "email": "fixture"})
    with patch("web.routes.chart_routes.load_ohlcv", return_value=df):
        r = TestClient(app).get("/chart/ABC.NS")
    assert r.status_code == 200, r.text[:400]
    return r.text


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_chart_page_prints_the_card_above_the_chart(tmp_path, monkeypatch):
    df = _bars()
    last = float(df["Close"].iloc[-1])
    pos = PaperPosition(ticker="ABC.NS", qty=40, entry_price=last / 1.05, entry_date=datetime(2026, 11, 20, 4, 0),
                        sector="IT", peak_price=last, entry_score=0.0, reasoning="fixture", mode="",
                        tags=["owner_pick"])                   # a hand buy: score 0, blank mode, no levels
    html = _page(df, monkeypatch, tmp_path, positions={"ABC.NS": pos})
    assert html.index("stock_card_marker") < html.index("<svg")
    text = _text(html)
    assert "Segments, stops and targets" in text and "display only, never a buy signal" in text
    for label in ("Momentum", "Intraday", "Short", "Swing", "Positional"):
        assert label in text
    assert html.count("data-stock-card-segment=") == 5
    assert "In your book — Momentum (your own buy)" in text
    assert "stop — (segment rule)" in text and "target — (segment rule)" in text
    assert "Priced off 5-minute bars at the moment of entry" in text
    assert "Valuation target: none would be used" in text


def test_the_page_renders_with_no_history_and_when_the_card_fails(tmp_path, monkeypatch):
    html = _page(None, monkeypatch, tmp_path)
    assert "stock_card_marker" in html and "DATA_NEEDED" in html
    assert "no stored daily history" in _text(html)
    monkeypatch.setattr(stock_card, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    html = _page(_bars(), monkeypatch, tmp_path)
    assert "stock_card_marker" not in html and "<svg" in html      # the chart still draws
