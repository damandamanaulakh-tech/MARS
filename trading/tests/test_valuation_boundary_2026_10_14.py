"""Valuation chain (sequence stages 07-18) — the boundary that keeps it
DISPLAY ONLY (owner order 2026-10-14 "lets hit the formulas work
completion"; money impact stated as NONE before the build).

OWNER ORDER 2026-10-21 (item 19, "Wire A3" + "Exit as tested", after the
pre-registered test): the money path MAY now import ONE module,
trading/valuation_exit.py — the exit-target rule, which reads records the
web side precomputes. It still never imports the valuation pipeline
(trading/valuation/), the context stage or web; and valuation_exit.py
itself is held to the same purity sweep as the package.

Pins:
  * no money-path module (engine, risk, orders, danger, sizing, targets,
    exits, scoring) imports the valuation package, valuation_context or
    web — trading.valuation_exit is the one allowed import;
  * every module in trading/valuation/ is pure: no network client, no
    web import, no engine/risk/orders/danger import, no config mutation;
    trading/valuation_exit.py likewise, and it never imports the pipeline;
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


ALLOWED = "trading.valuation_exit"     # owner order 2026-10-21 (item 19)


def _imports(path: Path) -> list[str]:
    """Every module name a file imports, relative imports resolved
    against the trading package ("from . import x" -> trading.x)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = "trading" + (f".{base}" if base else "")
            out.append(base)
            out += [f"{base}.{a.name}" for a in node.names]
    return out


def _forbidden(mod: str) -> bool:
    if mod == ALLOWED or mod.startswith(ALLOWED + "."):
        return False
    return (mod.startswith("trading.valuation") or mod == "web"
            or mod.startswith("web."))


def test_money_path_never_imports_the_valuation_chain_or_web():
    for name in MONEY_PATH:
        bad = [m for m in _imports(ROOT / "trading" / name) if _forbidden(m)]
        assert not bad, f"trading/{name} imports {bad}"
        src = _operative_source(ROOT / "trading" / name)
        for pat in (r"trading\.valuation(?!_exit)", r"\bvaluation_context\b",
                    r"\bvaluation_view\b", r"\bvaluation_chain_view\b",
                    r"\bvaluation\.pipeline\b", r"import_module"):
            assert not re.search(pat, src), f"trading/{name}: {pat!r}"


def test_the_one_allowed_import_is_the_exit_rule():
    """The engine reaches valuation only through valuation_exit (the order
    moved the boundary on purpose — this pin says so)."""
    mods = _imports(ROOT / "trading" / "engine.py")
    assert ALLOWED in mods
    assert not any(_forbidden(m) for m in mods)


def test_the_exit_rule_module_is_pure():
    f = ROOT / "trading" / "valuation_exit.py"
    src = _operative_source(f)
    for pat in _BANNED_IN_PACKAGE:
        assert not re.search(pat, src), f"valuation_exit.py: forbidden {pat!r}"
    bad = [m for m in _imports(f)
           if m.startswith("trading.valuation.") or m == "trading.valuation"
           or m.startswith("web") or m.startswith("trading.engine")]
    assert not bad, bad
    assert not re.search(r"\bpipeline\b", src)


#: Display-only feed modules outside the package (VALFIX 2026-10-21): the
#: money path may not reach them either. filings.py / segments.py live inside
#: trading/valuation/ and are covered by the package pins.
DISPLAY_ONLY_FEEDS = ("macro_feeds",)


def test_money_path_never_references_the_display_only_feeds():
    for name in MONEY_PATH:
        src = _operative_source(ROOT / "trading" / name)
        for mod in DISPLAY_ONLY_FEEDS:
            assert mod not in src, f"trading/{name} reaches {mod}"


def test_new_valuation_data_modules_exist_and_are_pure():
    for name in ("filings.py", "segments.py"):
        f = PKG / name
        assert f.exists(), f"trading/valuation/{name} missing"
        src = _operative_source(f)
        for pat in _BANNED_IN_PACKAGE:
            assert not re.search(pat, src), f"{name}: forbidden reference {pat!r}"


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
