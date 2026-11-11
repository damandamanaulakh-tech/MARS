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
    # Observed publisher formats: NSE supplies a local clock without an
    # offset; SEBI supplies a calendar date plus offset but no clock. Keep
    # that precision instead of manufacturing UTC or a midnight timestamp.
    for pattern, fmt, date_only in (
        (r"\d{1,2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2}", "%d-%b-%Y %H:%M:%S", False),
        (r"\d{1,2} [A-Za-z]{3}, \d{4} [+-]\d{4}", "%d %b, %Y %z", True),
    ):
        if re.fullmatch(pattern, raw):
            try:
                stamp = datetime.strptime(raw, fmt)
                return stamp.date().isoformat() if date_only else stamp.isoformat()
            except ValueError:
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


def _document_url(raw: str, base: str) -> str:
    """Repair only an exact repeated origin observed in SEBI's feed links."""
    url = safe_url(raw, base)
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    if parsed.path.startswith(("/https://", "/http://")):
        origin = f"{parsed.scheme}://{parsed.netloc}"
        repeated = origin + "/" + origin + "/"
        if not url.startswith(repeated):
            return ""  # an embedded different origin has no proven repair
        inner = url[len(origin) + 1:]
        # More than one duplication is not the publisher pattern verified.
        if urllib.parse.urlsplit(inner).path.startswith(("/https://", "/http://")):
            return ""
        return safe_url(inner)
    return url


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
    items, seen = [], set()
    omitted = {"missing_document": 0, "unsafe_link": 0, "missing_title": 0}
    for node in nodes:
        title = _text(_child(node, {"title"}))[:500]
        link = _child(node, {"link"})
        raw_url = (link.get("href") or _text(link)).strip() if link is not None else ""
        if not title:
            omitted["missing_title"] += 1
            continue
        if not raw_url:
            omitted["missing_document"] += 1
            continue
        url = _document_url(raw_url, spec["url"])
        if not url:
            omitted["unsafe_link"] += 1
            continue
        source_title, subject, purpose = title, "", ""
        if spec.get("key") in ("nse_announcements", "nse_actions"):
            description = _text(_child(node, {"description"}))
            field = "PURPOSE" if spec["key"] == "nse_actions" else "SUBJECT"
            match = re.search(r"(?:^|\|)\s*" + field + r"\s*:\s*([^|]+)", description, re.IGNORECASE)
            if match:
                detail = match.group(1).strip()[:300]
                if field == "PURPOSE":
                    purpose = detail
                else:
                    subject = detail
                title = f"{title} — {detail}"[:500]
        identity = (title, url)
        if identity in seen:
            continue
        seen.add(identity)
        published_raw = _text(_child(node, {"pubDate", "published", "updated", "date"}))[:200]
        published = _published(published_raw)
        items.append({"title": title, "url": url, "published_at": published,
                      "published_raw": published_raw, "source_title": source_title,
                      "subject": subject, "purpose": purpose,
                      "source": spec["name"], "source_url": spec["url"]})
    items.sort(key=lambda item: item["published_at"], reverse=True)
    truncated = max(0, len(items) - MAX_ITEMS)
    items = items[:MAX_ITEMS]
    status = "PARTIAL" if any(omitted.values()) or truncated else "OK" if items else "EMPTY"
    if nodes and not items:
        status = "FAILED"
    errors = []
    if omitted["missing_document"]:
        errors.append(f"{omitted['missing_document']} item(s) without a document link omitted.")
    if omitted["unsafe_link"]:
        errors.append(f"{omitted['unsafe_link']} invalid or unsafe document link(s) omitted.")
    if omitted["missing_title"]:
        errors.append(f"{omitted['missing_title']} item(s) without a title omitted.")
    if truncated:
        errors.append(f"Latest feed limited to {MAX_ITEMS} items; not a complete archive.")
    return {"status": status, "items": items, "error": " ".join(errors), "omitted": omitted,
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
