"""The nine news-fed triggers (owner order 2026-08-14: "wire them").

T12W_0097/0099/0103/0104/0106/0107/0108/0109/0110 each combine a news flag
with a price condition. What these tests pin:

  1. no reading, or a reading whose fetch FAILED, leaves all nine
     DATA_NEEDED — "we could not look" must never become a quiet False;
  2. the three whose formula names another trigger read that trigger's ACTUAL
     hit, so the page can never show T12W_0057 quiet while T12W_0104 claims
     it fired;
  3. none of the nine can reach the money path, and the news bar cannot drift
     away from the news module's own TRIGGER score.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading import external_panels as XP
from trading import news_feed, triggers as T


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bars(n: int = 120, seed: int = 7) -> pd.DataFrame:
    """A plain synthetic series — production-shaped columns."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.0, n))
    close = np.maximum(close, 5.0)
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({
        "Open": close, "High": high, "Low": low, "Close": close,
        "Volume": rng.integers(4_000_000, 6_000_000, n).astype(float),
    }, index=pd.bdate_range("2025-01-01", periods=n))


def _reading(**kw) -> dict:
    base = {"verdict": "PASS", "tags": [], "risk_score": 0.0, "opp_score": 0.0,
            "sentiment": 0.0, "asof": "2026-08-14", "fetch_failed": False,
            "headlines": []}
    base.update(kw)
    return base


def _ext(reading: dict | None = None) -> XP.StockExternals:
    sx = XP.StockExternals(ticker="X.NS")
    if reading is not None:
        XP.attach_news(sx, reading)
    return sx


def _hits(df: pd.DataFrame, reading: dict | None) -> dict:
    return T.evaluate(df, ext=_ext(reading))


# ---------------------------------------------------------------------------
# 1. Absence of a reading is DATA_NEEDED, never a quiet False
# ---------------------------------------------------------------------------

def test_all_nine_are_wired_and_only_the_options_chain_is_left():
    assert len(T.NEWS_FED) == 9
    wired = {t.id for t in T.EXTERNAL_WIRED}
    assert set(T.NEWS_FED) <= wired
    # the one external trigger still genuinely blocked
    assert [t.id for t in T.EXTERNAL_PENDING] == ["T12W_0102"]


def test_no_reading_leaves_every_news_trigger_data_needed():
    h = _hits(_bars(), None)
    assert all(h[tid] is None for tid in T.NEWS_FED)


def test_a_failed_fetch_is_data_needed_not_quiet():
    """UNKNOWN means the feed broke. Treating it as "no bad news" would turn
    an outage into a silent all-clear on every stock at once."""
    broken = _reading(verdict="UNKNOWN", fetch_failed=True,
                      tags=["regulatory"], risk_score=9.0)
    h = _hits(_bars(), broken)
    assert all(h[tid] is None for tid in T.NEWS_FED)


def test_a_genuinely_quiet_stock_does_produce_false():
    """NO_NEWS is a real answer: we looked and nothing happened."""
    h = _hits(_bars(), _reading(verdict="NO_NEWS"))
    assert h["T12W_0097"] is False           # no negative-event flag
    assert h["T12W_0099"] is False           # no governance tag


def test_attach_news_ignores_anything_that_is_not_a_reading():
    for junk in (None, "TRIGGER", 3, [], {"verdict": "WHATEVER"}):
        sx = XP.attach_news(XP.StockExternals(ticker="X.NS"), junk)
        assert sx.news_risk is None and sx.news_verdict is None


# ---------------------------------------------------------------------------
# 2. The news half fires when the owner's flag is present
# ---------------------------------------------------------------------------

def test_0097_fires_on_a_negative_event_at_the_bar():
    df = _bars()
    hot = _reading(verdict="TRIGGER", tags=["regulatory"], risk_score=2.5)
    assert _hits(df, hot)["T12W_0097"] is True
    mild = _reading(tags=["rumour"], risk_score=1.0)   # below the bar
    assert _hits(df, mild)["T12W_0097"] is False


def test_0108_needs_both_legs_regulatory_and_a_distribution_cluster():
    df = _bars()
    reg = _reading(verdict="TRIGGER", tags=["regulatory"], risk_score=2.5)
    h = _hits(df, reg)
    dist = h and T.family_counts(h).get(T.F_DISTRIBUTION, 0)
    # whatever the price side says, the trigger must agree with it
    assert h["T12W_0108"] is (dist >= 2)
    # and with no regulatory tag it is quiet regardless of the price side
    assert _hits(df, _reading(tags=["upgrade"], opp_score=1.5))[
        "T12W_0108"] is False


def test_0109_wants_an_earnings_miss_and_a_10d_low_break():
    df = _bars()
    df.iloc[-1, df.columns.get_loc("Close")] = float(df["Low"][-11:-1].min()) - 1
    miss = _reading(verdict="TRIGGER", tags=["results_miss"], risk_score=2.0)
    assert _hits(df, miss)["T12W_0109"] is True
    # same price break, no miss in the news -> quiet
    assert _hits(df, _reading(tags=["upgrade"], opp_score=1.5))[
        "T12W_0109"] is False


