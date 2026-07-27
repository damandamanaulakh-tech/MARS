"""Settings 1/0 toggles must store real booleans (2026-07-27 audit fix).

settings_save() coerced by inspecting the existing value's type. bool was
excluded from the int branch and is not a float, so it fell through to
`val = raw` and stored the STRING "0" — which is truthy. Entering 0 to switch
a flag OFF switched it ON. Live for pyramid_enabled; would have silently
defeated the new ocean_enabled park switch.
"""

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from web.routes.settings_routes import EDITABLE_FIELDS

BOOL_FIELDS = ["ocean_enabled", "pyramid_enabled"]


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _form(cfg, **overrides) -> dict:
    pct = {x[0] for x in EDITABLE_FIELDS if x[6]}
    form = {}
    for name, *_ in EDITABLE_FIELDS:
        v = getattr(cfg, name, None)
        if v is None:
            continue
        if isinstance(v, bool):
            form[name] = "1" if v else "0"
        elif name in pct:
            form[name] = round(v * 100, 4)
        else:
            form[name] = v
    form["mode"] = cfg.mode
    form.update(overrides)
    return form


def test_bool_fields_are_editable_from_settings():
    names = {n for n, *_ in EDITABLE_FIELDS}
    for f in BOOL_FIELDS:
        assert f in names, f"{f} is not exposed in Settings"


def test_toggle_off_stores_real_false_not_the_string_zero():
    c = _client()
    cfg = state.cfg()
    saved = {f: getattr(cfg, f) for f in BOOL_FIELDS}
    try:
        for f in BOOL_FIELDS:
            setattr(cfg, f, True)
        c.post("/settings", data=_form(cfg, **{f: "0" for f in BOOL_FIELDS}))

        cfg = state.cfg()
        for f in BOOL_FIELDS:
            val = getattr(cfg, f)
            assert val is False, f"{f} stored {val!r}, not False"
            assert not val, f"{f} is truthy after being switched off"
    finally:
        for f, v in saved.items():
            setattr(state.cfg(), f, v)


def test_toggle_on_stores_real_true():
    c = _client()
    cfg = state.cfg()
    saved = {f: getattr(cfg, f) for f in BOOL_FIELDS}
    try:
        for f in BOOL_FIELDS:
            setattr(cfg, f, False)
        c.post("/settings", data=_form(cfg, **{f: "1" for f in BOOL_FIELDS}))

        cfg = state.cfg()
        for f in BOOL_FIELDS:
            assert getattr(cfg, f) is True, f"{f} did not switch on"
    finally:
        for f, v in saved.items():
            setattr(state.cfg(), f, v)


def test_ocean_stays_parked_across_a_settings_save_that_does_not_touch_it():
    """Saving unrelated settings must not flip the park switch."""
    c = _client()
    cfg = state.cfg()
    saved = cfg.ocean_enabled
    try:
        cfg.ocean_enabled = False
        c.post("/settings", data=_form(cfg, min_score_to_enter=57))
        assert state.cfg().ocean_enabled is False
    finally:
        state.cfg().ocean_enabled = saved
