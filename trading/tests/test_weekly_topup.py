"""Weekly OHLCV top-up (owner order 2026-07-27: "renewed every week").

The killer requirement: MERGE, never replace. A naive weekly re-save with
years=1 would truncate the 12-year research store to 12 months.
"""

from datetime import date

import pandas as pd

from trading.history_store import df_to_records, top_up_history


def _frame(start, periods, price=100.0):
    idx = pd.bdate_range(start, periods=periods)
    return pd.DataFrame({
        "Open": price, "High": price, "Low": price,
        "Close": price, "Volume": 1e6,
    }, index=idx)


class _Broker:
    def __init__(self, df):
        self._df = df
        self.calls = 0

    def get_daily_candles(self, key, start, end):
        self.calls += 1
        return self._df.loc[start:end]


def test_topup_merges_and_never_truncates_the_store():
    old = _frame("2014-06-30", 3000)              # ~12y stored
    store = {"X.NS": df_to_records("X.NS", old)}
    fresh = _frame("2026-07-01", 20, price=200.0)  # the new week(s)

    summ = top_up_history(
        _Broker(fresh), {"X.NS": "NSE_EQ|X"},
        load_doc=store.get, save_doc=lambda d: store.update({d["_id"]: d}),
        today=date(2026, 7, 27),
    )
    assert summ["ok"] == 1
    doc = store["X.NS"]
    assert doc["start"] == "2014-06-30"            # 12y history SURVIVES
    assert doc["end"] >= "2026-07-24"              # new candles appended
    assert doc["n"] > 3000                          # merged, not replaced


def test_topup_skips_already_fresh_tickers():
    old = _frame("2026-01-01", 146)                # ends 2026-07-23 (fresh: >= today-4d)
    store = {"Y.NS": df_to_records("Y.NS", old)}
    broker = _Broker(_frame("2026-07-01", 20))

    summ = top_up_history(
        broker, {"Y.NS": "NSE_EQ|Y"},
        load_doc=store.get, save_doc=lambda d: store.update({d["_id"]: d}),
        today=date(2026, 7, 27),
    )
    assert summ["skipped_fresh"] == 1
    assert broker.calls == 0                        # no wasted API call


def test_topup_overlap_days_deduped_keeping_newest():
    old = _frame("2026-06-01", 30, price=100.0)     # stale by weeks
    store = {"Z.NS": df_to_records("Z.NS", old)}
    fresh = _frame("2026-06-20", 25, price=150.0)   # overlaps the old tail

    top_up_history(
        _Broker(fresh), {"Z.NS": "NSE_EQ|Z"},
        load_doc=store.get, save_doc=lambda d: store.update({d["_id"]: d}),
        today=date(2026, 7, 27),
    )
    from trading.history_store import records_to_df
    df = records_to_df(store["Z.NS"])
    assert not df.index.duplicated().any()
    assert float(df.loc["2026-06-22", "Close"]) == 150.0   # newest wins


def test_topup_counts_failures_without_raising():
    class _DeadBroker:
        def get_daily_candles(self, *a):
            raise OSError("token expired")

    summ = top_up_history(
        _DeadBroker(), {"A.NS": "NSE_EQ|A"},
        load_doc=lambda t: None, save_doc=lambda d: None,
        today=date(2026, 7, 27),
    )
    assert summ["failed"] == 1 and summ["ok"] == 0
