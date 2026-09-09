"""A3 breadth gate (owner: "wire it", 2026-07-27) + intraday removed from
the daily rotation. Evidence: docs/breadth_gate_results.json."""

from datetime import datetime

import pandas as pd

from trading.config import Config
from trading.regime import compute_breadth


def _hist(n_above: int, n_below: int, bars: int = 260) -> dict:
    """Panel where n_above tickers trend up (close > 200DMA) and n_below
    trend down."""
    idx = pd.bdate_range("2024-06-01", periods=bars)
    out = {}
    for i in range(n_above):
        px = pd.Series(range(100, 100 + bars), index=idx, dtype=float)
        out[f"UP{i}.NS"] = pd.DataFrame(
            {"Open": px, "High": px, "Low": px, "Close": px, "Volume": 1e6})
    for i in range(n_below):
        px = pd.Series(range(100 + bars, 100, -1), index=idx, dtype=float)
        out[f"DN{i}.NS"] = pd.DataFrame(
            {"Open": px, "High": px, "Low": px, "Close": px, "Volume": 1e6})
    return out


# ---- compute_breadth ----

def test_breadth_counts_above_own_200dma():
    breadth, sample = compute_breadth(_hist(30, 10))
    assert sample == 40
    assert abs(breadth - 0.75) < 1e-9


def test_breadth_excludes_short_history_tickers():
    hist = _hist(5, 5)
    hist["NEW.NS"] = _hist(1, 0, bars=50)["UP0.NS"]     # < 200 bars
    breadth, sample = compute_breadth(hist)
    assert sample == 10                                  # NEW.NS not counted


def test_breadth_empty_panel_returns_none():
    assert compute_breadth({}) == (None, 0)


# ---- the gate inside engine.scan (drive the exact block) ----

def _gate(picks, history, cfg, journal):
    """Drives the REAL engine method — no replicated logic."""
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg.state_dir = "/tmp/breadth_gate_tests"
    eng = TradingEngine(cfg=cfg, portfolio=Portfolio.fresh(cfg))
    eng.portfolio.decision_journal = journal
    if eng._breadth_gate_blocks(history, datetime(2026, 7, 27),
                                n_picks=len(picks)):
        return []
    return picks


def test_gate_defaults_and_knobs_exist():
    cfg = Config()
    assert cfg.breadth_gate_enabled is True
    assert cfg.breadth_block_low == 0.40          # re-cut 2026-09-09
    assert cfg.breadth_block_high == 0.65
    assert cfg.breadth_min_sample == 30


def test_the_superseded_single_threshold_knob_is_gone():
    """breadth_min_pct described a rule the measurement retired. Leaving the
    field would invite someone to 'tune' a knob nothing reads."""
    import inspect
    from trading import config as _c
    src = "\n".join(ln for ln in inspect.getsource(_c).splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "breadth_min_pct" not in src
    assert not hasattr(Config(), "breadth_min_pct")


def test_dead_band_blocks_and_journals():
    """20 up / 20 down = 50% breadth, inside the measured 40-65% dead band."""
    cfg = Config(mode="nifty200_momentum")
    journal: list = []
    picks = _gate([{"ticker": "X.NS"}], _hist(20, 20), cfg, journal)
    assert picks == []
    assert journal and journal[-1]["action"] == "BREADTH_BLOCKED"
    assert "dead band" in journal[-1]["why"]


def test_washed_out_tape_is_now_BUYABLE():
    """THE BEHAVIOUR CHANGE (owner order 2026-09-09 "fix it"). 10 up / 30
    down = 25%: the old rule blocked this, and measured on the engine's own
    picks it was one of the best states to buy. Below the band, entries go."""
    cfg = Config(mode="nifty200_momentum")
    journal: list = []
    picks = _gate([{"ticker": "X.NS"}], _hist(10, 30), cfg, journal)
    assert picks == [{"ticker": "X.NS"}]
    assert not [e for e in journal if e.get("action") == "BREADTH_BLOCKED"]


def test_strong_breadth_allows():
    cfg = Config(mode="nifty200_momentum")
    picks = _gate([{"ticker": "X.NS"}], _hist(30, 10), cfg, [])
    assert picks == [{"ticker": "X.NS"}]


def test_the_band_is_the_measured_one_and_the_engine_reads_both_edges():
    """A one-sided read would silently restore the retired rule."""
    import inspect
    from trading.engine import TradingEngine
    src = "\n".join(ln for ln in
                    inspect.getsource(TradingEngine._breadth_gate_blocks).splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "breadth_block_low" in src and "breadth_block_high" in src
    assert "lo <= breadth < hi" in src


def test_thin_sample_fails_open():
    """Fewer than breadth_min_sample tickers with 200d history = no block."""
    cfg = Config(mode="nifty200_momentum")
    picks = _gate([{"ticker": "X.NS"}], _hist(2, 8), cfg, [])   # sample 10 < 30
    assert picks == [{"ticker": "X.NS"}]


def test_gate_is_momentum_mode_only():
    cfg = Config(mode="swing")
    picks = _gate([{"ticker": "X.NS"}], _hist(10, 30), cfg, [])
    assert picks == [{"ticker": "X.NS"}]


def test_owner_toggle_disables():
    cfg = Config(mode="nifty200_momentum", breadth_gate_enabled=False)
    picks = _gate([{"ticker": "X.NS"}], _hist(10, 30), cfg, [])
    assert picks == [{"ticker": "X.NS"}]


# ---- 17: intraday out of the morning rotation ----

def test_daily_rotation_runs_all_five_categories():
    """OWNER ORDER 2026-08-30: "make sure category — Intraday, short, swing &
    positional & N200 momentum execute the trades". Supersedes their
    2026-07-27 decision that had dropped intraday from the rotation.
    Momentum stays FIRST so the primary strategy gets first call on cash."""
    from web.scheduler import _DAILY_MODE_ORDER
    assert _DAILY_MODE_ORDER == ["nifty200_momentum", "intraday", "short",
                                 "swing", "positional"]
    assert _DAILY_MODE_ORDER[0] == "nifty200_momentum"

def test_vix_reentry_bypasses_the_breadth_gate():
    """Owner decision 2026-07-27: at the turn of a crash breadth is always
    still weak — the v4-tested VIX-crush entries must not be breadth-blocked
    (the +24% COVID rebound would have been). bypass=True = gate stands down."""
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = Config(mode="nifty200_momentum", state_dir="/tmp/breadth_gate_tests")
    eng = TradingEngine(cfg=cfg, portfolio=Portfolio.fresh(cfg))
    # 50% breadth: inside the measured dead band, so the gate blocks here
    # (25% would NOT block since the 2026-09-09 re-cut — a washed-out tape
    # is buyable, which is exactly the state a VIX-crush entry arrives in).
    weak = _hist(20, 20)
    assert eng._breadth_gate_blocks(weak, datetime(2026, 7, 27)) is True
    assert eng._breadth_gate_blocks(weak, datetime(2026, 7, 27),
                                    bypass=True) is False
    # bypass leaves no BREADTH_BLOCKED line — the buys go through visibly
    blocked = [e for e in eng.portfolio.decision_journal
               if e.get("action") == "BREADTH_BLOCKED"]
    assert len(blocked) == 1                 # only the non-bypassed call
