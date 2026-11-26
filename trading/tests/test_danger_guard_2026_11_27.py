"""XR-060 follow-up — the danger layer's "once per ticker per day" guard did
not hold on a real-sized book.

Found in the adversarial review of the verdict repair. The guard read the
last 300 rows of the decision journal, but that journal is BYTE-capped
(paper.JOURNAL_BYTES_CAP, 2 MiB) and trimmed inside the save the danger pass
itself calls. One pass on a 56-position book writes about 112 rows — a
DANGER_* row and a SELL_PARTIAL row per acted name — of which only ~103
survive, so four names lost their own guard every pass and were half-sold
again. Over one day's thirteen passes 56 intended half-sales became 104 and
the worst position fell from 1,000 shares to 250.

The record now lives on the book, which is persisted and not byte-capped.
These tests fail on the code before that change.
"""
from __future__ import annotations

import os
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

BOOK = 56                    # the owner's live book size on 27 November
PASSES = 3                   # the 30-minute in-session cadence


@pytest.fixture(autouse=True)
def _hours(monkeypatch):
    monkeypatch.setenv("SB_IGNORE_MARKET_HOURS", "1")
    yield


def _frame(bars=300):
    c = np.linspace(100.0, 140.0, bars)
    c[-1] = c[-2] * 0.88
    h, l = c * 1.01, c * 0.99
    l[-1], h[-1] = c[-1] * 0.995, c[-2] * 0.995
    v = np.full(bars, 1_000_000.0)
    v[-1] = 9_000_000.0
    return pd.DataFrame({"Open": c, "High": h, "Low": l, "Close": c, "Volume": v},
                        index=pd.bdate_range("2025-01-01", periods=bars))


def _book(tmp_path, n=BOOK):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.danger_layer_enabled = True
    p = Portfolio.fresh(cfg)
    df = _frame()
    last = float(df["Close"].iloc[-1])
    for i in range(n):
        t = f"N{i:02d}.NS"
        p.positions[t] = PaperPosition(
            ticker=t, qty=1000, entry_price=last * 1.2, entry_date=datetime(2025, 6, 1),
            stop_price=last * 1.14, target_price=last * 1.5, mode="nifty200_momentum",
            sector="IT", peak_price=last * 1.2, entry_score=70.0, reasoning="fixture")
    p.danger_alerts_start = "2020-01-01"          # past the alert-only window
    eng = TradingEngine(cfg, p)
    prices = {t: last for t in p.positions}
    return eng, p, prices, {t: df for t in p.positions}


def test_a_position_is_acted_on_once_a_day_however_big_the_book(tmp_path):
    eng, p, prices, hist = _book(tmp_path)
    for _ in range(PASSES):
        eng._danger_pass(prices, hist, datetime.utcnow())
    assert len(p.trades) == BOOK, (
        f"{len(p.trades)} half-sales across {PASSES} passes on a {BOOK}-position "
        "book; the guard must allow one per ticker per day")
    assert {pos.qty for pos in p.positions.values()} == {500}, "halved more than once"


def test_the_guard_does_not_depend_on_the_byte_capped_journal(tmp_path):
    """The journal is trimmed below one pass's worth of rows — the guard must
    not care."""
    from trading.paper import JOURNAL_BYTES_CAP
    eng, p, prices, hist = _book(tmp_path)
    eng._danger_pass(prices, hist, datetime.utcnow())
    import json
    kept = len(json.dumps(p.decision_journal, default=str))
    assert kept <= JOURNAL_BYTES_CAP
    danger_rows = [e for e in p.decision_journal[-300:]
                   if str(e.get("action", "")).startswith("DANGER_")]
    assert len(danger_rows) < BOOK, "fixture must actually overflow the cap"
    assert set(p.danger_acted.get("tickers", [])) == set(p.positions)
    assert p.danger_acted.get("day") == datetime.utcnow().date().isoformat()


def test_the_record_survives_a_restart(tmp_path):
    eng, p, prices, hist = _book(tmp_path, n=4)
    eng._danger_pass(prices, hist, datetime.utcnow())
    assert len(p.trades) == 4
    p.save()
    again = Portfolio.load_or_fresh(eng.cfg)
    assert set(again.danger_acted.get("tickers", [])) == set(p.positions)
    eng2 = TradingEngine(eng.cfg, again)
    eng2._danger_pass(prices, hist, datetime.utcnow())
    assert len(again.trades) == 4, "a restart must not re-arm the same day"


def test_a_new_day_re_arms_the_layer(tmp_path):
    eng, p, prices, hist = _book(tmp_path, n=4)
    eng._danger_pass(prices, hist, datetime.utcnow())
    assert len(p.trades) == 4
    p.danger_acted = {"day": "1999-01-01", "tickers": list(p.positions)}
    eng._danger_pass(prices, hist, datetime.utcnow())
    assert len(p.trades) == 8, "yesterday's record must not guard today"
    assert p.danger_acted["day"] == datetime.utcnow().date().isoformat()
