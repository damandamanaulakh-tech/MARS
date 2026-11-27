"""RUN-20261125-01 — a score input with weight 0 is not fetched from outside.

On 25 Nov 2026 both intraday scans scored nobody: bars are valid for six
minutes and the scorer spent about 0.9 s per stock calling Yahoo for earnings
and analyst readings that carry weight 0.00 in the intraday score. These tests
fail on the code before the fix (the outside functions were always called).
"""
import numpy as np
import pandas as pd
import pytest

from trading import scoring
from trading.config import Config
from trading.modes import WEIGHTS


def _frame(n=120):
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    c = 100 + np.arange(n) * 0.2 + np.sin(np.arange(n)) * 0.5
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


@pytest.fixture
def spies(monkeypatch):
    calls = {"earnings": 0, "analyst": 0, "catalyst": 0}

    def earnings(ticker):
        calls["earnings"] += 1
        return 91.0

    def analyst(ticker):
        calls["analyst"] += 1
        return 17.0

    class _S:
        score = 73.0

    def sentiment(ticker):
        calls["catalyst"] += 1
        return _S()

    monkeypatch.setattr(scoring, "score_earnings_growth", earnings)
    monkeypatch.setattr(scoring, "_analyst_revision_score", analyst)
    monkeypatch.setattr("trading.signals.news.fetch_sentiment", sentiment)
    scoring.set_scoring_context(fii_score=None, hot_sectors=set())
    return calls


def _cfg(mode):
    cfg = Config()
    cfg.mode = mode
    cfg.apply_mode_preset()
    return cfg


def test_intraday_does_not_fetch_its_zero_weight_inputs(spies):
    assert WEIGHTS["intraday"]["earnings_growth"] == 0.0
    assert WEIGHTS["intraday"]["analyst_revision"] == 0.0
    f = _frame()
    total, comp = scoring.compute_score("AAA.NS", f, f, {}, _cfg("intraday"), mode="intraday")
    assert spies["earnings"] == 0 and spies["analyst"] == 0
    assert comp["earnings_growth"] == 50.0 and comp["analyst_revision"] == 50.0
    # catalyst has weight 0.10 in intraday, so it IS still read
    assert spies["catalyst"] == 1 and comp["catalyst"] == 73.0
    assert scoring.unfetched_inputs("intraday") == ("earnings_growth", "analyst_revision")


def test_the_total_is_identical_to_fetching_them(spies, monkeypatch):
    """Weight 0 x any reading = 0: skipping the fetch cannot move the score."""
    f = _frame()
    cfg = _cfg("intraday")
    skipped, _ = scoring.compute_score("AAA.NS", f, f, {}, cfg, mode="intraday")
    w = scoring.weights_for("intraday")
    _, comp = scoring.compute_score("AAA.NS", f, f, {}, cfg, mode="intraday")
    fetched = dict(comp, earnings_growth=91.0, analyst_revision=17.0)
    assert sum(fetched[k] * w[k] for k in w) == pytest.approx(skipped, abs=1e-12)


@pytest.mark.parametrize("mode", ["short", "swing", "positional"])
def test_categories_that_weight_them_still_fetch(spies, mode):
    f = _frame()
    _, comp = scoring.compute_score("AAA.NS", f, f, {}, _cfg(mode), mode=mode)
    assert spies["earnings"] == 1 and spies["analyst"] == 1 and spies["catalyst"] == 1
    assert comp["earnings_growth"] == 91.0 and comp["analyst_revision"] == 17.0
    assert scoring.unfetched_inputs(mode) == ()


def test_no_category_weight_was_changed():
    """This repair touches fetching only; the owner's weights stay as ordered."""
    assert WEIGHTS["intraday"] == {
        "momentum_1m": 0.10, "volume_expansion": 0.20, "earnings_growth": 0.00,
        "catalyst": 0.10, "relative_strength": 0.10, "vol_adj_upside": 0.10,
        "short_interest": 0.00, "analyst_revision": 0.00,
        "breakout_quality": 0.30, "sector_alignment": 0.10}
