"""Tests for the C1-C4 ports: quality co-scorer, proof ledger, universe CSV."""

import numpy as np
import pandas as pd

from trading.quality import (
    blend_scores, fundamental_score, liquidity_score, low_vol_score, quality_score,
)


def _series(daily_vol, n=300, seed=1):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0, daily_vol, n))))


def test_low_vol_beats_high_vol():
    assert low_vol_score(_series(0.008)) > low_vol_score(_series(0.045))


def test_scores_are_bounded():
    for s in (_series(0.005), _series(0.06)):
        assert 0.0 <= low_vol_score(s) <= 100.0
        assert 0.0 <= quality_score(s, pd.Series([500_000.0] * 40)) <= 100.0


def test_liquidity_tiers():
    assert liquidity_score(pd.Series([2_000_000.0] * 30)) == 90.0
    assert liquidity_score(pd.Series([50_000.0] * 30)) == 30.0
    assert liquidity_score(None) == 50.0


def test_blend_math():
    assert blend_scores(80, 40, 0.0) == 80.0
    assert blend_scores(80, 40, 0.5) == 60.0
    assert blend_scores(80, 40, 1.0) == 40.0


def test_fundamentals_optional_and_bounded():
    assert fundamental_score(None) is None
    v = fundamental_score({"pe": 12, "beta": 0.7, "analyst_rec": "strong_buy"})
    assert 0.0 <= v <= 100.0


def test_proof_ledger_shape():
    from trading.proof import proof_gap_ledger
    ledger = proof_gap_ledger()
    assert len(ledger) >= 3
    assert all({"id", "gap", "status"} <= set(g) for g in ledger)


def test_universe_csv_loader(tmp_path):
    from trading.universes import load_universe_from_csv
    p = tmp_path / "u.csv"
    p.write_text("ticker\nRELIANCE\nTCS.NS\nRELIANCE\nINFY.BO\n")
    got = load_universe_from_csv(str(p))
    assert got == ["RELIANCE.NS", "TCS.NS", "INFY.BO"]  # suffix added, de-duped
