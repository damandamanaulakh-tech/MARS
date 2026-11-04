"""Cached public sources and explicit freshness, separate from trading decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
import time

from . import db

_REFRESH_LOCK = Lock()
_VOLATILE: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age_hours(value: str, now: datetime) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (now - stamp).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def record_attempt(key: str, status: str, reason: str = "") -> None:
    """Public-source diagnostics only. Failure to record cannot stop a job."""
    stamp = _now()
    stored = db.load_resource_feeds()
    if db.LAST_RESOURCE_READ_ERROR:
        return  # prior success is unreadable; do not replace it with blanks
    previous = _VOLATILE.get(key) or stored.get(key) or {}
    snapshot = {"last_attempt": stamp, "status": status,
                "last_success": stamp if status == "OK" else previous.get("last_success", ""),
                "error": reason[:300]}
    if not db.save_resource_feed(key, snapshot):
        _VOLATILE[key] = {**snapshot, "persisted": False}
    else:
        _VOLATILE.pop(key, None)


def refresh_resources(budget_s: float = 60) -> dict:
    """Fixed public URLs only; finite requests, no portfolio lock or scan.

    Last good notices survive outages. A refused database write remains
    visible in this process and is never described as persisted.
    """
    from trading.online_resources import FEEDS, fetch_feed
    if not _REFRESH_LOCK.acquire(blocking=False):
        return {"status": "BUSY"}
    try:
        old = db.load_resource_feeds()
        if db.LAST_RESOURCE_READ_ERROR:
            # A transient read failure is not an empty catalog. Replacing
            # those unreadable rows could erase the last good evidence.
            return {"status": "STORE_UNAVAILABLE", "error": db.LAST_RESOURCE_READ_ERROR}
        started = time.monotonic()
        results = {}
        for spec in FEEDS:
            key = spec["key"]
            previous = _VOLATILE.get(key) or old.get(key) or {}
            attempt = _now()
            remaining = budget_s - (time.monotonic() - started)
            if remaining <= 0:
                result = {"status": "DEFERRED", "error": "Refresh budget exhausted; next scheduled attempt."}
            else:
                try:
                    result = fetch_feed(spec, timeout=min(8.0, remaining))
                except Exception as exc:
                    result = {"status": "FAILED", "error": f"Fetch failed ({type(exc).__name__})."}
            good = result.get("status") in ("OK", "EMPTY", "PARTIAL")
            # Empty/failed results do not erase previously observed notices.
            incoming = result.get("items") or []
            items = incoming if incoming else previous.get("items", [])
            snapshot = {"key": key, "last_attempt": attempt,
                        "last_success": attempt if good else previous.get("last_success", ""),
                        "status": result.get("status", "FAILED"),
                        "error": str(result.get("error") or "")[:300],
                        "items": items[:40], "count": len(incoming),
                        "retained": not incoming and bool(items)}
            landed = db.save_resource_feed(key, snapshot)
            snapshot["persisted"] = landed
            if landed:
                _VOLATILE.pop(key, None)
            else:
                _VOLATILE[key] = snapshot
            results[key] = snapshot["status"] if landed else "NOT_STORED"
        return {"status": "COMPLETE", "sources": results}
    finally:
        _REFRESH_LOCK.release()


def resource_view(now: datetime | None = None) -> dict:
    """No external fetching on a page view; show observed data, not promises."""
    from trading.online_resources import FEEDS
    from trading import macro_feeds as mf
    from .valuation_market_view import NSE_STALE_DAYS
    from . import state

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    saved = {**db.load_resource_feeds(), **_VOLATILE}
    sources, notices, warnings = [], [], []
    for spec in FEEDS:
        snap = saved.get(spec["key"], {})
        status = snap.get("status", "NOT_FETCHED")
        age = _age_hours(snap.get("last_success", ""), now)
        if status in ("OK", "EMPTY", "PARTIAL") and (age is None or age < 0 or age > 18):
            status = "STALE"
        if snap.get("persisted") is False:
            status = "NOT_STORED"
        items = snap.get("items") or []
        sources.append({**spec, "status": status,
                        "as_of": max((x.get("published_at", "") for x in items), default=""),
                        "last_attempt": snap.get("last_attempt", ""),
                        "last_success": snap.get("last_success", ""),
                        "error": snap.get("error", ""),
                        "reason": ("Showing retained notices from an earlier successful fetch."
                                   if snap.get("retained") else "Recent public notices; this feed is not an exhaustive archive."),
                        "remedy": "Automatic checks at 09:10, 12:10 and 16:10 IST; open the official source for its full archive."})
        notices.extend({**x, "source": spec["name"], "feed_status": status} for x in items)

    # One cached database read serves all macro cards. Fetch time and economic
    # observation date remain separate (the Fed's FX release is weekly).
    latest = {}
    for row in db.latest_macro_resources():
        key = row.get("series", "")
        if key and str(row.get("date", "")) > str(latest.get(key, {}).get("date", "")):
            latest[key] = row
    catalog = [(k, mf.FRED_LABEL[k], "https://fred.stlouisfed.org/series/" + v,
                mf.FRED_STALE_DAYS[mf.fred_kind(k)]) for k, v in mf.FRED_SERIES.items()]
    catalog += [("rbi_repo", "RBI policy repo rate", "https://www.rbi.org.in/", mf.STALE_DAYS),
                ("rbi_usdinr", "RBI / FBIL USD–INR reference", "https://www.rbi.org.in/", mf.STALE_DAYS),
                ("gsec10y_yield", "10-year government bond yield", "https://upstox.com/developer/api-documentation/historical-candle-data-v3/", mf.STALE_DAYS)]
    for key, name, url, days in catalog:
        row = latest.get(key, {})
        stamp = row.get("date", "")
        age = _age_hours(stamp, now)
        valid_value = mf._num(row.get("value")) is not None
        status = "DATA_NEEDED" if not row or not valid_value else ("STALE" if age is None or age < 0 or age > days * 24 else "STORED")
        sources.append({"key": key, "name": name, "url": url, "kind": "Market data",
                        "status": status, "as_of": stamp, "last_success": row.get("fetched", ""),
                        "last_attempt": "", "error": "", "reason": row.get("source") or "No observation stored for this series.",
                        "remedy": "Scheduled market refresh at 09:20 and 15:35 IST. These are dated observations, not live quotes."})
    nse = [r for k, r in latest.items() if k.startswith("nse_pe:")]
    nse_asof = max((r.get("date", "") for r in nse), default="")
    expected_nse = {f"nse_pe:{name}" for name in mf.nse_index_names()}
    fresh_nse = {r["series"] for r in nse
                 if (age := _age_hours(r.get("date", ""), now)) is not None
                 and 0 <= age <= NSE_STALE_DAYS * 24
                 and mf._num(r.get("value")) is not None}
    nse_status = ("DATA_NEEDED" if not nse else "STALE" if not fresh_nse
                  else "PARTIAL" if expected_nse - fresh_nse else "STORED")
    sources.append({"key": "nse_valuation", "name": "NSE sector P/E and P/B", "url": "https://www.nseindia.com/market-data/live-market-indices",
                    "kind": "Market data", "status": nse_status,
                    "as_of": nse_asof, "reason": f"{len(fresh_nse & expected_nse)} of {len(expected_nse)} expected sector/index P/E readings within {NSE_STALE_DAYS} days; P/B availability varies by sector.",
                    "remedy": "Official NSE allIndices refresh; a failed or missing sector stays unavailable."})
    for item in sources:
        if item["kind"] != "Market data":
            continue
        attempt = saved.get(item["key"], {})
        item["last_attempt"] = attempt.get("last_attempt", "")
        item["last_success"] = attempt.get("last_success") or item.get("last_success", "")
        item["error"] = attempt.get("error", "")
        if attempt.get("status") in ("FAILED", "DEFERRED"):
            item["status"] = attempt["status"]
        if attempt.get("persisted") is False:
            item["status"] = "NOT_STORED"
    from trading.quote_router import status as quote_status
    quote = quote_status(getattr(getattr(state, "_ENGINE", None), "broker", None))
    sources.append({"key": "upstox", "name": "NSE / BSE prices and option chains", "url": "https://upstox.com/developer/api-documentation/",
                    "kind": "Stocks", "status": "CONNECTED" if quote.get("healthy") else "LOGIN_NEEDED",
                    "reason": "Live quotes and option chains use Upstox. Public daily candles can work without a login; options require listed F&O contracts.",
                    "remedy": "Connect Upstox using the existing secure login; never paste tokens into chat."})
    portfolio = state.portfolio()
    news = getattr(portfolio, "news_watch", {}) or {}
    news_age = _age_hours(news.get("ts", ""), now)
    news_rows = news.get("rows") or {}
    bad_news = {t for t, row in news_rows.items() if row.get("fetch_failed") or row.get("verdict") == "UNKNOWN"}
    bad_news.update(news.get("fetch_failed") or [])
    news_status = ("DATA_NEEDED" if news_age is None or not news_rows else
                   "STALE" if news_age < 0 or news_age > 36 else
                   "FAILED" if set(news_rows) <= bad_news else
                   "PARTIAL" if bad_news else "STORED")
    sources.append({"key": "company_news", "name": "Company news — Google News", "url": "https://news.google.com/",
                    "kind": "News", "status": news_status,
                    "as_of": max((r.get("asof", "") for r in (news.get("rows") or {}).values()), default=""),
                    "last_attempt": news.get("ts", ""), "reason": "Daily scan scope: up to 60 held and selected stocks. Each stock retains its own fetch-failure verdict.",
                    "remedy": "Open the stock's news detail for dated linked headlines; unavailable news is not a PASS."})
    filings = getattr(portfolio, "filings", []) or []
    journal = getattr(portfolio, "decision_journal", []) or []
    filing_attempt = next((e for e in reversed(journal)
                           if e.get("action") in ("FILINGS_REFRESH", "FILINGS_FAILED")), {})
    filing_success = next((e for e in reversed(journal)
                           if e.get("action") == "FILINGS_REFRESH"), {})
    filing_age = _age_hours(filing_attempt.get("ts", ""), now)
    filing_status = ("NOT_FETCHED" if not filing_attempt else
                     "FAILED" if filing_attempt.get("action") == "FILINGS_FAILED" else
                     "STALE" if filing_age is None or filing_age < 0 or filing_age > 36 else "PARTIAL")
    sources.append({"key": "bse_filings", "name": "BSE company announcements",
                    "url": "https://www.bseindia.com/corporates/ann.html", "kind": "Company filings",
                    "status": filing_status,
                    "as_of": max((f.get("when", "") for f in filings), default=""),
                    "last_attempt": filing_attempt.get("ts", ""),
                    "last_success": filing_success.get("ts", ""),
                    "error": "BSE announcement request failed; previous matches retained." if filing_status == "FAILED" else "",
                    "reason": "Held/watchlist company-name matches; two-day search, at most eight pages. Partial exchange coverage, not a complete filings archive.",
                    "remedy": "Existing 09:05 IST weekday refresh. Open BSE for issuer-specific filings and older announcements."})
    notices.extend({"title": f.get("headline", ""), "url": f.get("pdf", ""),
                    "source": "BSE company announcements", "ticker": f.get("ticker", ""),
                    "published_at": f.get("when", ""), "feed_status": filing_status}
                   for f in filings[:100] if f.get("headline"))
    cash_flows = db.latest_cash_flow_resources()
    for key, name, url, source_key in (
        ("fii", "FPI cash activity — NSDL settlement", "https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx", "source"),
        ("dii", "DII cash activity — Upstox", "https://upstox.com/developer/api-documentation/", "dii_source"),
    ):
        row = cash_flows.get(key) or {}
        stamp = row.get("date", "")
        age = _age_hours(stamp, now)
        net = mf._num(row.get(f"{key}_net"))
        status = ("DATA_NEEDED" if net is None else "STALE" if age is None or age < 0 or age > 7 * 24 else "STORED")
        sources.append({"key": key + "_cash", "name": name, "url": url,
                        "kind": "Institutional flows", "status": status, "as_of": stamp,
                        "reason": (f"Net {net:,.2f} crore INR; source {row.get(source_key) or 'not recorded'}. "
                                   if net is not None else "No valid cash-flow observation stored. ")
                                  + "FPI and DII dates are independent; derivatives are a different dataset.",
                        "remedy": "NSDL weekly cash refresh and existing Upstox FII/DII trigger-pass snapshot. A seven-day diagnostic age threshold labels old readings; trading thresholds are unchanged."})
    warnings.extend([
        "Feed availability does not establish coverage of every company or historical period.",
        "Financial statements, geographic sales, R&D, ESG, ratings and true short interest still need the relevant filing or licensed dataset; a news headline cannot fill them.",
        "Insufficient price or option history needs additional dated observations; valuation NOT COMPUTED can also indicate a compute or storage limit."])
    if not db.is_connected():
        warnings.insert(0, "Database unavailable: stored source coverage cannot be verified. No missing value should be interpreted as zero.")
    for error in (db.LAST_RESOURCE_READ_ERROR, db.LAST_MACRO_RESOURCE_READ_ERROR):
        if error:
            warnings.insert(0, error)
    return {"sources": sources, "notices": sorted(notices, key=lambda x: x.get("published_at", ""), reverse=True)[:160],
            "updated_at": max((s.get("last_attempt", "") for s in sources), default=""), "warnings": warnings}
