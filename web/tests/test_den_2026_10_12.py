"""🧪 MARS Den (owner order 2026-10-12): every formula on one page, the algo
half of Settings moved here, and a sandbox that never touches the engine.

Owner's words: "Instead of having universe lens, trigger, weight, selection
register, Engine regime can be merged in one section named as MARS Den, where
most of the formulas must be visible and replica" · "setting will only have
the APP SETTINGS, NOT THE ALGO, that will move to MARS Den" · "a special tab
in left where I have all formulas, but editable which will not change the app
formula but assist for correction at that time and have option to add any
share over there".

What these tests pin:
  * /den renders for the owner, every section anchor is on it;
  * the numbers it prints ARE the code's numbers (floor, ATR stop k and cap,
    target k per category, the inline literals read out of the functions);
  * /settings keeps the app items only, /den carries the algo form with the
    SAME action and field names the settings POST reads;
  * a sandbox POST stores a draft and leaves the live Config byte-identical;
  * viewers and non-owner roles can never write a draft;
  * no money-path module imports the Den.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trading.modes import PRESETS
from web import auth as auth_mod, state
from web.main import app
from web.routes import den_routes as D

# main.py registers the Den router (item 23). Belt and braces: if a checkout
# runs this file before that line lands, register it here so the suite does
# not report a 404 as a formula fault.
if not any(getattr(r, "path", "") == "/den" for r in app.routes):  # pragma: no cover
    app.include_router(D.router)

REPO = Path(__file__).resolve().parents[2]


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303, "owner login failed"
    return c


def _as_role(monkeypatch, role: str) -> TestClient:
    email = f"{role}@sourceborn.in"
    monkeypatch.setitem(auth_mod.SEEDED_USERS, email,
                        {"role": role, "password": "r-test", "name": role})
    c = TestClient(app)
    c.post("/login", data={"email": email, "password": "r-test"})
    return c


def _den(c: TestClient | None = None) -> str:
    r = (c or _owner()).get("/den")
    assert r.status_code == 200, r.status_code
    # The page is real, not an error body that happens to be 200.
    assert "den_nav_marker" in r.text and "den_engines_marker" in r.text
    return r.text


@pytest.fixture
def clean_draft(monkeypatch):
    """No stored draft before or after; Mongo writes captured, never sent."""
    calls: list[tuple] = []
    monkeypatch.setattr(D.db, "save_settings",
                        lambda d, doc_id="default": calls.append(("save", doc_id, dict(d))))
    monkeypatch.setattr(D.db, "unset_settings_keys",
                        lambda keys, doc_id="default": calls.append(("unset", doc_id, list(keys))))
    monkeypatch.setattr(D.db, "load_settings", lambda doc_id="default": {})
    D.LAST_DRAFT.clear()
    yield calls
    D.LAST_DRAFT.clear()


# ------------------------------------------------------------------ page

def test_den_renders_for_the_owner_and_redirects_a_stranger():
    assert "MARS Den" in _den()
    r = TestClient(app).get("/den", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_every_section_anchor_is_on_the_page():
    html = _den()
    ids = [sid for sid, _ in D.SECTIONS]
    # "valuation" = the context stage, owner order 2026-10-12 ("both,
    # indian sector only, discount rate - go").
    # "chain" = stages 07-18 as code, owner order 2026-10-14 ("lets hit the
    # formulas work completion").
    # "formulas" = every formula printed + every ASSUMED number editable as
    # a draft, owner order 2026-10-14 ("put the formulas in the den now …
    # need editable as said in 40+ points").
    assert ids == ["sequence", "engines", "weights", "triggers", "timing",
                   "capacity", "register", "evidence", "algo-settings",
                   "sandbox", "valuation", "chain", "formulas"]
    for sid in ids:
        assert f'id="{sid}"' in html, sid
        assert f'href="#{sid}"' in html, sid          # the chip row links it


def test_the_sequence_is_the_owners_25_codes_and_dated():
    seq = D._sequence()
    codes = [r["code"] for r in seq["rows"]]
    # The owner's 25 codes, in order, plus the ONE stage they ordered on
    # 2026-10-12 — the market/sector/rates/sentiment context, which sits
    # right after the justified range (17) and before selection (18).
    assert [c for c in codes if c != "17b"] == [f"{i:02d}" for i in range(25)]
    assert codes[codes.index("17") + 1] == "17b"
    assert {r["status"] for r in seq["rows"]} <= {"exists", "partly", "not yet"}
    assert seq["exists"] + seq["partly"] + seq["not_yet"] == len(codes) == 26
    # a "partly"/"exists" stage must say where it lives — never a bare claim
    assert all(r["where"] for r in seq["rows"] if r["status"] != "not yet")
    assert f"status as of <strong>{D.SEQUENCE_STATUS_ASOF}</strong>" in _den()


def test_every_table_scrolls_inside_its_card_at_phone_width():
    html = _den()
    starts = [m.start() for m in re.finditer(r"<table\b", html)]
    assert starts
    for s in starts:
        assert "overflow-x-auto" in html[max(0, s - 400):s], html[s - 200:s + 80]


# ------------------------------------------------------- live numbers

def test_everest_floor_printed_is_the_presets_floor():
    cfg = state.cfg()
    saved = dict(getattr(cfg, "preset_overrides", None) or {})
    cfg.preset_overrides = {k: v for k, v in saved.items()
                            if k != "nifty200_momentum"}
    try:
        html = _den()
        m = re.search(r'data-den-floor="nifty200_momentum">([\d.]+)', html)
        assert m, "momentum floor cell missing"
        assert float(m.group(1)) == PRESETS["nifty200_momentum"].min_score_to_enter
        for mode, pr in PRESETS.items():
            m = re.search(rf'data-den-floor="{mode}">([\d.]+)', html)
            if m:                                    # every category shown
                assert float(m.group(1)) == pr.min_score_to_enter, mode
    finally:
        cfg.preset_overrides = saved


def test_stop_and_target_constants_printed_are_the_targets_module():
    from trading import targets as T
    cfg = state.cfg()
    old = getattr(cfg, "atr_stop_enabled", True)
    cfg.atr_stop_enabled = True
    try:
        html = _den()
    finally:
        cfg.atr_stop_enabled = old
    assert f'data-den-stop-k="{T.ATR_STOP_K}"' in html
    assert f'data-den-stop-cap="{round(T.ATR_STOP_CAP * 100, 1)}"' in html
    assert f"{T.ATR_STOP_K} × ATR{T.ATR_WINDOW}" in html
    for mode, k in T.ATR_TARGET_K.items():
        m = re.search(rf'data-den-target-k="{mode}">([\d.]+)×', html)
        assert m and float(m.group(1)) == k, mode
    assert f"median ATR = {T.MEDIAN_ATR_PCT_OF_PRICE}% of price" in html


def test_the_inline_literals_are_read_from_the_code_not_typed():
    """The Den reads a few rules out of function source (they are literals,
    not constants). If one of those lines moves, the reader returns None and
    the page prints DATA_NEEDED — this test turns that into a red suite
    instead of a quiet DATA_NEEDED on the owner's screen."""
    from trading import lens_v2, sizing
    from trading.strategies.nifty200_momentum import momentum_score
    mf = D._momentum_formula()
    assert (mf["center"], mf["slope"], mf["min_bars"]) == (50.0, 25.0, 252)
    # the bar count read is the function's real cut-off
    walk = _walk(n=mf["min_bars"])["Close"]
    assert momentum_score(walk) is not None
    assert momentum_score(walk.iloc[1:]) is None
    z = D._z_divisor()
    assert z == 30.0
    mu, sig = sizing.edge_estimate(62.0 + z, pd.Series(
        100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 300)))),
        ic=0.05, floor=62.0)
    assert abs(mu - 0.05 * sig * 1.0) < 1e-12     # one z-unit = (floor + divisor)
    cap = D._capacity(state.cfg())
    assert cap["sigma_days"] and cap["min_bars"] == 60 and cap["trap_flags"] == 2
    assert lens_v2.is_trap(["a"] * cap["trap_flags"])
    assert not lens_v2.is_trap(["a"] * (cap["trap_flags"] - 1))
    oc = D._ocean_block(state.cfg())
    for k in ("below_200dma_pct", "weekly_rsi_max", "climax_ratio_min", "de_max"):
        assert oc[k] is not None, k
    assert D._register(state.portfolio())["precompute_min_names"]
    assert D._market_hours()


