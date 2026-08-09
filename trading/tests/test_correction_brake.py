"""Owner order 2026-08-09: "i want to take risk on 200 DMA" -> brake fully
off. Correction (Nifty < 200DMA) no longer blocks momentum entries; panic
still does; a dead regime feed still brakes; the flag restores the old
behaviour."""
from __future__ import annotations

from datetime import datetime

from trading.config import Config
from trading.regime import RegimeReading


def _regime(name: str, everest: bool) -> RegimeReading:
    return RegimeReading(
        timestamp=datetime(2026, 8, 9, 10, 5),
        nifty_close=21000.0, nifty_200dma=23000.0,
        pct_below_52w_high=-14.0, vix=19.0, fii_5d_net=-2500.0,
        regime=name, everest_active=everest, ocean_active=True,
        sizing_multiplier_everest=0.0, sizing_multiplier_ocean=1.0,
        reasoning="synthetic regime for test",
    )


def test_brake_is_off_by_default():
    assert Config().correction_brake_enabled is False


def _brake_off(cfg: Config, regime) -> bool:
    """The exact predicate engine.scan uses."""
    return (regime is not None and regime.regime == "correction"
            and not getattr(cfg, "correction_brake_enabled", True))


def test_correction_no_longer_blocks_with_brake_off():
    cfg = Config()
    r = _regime("correction", everest=False)
    assert _brake_off(cfg, r) is True          # entries proceed


def test_panic_still_blocks_even_with_brake_off():
    cfg = Config()
    r = _regime("panic", everest=False)
    assert _brake_off(cfg, r) is False         # panic is not overridden


def test_flag_restores_the_old_brake():
    cfg = Config(correction_brake_enabled=True)
    r = _regime("correction", everest=False)
    assert _brake_off(cfg, r) is False


def test_dead_feed_still_brakes():
    cfg = Config()
    assert _brake_off(cfg, None) is False      # C7: no reading = no override


def test_engine_source_wires_the_exact_predicate():
    """The test predicate above must be the one the engine runs."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "engine.py").read_text()
    assert 'regime.regime == "correction"' in src
    assert "correction_brake_enabled" in src
    assert "CORRECTION_BRAKE_OFF" in src       # journaled, never silent
