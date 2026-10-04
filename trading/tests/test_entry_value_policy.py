"""F-007 and F-009 from the 2026-08-29 external review (owner: "fix f-007
and f-009 too").

F-007 — the three entry-value numbers lived in three fields and were never
stated together, so a reviewer read them as a contradiction: the owner's
order was "roughly 1-1.25 for each trade", yet the floor is Rs 25,000 and
Ocean has its own ticket. They are separate owner decisions, not a
conflict. Pinned here so the policy and the code can never
drift apart, and so the owner's own manual keeps stating all three.

F-009 — risk_v2 carried an 18/12/6% volatility-tier sizing scheme that was
never wired (engine.py imported it and never called it; the import went in
the 2026-08-09 audit) but was documented and TESTED like a live control,
which is exactly how the reviewer read it. The code and its tests are gone;
this pins that they stay gone, and that the rest of risk_v2 — which really
does run in the money path — is untouched.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from trading import risk_v2
from trading.config import Config

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------- F-007

def test_the_three_entry_values_are_the_owners_numbers():
    cfg = Config()
    assert cfg.position_size_value == 125_000.0   # Everest, order 2026-07-30
    assert cfg.ocean_position_value == 168_750.0  # Everest +35%, 08-30
    # dust guard 2026-07-11 was Rs 25,000; owner order 2026-10-04 ("leave
    # the 1.5 minimum value no 25 k is needed") made it the Rs 1.5 lakh slab
    assert cfg.min_position_value == 150_000.0
    assert cfg.min_position_value == cfg.position_slab_value


def test_ocean_is_exactly_everest_plus_35_percent():
    """Owner order 2026-08-30 ("when ocean activate it will use more in of
    35%"), superseding the half size of 2026-08-26. If either number moves
    without the other, the config block and the brief stop being true."""
    cfg = Config()
    assert cfg.ocean_position_value == round(cfg.position_size_value * 1.35, 2)
    assert cfg.ocean_position_value > cfg.position_size_value


def test_the_floor_never_blocks_an_engines_own_ticket():
    """The floor (Rs 1.5 lakh since 2026-10-04) sits at the slab and below
    Ocean's ticket. The legacy fixed Rs 1,25,000 is below it, so the
    effective floor is capped at the trade's own size (risk.min_trade_value)
    — otherwise that Settings choice would silently refuse every trade."""
    from trading.risk import min_trade_value
    cfg = Config()
    assert cfg.min_position_value <= cfg.ocean_position_value
    assert min_trade_value(cfg, cfg.position_size_value) == cfg.position_size_value
    assert min_trade_value(cfg, 25_00_000.0) == cfg.min_position_value


def test_config_states_the_whole_policy_in_one_place():
    """The reviewer's actual complaint: no single place said all three."""
    src = (ROOT / "trading" / "config.py").read_text()
    block = src.split("ENTRY VALUE POLICY")[1][:2000]
    for token in ("1,25,000", "1,68,750", "1,50,000"):
        assert token in block, token
    assert "Ocean" in block and "Everest" in block


def test_the_owners_manual_states_all_three_sizes():
    """APP_BRIEF is the owner's manual. Before this fix it named Rs 1.25
    lakh and never mentioned Ocean's own ticket size at all."""
    brief = (ROOT / "docs" / "APP_BRIEF.md").read_text()
    # 2026-10-02: Everest is the slab ladder (at least Rs 1,50,000) since
    # 2026-09-10; the old fixed Rs 1,25,000 is still named as the Settings
    # alternative.
    for token in ("1,50,000", "1,25,000", "1,68,750"):
        assert token in brief, token


# --------------------------------------------------------------------- F-009

DEAD_VOL_TIER_NAMES = ("TIER_THRESHOLDS", "vol_tier", "vol_adjusted_qty",
                       "annualised_vol_pct")


def test_the_never_wired_vol_tier_scheme_is_gone():
    for name in DEAD_VOL_TIER_NAMES:
        assert not hasattr(risk_v2, name), f"{name} came back"


def test_no_production_module_references_the_dead_names():
    """Production only — this file necessarily names them, and so may any
    future regression test."""
    for py in list((ROOT / "trading").rglob("*.py")) + \
            list((ROOT / "web").rglob("*.py")):
        if "tests" in py.parts:
            continue
        body = "\n".join(ln for ln in py.read_text().splitlines()
                         if not ln.lstrip().startswith("#"))
        for name in DEAD_VOL_TIER_NAMES:
            assert name not in body, f"{py.name} still references {name}"


def test_the_live_half_of_risk_v2_is_untouched():
    """Deleting the dead scheme must not take the real controls with it —
    these four run in the money path."""
    for name in ("DrawdownGovernor", "ConsecutiveLossTracker",
                 "dynamic_cash_reserve_pct", "correlation_gate_blocks",
                 "passes_liquidity"):
        assert hasattr(risk_v2, name), name
    assert inspect.isclass(risk_v2.DrawdownGovernor)
