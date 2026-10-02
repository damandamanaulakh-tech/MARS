"""Owner order 2026-10-02 ("go for next"): audit file rows R-2..R-11.

R-2  scripts/breadth_gate_study.py could not be parsed (SyntaxError).
R-3  scripts/breadth_gate_recut.py read a deleted config field and a
     hard-coded home path.
R-4  four byte-identical "(1)" copies of raw NSE files.
R-5  a Nifty 200 snapshot byte-identical to the one before it.
R-6  a Procfile nothing reads (render.yaml starts the app).
R-9  superseded status boards moved to docs/archive/.
R-10 functions nothing called, and register labels that named helpers no
     screen ever runs.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _code(path: Path) -> str:
    """Source with comments and docstrings removed (the source-pin rule)."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body[0] = ast.Pass()
    return ast.unparse(tree)


# ------------------------------------------------------------- R-2 / R-3

SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_script_parses(path):
    ast.parse(path.read_text(), filename=str(path))


def test_no_script_reads_the_deleted_breadth_field():
    for path in SCRIPTS:
        assert "breadth_min_pct" not in _code(path), path.name


def test_no_script_hard_codes_a_home_path():
    for path in SCRIPTS:
        assert "/home/user" not in _code(path), path.name


# ------------------------------------------------------------- R-4 / R-5 / R-6

def test_the_duplicate_files_are_gone_and_the_originals_stay():
    raw = ROOT / "data" / "history_15stock_raw"
    for name in ("BAJAJFINSV", "BHARTIARTL", "DRREDDY", "MARUTI"):
        assert not [p for p in raw.glob(f"*{name}*(1).csv")], name
        assert [p for p in raw.glob(f"*{name}*.csv")], name
    # the RELIANCE / SBIN "(1)" files are the only copies — they stay
    for name in ("RELIANCE", "SBIN"):
        assert [p for p in raw.glob(f"*{name}*(1).csv")], name
    snaps = ROOT / "data" / "universe_snapshots"
    assert not (snaps / "ind_nifty200list_2018-01-13.csv").exists()
    assert (snaps / "ind_nifty200list_2017-11-13.csv").exists()
    assert not (ROOT / "Procfile").exists()
    assert "uvicorn web.main:app" in (ROOT / "render.yaml").read_text()


# ------------------------------------------------------------- R-9

def test_the_archived_boards_live_in_the_archive():
    docs = ROOT / "docs"
    for name in ("STATUS_BOARD.md", "WORK_LIST.md", "APP_REVIEW.md"):
        assert not (docs / name).exists(), name
        assert (docs / "archive" / name).exists(), name
    assert (docs / "archive" / "README.md").exists()


# ------------------------------------------------------------- R-10

DELETED = {
    "trading.alerts": ("send_email", "notify_kill_switch"),
    "trading.data": ("fetch_quote", "fetch_market_cap_cr"),
    "trading.external_panels": ("reset_caches",),
    "trading.news_feed": ("_accept_phrases",),
    "web.auth": ("require_owner",),
    "web.market_gate": ("reason_text",),
    "web.routes.register_routes": ("_group_of",),
}


@pytest.mark.parametrize("module", sorted(DELETED))
def test_the_unused_functions_stay_deleted(module):
    mod = importlib.import_module(module)
    for name in DELETED[module]:
        assert not hasattr(mod, name), f"{module}.{name}"


def test_the_kept_neighbours_still_work():
    from trading import alerts, data, news_feed
    from web import market_gate
    assert callable(alerts.send_slack) and callable(alerts.alerts_status)
    assert callable(data.fetch_quotes)
    assert news_feed.mentions_company("TCS shares rise", "TCS.NS")
    assert callable(market_gate.blocked_redirect)


def _production_sources():
    for top in ("trading", "web"):
        for p in (ROOT / top).rglob("*.py"):
            if "tests" in p.parts:
                continue
            yield p


def test_a_register_label_that_says_library_only_is_true():
    """If /register says a helper is library-only, no production module may
    import it; and the one it calls live (adx) must really be imported."""
    from trading.register_v4 import AM07_HAVE
    imported = set()
    for p in _production_sources():
        code = _code(p)
        for m in re.finditer(r"from \.?\.?signals\.technicals import ([\w, ]+)"
                             r"|from trading\.signals\.technicals import ([\w, ]+)",
                             code):
            names = (m.group(1) or m.group(2) or "").split(",")
            imported |= {n.strip().split(" as ")[0] for n in names if n.strip()}
    assert "adx" in imported
    for rid, label in AM07_HAVE.items():
        if "library helper only" not in label:
            continue
        for helper in re.findall(r"\b(sma|ema|ma_stack_score|rsi|macd|"
                                 r"beta_vs_benchmark|bollinger\w*)\b", label):
            assert helper not in imported, (rid, helper)


def test_the_register_no_longer_calls_ocean_parked():
    from trading.register_v4 import AM07_HAVE
    assert "parked" not in AM07_HAVE["H-23"].lower()
