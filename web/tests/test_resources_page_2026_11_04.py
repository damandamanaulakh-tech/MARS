"""Source diagnostics are authenticated, cached-only and safe to render."""
from copy import deepcopy
from types import SimpleNamespace
import sys

from fastapi.testclient import TestClient

from web import auth
from web.main import app


def client(role="owner", monkeypatch=None):
    out = TestClient(app)
    if role:
        if role == "viewer":
            monkeypatch.setitem(auth.SEEDED_USERS, "viewer@test.invalid", {
                "role": "viewer", "name": "Viewer", "password": "test-only",
            })
            out.post("/login", data={"email": "viewer@test.invalid", "password": "test-only"})
        else:
            out.post("/login", data={"email": "owner@sourceborn.in", "password": auth._SEEDED_PWD})
    return out


def report():
    return {
        "updated_at": "2026-11-04T08:15:00+05:30",
        "sources": [{
            "key": "official_nse", "name": "NSE notices", "url": "https://www.nseindia.com/",
            "kind": "Official exchange notices", "status": "STALE",
            "as_of": "2026-11-01", "last_attempt": "2026-11-04T08:15:00+05:30",
            "last_success": "2026-11-02T08:15:00+05:30",
            "reason": "Saved notices are older than the refresh window.",
            "error": "HTTP 503", "remedy": "The scheduled refresh will retry.",
        }],
        "notices": [{
            "title": "Example exchange announcement", "url": "https://www.nseindia.com/example",
            "source": "NSE", "published_at": "2026-11-01T10:00:00+05:30", "ticker": "EXAMPLE.NS",
        }],
        "warnings": ["One source is overdue."],
    }


def install_report(monkeypatch, saved):
    calls = []
    def view():
        calls.append(True)
        return saved
    module = SimpleNamespace(resource_view=view)
    import web
    monkeypatch.setitem(sys.modules, "web.resource_feed", module)
    monkeypatch.setattr(web, "resource_feed", module, raising=False)
    return calls


def test_auth_checked_before_reading_saved_resources(monkeypatch):
    calls = install_report(monkeypatch, report())
    response = client(role=None).get("/resources", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    response = client("viewer", monkeypatch).get("/resources")
    assert response.status_code == 403
    assert calls == []


def test_saved_source_status_and_observation_dates_remain_distinct(monkeypatch):
    saved = report()
    before = deepcopy(saved)
    calls = install_report(monkeypatch, saved)
    body = client().get("/resources").text
    assert calls == [True] and saved == before
    for shown in ("NSE notices", "STALE", "2026-11-01", "2026-11-04T08:15:00+05:30",
                  "2026-11-02T08:15:00+05:30", "HTTP 503", "scheduled refresh will retry",
                  "Example exchange announcement", "EXAMPLE.NS", "One source is overdue."):
        assert shown in body
    assert "opening it does not refresh external feeds" in body
    assert 'href="https://www.nseindia.com/example"' in body
    assert "Company filing detail needed" in body
    assert "More historical observations needed" in body
    assert "Data as of" in body and "Last success" in body


def test_empty_feed_is_not_presented_as_no_announcements(monkeypatch):
    install_report(monkeypatch, {})
    body = client().get("/resources").text
    assert "No refresh report has been saved yet" in body
    assert "No source-status records are available yet" in body
    assert "an empty list is not proof that no announcement was made" in body


def test_retained_notice_carries_its_failed_feed_status(monkeypatch):
    saved = report()
    saved["sources"][0]["status"] = "FAILED"
    saved["sources"][0]["reason"] = "Showing retained notices from an earlier successful fetch."
    saved["notices"][0]["feed_status"] = "FAILED"
    install_report(monkeypatch, saved)
    body = client().get("/resources").text
    assert "Showing retained notices" in body
    assert "Example exchange announcement" in body
    assert 'data-notice-feed-status>Feed status:' in body
    assert body.count(">FAILED</span>") == 2


def test_untrusted_notice_html_and_unsafe_links_are_not_active(monkeypatch):
    saved = report()
    saved["notices"][0]["title"] = '<script>alert("notice")</script>'
    saved["notices"][0]["url"] = "javascript:alert(1)"
    saved["sources"][0]["url"] = "https://user:secret@example.com/"
    saved["sources"][0]["error"] = '<img src=x onerror=alert(1)>'
    install_report(monkeypatch, saved)
    body = client().get("/resources").text
    assert '<script>alert("notice")</script>' not in body
    assert "&lt;script&gt;" in body and "&lt;img" in body
    assert 'href="javascript:' not in body
    assert "https://user:secret" not in body


def test_source_read_failure_is_visible_without_exposing_exception(monkeypatch):
    def failed():
        raise RuntimeError("private connection detail")
    import web
    fake = SimpleNamespace(resource_view=failed)
    monkeypatch.setitem(sys.modules, "web.resource_feed", fake)
    monkeypatch.setattr(web, "resource_feed", fake, raising=False)
    response = client().get("/resources")
    assert response.status_code == 200
    assert "Saved source readings could not be loaded" in response.text
    assert "private connection detail" not in response.text


def test_page_has_no_mutation_route(monkeypatch):
    calls = install_report(monkeypatch, report())
    response = client().post("/resources")
    assert response.status_code == 405
    assert calls == []
