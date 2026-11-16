"""Bounded, separately sealed acquisition. Never imported by the application."""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
from urllib.parse import quote

import httpx

from research.profit_protection_2026_11_16.fetch import months, digest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL_COMMIT = '75e3ac34ba8de8f702d16b73e36bbbc5b6bfa331'


def acquire(stage, directory=HERE, *, resume=False):
    if stage not in ('training', 'holdout'):
        raise ValueError('unknown_acquisition_stage')
    directory = Path(directory)
    output = directory/stage
    if (output/'raw_responses.json.gz').exists() or (output/'manifest.json').exists():
        raise ValueError('snapshot_already_exists; no silent replacement')
    if stage == 'holdout':
        from .study import open_holdout
        open_holdout(directory, resume=resume)  # Before even the first request.
    elif resume or (directory/'selection.json').exists():
        raise ValueError('training_already_sealed_or_invalid_resume')
    protocol_bytes = (directory/'protocol.json').read_bytes()
    protocol = json.loads(protocol_bytes)
    start, end = ((protocol['data_start'], protocol['splits']['validation'][1])
                  if stage == 'training' else protocol['splits']['holdout'])
    mapping_path = ROOT/'data/upstox_nse_equities.csv'
    with mapping_path.open() as handle:
        mapping = {row['ticker']: row for row in csv.DictReader(handle)}
    requests = []
    for symbol in protocol['symbols']:
        instrument = mapping[symbol]['instrument_key']
        for first, last in months(start, end):
            url = ('https://api.upstox.com/v3/historical-candle/'
                   f'{quote(instrument, safe="")}/minutes/5/{last}/{first}')
            requests.append((symbol, first, last, url))

    def one(item):
        symbol, first, last, url = item
        meta = dict(symbol=symbol, start=first, end=last, url=url,
                    attempted_at=datetime.now(timezone.utc).isoformat())
        try:
            response = httpx.get(url, headers={'Accept': 'application/json'}, timeout=25)
            body = response.content
            meta.update(http_status=response.status_code, body_sha256=digest(body),
                        bytes=len(body), received_at=datetime.now(timezone.utc).isoformat())
            raw = {**meta, 'body': body.decode('utf-8')}
            meta['rows'] = (len((response.json().get('data') or {}).get('candles') or [])
                            if response.status_code == 200 else 0)
        except Exception as exc:
            meta.update(error=type(exc).__name__, rows=0)
            raw = dict(meta)
        print(json.dumps({k: meta[k] for k in ('symbol', 'start', 'end', 'rows')}), flush=True)
        return meta, raw

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, requests))
    payload = json.dumps([r[1] for r in results], sort_keys=True, separators=(',', ':')).encode()
    archive = gzip.compress(payload, mtime=0)
    manifest = dict(schema=1, stage=stage, provider='Upstox V3', interval_minutes=5,
                    timestamp_kind='bar_start', protocol_sha256=digest(protocol_bytes),
                    protocol_commit_before_acquisition=PROTOCOL_COMMIT,
                    mapping_sha256=digest(mapping_path.read_bytes()),
                    raw_archive_sha256=digest(archive), raw_payload_sha256=digest(payload),
                    started_at=min(r[0]['attempted_at'] for r in results),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    data_start=start, data_end=end, requests=[r[0] for r in results],
                    scope='Public market candles only; no credentials, production scans or trades.',
                    adjustments='Provider adjustment/revision and historical universe not independently certified.')
    if stage == 'holdout':
        manifest['selection_sha256_before_request'] = digest((directory/'selection.json').read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    (output/'protocol.json').write_bytes(protocol_bytes)
    (output/'raw_responses.json.gz').write_bytes(archive)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'stage': stage, 'requests': len(results),
                      'rows': sum(r[0]['rows'] for r in results),
                      'http_200': sum(r[0].get('http_status') == 200 for r in results)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['training', 'holdout'])
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    acquire(args.stage, resume=args.resume)
