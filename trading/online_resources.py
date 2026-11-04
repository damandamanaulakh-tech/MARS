"""Bounded public bulletins, for display and provenance only.

No company matching, prices, trading signals or portfolio mutation lives here.
An unavailable or malformed source is FAILED, never an empty successful feed.
"""
from __future__ import annotations

import html
import ipaddress
import math
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 40
FEEDS = (
    {"key": "nse_announcements", "name": "NSE corporate announcements",
     "url": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", "kind": "Company filings"},
    {"key": "nse_actions", "name": "NSE corporate actions",
     "url": "https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml", "kind": "Company filings"},
    {"key": "sebi", "name": "SEBI circulars, releases and orders",
     "url": "https://www.sebi.gov.in/sebirss.xml", "kind": "Regulatory"},
    {"key": "fed_monetary", "name": "US Federal Reserve monetary policy",
     "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "kind": "Global policy"},
)


def safe_url(value: str, base: str = "") -> str:
    """HTTP(S) public links only; untrusted feed URLs never become script links."""
    raw = html.unescape(str(value or "")).strip()
    if not raw or any(ord(c) < 32 for c in raw) or "\\" in raw:
        return ""
    try:
        url = urllib.parse.urljoin(base, raw) if base else raw
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return ""
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                return ""
        _ = parsed.port  # reject malformed ports
        return url
    except (ValueError, TypeError):
        return ""


def _text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", "".join(node.itertext())))).strip()


def _child(node, names):
    return next((c for c in node if c.tag.rsplit("}", 1)[-1] in names), None)


def _published(raw: str) -> str:
    if not raw:
        return ""
    try:
        stamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return ""
    # A date or clock without an offset is kept as supplied, not labelled UTC.
    return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else stamp.isoformat()


def parse_feed(content: bytes | str, spec: dict) -> dict:
    """RSS/Atom to bounded rows with explicit EMPTY/PARTIAL/FAILED status."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    if len(raw) > MAX_BYTES:
        return {"status": "FAILED", "items": [], "error": "Feed exceeds the 2 MB limit."}
    if b"\x00" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return {"status": "FAILED", "items": [], "error": "XML declarations are not accepted."}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {"status": "FAILED", "items": [], "error": "Source did not return valid RSS/Atom XML."}
    if root.tag.rsplit("}", 1)[-1].lower() not in ("rss", "feed", "rdf"):
        return {"status": "FAILED", "items": [], "error": "Source returned a page instead of a feed."}
    if root.tag.rsplit("}", 1)[-1].lower() in ("rss", "rdf") and _child(root, {"channel"}) is None:
        return {"status": "FAILED", "items": [], "error": "RSS source is missing its channel."}
    nodes = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] in ("item", "entry")]
    items, seen, rejected = [], set(), 0
    for node in nodes:
        title = _text(_child(node, {"title"}))[:500]
        link = _child(node, {"link"})
        url = safe_url((link.get("href") or _text(link)) if link is not None else "", spec["url"])
        if not title or not url:
            rejected += 1
            continue
        identity = (title, url)
        if identity in seen:
            continue
        seen.add(identity)
        published = _published(_text(_child(node, {"pubDate", "published", "updated", "date"})))
        items.append({"title": title, "url": url, "published_at": published,
                      "source": spec["name"], "source_url": spec["url"]})
    items.sort(key=lambda item: item["published_at"], reverse=True)
    truncated = max(0, len(items) - MAX_ITEMS)
    items = items[:MAX_ITEMS]
    status = "PARTIAL" if rejected or truncated else "OK" if items else "EMPTY"
    if nodes and not items:
        status = "FAILED"
    errors = []
    if rejected:
        errors.append(f"{rejected} invalid or unsafe item(s) omitted.")
    if truncated:
        errors.append(f"Latest feed limited to {MAX_ITEMS} items; not a complete archive.")
    return {"status": status, "items": items, "error": " ".join(errors),
            "as_of": max((r["published_at"] for r in items), default="")}


def fetch_feed(spec: dict, timeout: float = 8) -> dict:
    """Single bounded stream read; no retries, authentication or body logging."""
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {"status": "FAILED", "items": [], "error": "", "fetched_at": fetched,
              "source": spec["name"], "source_url": spec["url"]}
    if not math.isfinite(timeout) or timeout <= 0 or not safe_url(spec["url"]):
        result["error"] = "Invalid source or request budget."
        return result
    started = time.monotonic()
    try:
        parts, size = [], 0
        with httpx.stream("GET", spec["url"], timeout=timeout, follow_redirects=False,
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    result["error"] = "Feed exceeds the 2 MB limit."
                    return result
                if time.monotonic() - started > timeout:
                    result["error"] = "Feed read exceeded its request budget."
                    return result
                parts.append(chunk)
        result.update(parse_feed(b"".join(parts), spec))
    except httpx.HTTPStatusError as exc:
        result["error"] = f"Source returned HTTP {exc.response.status_code}."
    except (httpx.HTTPError, OSError, ValueError):
        result["error"] = "Source unavailable or request timed out."
    return result
