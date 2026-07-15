"""Tests for the Upstox instrument master cache."""

from unittest.mock import patch, MagicMock

import pytest

from trading.brokers import upstox_instruments as inst


SAMPLE_CSV = (
    b"instrument_key,exchange_token,tradingsymbol,name,last_price,expiry,"
    b"strike,tick_size,lot_size,instrument_type,option_type,exchange\n"
    b"NSE_EQ|INE002A01018,2885,RELIANCE,Reliance Industries Ltd,2475.5,,,0.05,1,EQ,,NSE\n"
    b"NSE_EQ|INE467B01029,11536,TCS,Tata Consultancy Services Ltd,3300.0,,,0.05,1,EQ,,NSE\n"
    b"NSE_FO|FUTSTK_RELIANCE_30JUN2026,9999,RELIANCE26JUNFUT,Reliance Futures,,,,,1,FUT,,NSE\n"
)


def _reset_module_cache():
    inst._LOOKUP = None
    inst._LOOKUP_BUILT_AT = 0.0


def test_build_lookup_parses_only_nse_equities():
    _reset_module_cache()
    out = inst._build_lookup(SAMPLE_CSV)
    assert out["RELIANCE.NS"] == "NSE_EQ|INE002A01018"
    assert out["TCS.NS"] == "NSE_EQ|INE467B01029"
    # Futures (NSE_FO) are excluded
    assert "RELIANCE26JUNFUT.NS" not in out
    assert len(out) == 2


def test_build_lookup_empty_csv():
    _reset_module_cache()
    out = inst._build_lookup(b"")
    assert out == {}


def test_build_lookup_handles_bad_csv():
    _reset_module_cache()
    out = inst._build_lookup(b"not,real,csv\xff\xff")
    # Should not raise; returns whatever it managed to parse (likely empty)
    assert isinstance(out, dict)


def test_to_instrument_key_returns_none_for_unknown():
    _reset_module_cache()
    with patch.object(inst, "get_lookup", return_value={"RELIANCE.NS": "NSE_EQ|X"}):
        assert inst.to_instrument_key("UNKNOWN.NS") is None
        assert inst.to_instrument_key("RELIANCE.NS") == "NSE_EQ|X"


def test_to_instrument_key_empty_input():
    assert inst.to_instrument_key("") is None
    assert inst.to_instrument_key(None) is None


def test_to_instrument_keys_batch_filters_unknown():
    with patch.object(
        inst, "get_lookup",
        return_value={"RELIANCE.NS": "K1", "TCS.NS": "K2"},
    ):
        out = inst.to_instrument_keys(["RELIANCE.NS", "TCS.NS", "UNKNOWN.NS"])
    assert out == {"RELIANCE.NS": "K1", "TCS.NS": "K2"}


def test_get_lookup_uses_memory_cache_when_fresh():
    _reset_module_cache()
    import time
    inst._LOOKUP = {"CACHED.NS": "X"}
    inst._LOOKUP_BUILT_AT = time.time()
    # Even if download function exists, it should not be called
    with patch.object(inst, "_ensure_cached") as mock_dl:
        out = inst.get_lookup()
    mock_dl.assert_not_called()
    assert out == {"CACHED.NS": "X"}


def test_get_lookup_returns_empty_when_download_fails():
    _reset_module_cache()
    with patch.object(inst, "_ensure_cached", return_value=None):
        out = inst.get_lookup(force_refresh=True)
    assert out == {}


def test_get_lookup_builds_from_downloaded_csv():
    _reset_module_cache()
    with patch.object(inst, "_ensure_cached", return_value=SAMPLE_CSV):
        out = inst.get_lookup(force_refresh=True)
    assert out["RELIANCE.NS"] == "NSE_EQ|INE002A01018"
    assert out["TCS.NS"] == "NSE_EQ|INE467B01029"


def test_status_reports_lookup_size():
    inst._LOOKUP = {"A": "B", "C": "D"}
    s = inst.status()
    assert s["lookup_size"] == 2
