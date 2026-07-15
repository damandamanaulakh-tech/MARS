"""Policy tracker tests — mocked feedparser, no network."""

from datetime import datetime, timedelta
from time import mktime
from unittest.mock import MagicMock, patch

import pytest

from trading.signals.policy import (
    PolicyEvent, SECTOR_KEYWORDS, _keyword_match_sectors,
    active_sector_boosts, policy_boost_for_sector,
    scan_recent_policy_events,
)


def test_keyword_match_pli_pharma():
    sectors = _keyword_match_sectors("Pharma PLI scheme announced for API manufacturing")
    assert "Pharma" in sectors


def test_keyword_match_renewable_energy():
    sectors = _keyword_match_sectors("Government boosts solar capacity to 500 GW by 2030")
    assert "Energy" in sectors


def test_keyword_match_defence():
    sectors = _keyword_match_sectors("Defence ministry orders 100 Tejas Mk2 fighter jets")
    assert "Defence" in sectors


def test_keyword_no_match():
    assert _keyword_match_sectors("Cricket team announces new captain") == []


def test_scan_recent_returns_empty_when_feed_empty():
    with patch("trading.signals.policy._fetch_pib_feed", return_value=[]):
        assert scan_recent_policy_events() == []


def test_scan_recent_filters_by_sector_match():
    fake_entries = [
        {
            "title": "PM launches semiconductor PLI scheme phase 2",
            "summary": "New chip incentive ₹76,000 cr",
            "link": "https://pib.gov.in/x",
            "published_parsed": (datetime.utcnow() - timedelta(hours=2)).timetuple(),
        },
        {
            "title": "Cricket team selection",
            "summary": "Squad announced",
            "link": "x",
            "published_parsed": (datetime.utcnow() - timedelta(hours=1)).timetuple(),
        },
    ]
    with patch("trading.signals.policy._fetch_pib_feed", return_value=fake_entries):
        events = scan_recent_policy_events(lookback_hours=48)
    assert any(ev.ticker_sector == "IT" for ev in events)
    assert all(ev.ticker_sector != "Sports" for ev in events)


def test_scan_recent_drops_old_entries():
    old = (datetime.utcnow() - timedelta(days=10)).timetuple()
    fake_entries = [{
        "title": "Old PLI announcement",
        "summary": "",
        "link": "x",
        "published_parsed": old,
    }]
    with patch("trading.signals.policy._fetch_pib_feed", return_value=fake_entries):
        events = scan_recent_policy_events(lookback_hours=24)
    assert events == []


def test_active_sector_boosts_aggregates():
    now = datetime.utcnow()
    events = [
        PolicyEvent("IT", "h1", now, "src1", 0.8, 20.0),
        PolicyEvent("IT", "h2", now, "src2", 0.6, 15.0),  # would push to 35 -> clamps to 30
        PolicyEvent("Pharma", "h3", now, "src3", 0.5, 10.0),
    ]
    boosts = active_sector_boosts(today_events=events)
    assert boosts["IT"] == 30.0   # clamped
    assert boosts["Pharma"] == 10.0


def test_active_sector_boosts_drops_stale():
    old = datetime.utcnow() - timedelta(days=60)
    events = [PolicyEvent("IT", "h", old, "src", 0.8, 20.0)]
    boosts = active_sector_boosts(today_events=events, lookback_days=30)
    assert "IT" not in boosts


def test_policy_boost_for_sector_unknown_returns_zero():
    assert policy_boost_for_sector("UnknownSector", {"IT": 20.0}) == 0.0


def test_policy_boost_for_sector_known_returns_value():
    assert policy_boost_for_sector("IT", {"IT": 25.0}) == 25.0


def test_sector_keywords_all_have_entries():
    """Make sure none of the SECTOR_KEYWORDS lists is empty."""
    for sector, kws in SECTOR_KEYWORDS.items():
        assert len(kws) > 0, f"sector {sector} has no keywords"
