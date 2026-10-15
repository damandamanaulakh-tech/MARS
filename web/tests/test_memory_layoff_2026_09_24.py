"""Owner order 2026-09-24: "update scan to 1200 / also chk mongo / its
hitting 512 + / need to layoff unwanted".

MEASURED before anything was cut (see PROD_SOURCES in external_panels):
boot 114 MB · one 1,200-name scan +72 MB · trigger panels +41 MB resident ·
the DELIVERY stream **+153 MB peak**, daily AND on every /triggers page tap,
feeding a REJECTED trigger on data frozen at 2026-08-07. That stream is the
layoff. The two 512s (Render RAM, Atlas storage) and the size of the list
the scan really reads now sit on /healthz so nobody has to guess again.

Every source pin here strips comment lines first — the fault class that has
tripped this repo five times.
"""
from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from web.main import app


def _operative(src: str) -> str:
    """Source with comment lines removed — assert on code, never on the
    explanatory comment that quotes the old wording."""
    return "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))


# ---------------------------------------------------------------------------
# 1. the layoff: delivery out of every production path
# ---------------------------------------------------------------------------

def test_prod_sources_exclude_the_delivery_stream():
    from trading import external_panels as XP
    assert "delivery" not in XP.PROD_SOURCES
    # and nothing else was dropped by accident
    assert set(XP.PROD_SOURCES) == set(XP.PanelSet.ALL_SOURCES) - {"delivery"}


def test_prepare_honours_sources_and_reads_data_needed_not_false():
    """An unloaded source must read DATA_NEEDED (None), never a quiet False —
    the rule every external trigger already relies on."""
    from trading import external_panels as XP
    ed = XP.prepare(["TCS.NS"], sources=XP.PROD_SOURCES)
    x = ed.for_ticker("TCS.NS")
    assert x.delivery_ratio is None
    assert "delivery" in x.missing()
    assert "delivery" not in ed.sources or not ed.sources.get("delivery")


def test_prepare_default_is_unchanged_for_research_callers():
    """Calibration and study scripts never named their sources; the default
    must stay ALL_SOURCES so committed evidence still regenerates."""
    from trading import external_panels as XP
    sig = inspect.signature(XP.prepare)
    assert sig.parameters["sources"].default == XP.PanelSet.ALL_SOURCES


def test_the_daily_trigger_pass_passes_prod_sources():
    from web import scheduler
    src = _operative(inspect.getsource(scheduler.run_trigger_layer))
    assert "sources=external_panels.PROD_SOURCES" in src


def test_the_per_stock_trigger_page_passes_prod_sources():
    """One page tap used to stream the whole 3.5M-row file."""
    from web.routes import trigger_routes
    src = _operative(inspect.getsource(trigger_routes))
    i = src.find("external_panels.prepare(")
    assert i > 0
    assert "sources=external_panels.PROD_SOURCES" in src[i:i + 600]


def test_the_danger_layer_is_untouched():
    """It already loaded panel+index only (test_danger_layer pins that);
    this change must not have widened it. Pinned on the CALL, not on prose —
    its docstring legitimately mentions the delivery file it refuses."""
    from trading import danger
    src = _operative(inspect.getsource(danger.externals_for))
    assert 'sources=("panel", "index")' in src.replace("'", '"')


# ---------------------------------------------------------------------------
# 2. the 1,200: the owner's hand-ordered names survive the liquidity rebuild
# ---------------------------------------------------------------------------

