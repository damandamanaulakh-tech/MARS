"""Owner order 2026-10-04: "also make these things as finale set V 1.1".

docs/FINAL_SET_V1_1.md records the live recipe. These pins check its key
numbers against the code, so a later change cannot leave the record saying
something the app no longer does. Changing a pinned number means the owner
ordered a change — and the page then becomes V1.2.
"""

from pathlib import Path

from trading.config import Config
from trading.modes import get_preset
from trading.risk_v2 import DRAWDOWN_LEVELS, DYNAMIC_CASH_RESERVE
from trading.targets import ATR_TARGET_K

DOC = Path("docs/FINAL_SET_V1_1.md")


def _doc() -> str:
    return DOC.read_text()


def test_the_record_exists_and_names_itself():
    d = _doc()
    assert d.startswith("# FINAL SET V1.1")
    assert "finale set V 1.1" in d          # the owner's own words


def test_money_lines_match_the_config():
    c, d = Config(), _doc()
    assert c.starting_capital == 5_00_00_000 and "₹5,00,00,000" in d
    assert c.sizing_mode == "slab" and c.kelly_fraction == 0.29
    assert "0.29 × Kelly" in d
    assert c.position_slab_value == 150_000 == c.min_position_value
    assert "**₹1,50,000**" in d
    assert c.kelly_max_pct == 0.05 and "5% of the book (₹25 lakh)" in d
    assert c.max_positions == 222 and "| 222." in d
    assert c.ocean_position_value == 168_750 and c.ocean_max_positions == 8
    assert "₹1,68,750, up to 8" in d
    assert c.measured_kelly_enabled is True


def test_category_shares_match_the_config():
    c, d = Config(), _doc()
    assert c.category_budgets_enabled is True
    assert c.budget_share_momentum == 0.40 and "**Momentum 40%**" in d
    for k in ("intraday", "short", "swing", "positional"):
        assert getattr(c, f"budget_share_{k}") == 0.15
    assert "**15% each**" in d


def test_reserve_ladder_matches_risk_v2():
    d = _doc()
    assert DYNAMIC_CASH_RESERVE["correction"] == 0.20
    assert DYNAMIC_CASH_RESERVE["panic"] == 0.40
    assert Config().cash_reserve_pct == 0.0
    assert "0% normally, 20% in a correction, 40% in a panic" in d


def test_entry_rules_match_the_config():
    c, d = Config(), _doc()
    assert c.universe_target_size == 1200 and "~1,200" in d
    assert c.ticker_blocklist == ("YESBANK.NS",) and "YESBANK" in d
    assert c.quality_blend_pct == 0.35 and "blended 35% with quality" in d
    assert (c.breadth_gate_enabled, c.breadth_block_low,
            c.breadth_block_high) == (True, 0.40, 0.65)
    assert "40%–65%" in d
    assert c.correction_brake_enabled is False
    assert c.candle_confirm_skip_modes == ("nifty200_momentum",)
    assert c.multi_formula_gate is False
    assert c.max_sector_positions == 12 and c.max_sector_exposure_pct == 0.25
    assert c.daily_loss_halt_pct == -0.02 and "−2% of equity" in d
    assert c.max_portfolio_heat_pct == 0.25


def test_floors_and_scan_order_match_the_code():
    from web.scheduler import _DAILY_MODE_ORDER
    d = _doc()
    assert list(_DAILY_MODE_ORDER) == [
        "nifty200_momentum", "intraday", "short", "swing", "positional"]
    assert "momentum → intraday → short → swing → positional" in d
    floors = {"nifty200_momentum": 62.0, "intraday": 72.0, "short": 70.0,
              "swing": 60.0, "positional": 68.0}
    for mode, floor in floors.items():
        assert get_preset(mode).min_score_to_enter == floor, mode
    assert "momentum **62** · intraday 72 · short 70 · swing 60 · positional 68" in d


def test_exit_table_matches_presets_and_targets():
    d = _doc()
    rows = {
        "nifty200_momentum": ("| Momentum | −5% |", -0.05, -0.15, 40.0, 200, 8.0),
        "intraday": ("| Intraday | −1.5% |", -0.015, -0.01, 40.0, 1, 1.0),
        "short": ("| Short | −3% |", -0.03, -0.04, 45.0, 63, 5.0),
        "swing": ("| Swing | −5% |", -0.05, -0.08, 45.0, 20, 4.0),
        "positional": ("| Positional | −10% |", -0.10, -0.12, 45.0, 252, 10.0),
    }
    for mode, (line, stop, trail, decay, hold, k) in rows.items():
        p = get_preset(mode)
        assert line in d, mode
        assert p.stop_loss_pct == stop, mode
        assert p.trailing_stop_pct == trail, mode
        assert p.score_decay_exit == decay, mode
        assert p.max_hold_days == hold, mode
        assert ATR_TARGET_K[mode] == k, mode
    assert get_preset("nifty200_momentum").take_profit_pct == 0.20
    assert "+20% (8 × ATR" in d


def test_drawdown_ladder_matches_risk_v2():
    d = _doc()
    assert [lvl[0] for lvl in DRAWDOWN_LEVELS] == [
        -0.05, -0.08, -0.10, -0.15, -0.20]
    assert DRAWDOWN_LEVELS[0][2] == 0.75
    assert "−5% → new trades 75% size" in d
    assert "−20% → close everything" in d


def test_switches_that_are_on_say_on():
    c, d = Config(), _doc()
    assert c.danger_layer_enabled and "| Danger layer | On." in d
    assert c.rotation_auto_enabled and c.rotation_min_progress == 0.90
    assert "| Rotation | On." in d and "90%+" in d
    assert c.ocean_enabled and "| Ocean | On," in d
    assert c.intraday_refresh_minutes == 30 and "every 30 minutes" in d
