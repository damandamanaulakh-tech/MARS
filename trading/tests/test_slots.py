"""Slot sizing (owner order 2026-08-15: "wire the slot sizing too").

The one thing that matters here: the number on the screen must be the number
`risk.can_open` will actually enforce — same deployed-at-entry convention,
same reserve, same per-trade notional, same position ceiling. A display that
promises a slot the gate refuses is worse than no display.
"""
from __future__ import annotations

from pathlib import Path

from trading.config import Config
from trading.paper import Portfolio
from trading.slots import slot_view


def _book(cash: float, n_positions: int = 0, entry: float = 100.0,
          qty: int = 1250, **cfg_kw) -> tuple:
    cfg = Config(state_dir="/tmp/sb_slots_test", **cfg_kw)
    p = Portfolio.fresh(cfg)
    p.cash = cash
    for i in range(n_positions):
        from datetime import date

        from trading.paper import PaperPosition
        t = f"HELD{i}.NS"
        p.positions[t] = PaperPosition(
            ticker=t, qty=qty, entry_price=entry, entry_date=date(2026, 8, 1),
            sector="Test", peak_price=entry, entry_score=70.0,
            reasoning="fixture")
    return p, cfg


def test_the_owners_own_arithmetic_50l_by_125k_is_40():
    """Their words: '50 lack ... 1.25 lac each' → 40 slots on a fresh book."""
    p, cfg = _book(cash=5_000_000.0)
    assert cfg.position_notional() == 125_000.0
    s = slot_view(p, cfg)
    assert s.free == 40 and s.limiting == "cash"


def test_at_60_70_percent_deployed_only_12_16_remain():
    """The exact scenario they described: 60-70% used leaves 12-16 slots."""
    for used_frac, expect in ((0.60, 16), (0.70, 12)):
        deployed = 5_000_000.0 * used_frac
        n = int(deployed // 125_000)          # positions of 1.25L each
        p, cfg = _book(cash=5_000_000.0 - deployed, n_positions=n,
                       entry=100.0, qty=1250)
        s = slot_view(p, cfg)
        assert s.free == expect, (used_frac, s.free)


def test_regime_reserve_shrinks_the_slots_like_the_gate_does():
    """risk.can_open caps deployment at base*(1-reserve); the display must
    use the same line, or it promises slots the gate refuses."""
    p, cfg = _book(cash=5_000_000.0)
    cfg.cash_reserve_pct = 0.40                # panic ladder
    s = slot_view(p, cfg)
    assert s.free == 24                        # 3L deployable / 1.25L
    assert s.deploy_cap == 3_000_000.0


def test_position_ceiling_binds_when_cash_does_not():
    # Ceiling raised 80 -> 222 on the owner's order 2026-09-10, so the book
    # has to be that much fuller before the ceiling is what binds. Derived
    # from cfg, not hardcoded: a future ceiling change is a config decision,
    # not a test failure.
    from trading.config import Config
    n = Config(state_dir="/tmp/sb_slots_test").max_positions - 2
    p, cfg = _book(cash=50_000_000.0, n_positions=n)
    s = slot_view(p, cfg)
    assert s.free == 2 and s.limiting == "max_positions"


def test_full_book_reads_zero_and_names_the_binding_limit():
    p, cfg = _book(cash=40_000.0)              # less than one trade
    s = slot_view(p, cfg)
    assert s.free == 0 and s.limiting == "cash"


def test_deployed_counts_entry_prices_exactly_like_risk_can_open():
    """The convention matters: can_open sums qty*entry_price. A display on
    live marks would drift from the gate every day the book moves."""
    p, cfg = _book(cash=1_000_000.0, n_positions=2, entry=200.0, qty=625)
    s = slot_view(p, cfg)
    assert s.deployed == 2 * 625 * 200.0


def test_display_only_the_money_path_never_imports_slots():
    root = Path(__file__).resolve().parents[1]
    for name in ("engine.py", "risk.py", "orders.py", "danger.py"):
        src = (root / name).read_text()
        assert "from .slots" not in src and "import slots" not in src, (
            f"{name} must not import slots — slot sizing is the owner's "
            "display funnel; risk.can_open stays the sole authority")
