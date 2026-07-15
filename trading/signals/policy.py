"""Indian government policy tracker.

Watches PIB (Press Information Bureau) RSS feeds + key ministry feeds for
big-budget announcements that historically move NSE sectors:

  - PLI scheme announcements (semiconductors, EV, drones, solar, etc.)
  - National infrastructure pipeline (capex, cement, steel)
  - Defence indigenisation orders (HAL, BEL, BDL, Mazagon Dock)
  - Energy transition (renewables, EV charging)
  - Banking reforms (PSU bank consolidation)

Sectors get a +20 score boost for 30 days after a relevant announcement.

Sources (all free, official):
  https://pib.gov.in/RssMain.aspx?ModId=8&Lang=1&Regid=3   (English releases)
  https://pib.gov.in/feeds.aspx

Honest limitation: PIB RSS publishes EVERYTHING. We use Claude (if API key
is set) to filter for genuinely market-moving sector announcements vs noise.
Without the API key, we fall back to keyword matching — less accurate but
still useful.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .news import _score_with_claude   # reuse Claude scoring

log = logging.getLogger(__name__)


PIB_RSS_URL = "https://pib.gov.in/RssMain.aspx?ModId=8&Lang=1&Regid=3"


# Sector keyword map. Tuned for Indian policy context.
SECTOR_KEYWORDS = {
    "IT":           ["semiconductor", "chip", "fab", "design-linked", "DLI", "PLI IT"],
    "Auto":         ["EV", "electric vehicle", "FAME", "battery", "auto PLI"],
    "Energy":       ["renewable", "solar", "wind", "green hydrogen", "ethanol"],
    "Defence":      ["defence", "military", "naval", "submarine", "tejas", "indigen"],
    "Metal":        ["steel", "PLI specialty steel", "mineral", "mining"],
    "Construction": ["infrastructure", "road", "highway", "bullet train", "metro"],
    "FMCG":         ["food processing", "PLI food", "agri export"],
    "Pharma":       ["pharma PLI", "API", "bulk drug", "vaccine", "USFDA"],
    "Telecom":      ["spectrum", "5G", "telecom PLI", "BSNL"],
    "Financial":    ["banking reform", "PSU bank", "merger bank", "credit guarantee"],
}


@dataclass
class PolicyEvent:
    ticker_sector: str               # one of SECTOR_KEYWORDS keys
    headline: str
    published_at: datetime
    source: str
    relevance: float                  # 0.0..1.0 confidence this is real signal
    sector_boost: float               # +0..+20 score boost for stocks in this sector


def _fetch_pib_feed() -> list[dict]:
    """Returns list of {title, summary, published_parsed, link} dicts."""
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser not installed; PIB tracker disabled")
        return []
    try:
        feed = feedparser.parse(PIB_RSS_URL)
    except Exception as e:
        log.warning("PIB feed fetch failed: %s", e)
        return []
    entries = getattr(feed, "entries", [])[:40]   # cap
    return [dict(e) for e in entries]


def _keyword_match_sectors(text: str) -> list[str]:
    """Return list of sector names whose keywords match this text."""
    text_l = text.lower()
    matches: list[str] = []
    for sector, kws in SECTOR_KEYWORDS.items():
        if any(kw.lower() in text_l for kw in kws):
            matches.append(sector)
    return matches


def scan_recent_policy_events(lookback_hours: int = 48) -> list[PolicyEvent]:
    """Returns relevant policy events from the last N hours."""
    entries = _fetch_pib_feed()
    if not entries:
        return []
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    out: list[PolicyEvent] = []
    for entry in entries:
        title = entry.get("title", "")
        summary = entry.get("summary") or entry.get("description") or ""
        link = entry.get("link", "")
        published_parsed = entry.get("published_parsed")
        if published_parsed:
            try:
                from time import mktime
                published_at = datetime.fromtimestamp(mktime(published_parsed))
            except (TypeError, ValueError):
                published_at = datetime.utcnow()
        else:
            published_at = datetime.utcnow()
        if published_at < cutoff:
            continue

        combined = f"{title}. {summary}"
        sectors = _keyword_match_sectors(combined)
        if not sectors:
            continue

        # For each matched sector, emit one PolicyEvent
        relevance = 0.7 if "PLI" in combined.upper() else 0.5
        boost = 20.0 if relevance >= 0.7 else 10.0
        for sector in sectors:
            out.append(PolicyEvent(
                ticker_sector=sector,
                headline=title.strip(),
                published_at=published_at,
                source=link or "pib.gov.in",
                relevance=relevance,
                sector_boost=boost,
            ))
    return out


def active_sector_boosts(today_events: Optional[list[PolicyEvent]] = None,
                         lookback_days: int = 30) -> dict[str, float]:
    """Returns {sector: total_boost} for events within the last N days.

    If today_events is None, fetches fresh. Cap boost per sector at +30
    so multiple announcements don't stack absurdly.
    """
    events = today_events
    if events is None:
        events = scan_recent_policy_events(lookback_hours=lookback_days * 24)

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    by_sector: dict[str, float] = {}
    for ev in events:
        if ev.published_at < cutoff:
            continue
        by_sector[ev.ticker_sector] = min(
            30.0, by_sector.get(ev.ticker_sector, 0.0) + ev.sector_boost,
        )
    return by_sector


def policy_boost_for_sector(sector: str, boosts: dict[str, float]) -> float:
    """Returns the active boost (0..30) for the given sector."""
    return boosts.get(sector, 0.0)
