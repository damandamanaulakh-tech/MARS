"""The dashboard must not claim the auto-scan is off while it is running.

Owner order 2026-08-29: "fix the DAILY_SCHEDULER dashboard bug".

THE BUG: web/routes/dashboard.py read `DAILY_SCHEDULER != "1"` and printed
"Daily auto-scan is OFF", while web/scheduler.py read the same variable
with a default of "1" and treated anything but "0" as ON. render.yaml
never sets it. So on the live deployment — where it is unset — the
dashboard reported the engine idle every day while it scanned every
morning. Lying-screen class (audit B batch), found while auditing the
external review's F-012, which did not catch it.

Pinned here: unset means ON everywhere; the message appears only when the
scan is genuinely off, and then names the real cause; and no reader
re-derives the predicate from os.environ, which is how the three sites
drifted apart in the first place.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from fastapi.testclient import TestClient

from web import auth as auth_mod, scheduler
from web.main import app

_OFF_TEXT = "Daily auto-scan is OFF"


def _dashboard_html() -> str:
    """The rendered dashboard, proven to BE the dashboard.

    The first cut of this file fetched "/dashboard", which is a 404 — so
    the "banner is absent" assertion passed against an error page. A weak
    assertion is a fault mask (the Node 10 lesson); this asserts the page
    really rendered before any caller reads it.
    """
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200, r.status_code
    r = c.get("/")
    assert r.status_code == 200, r.status_code
    assert "Engine tweaks vs settings" in r.text or "tweaks" in r.text.lower()
    return r.text


def test_unset_means_on(monkeypatch):
    """The live deployment's actual state — render.yaml never sets it."""
    monkeypatch.delenv("DAILY_SCHEDULER", raising=False)
    assert scheduler.daily_enabled() is True


def test_zero_means_off_and_anything_else_means_on(monkeypatch):
    monkeypatch.setenv("DAILY_SCHEDULER", "0")
    assert scheduler.daily_enabled() is False
    monkeypatch.setenv("DAILY_SCHEDULER", "1")
    assert scheduler.daily_enabled() is True


def test_dashboard_does_not_claim_off_when_the_scan_is_running(monkeypatch):
    """THE BUG. Unset -> scanning daily -> the banner must be absent."""
    monkeypatch.delenv("DAILY_SCHEDULER", raising=False)
    body = _dashboard_html()
    assert _OFF_TEXT not in body


def test_dashboard_still_warns_when_the_scan_really_is_off(monkeypatch):
    """The message must not simply be deleted — it is right when it is right."""
    monkeypatch.setenv("DAILY_SCHEDULER", "0")
    body = _dashboard_html()
    assert _OFF_TEXT in body
    # and it must name the real cause, not the old backwards instruction
    assert "DAILY_SCHEDULER=0" in body


def test_no_reader_re_derives_the_predicate():
    """Three hand-written copies of one predicate is how this drifted. Every
    reader calls daily_enabled(); only daily_enabled() touches the env."""
    root = Path(__file__).resolve().parents[1]
    for rel in ("routes/dashboard.py", "scheduler.py"):
        src = (root / rel).read_text()
        body = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("#"))
        reads = body.count('environ.get("DAILY_SCHEDULER"')
        expected = 1 if rel == "scheduler.py" else 0
        assert reads == expected, f"{rel} has {reads} raw reads"
    # the one permitted read lives inside daily_enabled itself
    assert 'environ.get("DAILY_SCHEDULER", "1")' in inspect.getsource(
        scheduler.daily_enabled)


def test_module_docstring_no_longer_contradicts_the_code():
    """The header claimed "Disabled by default. Set DAILY_SCHEDULER=1 to
    switch on" while start_if_enabled's own docstring said the opposite."""
    # Read only the OPERATIVE text — the TRUTH-PASS note below it quotes the
    # old wrong wording on purpose, and an assertion that trips on its own
    # explanatory note is a bug I have now written three times.
    doc = (scheduler.__doc__ or "").split("TRUTH-PASS")[0]
    assert "Disabled by default" not in doc
    assert "ON by default" in doc
