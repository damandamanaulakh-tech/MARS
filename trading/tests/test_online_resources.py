from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from trading import online_resources as resources
from trading import news_feed

SPEC = {"key": "test", "name": "Official source", "url": "https://www.sebi.gov.in/sebirss.xml", "kind": "Regulatory"}


def item(title="Notice", url="https://www.sebi.gov.in/order", published="Wed, 04 Nov 2026 03:00:00 +0000"):
    return f"<item><title>{title}</title><link>{url}</link><pubDate>{published}</pubDate></item>"


def rss(items=""):
    return f"<rss><channel><title>Official source</title>{items}</channel></rss>"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,test", "file:///etc/passwd", "https://user:pass@example.com/x", "http://127.0.0.1/", "http://[::1]/", "https://localhost/x", "https://host.internal/x", "https://evil.com\\@sebi.gov.in", "https://exa\nmple.com"])
def test_unsafe_links_never_become_clickable(url):
    assert resources.safe_url(url) == ""
    reading = news_feed.Headline("News", "date", "source", url=url)
    assert news_feed.NewsReading("TCS.NS", "date", headlines=[reading]).to_dict()["headlines"][0]["url"] == ""


def test_relative_official_links_and_dates_have_provenance():
    result = resources.parse_feed(rss(item(url="/enforcement/order.html")), SPEC)
    assert result["status"] == "OK"
    row = result["items"][0]
    assert row["url"] == "https://www.sebi.gov.in/enforcement/order.html"
    assert row["published_at"] == "2026-11-04T03:00:00+00:00"
    assert row["source_url"] == SPEC["url"] and row["source"] == SPEC["name"]


def test_failed_html_and_malformed_xml_are_not_empty_success():
    for body in ("<html><body>Access denied</body></html>", "<rss><channel>", "<!DOCTYPE rss><rss/>", "<rss/>", '<rss><channel/></rss>'.encode("utf-16")):
        assert resources.parse_feed(body, SPEC)["status"] == "FAILED"
    assert resources.parse_feed(rss(), SPEC)["status"] == "EMPTY"


def test_unsafe_rows_are_omitted_with_partial_coverage():
    result = resources.parse_feed(rss(item() + item(title="Bad", url="javascript:alert(1)")), SPEC)
    assert result["status"] == "PARTIAL"
    assert len(result["items"]) == 1 and "1 invalid" in result["error"]
    assert resources.parse_feed(rss(item(url="javascript:alert(1)")), SPEC)["status"] == "FAILED"


def test_latest_forty_are_sorted_before_cap_and_duplicates_removed():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [item(title=f"Notice {n}", url=f"https://www.sebi.gov.in/{n}", published=(start + timedelta(days=n)).isoformat()) for n in range(45)]
    result = resources.parse_feed(rss("".join(rows + rows[:2])), SPEC)
    assert result["status"] == "PARTIAL" and len(result["items"]) == 40
    assert result["items"][0]["title"] == "Notice 44"
    assert result["items"][-1]["title"] == "Notice 5"


def test_atom_dates_and_links():
    body = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Policy</title><link href="https://www.sebi.gov.in/policy"/><updated>2026-11-04T10:00:00+05:30</updated></entry></feed>'
    result = resources.parse_feed(body, SPEC)
    assert result["status"] == "OK" and result["as_of"] == "2026-11-04T04:30:00+00:00"


