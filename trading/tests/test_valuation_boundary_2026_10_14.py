"""Valuation chain (sequence stages 07-18) — the boundary that keeps it
DISPLAY ONLY (owner order 2026-10-14 "lets hit the formulas work
completion"; money impact stated as NONE before the build).

Pins:
  * no money-path module (engine, risk, orders, danger, sizing, targets,
    exits, scoring) references the valuation package;
  * every module in trading/valuation/ is pure: no network client, no
    web import, no engine/risk/orders/danger import, no config mutation;
  * the package's DATA_NEEDED shape is the one every module uses.
Comment lines and docstrings are stripped before the source is read — a
pin that trips on its own explanatory comment is the documented
fault-mask class (F1 test, DAILY_SCHEDULER guard, …).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "trading" / "valuation"

MONEY_PATH = ("engine.py", "risk.py", "orders.py", "danger.py", "sizing.py",
              "targets.py", "exits.py", "scoring.py")

_BANNED_IN_PACKAGE = (
    r"\bimport\s+httpx\b", r"\bimport\s+requests\b", r"\bimport\s+urllib\b",
    r"\bimport\s+yfinance\b", r"\bfrom\s+web\b", r"\bimport\s+web\b",
    r"\bfrom\s+trading\.engine\b", r"\bfrom\s+trading\.risk\b",
    r"\bfrom\s+trading\.orders\b", r"\bfrom\s+trading\.danger\b",
    r"\bfrom\s+trading\s+import\s+engine\b", r"\bfrom\s+\.\.\s*import\s+engine\b",
    r"place_order", r"cfg\.mode\s*=", r"apply_mode_preset",
)


def _operative_source(path: Path) -> str:
    """Source with comment lines and docstrings removed."""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:            # a broken module is a failure of its own
        return text
    doc_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(getattr(body[0], "value", None), ast.Constant) and \
                    isinstance(body[0].value.value, str):
                doc_spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    lines = text.splitlines()
    keep = []
    for i, line in enumerate(lines, start=1):
        if any(a <= i <= b for a, b in doc_spans):
            continue
        if line.strip().startswith("#"):
            continue
        keep.append(line.split("  #")[0])
    return "\n".join(keep)


def test_money_path_never_references_the_valuation_package():
    for name in MONEY_PATH:
        src = _operative_source(ROOT / "trading" / name)
        assert "valuation" not in src, f"trading/{name} reaches the valuation chain"


def test_every_valuation_module_is_pure():
    files = sorted(PKG.glob("*.py"))
    assert files, "trading/valuation/ has no modules"
    for f in files:
        src = _operative_source(f)
        for pat in _BANNED_IN_PACKAGE:
            assert not re.search(pat, src), f"{f.name}: forbidden reference {pat!r}"


def test_the_data_needed_shape_is_shared():
    from trading import valuation as v
    d = v.dn("no statements")
    assert d == {"status": "DATA_NEEDED", "reason": "no statements"}
    assert v.is_dn(d) and not v.is_dn({"status": "OK"}) and not v.is_dn(1.0)
    assert v.num(d) is None and v.num("x") is None and v.num(float("nan")) is None
    assert v.num("12.5") == 12.5
    assert len(v.SNAPSHOT_FIELDS) == len(set(v.SNAPSHOT_FIELDS))
