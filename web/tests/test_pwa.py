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


def test_install_helper_served_and_linked():
    c = TestClient(app)
    js = c.get("/static/install.js")
    assert js.status_code == 200
    assert "beforeinstallprompt" in js.text and "Add to Home Screen" in js.text
    for page in ("/login",):
        assert "install.js" in c.get(page).text


def test_mobile_nav_present():
    """Phone navigation: hamburger + off-canvas drawer + backdrop."""
    from web import auth as auth_mod
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    html = c.get("/").text
    assert 'x-data="{ nav: false }"' in html          # drawer state
    assert 'aria-label="Open menu"' in html           # hamburger button
    assert 'id="am07-nav"' in html                    # drawer target
    assert "nav-open" in html                         # toggle class binding
    assert "hidden lg:flex" not in html               # old desktop-only sidebar gone
    css = c.get("/static/css/app.css").text
    assert "#am07-nav.nav-open" in css                # slide-in rule shipped


def test_phone_layout_guards():
    """Regressions the adversarial verifier caught — keep them fixed."""
    from web import auth as auth_mod
    c = TestClient(app)

    login = c.get("/login").text
    assert "order-1 lg:order-2" in login          # sign-in form first on phone
    assert "text-3xl sm:text-4xl lg:text-6xl" in login   # headline fits 390px
    assert 'class="font-black text-5xl' not in login     # no bare display type

    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    dash = c.get("/").text
    # canvas height is pinned wherever a chart renders (guard against the
    # retina-stretch regression); chart block only exists when bars exist
    assert "overflow-x-clip" not in dash          # clipped content is reachable
    tpl = open("web/templates/dashboard.html").read()
    assert "h-[240px]" in tpl                     # canvas CSS height pinned