def test_stream_size_limit_fails_without_retaining_payload(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass
        def iter_bytes(self):
            yield b"a" * (resources.MAX_BYTES + 1)
            pytest.fail("must stop immediately at the byte bound")
    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs["timeout"] == 8 and kwargs["follow_redirects"] is False
        yield Response()
    monkeypatch.setattr(resources.httpx, "stream", stream)
    result = resources.fetch_feed(SPEC)
    assert result["status"] == "FAILED" and "2 MB" in result["error"]
    assert not result["items"] and result["fetched_at"]


def test_fetch_timeout_is_not_reported_as_no_notices(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("fixture timeout")
    monkeypatch.setattr(resources.httpx, "stream", fail)
    result = resources.fetch_feed(SPEC)
    assert result["status"] == "FAILED" and "unavailable" in result["error"]


def test_news_article_url_survives_parse_and_storage():
    body = rss(item(title="TCS wins new contract", url="https://news.google.com/rss/articles/ABC?x=1&amp;y=2"))
    headlines = news_feed._parse_rss(body)
    assert headlines[0].url == "https://news.google.com/rss/articles/ABC?x=1&y=2"
    stored = news_feed.judge("TCS.NS", headlines, "2026-11-04").to_dict()
    assert stored["headlines"][0]["url"] == headlines[0].url


def test_old_headlines_without_urls_still_work():
    head = news_feed._parse_rss(rss("<item><title>TCS wins new contract</title></item>"))[0]
    assert head.url == "" and news_feed.judge("TCS.NS", [head], "date").verdict == "TRIGGER"


def test_observed_nse_local_clock_and_subject_keep_source_precision():
    spec = next(s for s in resources.FEEDS if s["key"] == "nse_announcements")
    body = rss('<item><title>Example Industries Limited</title>'
               '<link>https://nsearchives.nseindia.com/corporate/example.pdf</link>'
               '<description><![CDATA[Company: Example Industries Limited |SUBJECT: Board Meeting Intimation |Broadcast Date: 04-Nov-2026]]></description>'
               '<pubDate>04-Nov-2026 11:27:09</pubDate></item>')
    result = resources.parse_feed(body, spec)
    row = result["items"][0]
    assert row["published_at"] == "2026-11-04T11:27:09"
    assert row["published_raw"] == "04-Nov-2026 11:27:09"
    assert row["source_title"] == "Example Industries Limited"
    assert row["subject"] == "Board Meeting Intimation"
    assert row["title"] == "Example Industries Limited — Board Meeting Intimation"
    assert result["as_of"] == "2026-11-04T11:27:09"


def test_observed_sebi_date_only_and_exact_duplicated_origin():
    document = "https://www.sebi.gov.in/sebi_data/attachdocs/nov-2026/example.pdf"
    result = resources.parse_feed(rss(item(url="https://www.sebi.gov.in/" + document,
                                           published="03 Nov, 2026 +0530")), SPEC)
    row = result["items"][0]
    assert row["published_at"] == "2026-11-03"
    assert row["published_raw"] == "03 Nov, 2026 +0530"
    assert row["url"] == document
    assert result["as_of"] == "2026-11-03"


def test_missing_document_links_are_counted_separately_from_unsafe_links():
    result = resources.parse_feed(rss(item() + item(title="NAV without a document", url="")
                                      + item(title="Unsafe document", url="javascript:alert(1)")), SPEC)
    assert result["status"] == "PARTIAL" and len(result["items"]) == 1
    assert result["omitted"]["missing_document"] == 1
    assert result["omitted"]["unsafe_link"] == 1
    assert "1 item(s) without a document link omitted" in result["error"]
    assert "1 invalid or unsafe document link(s) omitted" in result["error"]


@pytest.mark.parametrize("url", [
    "https://www.sebi.gov.in/https://other.example/document.pdf",
    "https://www.sebi.gov.in/http://www.sebi.gov.in/document.pdf",
    "https://www.sebi.gov.in/https://www.sebi.gov.in:444/document.pdf",
])
def test_malformed_embedded_different_origin_is_unavailable(url):
    result = resources.parse_feed(rss(item(url=url)), SPEC)
    assert result["status"] == "FAILED" and not result["items"]
    assert result["omitted"]["unsafe_link"] == 1


def test_invalid_source_dates_stay_unknown_without_invented_timestamp():
    result = resources.parse_feed(rss(item(published="31 Nov, 2026 +0530")), SPEC)
    assert result["items"][0]["published_at"] == ""
    assert result["items"][0]["published_raw"] == "31 Nov, 2026 +0530"


def test_nse_display_title_is_bounded_and_does_not_consume_other_fields():
    spec = next(s for s in resources.FEEDS if s["key"] == "nse_announcements")
    body = rss('<item><title>' + "A" * 490 + '</title><link>https://www.nseindia.com/doc.pdf</link>'
               '<description>|SUBJECT: ' + "B" * 700 + '|CATEGORY: Announcement</description></item>')
    row = resources.parse_feed(body, spec)["items"][0]
    assert len(row["title"]) <= 500 and len(row["subject"]) <= 300
    assert "CATEGORY" not in row["subject"]


def test_observed_nse_actions_purpose_remains_source_text():
    spec = next(s for s in resources.FEEDS if s["key"] == "nse_actions")
    body = rss('<item><title>Example Industries Limited - Ex-Date:04-Nov-2026</title>'
               '<link>https://www.nseindia.com/document.pdf</link>'
               '<description>SERIES:EQ |PURPOSE:INTERIM DIVIDEND - RS10.25 PER SHARE |FACE VALUE:10</description>'
               '<pubDate>04-Nov-2026 10:11:28</pubDate></item>')
    row = resources.parse_feed(body, spec)["items"][0]
    assert row["published_at"] == "2026-11-04T10:11:28"
    assert row["purpose"] == "INTERIM DIVIDEND - RS10.25 PER SHARE"
    assert row["title"].endswith(" — INTERIM DIVIDEND - RS10.25 PER SHARE")
    assert "FACE VALUE" not in row["title"]
