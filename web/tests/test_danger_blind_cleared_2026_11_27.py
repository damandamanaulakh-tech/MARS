"""XR-060 on the screens — the Den's "the layer is blind" warning must clear
when the layer stops being blind, and must still appear if it ever is again.

The probe used to decide blindness from the RETURN TYPE of each tripwire.
The repair did not change those types — it changed how the layer reads them
— so a type-based probe would have kept the red warning on the page for ever
after the fault was gone. It now passes each answer through the production
reader instead.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from trading import danger
from web import auth as auth_mod
from web.main import app
from web.routes import den_routes as D


@pytest.fixture(autouse=True)
def _fresh_probe():
    D._BLIND = None
    yield
    D._BLIND = None


def _den() -> str:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303, "owner login failed"
    page = c.get("/den")
    assert page.status_code == 200
    assert "den_danger_marker" in page.text      # the section really rendered
    return page.text


def test_nothing_is_blind_now_and_every_price_tripwire_counts():
    out = D._danger_blind()
    assert out["blind"] == []
    assert len(out["countable"]) == len(danger._FNS)


def test_the_probe_still_catches_a_tripwire_the_layer_could_not_read(monkeypatch):
    """Proves the probe is not simply hard-wired to say 'fine'."""
    tid = sorted(danger._FNS)[0]
    patched = dict(danger._FNS)
    patched[tid] = lambda x: np.array([True, False])      # not a verdict
    monkeypatch.setattr(danger, "_FNS", patched)
    out = D._danger_blind()
    assert out["blind"] == [tid]
    assert tid not in out["countable"]


def test_an_abstaining_tripwire_is_not_called_blind(monkeypatch):
    tid = sorted(danger._FNS)[0]
    patched = dict(danger._FNS)
    patched[tid] = lambda x: None
    monkeypatch.setattr(danger, "_FNS", patched)
    out = D._danger_blind()
    assert tid in out["unknown"] and tid not in out["blind"]


def test_the_den_no_longer_prints_the_red_blind_warning():
    assert "data-den-danger-blind" not in _den()


def test_the_red_warning_returns_if_the_layer_goes_blind_again(monkeypatch):
    tid = sorted(danger._FNS)[0]
    patched = dict(danger._FNS)
    patched[tid] = lambda x: np.array([True, False])
    monkeypatch.setattr(danger, "_FNS", patched)
    assert "data-den-danger-blind" in _den()
