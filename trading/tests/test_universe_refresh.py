"""Daily NSE universe auto-refresh (user ask 2026-07-21). No network:
parse/merge/write logic is exercised on fixture text."""

import csv
import json
import os

import pytest

from trading.universe_refresh import (
    parse_eq_rows,
    read_existing,
    refresh_universe,
    refreshed_today,
)

MASTER = json.dumps([
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "TCS",
     "name": "TATA CONSULTANCY", "isin": "INE467B01029",
     "instrument_key": "NSE_EQ|INE467B01029"},
    {"segment": "NSE_EQ", "instrument_type": "SG", "trading_symbol": "749RJ35",
     "name": "SDL RJ", "isin": "IN2920250163",
     "instrument_key": "NSE_EQ|IN2920250163"},          # state bond — out
    {"segment": "NSE_EQ", "instrument_type": "BE", "trading_symbol": "SUSPCO",
     "name": "TRADE-TO-TRADE CO", "isin": "INE000X01000",
     "instrument_key": "NSE_EQ|INE000X01000"},          # BE series — out
    {"segment": "NSE_FO", "instrument_type": "EQ", "trading_symbol": "BANKNIFTY",
     "name": "DERIV", "isin": "", "instrument_key": "NSE_FO|123"},  # not cash
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "ABB",
     "name": "ABB INDIA", "isin": "INE117A01022",
     "instrument_key": "NSE_EQ|INE117A01022"},
])


def test_parse_filters_to_eq_series_only():
    rows = parse_eq_rows(MASTER)
    assert [r["ticker"] for r in rows] == ["ABB.NS", "TCS.NS"]  # sorted
    assert rows[1]["isin"] == "INE467B01029"
    assert rows[0]["instrument_key"] == "NSE_EQ|INE117A01022"


def _write_current(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "trading_symbol", "name",
                                           "isin", "instrument_key", "mis_allowed"])
        w.writeheader()
        w.writerows(rows)


def test_refresh_merges_mis_and_diffs(tmp_path, monkeypatch):
    path = str(tmp_path / "universe.csv")
    _write_current(path, [
        {"ticker": "TCS.NS", "trading_symbol": "TCS", "name": "TCS",
         "isin": "INE467B01029", "instrument_key": "NSE_EQ|INE467B01029",
         "mis_allowed": "1"},
        {"ticker": "GONECO.NS", "trading_symbol": "GONECO", "name": "DELISTED",
         "isin": "INE0GONE", "instrument_key": "NSE_EQ|INE0GONE",
         "mis_allowed": "1"},
    ])
    monkeypatch.setattr("trading.universe_refresh.fetch_master_text",
                        lambda url=None, timeout=0: MASTER)
    monkeypatch.setattr("trading.universe_refresh.MIN_ROWS", 1)
    s = refresh_universe(path=path)

    assert s == {"total": 2, "added": 1, "removed": 1,
                 "added_names": ["ABB.NS"], "removed_names": ["GONECO.NS"],
                 "was": 2}
    written = read_existing(path)
    assert written["TCS.NS"]["mis_allowed"] == "1"   # carried over
    assert written["ABB.NS"]["mis_allowed"] == "0"   # new — no intraday unvetted
    assert "GONECO.NS" not in written                # delisted drops


def test_refresh_refuses_tiny_master(tmp_path, monkeypatch):
    path = str(tmp_path / "universe.csv")
    _write_current(path, [{"ticker": "TCS.NS", "trading_symbol": "TCS",
                           "name": "TCS", "isin": "X",
                           "instrument_key": "K", "mis_allowed": "1"}])
    monkeypatch.setattr("trading.universe_refresh.fetch_master_text",
                        lambda url=None, timeout=0: MASTER)
    with pytest.raises(ValueError, match="only 2 equities"):
        refresh_universe(path=path)
    # old file untouched on failure
    assert read_existing(path)["TCS.NS"]["mis_allowed"] == "1"


def test_refreshed_today_tracks_mtime(tmp_path):
    path = str(tmp_path / "universe.csv")
    assert refreshed_today(path) is False          # missing file
    _write_current(path, [])
    assert refreshed_today(path) is True           # just written
    os.utime(path, (1_500_000_000, 1_500_000_000))  # 2017
    assert refreshed_today(path) is False
