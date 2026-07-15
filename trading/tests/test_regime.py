"""Tests for the dual-regime master classifier (no network)."""

from trading.regime import classify


def test_panic_when_nifty_crashed_and_vix_high():
    regime, ev, oc, mult_e, mult_o, _ = classify(
        nifty_close=20_000, nifty_200dma=22_000,
        pct_below_52w_high=-12.0, vix=28.0, fii_5d_net=-5000,
    )
    assert regime == "panic"
    assert ev is False
    assert oc is True
    assert mult_e == 0.0
    assert mult_o == 0.5


def test_correction_when_below_200dma():
    regime, ev, oc, *_ = classify(
        nifty_close=21_000, nifty_200dma=22_000,
        pct_below_52w_high=-5.0, vix=17.0, fii_5d_net=None,
    )
    assert regime == "correction"
    assert ev is False
    assert oc is True


def test_correction_when_deep_off_52w_high():
    regime, *_ = classify(
        nifty_close=22_100, nifty_200dma=22_000,
        pct_below_52w_high=-9.0, vix=20.0, fii_5d_net=None,
    )
    assert regime == "correction"


def test_neutral_zone_at_200dma():
    regime, ev, oc, mult_e, mult_o, _ = classify(
        nifty_close=22_100, nifty_200dma=22_000,
        pct_below_52w_high=-3.0, vix=15.0, fii_5d_net=200,
    )
    assert regime == "neutral"
    assert ev is True and oc is True
    assert mult_e == 0.5 and mult_o == 0.5


def test_bull_extension_above_200dma_well_off_low():
    regime, ev, oc, mult_e, mult_o, _ = classify(
        nifty_close=24_000, nifty_200dma=22_000,
        pct_below_52w_high=-1.0, vix=12.0, fii_5d_net=3000,
    )
    assert regime == "bull_extension"
    assert ev is True
    assert oc is False
    assert mult_e == 1.0
    assert mult_o == 0.0


def test_fallback_when_no_data():
    regime, ev, oc, *_ = classify(
        nifty_close=None, nifty_200dma=None,
        pct_below_52w_high=None, vix=None, fii_5d_net=None,
    )
    assert regime == "neutral"
    # Default: Everest stays on (don't accidentally turn off in unknown state)
    assert ev is True
    assert oc is False
