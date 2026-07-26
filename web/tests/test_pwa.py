"""Phone app (PWA) — manifest, icons, service worker (owner-approved)."""

from fastapi.testclient import TestClient

from web.main import app


def test_manifest_served():
    r = TestClient(app).get("/static/manifest.webmanifest")
    assert r.status_code == 200
    assert r.json()["short_name"] == "AM07"
    assert r.json()["display"] == "standalone"


def test_icons_and_sw():
    c = TestClient(app)
    assert c.get("/static/icon-192.png").status_code == 200
    assert c.get("/static/apple-touch-icon.png").status_code == 200
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "serviceWorker" not in sw.text[:1]


def test_base_head_links_manifest():
    html = TestClient(app).get("/login").text
    assert "manifest.webmanifest" in html
    assert "apple-touch-icon" in html