def test_ocean_card_prints_the_live_ticket_and_all_four_rails():
    cfg = state.cfg()
    html = _den()
    from web.templating import templates
    inr = templates.env.filters["inr"]
    assert f"₹<strong>{inr(cfg.ocean_position_value)}</strong>" in html
    for rid in ("R1", "R2", "R3", "R4"):
        assert f"<strong>{rid} " in html, rid


def test_weights_section_prints_each_categorys_own_weights():
    from trading.scoring import COMPOSITE_MODES, weights_for
    html = _den()
    for m in COMPOSITE_MODES:
        w = weights_for(m)
        assert abs(sum(w.values()) * 100 - next(
            t["total_pct"] for t in D._weights(state.cfg())["tables"]
            if t["mode"] == m)) < 0.2
        top = max(w, key=w.get)
        assert f"{round(w[top] * 100, 1)}%" in html, (m, top)


def test_capacity_card_states_the_real_impact_law():
    """The first draft said impact ∝ (notional/ADV)^Y — wrong: lens_v2's
    impact_bps is Y × σ_daily × √(Q/ADV) × 1e4. Y is the coefficient."""
    from trading import lens_v2
    html = _den()
    assert "√(notional / ADV)" in html
    assert f"Y = <strong>{lens_v2.IMPACT_Y}</strong>" in html
    assert "<sup>" not in html.split('id="capacity"')[1].split("</section>")[0]


