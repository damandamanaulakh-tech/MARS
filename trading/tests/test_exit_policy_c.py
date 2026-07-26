"""Policy C (2026-07-16): exits ON by default + per-mode stop/target retag."""

from datetime import datetime

from trading.config import Config
from trading.exits import check_exit, retag_positions_per_mode
from trading.paper import PaperPosition, Portfolio

NOW = datetime(2026, 7, 16, 10, 0)


def _pos(ticker, entry, mode="", entry_date=datetime(2026, 6, 17),
         stop=0.0, target=0.0):
    return PaperPosition(
        ticker=ticker, qty=10, entry_price=entry, entry_date=entry_date,
        sector="X", peak_price=entry, entry_score=70.0, reasoning="t",
        mode=mode, stop_price=stop, target_price=target)


def _book(*positions):
    p = Portfolio(cfg=Config(), cash=0.0)
    for pos in positions:
        p.positions[pos.ticker] = pos
    return p


def test_exits_are_on_by_default():
    assert Config().disable_exits is False


def test_take_profit_fires_by_default_config():
    cfg = Config()
    cfg.mode = "nifty200_momentum"
    cfg.apply_mode_preset()
    pos = _pos("W.NS", 100.0, mode="nifty200_momentum",
               stop=88.0, target=130.0)
    should, why = check_exit(pos, 131.0, 70.0, cfg, NOW)
    assert should and why == "TAKE_PROFIT"


def test_manual_blank_tag_gets_momentum_numbers():
    """CHOLAFIN case: Manual position, old tight numbers -> momentum table."""
    p = _book(_pos("CHOLAFIN.NS", 1688.14, mode="",
                   stop=1662.82, target=1730.35))
    assert retag_positions_per_mode(p, NOW) == 1
    pos = p.positions["CHOLAFIN.NS"]
    assert pos.stop_price == round(1688.14 * 0.95, 2)      # owner -5% (2026-07-26)
    assert pos.target_price == round(1688.14 * 1.12, 2)    # user's +12 (2026-07-21)
    entry = [e for e in p.decision_journal if e["action"] == "RETAG_EXITS"]
    assert entry and entry[0]["mode_used"] == "nifty200_momentum"


def test_stale_intraday_tag_gets_momentum_numbers():
    """CGCL case: intraday-snapshotted stops on a weeks-old holding."""
    p = _book(_pos("CGCL.NS", 219.03, mode="intraday",
                   entry_date=datetime(2026, 6, 17),
                   stop=215.74, target=224.51))
    assert retag_positions_per_mode(p, NOW) == 1
    pos = p.positions["CGCL.NS"]
    assert pos.stop_price == round(219.03 * 0.95, 2)       # owner -5% (2026-07-26)
    assert pos.target_price == round(219.03 * 1.12, 2)     # user's +12 (2026-07-21)


def test_same_day_intraday_keeps_intraday_numbers():
    p = _book(_pos("DAY.NS", 100.0, mode="intraday",
                   entry_date=datetime(2026, 7, 16, 9, 30)))
    retag_positions_per_mode(p, NOW)
    pos = p.positions["DAY.NS"]
    assert pos.stop_price == 98.5 and pos.target_price == 102.5


def test_swing_tag_keeps_swing_numbers():
    p = _book(_pos("SW.NS", 200.0, mode="swing"))
    retag_positions_per_mode(p, NOW)
    pos = p.positions["SW.NS"]
    assert pos.stop_price == 190.0 and pos.target_price == 220.0


def test_retag_is_idempotent():
    p = _book(_pos("CHOLAFIN.NS", 1688.14, mode=""),
              _pos("SW.NS", 200.0, mode="swing"))
    first = retag_positions_per_mode(p, NOW)
    assert first == 2
    assert retag_positions_per_mode(p, NOW) == 0
    retags = [e for e in p.decision_journal if e["action"] == "RETAG_EXITS"]
    assert len(retags) == 2
