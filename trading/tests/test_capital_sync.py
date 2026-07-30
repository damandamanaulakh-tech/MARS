"""Owner order 2026-07-30: Rs 50L capital / 80 positions / 1.25% per trade,
and Starting Capital changes must really move the book's cash (journaled) —
raising it in Settings used to be a silent no-op on a live book."""

from trading.config import Config
from trading.paper import Portfolio


def _p(tmp_path, capital=50_00_000.0):
    cfg = Config(state_dir=str(tmp_path), starting_capital=capital)
    return cfg, Portfolio.fresh(cfg)


def test_new_book_spec_is_50l_80_at_1_25pct():
    cfg = Config()
    assert cfg.starting_capital == 50_00_000.0
    assert cfg.max_positions == 80
    assert abs(cfg.max_position_pct - 0.0125) < 1e-12
    # guardrail sanity: full book's risk-at-stop stays far under the heat cap
    assert 80 * 0.0125 * abs(cfg.stop_loss_pct) < cfg.max_portfolio_heat_pct


def test_fresh_book_carries_capital_base(tmp_path):
    cfg, p = _p(tmp_path)
    assert p.cash == 50_00_000.0 and p.capital_base == 50_00_000.0
    assert p.sync_capital(cfg) is None          # already in sync — no-op


def test_raising_capital_adds_cash_and_journals(tmp_path):
    cfg, p = _p(tmp_path, capital=25_00_000.0)
    p.cash = 24_79_000.0                        # lifetime P&L stays real
    cfg.starting_capital = 50_00_000.0
    note = p.sync_capital(cfg)
    assert note and "2,500,000" in note and "5,000,000" in note
    assert p.cash == 24_79_000.0 + 25_00_000.0  # delta added, loss NOT erased
    assert p.capital_base == 50_00_000.0
    assert p.decision_journal[-1]["action"] == "CAPITAL_ADJUST"
    assert p.sync_capital(cfg) is None          # idempotent


def test_lowering_capital_removes_cash(tmp_path):
    cfg, p = _p(tmp_path)
    cfg.starting_capital = 40_00_000.0
    assert p.sync_capital(cfg)
    assert p.cash == 40_00_000.0


def test_withdrawal_below_free_cash_is_refused_and_journaled(tmp_path):
    cfg, p = _p(tmp_path)
    p.cash = 5_00_000.0                         # rest deployed in positions
    cfg.starting_capital = 10_00_000.0          # wants 40L out — impossible
    assert p.sync_capital(cfg) is None
    assert p.cash == 5_00_000.0                 # untouched
    assert p.capital_base == 50_00_000.0        # base kept -> retry later works
    assert p.decision_journal[-1]["action"] == "CAPITAL_ADJUST_BLOCKED"


def test_legacy_book_loads_with_25l_base(tmp_path):
    """A saved book from before capital_base must sync FROM 25L (its real
    start), not silently adopt the new default."""
    cfg, p = _p(tmp_path, capital=25_00_000.0)
    p.cash = 24_79_000.0
    p.save()
    import json
    import os
    path = os.path.join(str(tmp_path), "portfolio.json")
    data = json.load(open(path))
    data.pop("capital_base")                     # simulate the legacy file
    json.dump(data, open(path, "w"))
    cfg2 = Config(state_dir=str(tmp_path), starting_capital=50_00_000.0)
    p2 = Portfolio.load_or_fresh(cfg2)
    assert p2.capital_base == 25_00_000.0
    assert p2.sync_capital(cfg2)
    assert p2.cash == 24_79_000.0 + 25_00_000.0


def test_reset_resets_base_too(tmp_path):
    cfg, p = _p(tmp_path)
    cfg.starting_capital = 60_00_000.0
    p.reset()
    assert p.cash == 60_00_000.0 and p.capital_base == 60_00_000.0