def test_register_tile_never_counts_the_fallback_reasons_as_missing():
    """register_v4._STOCK_DATA_NEEDED is applied with setdefault AFTER the
    wiring — a fallback per symbol, not a list of missing feeds. The live
    count comes from the nightly precompute's own row states."""
    p = state.portfolio()
    old = getattr(p, "register_market", None)
    p.register_market = {"asof": "2026-10-09", "sample": 1205,
                         "rows": {"PX-01": {"state": "VALUE"},
                                  "FII-04": {"state": "QUIET"},
                                  "W-026": {"state": "DATA_NEEDED"}}}
    try:
        reg = D._register(p)
        assert reg["precompute_data_needed"] == 1
        html = _den()
        block = html.split("den_register_live_marker")[1][:600]
        assert ">1<" in block and "of 3 in the nightly precompute" in block
    finally:
        p.register_market = old


def test_evidence_moved_from_the_dashboard_renders_here():
    html = _den()
    sec = html.split('id="evidence"')[1].split("</section>")[0]
    assert ("den_walk_forward_marker" in sec
            or "DATA_NEEDED (docs/param_search_v3_results.json)" in sec)
    assert "den_horizons_marker" in sec


# ------------------------------------------------- settings split

def test_settings_keeps_app_items_and_the_algo_form_moved_to_den():
    c = _owner()
    settings = c.get("/settings").text
    den = _den(c)
    for field in ('name="stop_loss_pct"', 'name="custom_universe"',
                  'name="min_score_to_enter"'):
        assert field not in settings, field
        assert field in den, field
    assert "Phone alerts" in settings and "Storage" in settings
    assert 'href="/den#algo-settings"' in settings


