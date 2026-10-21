"""A book saved by a NEWER build must still load on an older one (2026-10-21).

Every new PaperPosition / ClosedTrade field (ca_applied, val_target, ...) used
to be fatal for a rollback: from_dict passed every stored key to the dataclass
constructor, so an unknown key raised TypeError and the whole book failed to
hydrate. Unknown keys are dropped now; known ones still load exactly.
"""
from datetime import datetime

from trading.paper import ClosedTrade, PaperPosition


def test_a_position_with_a_field_this_build_does_not_know_still_loads():
    d = {"ticker": "TCS.NS", "qty": 10, "entry_price": 3000.0,
         "entry_date": datetime(2026, 10, 1, 4, 0).isoformat(), "sector": "IT",
         "peak_price": 3000.0, "entry_score": 70.0, "reasoning": "fixture",
         "val_target": {"source": "k_atr"}, "ca_applied": ["TCS|2026-10-06"],
         "a_field_from_next_month": {"anything": 1}}
    pos = PaperPosition.from_dict(d)
    assert pos.ticker == "TCS.NS" and pos.qty == 10
    assert pos.val_target == {"source": "k_atr"} and pos.ca_applied == ["TCS|2026-10-06"]
    assert not hasattr(pos, "a_field_from_next_month")


def test_a_closed_trade_with_an_unknown_field_still_loads():
    d = {"ticker": "BLSE.NS", "qty": 100, "entry_date": datetime(2026, 10, 1, 4, 0).isoformat(),
         "exit_date": datetime(2026, 10, 7, 4, 0).isoformat(), "entry_price": 320.0,
         "exit_price": 158.0, "gross_pnl": -16200.0, "costs": 50.0, "net_pnl": -16250.0,
         "reason": "STOP_LOSS", "sector": "IT", "entry_score": 70.0, "reasoning": "fixture",
         "future_field": "x"}
    t = ClosedTrade.from_dict(d)
    assert t.ticker == "BLSE.NS" and t.net_pnl == -16250.0
    assert not hasattr(t, "future_field")
