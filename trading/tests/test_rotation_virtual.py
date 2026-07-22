"""F6 rotation + F8 rotating buckets + virtual shadow-book (no network)."""

from trading.config import Config
from trading.paper import PaperPosition
from trading.rotation import rotation_suggestions
from trading.universes import rotating_bucket
from trading.virtual import record_virtual, mark_virtual
from datetime import datetime


def _pos(ticker, entry, target, qty=100):
    return PaperPosition(ticker=ticker, qty=qty, entry_price=entry,
                         entry_date=datetime(2026, 6, 1), sector="IT",
                         peak_price=entry, entry_score=70.0, reasoning="t",
                         target_price=target)


def test_rotation_suggests_near_target_and_funds_top_pick():
    cfg = Config()
    positions = {"A.NS": _pos("A.NS", 100.0, 120.0),   # at 118 -> 90% progress
                 "B.NS": _pos("B.NS", 100.0, 120.0)}   # at 105 -> 25%
    prices = {"A.NS": 118.0, "B.NS": 105.0}
    watch = [{"ticker": "NEW.NS", "score": 88}, {"ticker": "MEH.NS", "score": 60}]
    out = rotation_suggestions(positions, prices, watch, cfg)
    assert len(out) == 1 and out[0]["ticker"] == "A.NS"
    assert out[0]["fund_ticker"] == "NEW.NS"      # highest-score fresh pick
    assert out[0]["progress_pct"] >= 90


def test_rotating_bucket_covers_universe():
    uni = [f"S{i}.NS" for i in range(120)]
    seen = set()
    for day in range(3):                           # 120/50 -> 3 buckets
        seen |= set(rotating_bucket(uni, 50, day))
    assert seen == set(uni)                        # full coverage in one cycle
    assert rotating_bucket(uni, 50, 0) == uni[:50]
    assert rotating_bucket(uni, 50, 3) == uni[:50]  # cycle repeats
    assert rotating_bucket([], 50, 0) == []
    assert rotating_bucket(uni, 999, 0) == uni      # size>=n returns all


def test_virtual_book_marks_and_freezes():
    book = {}
    record_virtual(book, "X.NS", 100.0, 80.0, "IT", "candle_wait_no_pattern",
                   0.30, -0.12, "2026-07-12T09:20:00")
    assert book["X.NS"]["status"] == "open"
    assert book["X.NS"]["target_px"] == 130.0 and book["X.NS"]["stop_px"] == 88.0
    # duplicate record while open is ignored
    record_virtual(book, "X.NS", 200.0, 90.0, "IT", "x", 0.30, -0.12, "later")
    assert book["X.NS"]["entry_px"] == 100.0
    # mark up past target -> freezes as 'target'
    summ = mark_virtual(book, {"X.NS": 131.0}, "2026-07-13T09:20:00")
    assert book["X.NS"]["status"] == "target"
    assert summ["target_hits"] == 1
    # mark of a stop
    book2 = {}
    record_virtual(book2, "Y.NS", 100.0, 80.0, "IT", "reserve", 0.30, -0.12, "t")
    mark_virtual(book2, {"Y.NS": 85.0}, "t2")
    assert book2["Y.NS"]["status"] == "stop"
