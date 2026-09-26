"""Owner 2026-09-26, from Render's Events tab: every deploy since PR #116
"failed", and the log ended with

    INFO: Uvicorn running on http://0.0.0.0:10000
    INFO: 127.0.0.1:49914 - "HEAD / HTTP/1.1" 405 Method Not Allowed

Render probes the freshly booted process with a HEAD request. A FastAPI
GET route answers HEAD with 405, so the probe failed, the deploy was marked
failed and the OLD build stayed serving — three merged fixes never reached
the live app, and from outside it looked like "Render is not deploying".

Pinned: HEAD / and HEAD /healthz answer 200 (no body); GET behaviour is
unchanged; render.yaml keeps /healthz as the health check path.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from web.main import app


def test_head_root_answers_200_for_the_deploy_probe():
    r = TestClient(app).head("/")
    assert r.status_code == 200
    assert r.content == b""


def test_head_healthz_answers_200():
    r = TestClient(app).head("/healthz")
    assert r.status_code == 200


def test_get_root_still_redirects_to_login():
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/login")


def test_get_healthz_unchanged():
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_render_health_check_path_stays_healthz():
    root = Path(__file__).resolve().parents[2]
    assert "healthCheckPath: /healthz" in (root / "render.yaml").read_text()
