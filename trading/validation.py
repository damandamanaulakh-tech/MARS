"""Versioned validation evidence; never selects a trade or changes a risk rule."""
from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from importlib.metadata import version

MODEL = "combined-validation-v1"
ROOT = Path(__file__).resolve().parents[1]
MODES = ("nifty200_momentum", "intraday", "short", "swing", "positional")
STATIC_INPUTS = (
    "research/bt_calibration/run_2026-08-09_strict15/trigger_calibration.csv",
    "research/bt_calibration_external/run_2026-08-13/external_trigger_calibration.csv",
    "research/bt_calibration/run_2026-08-09/trigger_calibration.csv",
    "research/chityy_archive/trigger_layer_v0_2/trigger_params_v0_2.csv",
    "research/bt06/run_2026-08-24/side_score_weights.csv",
    "research/ifr_frozen_2026-10-02/thresholds.json",
    "research/external/selection_register_v4_279.csv",
    "data/corporate_actions_2010_2026.csv",
    "data/fundamentals_n200_2026-08-09.csv",
    "data/history_upstox_export_2026-07-19.zip",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=str).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def settings(cfg):
    """Config has no credentials. Never serialise environment/auth/user settings."""
    value = asdict(cfg)
    value.pop("state_dir", None)
    return json.loads(canonical(value))


@lru_cache(maxsize=1)
def code_identity():
    # Runtime files, not Git HEAD alone: docs commits need not redeploy the app.
    paths = [p for folder in ("trading", "web") for p in (ROOT / folder).rglob("*.py")
             if "tests" not in p.parts and " 2" not in p.name]
    paths.append(ROOT / "requirements.txt")
    paths.extend(ROOT / name for name in STATIC_INPUTS)
    paths = [p for p in paths if p.exists()]
    hashes = {str(p.relative_to(ROOT)): file_hash(p) for p in sorted(paths)}
    return {"sha256": digest(hashes), "files": hashes,
            "revision": os.environ.get("RENDER_GIT_COMMIT"),
            "python": platform.python_version(),
            "packages": runtime_packages()}


def runtime_packages():
    return {name: version(name) for name in ("numpy", "pandas", "httpx", "yfinance", "python-dateutil")}


def identity(cfg):
    config = settings(cfg)
    code = code_identity()
    manifest = {"model": MODEL, "code": code, "settings": config,
                "settings_sha256": digest(config),
                "scheduler_environment": {name: os.environ.get(name, default) for name, default in
                    (("DAILY_SCHEDULER", "1"), ("INTRADAY_SCHEDULER", "0"), ("SB_IGNORE_MARKET_HOURS", "0"))}}
    # Revision is provenance; code bytes, settings and runtime define the run.
    manifest["id"] = digest({"model": MODEL, "code": code["sha256"],
                             "python": code["python"], "packages": code["packages"],
                             "scheduler": manifest["scheduler_environment"], "settings": config})
    return manifest


def frame_data(frame):
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unknown", "reason": "input_not_supplied", "sha256": None}
    columns = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame]
    rows = [[str(ts)] + [float(x) if math.isfinite(float(x)) else None for x in row]
            for ts, row in zip(frame.index, frame[columns].itertuples(index=False, name=None))]
    data = {"columns": ["timestamp"] + columns, "rows": rows,
            "validation": dict(frame.attrs.get("intraday_validation") or {})}
    return {"status": "captured", "sha256": digest(data), **data}


def entry_record(cfg, pick, now, qty=None):
    """Only executed entries retain raw own-symbol inputs, not 1205 scan frames.

    Peer panels and unarchived providers remain explicitly incomplete. This is
    forward outcome evidence, not a claim that the whole scan can be replayed.
    """
    manifest = identity(cfg)
    data = {"signal": frame_data(pick.get("history")),
            "daily_risk": frame_data(pick.get("risk_history"))}
    decision = pick.get("decision") or {}
    record = {"model": MODEL, "run": manifest, "at": str(now), "original_qty": qty,
              "path": decision.get("path", "unknown"),
              "data": data, "data_sha256": digest(data),
              "coverage": "own_symbol_inputs_only; full_peer_and_provider_snapshot_unavailable"}
    if isinstance(decision.get("strategy_batch"), dict):
        # Owner authority is part of the executed entry, not a later label.
        # Keep it independent of the scanner's mutable request/decision object.
        record["strategy_batch"] = deepcopy(decision["strategy_batch"])
    return record


