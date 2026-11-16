"""Explicit, bounded public-data acquisition; never imported by the application.

Run once after freezing protocol.json. Raw response bytes and request metadata
are retained; a failed/empty request is evidence, not a replacement price.
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def months(start, end):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    while start <= end:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        stop = min(end, following - timedelta(days=1))
        yield start.isoformat(), stop.isoformat()
        start = following


def acquire(output=HERE):
    output = Path(output)
    if (output / 'raw_responses.json.gz').exists():
        raise ValueError('raw_snapshot_already_exists; choose a new study, never overwrite')
    # Preserve the originally frozen acquisition request after the documented
    # source-only baseline correction. Existing snapshots are never replaced.
    acquisition_protocol = HERE / ('protocol-v1.json' if (HERE/'protocol-v1.json').exists() else 'protocol.json')
    protocol_bytes = acquisition_protocol.read_bytes()
    protocol = json.loads(protocol_bytes)
    mapping_path = ROOT / 'data/upstox_nse_equities.csv'
    mapping = {r['ticker']: r for r in csv.DictReader(mapping_path.open())}
    requests = []
    for symbol in protocol['symbols']:
        instrument = mapping[symbol]['instrument_key']
        for start, end in months(protocol['data_start'], protocol['data_end']):
            url = ('https://api.upstox.com/v3/historical-candle/'
                   f'{quote(instrument, safe="")}/minutes/5/{end}/{start}')
            requests.append((symbol, start, end, url))

    def one(item):
        symbol, start, end, url = item
        metadata = dict(symbol=symbol, start=start, end=end, url=url,
                        attempted_at=datetime.now(timezone.utc).isoformat())
        try:
            response = httpx.get(url, headers={'Accept': 'application/json'}, timeout=25)
            body = response.content
            metadata.update(http_status=response.status_code, body_sha256=digest(body),
                            bytes=len(body), received_at=datetime.now(timezone.utc).isoformat())
            # Preserve the original UTF-8 JSON text, including failed responses.
            raw = {**metadata, 'body': body.decode('utf-8')}
            if response.status_code == 200:
                metadata['rows'] = len((response.json().get('data') or {}).get('candles') or [])
            else:
                metadata['rows'] = 0
        except Exception as exc:
            metadata.update(error=type(exc).__name__, rows=0)
            raw = dict(metadata)
        print(json.dumps({k: metadata[k] for k in ('symbol', 'start', 'end', 'rows')}, sort_keys=True), flush=True)
        return metadata, raw

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, requests))
    payload = json.dumps([r[1] for r in results], sort_keys=True, separators=(',', ':')).encode()
    archive = gzip.compress(payload, mtime=0)
    manifest = {
        'schema': 1, 'provider': 'Upstox V3', 'interval_minutes': 5,
        'timestamp_kind': 'bar_start', 'protocol_sha256': digest(protocol_bytes),
        'protocol_commit_before_acquisition': 'dd6b5b2aa89c2d9e7f57d8f5efbd1a6250d4f35a',
        'mapping_sha256': digest(mapping_path.read_bytes()),
        'raw_archive_sha256': digest(archive), 'raw_payload_sha256': digest(payload),
        'finished_at': datetime.now(timezone.utc).isoformat(),
        'requests': [r[0] for r in results],
        'scope': 'Public market candles only; no credentials, portfolio, orders or production scan.',
        'adjustments': 'Provider historical OHLC; adjustment/revision and historical universe coverage are not independently certified.'
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'raw_responses.json.gz').write_bytes(archive)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'requests': len(results), 'rows': sum(r[0]['rows'] for r in results),
                      'http_200': sum(r[0].get('http_status') == 200 for r in results),
                      'archive_bytes': len(archive)}), flush=True)


if __name__ == '__main__':
    acquire()
