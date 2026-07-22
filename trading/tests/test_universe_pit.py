"""Tests for the point-in-time universe fix (P1)."""
from datetime import date
import os, sys, tempfile, textwrap
from trading.universes_pit import PITUniverse, BIAS_TAG, CLEAN_TAG, _parse_nse_csv


def _snap(*tickers):
    return frozenset(t + ".NS" for t in tickers)


def test_asof_picks_most_recent_snapshot_on_or_before():
    u = PITUniverse({
        date(2019, 3, 29): _snap("YESBANK", "DHFL", "TCS"),
        date(2020, 3, 27): _snap("TCS", "RELIANCE"),
    })
    t2019, tag = u.asof(date(2019, 6, 1))
    assert "YESBANK.NS" in t2019 and "DHFL.NS" in t2019 and tag == CLEAN_TAG
    t2021, tag = u.asof(date(2021, 1, 1))
    assert "YESBANK.NS" not in t2021 and tag == CLEAN_TAG  # dropped member gone


def test_dates_before_first_snapshot_are_loudly_biased():
    u = PITUniverse({date(2020, 3, 27): _snap("TCS")},
                    static_fallback=["TCS.NS", "RELIANCE.NS"])
    tickers, tag = u.asof(date(2015, 1, 1))
    assert tag == BIAS_TAG                       # contamination is labelled
    assert tickers == ["TCS.NS", "RELIANCE.NS"]  # fallback still usable


def test_no_snapshots_at_all_never_pretends_clean():
    u = PITUniverse(static_fallback=["TCS.NS"])
    _, tag = u.asof(date(2024, 1, 1))
    assert tag == BIAS_TAG
    assert "NO SNAPSHOTS" in u.coverage()


def test_nse_csv_parser_handles_real_format():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8-sig") as fh:
        fh.write(textwrap.dedent("""\
            Company Name,Industry,Symbol,Series,ISIN Code
            Tata Consultancy Services Ltd.,IT,TCS,EQ,INE467B01029
            Reliance Industries Ltd.,Energy,RELIANCE,EQ,INE002A01018
        """))
        p = fh.name
    members = _parse_nse_csv(p)
    os.unlink(p)
    assert members == frozenset({"TCS.NS", "RELIANCE.NS"})


def test_dropped_stock_reenters_only_while_member():
    """The Yes Bank property: buyable in 2019 snapshots, gone after removal."""
    u = PITUniverse({
        date(2019, 9, 27): _snap("YESBANK", "TCS"),
        date(2020, 3, 27): _snap("TCS"),
    })
    a, _ = u.asof(date(2019, 12, 1)); b, _ = u.asof(date(2020, 6, 1))
    assert "YESBANK.NS" in a and "YESBANK.NS" not in b


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