def test_den_algo_form_posts_to_settings_with_every_editable_field():
    """Same action, same field names: the settings POST handler is untouched."""
    from web.routes.settings_routes import EDITABLE_FIELDS
    html = _den()
    form = html.split("settings_algo_form_marker")[0].rsplit("<form", 1)[1]
    assert 'action="/settings"' in form and 'method="post"' in form
    body = html.split("settings_algo_form_marker")[1].split("</form>")[0]
    names = set(re.findall(r'name="([a-z_0-9]+)"', body))
    for n, *_ in EDITABLE_FIELDS:
        assert n in names, n
    assert {"mode", "custom_universe"} <= names


# ----------------------------------------------------------- sandbox

def test_sandbox_post_stores_a_draft_and_leaves_the_config_unchanged(clean_draft):
    cfg = state.cfg()
    before = dataclasses.asdict(cfg)
    floor_live = D._live_draft(cfg)["floor"]
    c = _owner()
    r = c.post("/den/sandbox", data={
        "floor": str(floor_live + 8), "blend_pct": "10", "target_k": "11",
        "symbols": "tcs, RELIANCE.NS junk!! tcs"}, follow_redirects=False)
    assert r.status_code == 303 and "draft=saved" in r.headers["location"]
    assert dataclasses.asdict(state.cfg()) == before          # untouched
    assert state.cfg() is cfg
    saves = [c for c in clean_draft if c[0] == "save"]
    assert saves and saves[-1][1] == D.DRAFT_DOC_ID
    d = D.LAST_DRAFT
    assert d["floor"] == floor_live + 8 and d["blend_pct"] == 10.0
    assert d["symbols"] == "TCS.NS RELIANCE.NS"               # deduped, junk dropped
    # a blank box falls back to the LIVE value, never to a guess
    assert d["slab"] == D._live_draft(cfg)["slab"]
    html = c.get(r.headers["location"]).text                 # where the owner lands
    assert "Draft stored" in html and "Draft differs from live on" in html
    changed = html.split("Draft differs from live on")[1][:200]
    assert "floor" in changed and "blend_pct" in changed and "slab" not in changed
    assert "Sandbox — nothing here changes the live engine" in html


def test_sandbox_clear_forgets_the_draft(clean_draft):
    c = _owner()
    c.post("/den/sandbox", data={"floor": "70"})
    assert D.LAST_DRAFT
    r = c.post("/den/sandbox/clear", follow_redirects=False)
    assert "draft=cleared" in r.headers["location"]
    assert not D.LAST_DRAFT
    unset = [x for x in clean_draft if x[0] == "unset"]
    assert unset and unset[-1][1] == D.DRAFT_DOC_ID and "floor" in unset[-1][2]
    assert not D._sandbox(state.cfg(), state.portfolio(), None)["has_draft"]


def test_a_non_owner_can_never_store_a_draft(monkeypatch, clean_draft):
    staff = _as_role(monkeypatch, "staff")
    r = staff.post("/den/sandbox", data={"floor": "99"}, follow_redirects=False)
    assert "draft=denied" in r.headers["location"]
    assert not D.LAST_DRAFT and not clean_draft
    viewer = _as_role(monkeypatch, "viewer")
    assert viewer.get("/den").status_code == 403         # main.py viewer guard
    assert viewer.post("/den/sandbox", data={"floor": "99"}).status_code == 403
    assert not D.LAST_DRAFT


def _walk(n=320, seed=7, drift=0.0015):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    idx = pd.bdate_range("2025-06-02", periods=n)
    return pd.DataFrame({"Open": close * 0.995, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close,
                         "Volume": rng.integers(2e5, 9e5, n).astype(float)},
                        index=idx)