def finite(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def capture_entry(portfolio, cfg, pick, now, qty):
    """Deduplicate manifests and compress raw bars before entering the book.

    A 222-position portfolio must not contain 222 copies of the code file map
    and 1,205-name universe. The manifest has its own durable outbox event.
    """
    import base64
    import zlib
    record = entry_record(cfg, pick, now, qty)
    run = record.pop("run")
    key = "manifest:" + run["id"]
    if key not in portfolio.validation_state.get("seen", []):
        portfolio.validation_pending.setdefault(key, {"_id": key, "model": MODEL,
            "kind": "manifest", "observed_at": str(now), "run": run})
    record["run"] = {"id": run["id"]}
    record["data_zlib_base64"] = base64.b64encode(zlib.compress(canonical(record.pop("data")))).decode()
    return record


def performance(trades, equity, initial_capital, open_trade_ids=()):
    """Net expectancy per fully closed economic trade, not per partial leg.

    All exit reasons count. Missing P&L or costs invalidate that economic trade.
    Drawdown starts at initial equity and includes terminal charges when the
    caller actually liquidates. A still-open book is never reported as cash.
    """
    grouped = {}
    unknown_identity = 0
    for row in trades:
        key = (row.get("trade_evidence") or {}).get("trade_id") or row.get("trade_id")
        if not key:
            unknown_identity += 1
            continue
        grouped.setdefault(key, []).append(row)
    open_ids = set(open_trade_ids)
    pnls, unknown = [], 0
    for key, legs in grouped.items():
        if key in open_ids:
            continue
        if not all(finite(r.get("net_pnl")) and finite(r.get("costs"))
                   and float(r["costs"]) >= 0
                   and (r.get("cost_evidence") or {}).get("status") != "unavailable" for r in legs):
            unknown += 1
            continue
        pnls.append(sum(float(r["net_pnl"]) for r in legs))
    values = [float(initial_capital)] if finite(initial_capital) and float(initial_capital) > 0 else []
    missing_marks = 0
    for row in equity:
        v = row[1]
        if finite(v):
            values.append(float(v))
        else:
            missing_marks += 1
    peak, worst = (values[0], 0.0 if values[0] > 0 else None) if values else (None, None)
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(0.0 if worst is None else worst, value / peak - 1)
    n = len(pnls)
    return {"closed_trades": n, "exit_legs": len(trades),
            "net_pnl": round(sum(pnls), 2) if n else None,
            "net_expectancy": round(sum(pnls) / n, 2) if n else None,
            "win_rate_pct": round(sum(x > 0 for x in pnls) / n * 100, 4) if n else None,
            "loss_rate_pct": round(sum(x < 0 for x in pnls) / n * 100, 4) if n else None,
            "breakeven_trades": sum(x == 0 for x in pnls),
            "max_drawdown_pct": round(-worst * 100, 6) if worst is not None else None,
            "equity_observations": len(equity), "missing_equity_marks": missing_marks,
            "final_equity": values[-1] if values else None,
            "open_economic_trades": len(open_ids), "unknown_trades": unknown,
            "unknown_identity_legs": unknown_identity,
            "status": "no_closed_sample" if not n else "measured",
            "acceptance": "not_established"}


def guard_research_output(output, *, parameters=None, root=ROOT):
    """XR-103: fail before reading or overwriting an unversioned/mismatched cache.

    Hash raw study inputs and all analysis sources, including constants/presets.
    Existing historical files are preserved; reruns must use a fresh output path.
    """
    from .config import Config
    root, output = Path(root), Path(output)
    sidecar = output.with_name(output.name + ".identity.json")
    if output.exists() and not sidecar.exists():
        raise ValueError(f"Unversioned historical result preserved: {output}. Use a new output path.")
    paths = [p for folder in ("trading", "scripts", "data") for p in (root / folder).rglob("*")
             if p.is_file() and " 2" not in p.name and "tests" not in p.parts
             and p.suffix in (".py", ".csv", ".json", ".zip", ".gz", ".parquet")
             and p.resolve() not in (output.resolve(), sidecar.resolve())]
    for name in STATIC_INPUTS + ("requirements.txt",):
        if (root / name).exists():
            paths.append(root / name)
    manifest = {"model": MODEL, "parameters": parameters or {},
                "defaults": settings(Config()), "python": platform.python_version(),
                "packages": runtime_packages(),
                "files": {str(p.relative_to(root)): file_hash(p) for p in sorted(set(paths))}}
    # Real studies use tuple-based windows/variants. Compare their canonical
    # JSON form with the stored form, not tuples with JSON-decoded lists.
    manifest = json.loads(canonical(manifest))
    expected = {"sha256": digest(manifest), "manifest": manifest}
    sidecar = output.with_name(output.name + ".identity.json")
    if output.exists() and not sidecar.exists():
        raise ValueError(f"Unversioned historical result preserved: {output}. Use a new output path.")
    if sidecar.exists():
        if json.loads(sidecar.read_text()) != expected:
            raise ValueError(f"Research identity mismatch: {output}. Use a new output path.")
    else:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with sidecar.open("x") as f:
            json.dump(expected, f, sort_keys=True, indent=2)
    return expected["sha256"]