def test_0099_wants_governance_and_a_broken_support():
    df = _bars()
    df.iloc[-1, df.columns.get_loc("Close")] = float(df["Low"][-21:-1].min()) - 1
    gov = _reading(verdict="TRIGGER", tags=["governance"], risk_score=2.5)
    assert _hits(df, gov)["T12W_0099"] is True
    assert _hits(df, _reading(tags=["regulatory"], risk_score=2.5))[
        "T12W_0099"] is False


# ---------------------------------------------------------------------------
# 3. The three composites must agree with the trigger their formula names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("composite,price_leg", [
    ("T12W_0104", "T12W_0057"),      # + volume_wakeup_after_dry_base
    ("T12W_0106", "T12W_0033"),      # + support_sweep_reclaim
    ("T12W_0107", "T12W_0066"),      # + gap_up_volume_rejection
])
def test_composites_read_the_real_hit_of_the_trigger_they_name(composite,
                                                               price_leg):
    """Their formulas name these triggers by name. Reading the actual hit —
    rather than a second implementation — is what makes it impossible for the
    page to show the leg quiet and the composite fired."""
    df = _bars(seed=3)
    # a reading that satisfies every news half at once
    loud = _reading(verdict="TRIGGER", tags=["order_win", "regulatory"],
                    risk_score=3.0, opp_score=2.5)
    h = _hits(df, loud)
    assert h[price_leg] is not None
    if h[price_leg] is False:
        assert h[composite] is False
    else:
        assert h[composite] is True


def test_0106_fires_when_bad_news_is_absorbed_by_a_real_sweep_reclaim():
    """The fired branch, not just the quiet one: sweep the 20d low intraday
    and close back above it, with bad news on the tape."""
    df = _bars(seed=11)
    support = float(df["Low"][-21:-1].min())
    i = len(df) - 1
    df.iloc[i, df.columns.get_loc("Low")] = support * 0.97   # undercut
    df.iloc[i, df.columns.get_loc("Close")] = support * 1.02  # reclaim
    df.iloc[i, df.columns.get_loc("High")] = support * 1.03
    bad = _reading(verdict="TRIGGER", tags=["governance"], risk_score=2.5)
    h = _hits(df, bad)
    assert h["T12W_0033"] is True, "fixture must actually sweep and reclaim"
    assert h["T12W_0106"] is True
    # and the same price action with NO bad news is not an absorption
    assert _hits(df, _reading(verdict="NO_NEWS"))["T12W_0106"] is False


def test_a_composite_is_data_needed_when_its_price_leg_is():
    """Too little history for T12W_0033 must leave T12W_0106 unknown, not
    False — a missing leg is not a quiet leg."""
    short = _bars(n=40)
    loud = _reading(verdict="TRIGGER", tags=["governance"], risk_score=3.0)
    h = _hits(short, loud)
    assert h["T12W_0033"] is None and h["T12W_0106"] is None


# ---------------------------------------------------------------------------
# 4. Boundaries: the money path, and the bar that must not drift
# ---------------------------------------------------------------------------

def test_the_news_bar_cannot_drift_from_the_news_modules_own_score():
    """triggers.py must not import news_feed (danger.py imports triggers), so
    the bar is duplicated. This test is what keeps the copy honest."""
    assert (XP.ASSUMED_EXTERNAL_CONSTANTS["news_event_bar"]
            == news_feed.TRIGGER_SCORE)


def test_triggers_module_does_not_import_the_news_feed():
    src = Path(T.__file__).read_text()
    assert "news_feed" not in src


def test_no_news_trigger_can_enter_the_danger_layer():
    """Nothing uncalibratable may act on money — the owner's own
    REQUIRES_15Y_BACKTEST rule. There is no archive of past headlines, so
    these nine can never earn a verdict, let alone a place."""
    from trading import danger
    allowed = getattr(danger, "_EXTERNAL_ALLOWED", ())
    for tid in T.NEWS_FED:
        assert tid not in danger.VERIFIED
        assert tid not in danger._EXT_FNS
        assert tid not in allowed


def test_danger_layer_never_receives_news():
    """danger.externals_for loads panels only. It takes no news argument and
    a fresh StockExternals carries no news, so the held-book path cannot see
    a news flag even by accident."""
    import inspect

    from trading import danger
    assert "news" not in inspect.signature(danger.externals_for).parameters
    assert XP.StockExternals(ticker="X.NS").news_risk is None


def test_calibration_runner_skips_the_news_triggers():
    """A backtest window has no news to read, so any "result" would be the
    price leg alone wearing a calibrated verdict's clothes."""
    src = Path("scripts/bt_calibration_external_v1.py").read_text()
    assert "if t.id in NEWS_FED:" in src and "continue" in src


def test_the_timing_caveat_is_on_the_page_not_only_in_a_comment():
    view = T.stock_view(_bars(), asof="2026-08-14", ext=_ext(_reading()))
    assert view["total_news_fed"] == 9
    assert "STORED price bar" in view["news_note"]
    assert "never be calibrated" in view["news_note"]
