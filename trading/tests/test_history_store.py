"""History-store bulk download + persistence round-trip (stub broker)."""

import numpy as np
import pandas as pd
from datetime import date
from trading.history_store import (download_history, download_ticker_history,
                                   df_to_records, records_to_df)


class _StubBroker:
    def __init__(self):
        self.calls = 0
    def get_daily_candles(self, key, frm, to):
        self.calls += 1
        # serve 2 pages then dry up (simulate listing start)
        if self.calls > 2:
            return None
        idx = pd.bdate_range(frm, periods=250)
        px = np.linspace(100, 130, 250).astype("float32")
        return pd.DataFrame({"Open": px, "High": px*1.01, "Low": px*0.99,
                             "Close": px, "Volume": px*1000}, index=idx)


def test_download_ticker_paginates_and_stops():
    b = _StubBroker()
    df = download_ticker_history(b, "NSE_EQ|X", years=10, today=date(2026, 6, 1))
    assert df is not None and len(df) > 200
    assert df.index.is_monotonic_increasing


def test_bulk_download_saves_each():
    b = _StubBroker()
    saved = {}
    summ = download_history(b, {"A.NS": "NSE_EQ|A"}, years=3,
                            today=date(2026, 6, 1),
                            save_one=lambda t, d: saved.__setitem__(t, d))
    assert summ["tickers_ok"] == 1 and summ["rows"] > 0
    assert "A.NS" in saved


def test_records_roundtrip():
    idx = pd.bdate_range("2020-01-01", periods=50)
    px = np.linspace(100, 120, 50).astype("float32")
    df = pd.DataFrame({"Open": px, "High": px, "Low": px, "Close": px,
                       "Volume": px*10}, index=idx)
    doc = df_to_records("Z.NS", df)
    assert doc["_id"] == "Z.NS" and doc["n"] == 50 and doc["start"] == "2020-01-01"
    back = records_to_df(doc)
    assert len(back) == 50 and str(back["Close"].dtype) == "float32"
    assert records_to_df({"candles": []}) is None
