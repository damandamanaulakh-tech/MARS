"""Fail-closed production readiness (adopted 2026-08-29, owner order "go 1").

From the external AM07-vs-AshStocks review, whose two HIGH AM07 findings
were verified against this code before adopting:

  F-001  MONGO_URL absent -> the live paper book falls back to JSON under
         /tmp, which Render erases on restart. The pre-existing degraded
         halt could not catch it: db.mongo_expected() is bool(MONGO_URL),
         so "never configured" looked exactly like "JSON on purpose".
  F-002  OWNER_PWD and APP_PASSWORD both absent -> auth seeds the guessable
         password "sourceborn" and only logs an error.

Pinned here: production is Render (the RENDER marker the Secure-cookie flag
already uses) and nothing else; each failure names the variable to set; a
fallback value wearing a real setting's clothes still fails; the escape
hatch works and is never silent; dev and the test suite are untouched; and
readiness never leaks a secret's value into /healthz.
"""
from __future__ import annotations

import pytest

from web import readiness

_GOOD = {"MONGO_URL": "mongodb+srv://real", "APP_PASSWORD": "a-real-one",
         "SESSION_SECRET": "a-real-secret"}


def _prod(monkeypatch, **env):
    """A production boot with the given environment, nothing inherited."""
    for k in ("RENDER", "SB_REQUIRE_PROD", "SB_ALLOW_INSECURE", "MONGO_URL",
              "OWNER_PWD", "APP_PASSWORD", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RENDER", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_dev_and_tests_are_never_gated(monkeypatch):
    """The suite and a laptop must boot with nothing configured — this fix
    must not become a wall in front of local work."""
    for k in ("RENDER", "SB_REQUIRE_PROD", "MONGO_URL", "OWNER_PWD",
              "APP_PASSWORD", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert readiness.is_production() is False
    assert readiness.problems() == []
    readiness.assert_ready()          # must not raise


def test_render_boot_with_everything_set_is_ready(monkeypatch):
    _prod(monkeypatch, **_GOOD)
    assert readiness.is_production() is True
    assert readiness.problems() == []
    readiness.assert_ready()


def test_missing_mongo_refuses_to_start_and_says_why(monkeypatch):
    """F-001: the case the degraded-boot halt structurally could not see."""
    env = dict(_GOOD)
    env.pop("MONGO_URL")
    _prod(monkeypatch, **env)
    probs = readiness.problems()
    assert len(probs) == 1
    assert "MONGO_URL" in probs[0] and "/tmp" in probs[0]
    with pytest.raises(RuntimeError, match="not production-ready"):
        readiness.assert_ready()


def test_missing_password_refuses_to_start(monkeypatch):
    """F-002: a guessable password on a public URL."""
    env = dict(_GOOD)
    env.pop("APP_PASSWORD")
    _prod(monkeypatch, **env)
    probs = readiness.problems()
    assert len(probs) == 1
    assert "APP_PASSWORD" in probs[0]
    with pytest.raises(RuntimeError):
        readiness.assert_ready()


def test_owner_pwd_alone_satisfies_the_password_check(monkeypatch):
    """Both names work — OWNER_PWD wins in auth, APP_PASSWORD is the name
    the owner actually used in Render (2026-07-27)."""
    env = dict(_GOOD)
    env.pop("APP_PASSWORD")
    env["OWNER_PWD"] = "a-real-one"
    _prod(monkeypatch, **env)
    assert readiness.problems() == []


def test_the_old_builtin_fallbacks_are_rejected_even_when_set(monkeypatch):
    """Set-to-the-fallback is worse than unset: it looks configured."""
    _prod(monkeypatch, **{**_GOOD, "APP_PASSWORD": "sourceborn"})
    assert any("fallback" in p for p in readiness.problems())
    _prod(monkeypatch, **{**_GOOD,
                          "SESSION_SECRET": "dev-only-change-me-in-render-env"})
    assert any("placeholder" in p for p in readiness.problems())


def test_missing_session_secret_is_caught(monkeypatch):
    env = dict(_GOOD)
    env.pop("SESSION_SECRET")
    _prod(monkeypatch, **env)
    assert any("SESSION_SECRET" in p for p in readiness.problems())


def test_every_failure_is_reported_together_not_one_at_a_time(monkeypatch):
    """One deploy, one list — the owner should not fix, redeploy, discover
    the next one, three times over."""
    _prod(monkeypatch)
    assert len(readiness.problems()) == 3


def test_escape_hatch_starts_but_check_still_reports(monkeypatch):
    """SB_ALLOW_INSECURE=1 must start AND must not pretend it is fine."""
    _prod(monkeypatch, SB_ALLOW_INSECURE="1")
    assert readiness.problems()            # the truth is unchanged
    assert readiness.check() == []         # ...but the boot is allowed
    readiness.assert_ready()               # must not raise
    assert readiness.status()["insecure_override"] is True
    assert readiness.status()["ready"] is False


def test_sb_require_prod_forces_the_same_bar_off_render(monkeypatch):
    for k in ("RENDER", "MONGO_URL", "OWNER_PWD", "APP_PASSWORD",
              "SESSION_SECRET", "SB_ALLOW_INSECURE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SB_REQUIRE_PROD", "1")
    assert readiness.is_production() is True
    assert readiness.problems()


def test_status_never_leaks_a_secret_value(monkeypatch):
    _prod(monkeypatch, **{**_GOOD, "MONGO_URL": "mongodb+srv://u:pa55w0rd@h"})
    blob = repr(readiness.status())
    assert "pa55w0rd" not in blob and "a-real-secret" not in blob


def test_healthz_exposes_readiness(monkeypatch):
    """A misconfigured deploy must be visible from outside, not only in the
    Render log the owner would have to go looking for."""
    from fastapi.testclient import TestClient

    from web.main import app
    body = TestClient(app).get("/healthz").json()
    assert "readiness" in body
    assert set(body["readiness"]) == {
        "production", "ready", "insecure_override", "problems"}


def test_readiness_imports_nothing_heavy():
    """It must never be the reason a boot fails: env only, no engine, no db."""
    from pathlib import Path
    src = Path(readiness.__file__).read_text()
    body = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    for banned in ("import trading", "from trading", "from . import db",
                   "from .state", "pymongo"):
        assert banned not in body, banned
