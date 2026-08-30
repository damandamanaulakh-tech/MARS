"""Weekly top-N-by-liquidity universe (owner order 2026-08-30).

"No automation for weekly new batch of stocks / batch size also reduce to
500 from 2300+", and from the options card: "Top 500 by liquidity/turnover,
weekly, replaces the 50". This retires the hand-typed 50-name list as the
SOURCE of the universe — the list going stale by hand was the owner's own
complaint ("system still use old stocks").

Pinned here: the ranking is rupee TURNOVER, not share count (a share-count
ranking puts penny stocks on top); the median is used so one block-deal day
cannot promote a thin name; names below the live Rs 1 crore entry floor are
dropped rather than carried; too little history is "not judged", never
"illiquid"; held names always ride along; and a short list reports HOW short
instead of passing for a full 500.
"""
from __future__ import annotations

from trading.universe_select import (DEFAULT_TARGET, median_turnover,
                                     select_liquid_universe)


def _doc(ticker, close, vol, bars=20):
    return {"ticker": ticker,
            "candles": [["2026-08-01", close, close, close, close, vol]
                        for _ in range(bars)]}


def test_target_is_the_owners_500():
    assert DEFAULT_TARGET == 500


def test_ranking_is_by_rupee_turnover_not_share_count():
    """THE point of the measure. A penny stock trading ten million shares is
    Rs 3 crore; a Rs 4,000 stock trading fifty thousand is Rs 20 crore."""
    penny = _doc("PENNY.NS", close=3.0, vol=10_000_000)      # Rs 3.0 Cr
    quality = _doc("QUALITY.NS", close=4000.0, vol=50_000)   # Rs 20.0 Cr
    res = select_liquid_universe([penny, quality], target=2)
    assert [r["ticker"] for r in res["ranked"]] == ["QUALITY.NS", "PENNY.NS"]


def test_median_ignores_a_single_block_deal_day():
    """One huge day must not lift a normally-thin name into the universe."""
    d = _doc("THIN.NS", close=100.0, vol=1_000)              # Rs 1 lakh/day
    d["candles"][-1] = ["2026-08-20", 100.0, 100, 100, 100.0, 100_000_000]
    assert median_turnover(d["candles"]) == 1_00_000.0
    assert select_liquid_universe([d], target=10)["tickers"] == []


def test_names_below_the_live_entry_floor_are_dropped():
    """Below Rs 1 crore a name could never pass risk.passes_liquidity, so
    carrying it in the universe would only waste a scan slot."""
    thinly = _doc("SMALL.NS", close=10.0, vol=50_000)        # Rs 5 lakh
    big = _doc("BIG.NS", close=500.0, vol=1_000_000)         # Rs 50 Cr
    res = select_liquid_universe([thinly, big], target=10)
    assert res["tickers"] == ["BIG.NS"]
    assert res["skipped_illiquid"] == 1


def test_too_little_history_is_not_judged_rather_than_illiquid():
    """"No data" and "known to be thin" are different facts."""
    assert median_turnover([["d", 1, 1, 1, 100.0, 5_000]] * 3) is None
    res = select_liquid_universe([{"ticker": "NEW.NS",
                                   "candles": [["d", 1, 1, 1, 100.0, 5_000]] * 3}],
                                 target=10)
    assert res["skipped_thin"] == 1 and res["skipped_illiquid"] == 0


def test_held_names_always_ride_along_however_illiquid():
    """A position must never fall out of its own scan universe."""
    big = _doc("BIG.NS", close=500.0, vol=1_000_000)
    dead = _doc("DEAD.NS", close=5.0, vol=100)
    res = select_liquid_universe([big, dead], target=10,
                                 always_include=["DEAD.NS"])
    assert "DEAD.NS" in res["tickers"] and "BIG.NS" in res["tickers"]
    assert res["held_added"] == ["DEAD.NS"]


def test_the_blocklist_still_wins_over_a_held_name():
    dead = _doc("YESBANK.NS", close=20.0, vol=10_000_000)
    res = select_liquid_universe([dead], target=10,
                                 blocklist=["YESBANK.NS"],
                                 always_include=["YESBANK.NS"])
    assert res["tickers"] == []


def test_a_short_list_reports_how_short_it_is():
    """Never pass a short list off as a full 500 — the store is still
    filling, so this is the honest state for now."""
    docs = [_doc(f"N{i}.NS", close=500.0, vol=1_000_000) for i in range(3)]
    res = select_liquid_universe(docs, target=500)
    assert len(res["tickers"]) == 3
    assert res["short_by"] == 497
    assert res["judged"] == 3 and res["sample"] == 3


def test_top_n_is_respected_and_ordered():
    docs = [_doc(f"N{i}.NS", close=100.0, vol=1_000_000 * (i + 1))
            for i in range(6)]
    res = select_liquid_universe(docs, target=3)
    assert [r["ticker"] for r in res["ranked"]] == ["N5.NS", "N4.NS", "N3.NS"]
    assert res["short_by"] == 0


def test_empty_or_malformed_input_is_quiet():
    assert select_liquid_universe([], target=5)["tickers"] == []
    assert select_liquid_universe([{}, {"ticker": ""}], target=5)["tickers"] == []
    assert median_turnover(None) is None
    assert median_turnover([["bad"], [1, 2]]) is None