def test_sandbox_readings_are_the_pure_functions_on_stored_history(monkeypatch):
    """Each reading is the engine's own scorer, stop, target and sizing math
    on a COPY of the config with the draft set; no stored history = DATA_NEEDED."""
    from trading import targets as T
    from trading.quality import blend_scores, quality_score
    from trading.sizing import position_notional_for
    from trading.strategies.nifty200_momentum import momentum_score
    df = _walk()
    monkeypatch.setattr("web.candle_chart.load_ohlcv",
                        lambda t, tail=420: df if t == "GOOD.NS" else None)
    cfg = state.cfg()
    before = dataclasses.asdict(cfg)
    draft = dict(D._live_draft(cfg), floor=50.0, blend_pct=20.0,
                 atr_stop_k=3.0, target_k=6.0, symbols="GOOD NOPE")
    rows = D._sandbox_readings(cfg, state.portfolio(), draft)
    assert dataclasses.asdict(cfg) == before
    good = next(r for r in rows if r["ticker"] == "GOOD.NS")
    nope = next(r for r in rows if r["ticker"] == "NOPE.NS")
    assert nope["status"] == "DATA_NEEDED" and "no stored history" in nope["why"]
    close = df["Close"]
    ms = momentum_score(close)
    q = quality_score(close, df["Volume"])
    assert good["status"] == "OK"
    assert good["momentum"] == round(ms, 1)
    assert good["draft_blend"] == blend_scores(ms, q, 0.20)
    assert good["draft_pass"] == (good["draft_blend"] >= 50.0)
    last = float(close.iloc[-1])
    sp, _ = T.stop_pct_for("nifty200_momentum", last, df,
                           -abs(draft["stop_pct"]) / 100, enabled=True, k=3.0,
                           cap=draft["atr_stop_cap_pct"] / 100)
    assert good["stop_pct"] == round(abs(sp) * 100, 2)
    lo, hi = T.TARGET_BAND["nifty200_momentum"]
    tgt = max(lo, min(hi, 6.0 * T.atr(df) / last))
    assert good["target_pct"] == round(tgt * 100, 2)
    n, _ = position_notional_for(D._draft_cfg(cfg, draft),
                                 float(state.portfolio().equity(
                                     D._entry_prices(state.portfolio()))),
                                 good["draft_blend"], close)
    assert good["notional"] == round(n)


def test_sandbox_book_reads_breadth_and_shares_from_stored_state():
    cfg = state.cfg()
    p = state.portfolio()
    old = (getattr(p, "breadth_store", None), getattr(p, "breadth_today", None))
    from datetime import date
    p.breadth_store = {"pct": 0.50, "sample": 2000,
                       "asof": date.today().isoformat()}
    try:
        live = D._live_draft(cfg)
        draft = dict(live, breadth_low=0.10, breadth_high=0.20)
        bk = D._sandbox_book(cfg, p, draft)
        assert bk["breadth_pct"] == 50.0
        if cfg.breadth_gate_enabled and live["breadth_low"] <= 0.5 < live["breadth_high"]:
            assert bk["live_blocks"] is True
        assert bk["draft_blocks"] is False         # 50% is above a 10-20% band
        if bk["shares_on"]:
            assert [s["label"] for s in bk["shares"]][0] == "Momentum"
    finally:
        p.breadth_store, p.breadth_today = old


# ---------------------------------------------------------- boundary

def test_no_money_path_module_imports_the_den():
    for mod in ("trading/engine.py", "trading/risk.py", "trading/orders.py",
                "trading/danger.py", "web/scheduler.py"):
        src = "\n".join(l for l in (REPO / mod).read_text().splitlines()
                        if not l.lstrip().startswith("#"))
        assert "den_routes" not in src, mod


def test_the_den_never_writes_the_live_config():
    """No assignment to the live cfg anywhere in the Den — draft fields are
    only ever set on copy.copy(cfg)."""
    src = (REPO / "web/routes/den_routes.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"\bcfg\.[a-z_]+\s*=(?!=)", code)
    assert "save_settings(" in code and "doc_id=DRAFT_DOC_ID" in code
    assert re.search(r'DRAFT_DOC_ID\s*=\s*"formula_drafts"', code)
