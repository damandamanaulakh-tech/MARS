"""The backtest button must never bulk-download (owner report 2026-07-27:
'back test failed again, still hitting 512 MB on Render'). The panel comes
from the Mongo store + the repo zip + the committed index CSV — zero network.
"""

from unittest.mock import patch

import pandas as pd

from web.routes.backtest_routes import _preloaded_history


def test_panel_loads_from_repo_zip_and_index_csv_without_network():
    """Mongo offline -> everything still comes from committed files."""
    with patch("web.db.is_connected", return_value=False):
        hist = _preloaded_history(["RELIANCE.NS", "TCS.NS", "NOSUCH.NS"],
                                  "^NSEI", "2024-01-01", "2024-12-31")
    assert "RELIANCE.NS" in hist and "TCS.NS" in hist
    assert "NOSUCH.NS" not in hist            # skipped, never downloaded
    assert "^NSEI" in hist                    # benchmark from index CSV
    df = hist["RELIANCE.NS"]
    assert str(df["Close"].dtype) == "float32"  # 512MB-safe dtype
    # sliced to window + ~420d lookback, not 12 years
    assert df.index.min() >= pd.Timestamp("2022-11-01")
    assert df.index.max() <= pd.Timestamp("2025-01-05")


def test_mongo_store_preferred_over_zip():
    idx = pd.bdate_range("2023-06-01", periods=400)
    doc = {"_id": "RELIANCE.NS", "ticker": "RELIANCE.NS",
           "candles": [[d.strftime("%Y-%m-%d"), 1.0, 1.0, 1.0, 999.0, 1e6]
                       for d in idx],
           "n": len(idx), "start": "2023-06-01", "end": "2024-12-12"}
    with patch("web.db.is_connected", return_value=True), \
         patch("web.db.load_history_doc",
               side_effect=lambda t: doc if t == "RELIANCE.NS" else None):
        hist = _preloaded_history(["RELIANCE.NS"], "^NSEI",
                                  "2024-01-01", "2024-12-31")
    assert float(hist["RELIANCE.NS"]["Close"].iloc[-1]) == 999.0  # Mongo copy won


def test_backtest_run_passes_preloaded_history():
    """The route must never call run_backtest without a preloaded panel."""
    import inspect
    from web.routes import backtest_routes
    src = inspect.getsource(backtest_routes)
    assert "preloaded_history=_preloaded_history(" in src
