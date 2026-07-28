"""34 candle structures — owner asks 2026-07-26 ("10-12 candle structures
as parameters") + 2026-07-28 ("i knw u have few but i want more": the
reference-chart set — three inside/outside, three line strike, abandoned
baby, kickers, doji stars, dragonfly/gravestone, hanging man, spinning
top, bearish harami)."""

import pandas as pd

from trading.candles import (BEARISH_PATTERNS, BULLISH_PATTERNS,
                             NEUTRAL_PATTERNS, _PATTERN_FUNCS,
                             abandoned_baby_bear, abandoned_baby_bull,
                             bearish_engulfing, bearish_harami,
                             dark_cloud_cover, doji, dragonfly_doji,
                             evening_doji_star, evening_star,
                             gravestone_doji, hanging_man, kicker_bear,
                             kicker_bull, marubozu, morning_doji_star,
                             scan_all_patterns, shooting_star, spinning_top,
                             three_black_crows, three_inside_down,
                             three_inside_up, three_line_strike,
                             three_outside_down, three_outside_up,
                             tweezer_top)


def _bars(rows):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"]).assign(Volume=1e6)


def test_structure_count_is_34():
    total = 2 + len([f for f in BULLISH_PATTERNS.values() if f]) \
        + len(BEARISH_PATTERNS) + len(NEUTRAL_PATTERNS)
    assert total == 34
    assert len(BEARISH_PATTERNS) == 14     # top-reading family, now full


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
    assert len(out) == 34
    assert all(isinstance(v, bool) for v in out.values())
    for name in BEARISH_PATTERNS:
        assert name in out          # bearish family is readable per stock


# ---- 2026-07-28 reference-chart set ----

def test_three_inside_up_and_down():
    # big red, small green inside it, green close above the red's open
    up = _bars([[110, 111, 99, 100], [102, 105, 101, 104], [104, 112, 103, 111]])
    assert three_inside_up(up) is True
    # third bar red -> no confirmation
    up_fail = _bars([[110, 111, 99, 100], [102, 105, 101, 104], [104, 105, 100, 101]])
    assert three_inside_up(up_fail) is False
    # mirror: big green, small red inside, red close below the green's open
    dn = _bars([[100, 111, 99, 110], [108, 109, 105, 106], [106, 107, 98, 99]])
    assert three_inside_down(dn) is True


def test_three_outside_up_and_down():
    # red bar engulfed by green, third green closes higher
    up = _bars([[105, 106, 99, 100], [99, 108, 98, 107], [107, 112, 106, 111]])
    assert three_outside_up(up) is True
    dn = _bars([[100, 106, 99, 105], [106, 107, 98, 99], [99, 100, 93, 94]])
    assert three_outside_down(dn) is True
    # no follow-through third bar
    dn_fail = _bars([[100, 106, 99, 105], [106, 107, 98, 99], [99, 104, 98, 103]])
    assert three_outside_down(dn_fail) is False


def test_three_line_strike_takes_back_the_decline():
    df = _bars([[110, 111, 104, 105], [105, 106, 99, 100], [100, 101, 94, 95],
                [95, 112, 94, 111]])
    assert three_line_strike(df) is True
    # strike bar too weak (doesn't clear the first red's open)
    weak = _bars([[110, 111, 104, 105], [105, 106, 99, 100], [100, 101, 94, 95],
                  [95, 106, 94, 105]])
    assert three_line_strike(weak) is False


def test_abandoned_baby_both_sides_need_real_gaps():
    bull = _bars([[110, 111, 100, 101], [96, 98, 94, 96.2], [100, 108, 99, 107]])
    assert abandoned_baby_bull(bull) is True
    no_gap = _bars([[110, 111, 100, 101], [99, 103, 97, 99.3], [100, 108, 99, 107]])
    assert abandoned_baby_bull(no_gap) is False
    bear = _bars([[100, 111, 99, 110], [113, 116, 112, 113.3], [110, 111, 102, 103]])
    assert abandoned_baby_bear(bear) is True


def test_kickers_gap_and_never_look_back():
    assert kicker_bull(_bars([[105, 106, 99, 100], [107, 113, 106, 112]])) is True
    # opens above prior open but dips back into the red bar -> not a kicker
    assert kicker_bull(_bars([[105, 106, 99, 100], [107, 113, 103, 112]])) is False
    assert kicker_bear(_bars([[100, 106, 99, 105], [98, 99, 92, 93]])) is True


def test_doji_family_and_spinning_top():
    down = [[120 - i, 121 - i, 118 - i, 119 - i] for i in range(5)]
    assert dragonfly_doji(_bars(down + [[115, 115.2, 105, 114.9]])) is True
    assert gravestone_doji(_bars([[100, 110, 99.8, 100.2]])) is True
    assert spinning_top(_bars([[100, 106, 94, 101.5]])) is True
    assert spinning_top(_bars([[100, 110, 100, 110]])) is False   # marubozu, not a top
    up = [[100 + i, 102 + i, 99 + i, 101 + i] for i in range(5)]
    assert hanging_man(_bars(up + [[110, 110.5, 100, 109.5]])) is True


def test_doji_star_variants():
    m = _bars([[110, 111, 99, 100], [99, 100.5, 98.5, 99.1], [100, 108, 99, 107]])
    assert morning_doji_star(m) is True
    e = _bars([[100, 111, 99, 110], [110.5, 112, 110, 110.6], [110, 111, 100, 101]])
    assert evening_doji_star(e) is True


def test_bearish_harami_completes_the_pair():
    assert bearish_harami(_bars([[100, 111, 99, 110], [108, 109, 105, 106]])) is True
    assert bearish_harami(_bars([[110, 111, 99, 100], [102, 105, 101, 104]])) is False


def test_strong_new_bulls_are_gate_capable_and_in_the_default_set():
    from trading.config import Config
    new = {"three_inside_up", "three_outside_up", "three_line_strike",
           "abandoned_baby_bull", "kicker_bull", "morning_doji_star"}
    assert new <= set(_PATTERN_FUNCS)
    assert new <= set(Config().candle_patterns)
    # needs-follow-up shapes must NOT silently loosen the entry gate
    for lane_only in ("dragonfly_doji", "inverted_hammer", "bullish_harami"):
        assert lane_only not in Config().candle_patterns


def test_piano_styling_derives_from_the_pattern_registries():
    from web.routes.scan_routes import _BEARISH_NAMES, _CANDLE_HIT, _STRUCT_DEFS
    assert _BEARISH_NAMES == set(BEARISH_PATTERNS)
    assert _CANDLE_HIT == {"breakout", "urr"} | set(_PATTERN_FUNCS)
    # every structure has a plain-language definition for its tap page
    for name in list(BULLISH_PATTERNS) + list(BEARISH_PATTERNS) + list(NEUTRAL_PATTERNS):
        assert name in _STRUCT_DEFS, name
