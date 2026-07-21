"""Indian-market event calendars — earnings, RBI MPC, Budget, USFDA, expiries.

All entries are MANUALLY-CURATED (no live scraping) for reliability. Update
this file annually when RBI publishes the next year's MPC schedule and
when Union Budget date is announced.

Used by the engine as overlays:
  - Halt new entries 5 days before earnings for that specific stock
  - Tighten stops during RBI/Budget weeks (system-wide)
  - Flag pharma stocks 3 days before known USFDA decisions
  - Don't misread ex-dividend gaps as crashes
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class CalendarEvent:
    event_type: str   # 'rbi_mpc' | 'budget' | 'earnings' | 'usfda' | 'expiry' | 'election' | 'ex_dividend'
    event_date: date
    description: str
    impact: str       # 'system_wide' | 'sector' | 'single_stock'
    affected: list[str] = None  # tickers affected; empty = system-wide

    def days_until(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        return (self.event_date - today).days


# --- RBI Monetary Policy Committee (MPC) dates ---
# Source: https://www.rbi.org.in/Scripts/MonetaryPolicy.aspx
# Updated annually. Next ~12 months listed below.
RBI_MPC_DATES_2026 = [
    date(2026, 2, 5),
    date(2026, 4, 3),
    date(2026, 6, 6),
    date(2026, 8, 8),
    date(2026, 10, 1),
    date(2026, 12, 5),
]
RBI_MPC_DATES_2027 = [
    date(2027, 2, 6),
    date(2027, 4, 7),
    date(2027, 6, 4),
    date(2027, 8, 6),
    date(2027, 10, 1),
    date(2027, 12, 3),
]

# --- Union Budget ---
# Always Feb 1 (since 2017); manually-confirmed exception years marked.
UNION_BUDGET_DATES = [
    date(2026, 2, 1),
    date(2027, 2, 1),
]

# --- Indian General Election RESULTS dates (the day Nifty actually moves) ---
# Source: Election Commission of India. Voting spans weeks, results are 1 day.
# Historical: 2014-05-16, 2019-05-23, 2024-06-04 (Nifty -5.93% intraday).
INDIA_GENERAL_ELECTION_RESULTS = [
    date(2014, 5, 16),
    date(2019, 5, 23),
    date(2024, 6, 4),
    date(2029, 5, 31),   # placeholder — refresh when announced
]

# --- Key State Election results (high market impact only) ---
# Includes Maharashtra, Haryana, UP, Gujarat, Bihar, Delhi where local
# policy drives sector flows materially.
INDIA_STATE_ELECTION_RESULTS = [
    date(2024, 6, 4),    # Odisha + Andhra Pradesh (alongside General)
    date(2024, 10, 8),   # Haryana + J&K
    date(2024, 11, 23),  # Maharashtra + Jharkhand
    date(2025, 2, 8),    # Delhi
    date(2025, 11, 14),  # Bihar (estimated)
]

# --- NSE trading holidays (P2: crons must not run on holidays) ---
# PARTIAL list: only nationally fixed dates are hardcoded with certainty.
# Variable-date holidays (Holi, Eid, Diwali, etc.) MUST be refreshed yearly
# from the NSE circular. Missing entries fail SAFE: the scan runs but the
# stale-data guard (cfg.max_stale_days) blocks buys on old candles.
NSE_FIXED_HOLIDAYS = [
    date(2026, 1, 26), date(2026, 8, 15), date(2026, 10, 2), date(2026, 12, 25),
    date(2027, 1, 26), date(2027, 8, 15), date(2027, 10, 2), date(2027, 12, 25),
]


def is_trading_day(today: Optional[date] = None) -> bool:
    """True on NSE trading days: Mon-Fri and not a known holiday."""
    today = today or date.today()
    return today.weekday() < 5 and today not in NSE_FIXED_HOLIDAYS


# --- F&O monthly expiry: last Thursday of every month ---
def fno_monthly_expiry(year: int, month: int) -> date:
    """Last Thursday of the given month."""
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    last_day = first_next - timedelta(days=1)
    # Thursday = weekday 3
    while last_day.weekday() != 3:
        last_day -= timedelta(days=1)
    return last_day


# --- Earnings calendar (NSE quarterly results) ---
# Source: NSE quarterly results calendar (manually curated).
# Format: ticker (NSE) -> [date(quarter_end_date)]
# Update every quarter from https://www.nseindia.com/companies-listing/corporate-filings-financial-results
#
# Rule: skip new entries during the 3-day window BEFORE earnings (volatility
# spike + binary risk). Existing positions are not force-exited.
#
# Indian Q1 results typically late Jul / Q2 late Oct / Q3 late Jan / Q4 late Apr-May.
INDIA_EARNINGS_DATES_2026 = {
    # Q1 FY27 (Jul 2026): banks and IT report first
    "TCS.NS":       [date(2026, 7, 11), date(2026, 10, 10), date(2027, 1, 13), date(2027, 4, 13)],
    "INFY.NS":      [date(2026, 7, 17), date(2026, 10, 16), date(2027, 1, 14), date(2027, 4, 18)],
    "WIPRO.NS":     [date(2026, 7, 18), date(2026, 10, 16), date(2027, 1, 16), date(2027, 4, 19)],
    "HCLTECH.NS":   [date(2026, 7, 14), date(2026, 10, 13), date(2027, 1, 14), date(2027, 4, 15)],
    "TECHM.NS":     [date(2026, 7, 25), date(2026, 10, 24), date(2027, 1, 24), date(2027, 4, 25)],
    "LTIM.NS":      [date(2026, 7, 19), date(2026, 10, 17), date(2027, 1, 17), date(2027, 4, 18)],
    "HDFCBANK.NS":  [date(2026, 7, 19), date(2026, 10, 18), date(2027, 1, 17), date(2027, 4, 19)],
    "ICICIBANK.NS": [date(2026, 7, 25), date(2026, 10, 25), date(2027, 1, 24), date(2027, 4, 26)],
    "SBIN.NS":      [date(2026, 8, 2),  date(2026, 11, 8),  date(2027, 2, 7),  date(2027, 5, 16)],
    "AXISBANK.NS":  [date(2026, 7, 23), date(2026, 10, 22), date(2027, 1, 22), date(2027, 4, 23)],
    "KOTAKBANK.NS": [date(2026, 7, 26), date(2026, 10, 25), date(2027, 1, 25), date(2027, 4, 27)],
    "INDUSINDBK.NS":[date(2026, 7, 23), date(2026, 10, 22), date(2027, 1, 22), date(2027, 4, 23)],
    "BAJFINANCE.NS":[date(2026, 7, 26), date(2026, 10, 25), date(2027, 1, 25), date(2027, 4, 27)],
    "RELIANCE.NS":  [date(2026, 7, 19), date(2026, 10, 18), date(2027, 1, 17), date(2027, 4, 19)],
    "MARUTI.NS":    [date(2026, 7, 31), date(2026, 10, 30), date(2027, 1, 30), date(2027, 4, 30)],
    "M&M.NS":       [date(2026, 8, 8),  date(2026, 11, 7),  date(2027, 2, 6),  date(2027, 5, 8)],
    "TATAMOTORS.NS":[date(2026, 7, 31), date(2026, 10, 31), date(2027, 1, 31), date(2027, 4, 30)],
    "BHARTIARTL.NS":[date(2026, 8, 7),  date(2026, 11, 7),  date(2027, 2, 6),  date(2027, 5, 14)],
    "ITC.NS":       [date(2026, 8, 1),  date(2026, 10, 31), date(2027, 1, 31), date(2027, 5, 16)],
    "LT.NS":        [date(2026, 7, 25), date(2026, 10, 25), date(2027, 1, 24), date(2027, 5, 8)],
    "SUNPHARMA.NS": [date(2026, 8, 2),  date(2026, 11, 1),  date(2027, 1, 31), date(2027, 5, 16)],
    "DRREDDY.NS":   [date(2026, 7, 26), date(2026, 10, 25), date(2027, 1, 25), date(2027, 5, 7)],
    "CIPLA.NS":     [date(2026, 8, 1),  date(2026, 10, 31), date(2027, 1, 31), date(2027, 5, 9)],
}


# --- USFDA decision calendar (manually curated for known PDUFA dates) ---
# Source: drugs.fda.gov + industry trackers. Update quarterly.
# Format: ticker (NSE) -> [(date, drug_name)]
USFDA_DECISIONS_2026 = {
    # Examples; user should refresh from industry sources
    "SUNPHARMA.NS": [(date(2026, 7, 15), "Generic XYZ ANDA")],
    "CIPLA.NS": [(date(2026, 9, 30), "Inhaler resubmission")],
    "LUPIN.NS": [(date(2026, 11, 18), "Albuterol generic")],
    "DRREDDY.NS": [(date(2026, 8, 22), "Rituximab biosimilar")],
}


def upcoming_events(today: Optional[date] = None, lookahead_days: int = 30) -> list[CalendarEvent]:
    """Returns all known events within the next `lookahead_days` days,
    ordered by date ascending."""
    today = today or date.today()
    cutoff = today + timedelta(days=lookahead_days)
    out: list[CalendarEvent] = []

    for d in RBI_MPC_DATES_2026 + RBI_MPC_DATES_2027:
        if today <= d <= cutoff:
            out.append(CalendarEvent(
                event_type="rbi_mpc", event_date=d,
                description="RBI Monetary Policy Committee meeting",
                impact="system_wide", affected=[],
            ))

    for d in UNION_BUDGET_DATES:
        if today <= d <= cutoff:
            out.append(CalendarEvent(
                event_type="budget", event_date=d,
                description="Union Budget presentation",
                impact="system_wide", affected=[],
            ))

    # Indian general elections — single biggest binary event for NSE
    for d in INDIA_GENERAL_ELECTION_RESULTS:
        if today <= d <= cutoff:
            out.append(CalendarEvent(
                event_type="election_general", event_date=d,
                description="India General Election results",
                impact="system_wide", affected=[],
            ))

    # State elections (high market impact)
    for d in INDIA_STATE_ELECTION_RESULTS:
        if today <= d <= cutoff:
            out.append(CalendarEvent(
                event_type="election_state", event_date=d,
                description="State Election results",
                impact="system_wide", affected=[],
            ))

    # F&O expiries (next 2 months)
    for offset in range(0, 2):
        y, m = (today.year, today.month + offset)
        if m > 12:
            y, m = y + 1, m - 12
        exp = fno_monthly_expiry(y, m)
        if today <= exp <= cutoff:
            out.append(CalendarEvent(
                event_type="expiry", event_date=exp,
                description="F&O monthly expiry",
                impact="system_wide", affected=[],
            ))

    # USFDA decisions
    for ticker, decisions in USFDA_DECISIONS_2026.items():
        for d, drug in decisions:
            if today <= d <= cutoff:
                out.append(CalendarEvent(
                    event_type="usfda", event_date=d,
                    description=f"USFDA decision: {drug}",
                    impact="single_stock", affected=[ticker],
                ))

    # Earnings dates — single-stock binary risk (volatility spike + gap risk)
    for ticker, dates in INDIA_EARNINGS_DATES_2026.items():
        for d in dates:
            if today <= d <= cutoff:
                out.append(CalendarEvent(
                    event_type="earnings", event_date=d,
                    description=f"Quarterly results: {ticker}",
                    impact="single_stock", affected=[ticker],
                ))

    out.sort(key=lambda e: e.event_date)
    return out


def is_system_event_within(days: int = 5, today: Optional[date] = None) -> Optional[CalendarEvent]:
    """Returns the nearest macro binary event (RBI / Budget / election) within
    N days, or None.

    F&O monthly expiry is system-wide but ROUTINE — halting entries for 3 days
    before every expiry wrongly benched the engine ~4 trading days a month, so
    expiry is excluded here. Genuine binary events (rate decisions, Budget,
    election results) still halt.
    """
    upcoming = upcoming_events(today, lookahead_days=days)
    for ev in upcoming:
        if ev.impact == "system_wide" and ev.event_type != "expiry":
            return ev
    return None


def is_ticker_event_within(ticker: str, days: int = 5, today: Optional[date] = None) -> Optional[CalendarEvent]:
    """Returns the nearest event affecting this ticker within N days, or None."""
    upcoming = upcoming_events(today, lookahead_days=days)
    for ev in upcoming:
        if ev.affected and ticker in ev.affected:
            return ev
    return None


# F2 v1.1 (user: "more festivals, wire the rest"): major market-relevant
# Indian festival dates. Lunar dates carry ±1 day uncertainty — immaterial
# inside the ±5-day window. FLAG + sector hint only; no invented alpha.
FESTIVAL_DATES = {
    # 2026
    "Makar Sankranti / Pongal": [date(2026, 1, 14), date(2027, 1, 14)],
    "Holi":                     [date(2026, 3, 4), date(2027, 3, 22)],
    "Eid-ul-Fitr":              [date(2026, 3, 20)],
    "Akshaya Tritiya":          [date(2026, 4, 20)],   # biggest gold-buying day
    "Eid-al-Adha":              [date(2026, 5, 27)],
    "Onam":                     [date(2026, 8, 26)],
    "Raksha Bandhan":           [date(2026, 8, 28)],
    "Janmashtami":              [date(2026, 9, 4)],
    "Ganesh Chaturthi":         [date(2026, 9, 14)],
    "Navratri start":           [date(2026, 10, 11)],
    "Dussehra":                 [date(2026, 10, 20)],
    "Dhanteras":                [date(2026, 11, 6)],   # gold/auto/durables peak
    "Diwali":                   [date(2026, 11, 8), date(2027, 10, 29)],
    "Bhai Dooj":                [date(2026, 11, 10)],
    "Guru Nanak Jayanti":       [date(2026, 11, 24)],
    "Christmas / year-end":     [date(2026, 12, 25)],
}

# Which sectors historically see festival demand (HINTS for the journal —
# not a score input until validated).
FESTIVAL_SECTOR_HINTS = {
    "Makar Sankranti / Pongal": "FMCG, Retail",
    "Holi": "FMCG, Consumer",
    "Eid-ul-Fitr": "FMCG, Retail, Apparel",
    "Akshaya Tritiya": "Gold/Jewellery (Titan etc.), Financials-gold-loan",
    "Eid-al-Adha": "FMCG, Retail",
    "Onam": "Retail, Gold, Consumer Durables (Kerala demand)",
    "Raksha Bandhan": "Retail, Sweets/FMCG, Gifting",
    "Janmashtami": "FMCG, Dairy",
    "Ganesh Chaturthi": "Consumer, Retail (west-India demand)",
    "Navratri start": "Autos (delivery bookings), Consumer Durables, Retail",
    "Dussehra": "Autos (peak delivery day), Consumer Durables",
    "Dhanteras": "Gold/Jewellery, Autos, Consumer Durables — biggest buying day",
    "Diwali": "Autos, Retail, FMCG, Consumer Durables, Jewellery",
    "Bhai Dooj": "Gifting, Retail",
    "Guru Nanak Jayanti": "Regional retail",
    "Christmas / year-end": "Hospitality, Aviation, Retail, QSR",
}

# Wedding-season windows (gold + apparel + hospitality demand)
WEDDING_SEASONS = [
    (date(2026, 1, 15), date(2026, 3, 10)),
    (date(2026, 4, 14), date(2026, 6, 30)),
    (date(2026, 11, 15), date(2026, 12, 15)),
]


def festival_within(days: int = 5, today: date | None = None):
    """Name of the nearest festival within ±`days`, else None."""
    today = today or date.today()
    best = None
    for name, dates in FESTIVAL_DATES.items():
        for d in dates:
            gap = abs((d - today).days)
            if gap <= days and (best is None or gap < best[0]):
                best = (gap, name)
    return best[1] if best else None


def festival_context(days: int = 5, today: date | None = None) -> str | None:
    """'Diwali (Autos, Retail, ...)' or None — for journal/dashboard."""
    name = festival_within(days=days, today=today)
    if not name:
        today = today or date.today()
        for a, b in WEDDING_SEASONS:
            if a <= today <= b:
                return "Wedding season (Gold/Jewellery, Apparel, Hospitality)"
        return None
    hint = FESTIVAL_SECTOR_HINTS.get(name, "")
    return f"{name} ({hint})" if hint else name
