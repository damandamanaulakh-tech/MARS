"""Tests for the macro regime classifier (no network)."""

from trading.macro import classify_regime


def test_risk_off_triggered_by_high_vix():
    regime, cap, mult, halt, why = classify_regime(
        vix=27.0, vix_5d=10.0, crude_5d=2.0, gold_5d=1.0, usd_inr_5d=0.5,
    )
    assert regime == "risk_off"
    assert cap == 0.5
    assert mult == 0.5
    assert halt is True
    assert "VIX 27" in why


def test_risk_off_triggered_by_vix_spike():
    regime, *_ = classify_regime(
        vix=20.0, vix_5d=40.0, crude_5d=2.0, gold_5d=1.0, usd_inr_5d=0.0,
    )
    assert regime == "risk_off"


def test_risk_off_triggered_by_crude_jump():
    regime, *_ = classify_regime(
        vix=15.0, vix_5d=5.0, crude_5d=12.0, gold_5d=0.0, usd_inr_5d=0.0,
    )
    assert regime == "risk_off"


def test_risk_off_triggered_by_inr_weakness():
    regime, *_ = classify_regime(
        vix=15.0, vix_5d=2.0, crude_5d=0.0, gold_5d=0.0, usd_inr_5d=2.0,
    )
    assert regime == "risk_off"


def test_risk_off_triggered_by_gold_spike():
    regime, *_ = classify_regime(
        vix=15.0, vix_5d=0.0, crude_5d=0.0, gold_5d=6.0, usd_inr_5d=0.0,
    )
    assert regime == "risk_off"


def test_neutral_zone_for_elevated_vix():
    regime, cap, mult, halt, why = classify_regime(
        vix=20.0, vix_5d=10.0, crude_5d=0.0, gold_5d=0.0, usd_inr_5d=0.0,
    )
    assert regime == "neutral"
    assert cap == 0.75
    assert mult == 1.0
    assert halt is False


def test_risk_on_when_all_calm():
    regime, cap, mult, halt, why = classify_regime(
        vix=14.0, vix_5d=2.0, crude_5d=1.0, gold_5d=0.5, usd_inr_5d=0.2,
    )
    assert regime == "risk_on"
    assert cap == 1.0
    assert mult == 1.0
    assert halt is False
    assert "RISK_ON" in why


def test_risk_on_with_all_none_inputs():
    """When data is missing entirely, we don't trigger risk-off."""
    regime, *_ = classify_regime(
        vix=None, vix_5d=None, crude_5d=None, gold_5d=None, usd_inr_5d=None,
    )
    assert regime == "risk_on"


def test_partial_data_still_classifies():
    regime, *_ = classify_regime(
        vix=22.0, vix_5d=None, crude_5d=None, gold_5d=None, usd_inr_5d=None,
    )
    assert regime == "neutral"
