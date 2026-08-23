"""Node 8 (owner order 2026-08-23: "continue with node 8") — the option-
chain snapshot that closes T12W_0102, the last unwired trigger.

What these tests pin: the chain summarizer's honesty (empty chain = None,
never a zero PCR), the freshness rule (a login-less day attaches nothing —
a stale chain must not wear today's confidence), the deterioration leg
needing our own forward archive, the trigger's three-way verdicts, the
money-path exclusions (uncalibrated = never in the danger layer, skipped
by the calibration runner), and the scheduler job's journaling.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading import external_panels as XP
from trading import options_chain as OC
from trading import triggers as T


# ---------------------------------------------------------------------------
# summarizer
# ---------------------------------------------------------------------------

def _strike(k, c_oi, p_oi, c_iv=None, p_iv=None, spot=None):
    row = {
        "strike_price": k,
        "call_options": {"market_data": {"oi": c_oi},
                         "option_greeks": {"iv": c_iv}},
        "put_options": {"market_data": {"oi": p_oi},
                        "option_greeks": {"iv": p_iv}},
    }
    if spot is not None:
        row["underlying_spot_price"] = spot
    return row


def test_summarize_reads_pcr_and_atm_skew():
    rows = [
        _strike(95, 1000, 3000, 20.0, 26.0, spot=100.0),
        _strike(100, 2000, 2000, 18.0, 22.5),
        _strike(105, 3000, 1000, 17.0, 20.0),
    ]
    s = OC.summarize_chain(rows)
    assert s["pcr_oi"] == 1.0                     # 6000 puts / 6000 calls
    assert s["atm_strike"] == 100
    assert s["iv_skew"] == 4.5                    # 22.5 − 18.0, puts pricier
    assert s["n_strikes"] == 3


def test_empty_chain_is_none_never_a_zero_pcr():
    """PCR 0 would read as maximally bullish — an empty chain is DATA_NEEDED."""
    assert OC.summarize_chain([]) is None
    assert OC.summarize_chain([_strike(100, 0, 0)]) is None


def test_nearest_expiry_skips_the_past():
    contracts = [{"expiry": "2026-08-14"}, {"expiry": "2026-08-27"},
                 {"expiry": "2026-09-24"}, {"expiry": "garbage"}]
    assert OC.nearest_expiry(contracts, date(2026, 8, 23)) == "2026-08-27"
    assert OC.nearest_expiry([], date(2026, 8, 23)) is None


def test_pcr_change_needs_the_forward_archive():
    """No history of past chains exists anywhere — deterioration cannot be
    read off a single day's level."""
    short = [("2026-08-21", 1.0, 2.0), ("2026-08-22", 1.1, 2.0)]
    assert OC.pcr_change(short) is None           # < SNAPSHOT_WINDOW
    hist = [(f"2026-08-{d:02d}", 1.0 + 0.05 * i, 2.0)
            for i, d in enumerate(range(15, 22))]
    ch = OC.pcr_change(hist)
    assert ch is not None and ch > 0.15


# ---------------------------------------------------------------------------
# attach + freshness
# ---------------------------------------------------------------------------

def _summary(pcr=1.4, skew=4.0):
    return {"pcr_oi": pcr, "iv_skew": skew, "iv_call": 18.0, "iv_put": 22.0,
            "ce_oi": 1000.0, "pe_oi": 1400.0, "expiry": "2026-08-27"}


def test_attach_only_when_fresh():
    today = date.today().isoformat()
    sx = XP.StockExternals(ticker="X.NS")
    XP.attach_chain(sx, _summary(), [], today)
    assert sx.pcr_oi == 1.4 and sx.iv_skew == 4.0
    stale = XP.StockExternals(ticker="X.NS")
    XP.attach_chain(stale, _summary(), [], "2026-01-05")
    assert stale.pcr_oi is None                   # a login-less week = nothing


def test_attach_ignores_junk():
    sx = XP.StockExternals(ticker="X.NS")
    for junk in (None, "x", 3, {}):
        XP.attach_chain(sx, junk, [], date.today().isoformat())
    assert sx.pcr_oi is None


# ---------------------------------------------------------------------------
# T12W_0102
# ---------------------------------------------------------------------------

def _near_support_df(n: int = 120) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = np.full(n, 100.0)
    c[-1] = 98.0                                   # within 3% of the 20d low
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.98,
                         "Close": c, "Volume": np.full(n, 1e6)}, index=idx)


