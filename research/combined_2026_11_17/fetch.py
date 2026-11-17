"""Explicit public-data acquisition after the code/protocol/settings seal."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import gzip
import json
from pathlib import Path
from urllib.parse import quote
import httpx
from trading.validation import file_hash, digest, canonical
from research.profit_protection_2026_11_16.fetch import months

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def acquire():
    seal = json.loads((HERE / "seal.json").read_text())
    for name, expected in seal["files"].items():
        if file_hash(ROOT / name) != expected:
            raise ValueError("Sealed input changed before acquisition: " + name)
    for name in ("opened.json", "raw_responses.json.gz", "manifest.json"):
        if (HERE / name).exists():
            raise ValueError("Holdout already opened; no silent reread or replacement")
    (HERE / "opened.json").write_bytes(canonical({"opened_at": datetime.now(timezone.utc).isoformat(),
        "seal_sha256": file_hash(HERE / "seal.json"), "state": "consumed_before_first_request"}))
    protocol = json.loads((HERE / "protocol.json").read_text())
    with (ROOT / "data/upstox_nse_equities.csv").open() as f:
        mapping = {r["ticker"]: r["instrument_key"] for r in csv.DictReader(f)}
    mapping.update({"^NSEI": "NSE_INDEX|Nifty 50", "^INDIAVIX": "NSE_INDEX|India VIX"})
    jobs = []
    for ticker in protocol["symbols"] + ["^NSEI", "^INDIAVIX"]:
        jobs.append((ticker, "days", 1, protocol["daily_warmup_start"], protocol["end"]))
        if ticker != "^INDIAVIX":
            jobs.extend((ticker, "minutes", 5, a, b) for a, b in months(
                protocol["intraday_warmup_start"], protocol["end"]))
    def one(job):
        ticker, unit, interval, start, end = job
        url = ("https://api.upstox.com/v3/historical-candle/" + quote(mapping[ticker], safe="")
               + f"/{unit}/{interval}/{end}/{start}")
        row = dict(ticker=ticker, unit=unit, interval=interval, start=start, end=end,
                   url=url, attempted_at=datetime.now(timezone.utc).isoformat())
        try:
            response = httpx.get(url, headers={"Accept": "application/json"}, timeout=25)
            row.update(status=response.status_code, body=response.text,
                       body_sha256=digest(response.text), received_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            row.update(status=None, error=type(exc).__name__)
        print(f"{ticker} {unit} {start} {row['status']}", flush=True)
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(one, jobs))
    raw = canonical(rows)
    (HERE / "raw_responses.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    manifest = {"schema": 1, "requests": [{k: x for k, x in r.items() if k != "body"} for r in rows],
        "seal_sha256": file_hash(HERE / "seal.json"), "protocol_sha256": file_hash(HERE / "protocol.json"),
        "raw_sha256": file_hash(HERE / "raw_responses.json.gz"),
        "finished_at": datetime.now(timezone.utc).isoformat(), "source": "Upstox Historical V3",
        "adjustments": "provider_revision_and_corporate_adjustments_not_independently_certified"}
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    acquire()
