"""18 candle structures — owner ask 2026-07-26 ("10-12 candle structures
as parameters"). The original 8 were all bullish; 6 bearish + 2 neutral
added so the engine can also read a top."""

import pandas as pd

from trading.candles import (BEARISH_PATTERNS, BULLISH_PATTERNS,
                             NEUTRAL_PATTERNS, bearish_engulfing,
                             dark_cloud_cover, doji, evening_star, marubozu,
                             scan_all_patterns, shooting_star,
                             three_black_crows, tweezer_top)


def _bars(rows):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"]).assign(Volume=1e6)


def test_structure_count_is_18():
    total = 2 + len([f for f in BULLISH_PATTERNS.values() if f]) \
        + len(BEARISH_PATTERNS) + len(NEUTRAL_PATTERNS)
    assert total == 18
    assert len(BEARISH_PATTERNS) == 6      # the family that did not exist


def test_bearish_engulfing_fires_on_a_top():
    df = _bars([[100, 105, 99, 104], [105, 106, 98, 99]])
    assert bearish_engulfing(df) is True
    df_up = _bars([[105, 106, 98, 99], [100, 105, 99, 104]])
    assert bearish_engulfing(df_up) is False


def test_three_black_crows_needs_three_strong_reds():
    df = _bars([[110, 111, 104, 105], [105, 106, 99, 100], [100, 101, 94, 95]])
    assert three_black_crows(df) is True
    df2 = _bars([[110, 111, 104, 105], [105, 106, 99, 100], [100, 106, 99, 105]])
    assert three_black_crows(df2) is False


def test_shooting_star_and_doji_and_marubozu():
    assert shooting_star(_bars([[100, 110, 99.5, 100.5]])) is True
    assert doji(_bars([[100, 104, 96, 100.1]])) is True
    assert marubozu(_bars([[100, 110, 100, 110]])) is True
    assert marubozu(_bars([[100, 120, 90, 105]])) is False


def test_evening_star_and_dark_cloud_and_tweezer_top():
    assert evening_star(_bars([[100, 110, 99, 109], [109, 111, 108, 109.5],
                               [109, 110, 100, 101]])) is True
    assert dark_cloud_cover(_bars([[100, 110, 99, 109], [110, 111, 102, 103]])) is True
    assert tweezer_top(_bars([[100, 110, 99, 109], [109, 110, 100, 101]])) is True


def test_scan_all_patterns_returns_every_structure():
    df = _bars([[100 + i, 101 + i, 99 + i, 100.5 + i] for i in range(40)])
    out = scan_all_patterns(df)
    assert len(out) == 18
    assert all(isinstance(v, bool) for v in out.values())
    for name in BEARISH_PATTERNS:
        assert name in out          # bearish family is readable per stock
