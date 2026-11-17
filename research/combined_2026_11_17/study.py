"""Reproduce the sealed actual-strategy holdout without any network or writes to app state."""
from collections import Counter
import argparse
import gzip
import json
from pathlib import Path
import pandas as pd
from trading import intraday_bars as ib
from trading.validation import canonical, digest, file_hash
from .replay import run

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(directory=HERE):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if file_hash(directory / "raw_responses.json.gz") != manifest["raw_sha256"]:
        raise ValueError("Raw data archive hash mismatch")
    protocol = json.loads((directory / "protocol.json").read_text())
    if file_hash(directory / "protocol.json") != manifest["protocol_sha256"]:
        raise ValueError("Protocol hash mismatch")
    records = json.loads(gzip.decompress((directory / "raw_responses.json.gz").read_bytes()))
    groups, coverage = {}, Counter()
    asof = pd.Timestamp(protocol["end"] + " 23:59", tz=ib.IST)
    for record in records:
        if record.get("status") != 200:
            coverage["failed_requests"] += 1
            continue
        if digest(record["body"]) != record["body_sha256"]:
            raise ValueError("Provider response hash mismatch")
        candles = json.loads(record["body"]).get("data", {}).get("candles", [])
        key = (record["ticker"], record["unit"])
        rows = groups.setdefault(key, {})
        for raw in candles:
            coverage[record["unit"] + "_raw_rows"] += 1
            try:
                ts = pd.Timestamp(raw[0])
                if ts.tzinfo is None or ts > asof:
                    raise ValueError("future_or_unzoned_timestamp")
                values = [float(x) for x in raw[1:6]]
                import math
                if not all(math.isfinite(x) for x in values) or min(values[:4]) <= 0 or values[4] < 0:
                    raise ValueError("invalid_ohlcv")
                if values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]):
                    raise ValueError("invalid_ohlcv_range")
                if record["unit"] == "minutes":
                    why = ib.bar_status(ts, 5, asof.to_pydatetime(), current_session=False, fresh=False)
                    if why:
                        raise ValueError(why)
                    ts = ts.tz_convert("UTC")
                else:
                    ts = pd.Timestamp(ts.tz_convert(ib.IST).date())
                if ts in rows and rows[ts] != values:
                    raise RuntimeError("Conflicting bars in immutable input")
                if ts in rows:
                    coverage["identical_duplicates"] += 1
                rows[ts] = values
            except (ValueError, TypeError, IndexError) as exc:
                coverage["rejected:" + str(exc)] += 1
    daily, intraday = {}, {}
    for (ticker, unit), rows in groups.items():
        frame = pd.DataFrame.from_dict(rows, orient="index", columns=ib.COLUMNS).sort_index()
        frame.attrs.update(interval="1d" if unit == "days" else "5m", source="frozen_upstox_v3")
        (daily if unit == "days" else intraday)[ticker] = frame
        coverage[unit + "_accepted_rows"] += len(frame)
    return daily, intraday, dict(coverage)


def evaluate(directory=HERE):
    directory = Path(directory)
    seal = json.loads((directory / "seal.json").read_text())
    for name, expected in seal["files"].items():
        if file_hash(ROOT / name) != expected:
            raise ValueError("Sealed evaluation source changed: " + name)
    protocol = json.loads((directory / "protocol.json").read_text())
    saved = json.loads((directory / "saved_settings.json").read_text())
    daily, intraday, coverage = load(directory)
    outcomes = {}
    for bps in protocol["slippage_bps_each_side"]:
        print(f"Replaying actual engine at {bps} bps per side", flush=True)
        outcomes[str(bps)] = run(daily, intraday, saved, protocol, bps)
    return {"schema": 1, "seal_sha256": file_hash(directory / "seal.json"),
            "data_sha256": file_hash(directory / "raw_responses.json.gz"),
            "input_coverage": coverage, "outcomes": outcomes,
            "promotion": "none", "forward_status": "pending_new_paper_sessions",
            "acceptance": "not_established; bounded_panel_and_missing_historical_context"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "verify"])
    args = parser.parse_args()
    payload = evaluate()
    path = HERE / "results.json.gz"
    raw = canonical(payload)
    if args.command == "verify":
        if gzip.decompress(path.read_bytes()) != raw:
            raise ValueError("Frozen combined-validation results differ")
        print("Combined validation exactly reproduced; no network, no settings writes.")
    else:
        if path.exists():
            raise ValueError("Results already exist; do not overwrite consumed holdout")
        path.write_bytes(gzip.compress(raw, mtime=0))
        (HERE / "summary.json").write_text(json.dumps({**payload,
            "outcomes": {k: r["summary"] for k, r in payload["outcomes"].items()}}, indent=2) + "\n")


if __name__ == "__main__":
    main()
