"""The login page must never carry a working credential (2026-07-27 audit).

/login is unauthenticated and public. It used to render a "Quick switch" box
with onclick="quick('owner@sourceborn.in','sourceborn')" — one tap, or View
Source, and a stranger was signed in as the owner. The viewer account
("viewer"/"viewer") went with it: the owner is the only user.

These tests are the guard against that coming back.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.main import app

LOGIN_HTML = Path(__file__).resolve().parents[1] / "templates" / "login.html"


def _page() -> str:
    r = TestClient(app).get("/login")
    assert r.status_code == 200
    return r.text


# ---- the rendered page ----

def test_login_page_does_not_echo_the_configured_owner_password(monkeypatch):
    """Uses a sentinel, not the real value.

    The live default password happens to be "sourceborn" — the brand name,
    which appears legitimately in the title, logo and email domain — so a
    plain `_SEEDED_PWD not in page` check can never be trusted. Swapping in a
    string that appears nowhere else is the only honest version of this test.
    (That the password IS the brand name is its own problem: set a real
    OWNER_PWD in Render.)
    """
    sentinel = "zZq7-sentinel-not-in-any-markup"
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "owner@sourceborn.in",
                        {"role": "owner", "password": sentinel, "name": "Owner"})
    assert sentinel not in _page()


def test_login_page_has_no_quick_switch_or_autofill_script():
    page = _page().lower()
    for banned in ("quick switch", "quick(", "input[name=password]"):
        assert banned not in page, f"login page still contains {banned!r}"


def test_login_page_does_not_mention_a_viewer_account():
    assert "viewer" not in _page().lower()


def test_login_page_never_prefills_a_password_field():
    """An email default is fine; a password default is not."""
    import re
    for tag in re.findall(r"<input[^>]*>", _page(), re.I):
        if 'name="password"' in tag.lower():
            assert "value=" not in tag.lower(), f"password input is prefilled: {tag}"


# ---- the template source (catches a hardcoded literal a render might hide) ----

def test_login_template_source_has_no_password_literals():
    src = LOGIN_HTML.read_text().lower()
    for banned in ("sourceborn.in','", 'sourceborn.in",', "'viewer'", '"viewer"'):
        assert banned not in src, f"login.html still contains {banned!r}"


# ---- the account list ----

def test_owner_is_the_only_seeded_account():
    assert list(auth_mod.SEEDED_USERS) == ["owner@sourceborn.in"]
    assert auth_mod.SEEDED_USERS["owner@sourceborn.in"]["role"] == "owner"


def test_the_removed_viewer_account_can_no_longer_log_in():
    assert auth_mod.verify("viewer@sourceborn.in", "viewer") is None
    r = TestClient(app).post("/login", data={"email": "viewer@sourceborn.in",
                                             "password": "viewer"})
    assert r.status_code in (200, 303)
    # A rejected login must not establish a session.
    c = TestClient(app)
    c.post("/login", data={"email": "viewer@sourceborn.in", "password": "viewer"})
    assert c.get("/settings").status_code in (303, 401, 403) or \
        "sign in" in c.get("/settings").text.lower()


def test_owner_login_still_works():
    assert auth_mod.verify("owner@sourceborn.in", auth_mod._SEEDED_PWD) is not None
    assert auth_mod.verify("owner@sourceborn.in", "wrong-password") is None


# ---- the deleted workflow ----

def test_hugging_face_sync_workflow_is_gone():
    """It force-pushed the whole 86MB repo (incl. data/) to a public HF Space
    on every push to main. All 16 runs failed on missing secrets, so nothing
    ever leaked — but setting those secrets would have published everything."""
    wf = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    assert not (wf / "sync-to-hf.yml").exists()
    for f in wf.glob("*.yml"):
        assert "huggingface" not in f.read_text().lower(), f"{f.name} still syncs to HF"