def _hits(df, sx):
    return T.evaluate(df, ext=sx)


def test_0102_is_data_needed_without_a_chain():
    assert _hits(_near_support_df(),
                 XP.StockExternals(ticker="X.NS"))["T12W_0102"] is None


def test_0102_fires_on_skew_at_support_and_stays_quiet_off_support():
    today = date.today().isoformat()
    sx = XP.StockExternals(ticker="X.NS")
    XP.attach_chain(sx, _summary(skew=5.0), [], today)     # skew ≥ 3 pts
    assert _hits(_near_support_df(), sx)["T12W_0102"] is True
    # same options picture, price NOT near support -> their formula says no
    idx = pd.bdate_range("2025-01-01", periods=120)
    c = np.linspace(100, 130, 120)
    strong = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                           "Close": c, "Volume": np.full(120, 1e6)}, index=idx)
    assert _hits(strong, sx)["T12W_0102"] is False


def test_0102_deterioration_leg_uses_the_forward_archive():
    today = date.today().isoformat()
    hist = [(f"2026-08-{d:02d}", 1.0 + 0.05 * i, 0.0)
            for i, d in enumerate(range(15, 22))]           # PCR rising 30%
    sx = XP.StockExternals(ticker="X.NS")
    XP.attach_chain(sx, _summary(skew=0.5), hist, today)    # skew leg quiet
    assert sx.pcr_change is not None
    assert _hits(_near_support_df(), sx)["T12W_0102"] is True


def test_0102_can_never_reach_the_money_path():
    """UNCALIBRATED (no backward archive exists) — the owner's
    REQUIRES_15Y_BACKTEST rule keeps it display/watchlist only."""
    from trading import danger
    assert "T12W_0102" not in danger.VERIFIED
    assert "T12W_0102" not in danger._EXT_FNS
    assert "T12W_0102" not in danger._EXTERNAL_ALLOWED
    assert T.CHAIN_FED == ("T12W_0102",)
    src = Path("scripts/bt_calibration_external_v1.py").read_text()
    assert "CHAIN_FED" in src                     # runner skips it explicitly


# ---------------------------------------------------------------------------
# scheduler job
# ---------------------------------------------------------------------------

class _FakeBroker:
    def __init__(self, connected=True):
        self._c = connected

    def is_connected(self):
        return self._c

    def get_option_contracts(self, ik):
        return [{"expiry": "2099-12-30"}] if "INE" in ik or "Nifty" in ik else None

    def get_option_chain(self, ik, expiry):
        return [_strike(95, 1000, 3000, 20.0, 26.0, spot=100.0),
                _strike(100, 2000, 2000, 18.0, 22.5)]


def test_job_snapshots_and_journals(monkeypatch):
    from web import scheduler, state
    p = state.portfolio()
    old = p.option_chain
    p.option_chain = {}
    monkeypatch.setattr(state.engine(), "broker", _FakeBroker())
    monkeypatch.setattr(
        "trading.brokers.upstox_instruments.to_instrument_keys",
        lambda ts: {t: "NSE_EQ|INE000TEST" for t in ts})
    try:
        out = scheduler.run_option_chain_snapshot("test")
        assert out["rows"] and "^NSEI" in out["rows"]
        assert any(e.get("action") == "OPTION_CHAIN_SNAPSHOT"
                   for e in p.decision_journal[-3:])
        # once-per-day guard: the second call is a no-op returning the stored
        again = scheduler.run_option_chain_snapshot("test2")
        assert again is p.option_chain
    finally:
        p.option_chain = old
        p.save()


def test_job_without_a_token_journals_the_skip_once(monkeypatch):
    from web import scheduler, state
    p = state.portfolio()
    old = p.option_chain
    p.option_chain = {}
    monkeypatch.setattr(state.engine(), "broker", _FakeBroker(connected=False))
    try:
        scheduler.run_option_chain_snapshot("test")
        skips = [e for e in p.decision_journal[-3:]
                 if e.get("action") == "OPTION_CHAIN_SKIPPED"]
        assert len(skips) == 1
        n = len(p.decision_journal)
        scheduler.run_option_chain_snapshot("test")        # same day again
        assert len(p.decision_journal) == n                # no second entry
    finally:
        p.option_chain = old
        p.save()