def test_liquidity_rebuild_always_includes_the_owner_list(monkeypatch):
    """Two standing orders reconciled: the 2026-09-16 list (defence,
    shipbuilding, pharma) rides along whatever its turnover rank, exactly as
    held and watched names do. Behavioural, not a grep: capture what the
    selector was actually handed."""
    from trading.universes import OWNER_UNIVERSE_2026_08_20 as OWNER
    from web import db, scheduler, state

    seen = {}

    def fake_select(docs, target, blocklist, always_include):
        seen["always"] = set(always_include)
        # The store must rank at least one name of its OWN: the empty-store
        # guard (PR #112) reads picked minus always_include, and a fake that
        # returns only always-include names trips it — this test then passed
        # only by inheriting an earlier test's universe_batch. Found 2026-09-24
        # when it ran alone.
        picked = sorted(set(always_include))[:5] + ["STORE1.NS", "STORE2.NS"]
        return {"tickers": picked, "sample": 7, "judged": 7, "short_by": 0,
                "skipped_thin": 0, "skipped_illiquid": 0,
                "held_added": [], "ranked": []}

    monkeypatch.setattr("trading.universe_select.select_liquid_universe",
                        fake_select)
    # bootstrap BEFORE patching db — state.cfg() runs the boot migrations,
    # which call save_settings(..., doc_id=...) and must see the real one
    cfg = state.cfg()
    state.portfolio()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "history_tails", lambda n: [])
    monkeypatch.setattr(db, "save_settings", lambda d, **kw: None)
    old = cfg.custom_universe
    try:
        scheduler.refresh_liquid_universe("test")
        assert set(OWNER) <= seen["always"], \
            "the hand-ordered list must be in always_include"
        batch = state.portfolio().universe_batch
        assert "owner_list_kept" in batch and "owner_list_carried" in batch
    finally:
        cfg.custom_universe = old


def test_daily_topup_docstring_no_longer_claims_fifty():
    """The "~50 names" claim became false the day the rebuild started
    writing 1,200 names into custom_universe.

    The docstring's own truth-pass note QUOTES the old wording, so this
    asserts only on the operative paragraph before the "SCALE" marker —
    the fault class that has tripped this repo's docstring pins before."""
    from web import scheduler
    doc = inspect.getdoc(scheduler._daily_universe_topup) or ""
    operative = doc.split("SCALE, truth-passed")[0]
    assert "~50" not in operative
    assert "1,200" in doc and "load-bearing" in doc


# ---------------------------------------------------------------------------
# 3. the check: both 512s and the real scan size are visible without login
# ---------------------------------------------------------------------------

def test_healthz_reports_memory_scan_universe_and_mongo():
    body = TestClient(app).get("/healthz").json()
    assert "memory" in body and "scan_universe" in body and "mongo" in body
    mem = body["memory"]
    assert mem and mem["peak_mb"] > 0
    su = body["scan_universe"]
    assert su and su["count"] > 0
    assert su["target"] == 0 or su["target"] >= su["count"]
    # Same unit (MiB): a KiB/MiB mix-up is a factor of 1024. The first cut
    # asserted |rss - peak| < 200, which compares the CURRENT size with the
    # process's LIFETIME peak — a test process that once peaked high and
    # later freed memory (CI 2026-10-15: rss 300, peak 631) failed it with
    # both numbers in MiB. Pin the unit, not the gap: rss can never exceed
    # the peak (allowing page-rounding), and a 1024x mix-up cannot pass.
    if mem["rss_mb"] is not None:
        assert mem["rss_mb"] <= mem["peak_mb"] + 8, "rss above the peak: units differ"
        assert mem["peak_mb"] / max(mem["rss_mb"], 1.0) < 64, \
            "peak more than 64x rss: a KiB read as MiB"
    # keys present even when Mongo is offline (the test box) — None, not absent
    assert body["mongo"] is None or "data_mb" in body["mongo"]


def test_storage_stats_is_read_only_and_never_raises(monkeypatch):
    from web import db
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.storage_stats() is None
    src = _operative(inspect.getsource(db.storage_stats))
    for verb in ("insert", "update", "replace", "delete", "drop"):
        assert verb not in src, f"storage_stats must never {verb}"


def test_healthz_leaks_no_values():
    """Sizes and counts only. A connection string or a password must never
    appear — the readiness block already keeps that promise."""
    import json
    body = json.dumps(TestClient(app).get("/healthz").json()).lower()
    for bad in ("mongodb+srv", "mongodb://", "password", "secret"):
        assert bad not in body, f"/healthz must not carry {bad!r}"
