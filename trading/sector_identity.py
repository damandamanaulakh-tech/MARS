"""Observed official industry / index membership, never a guessed sector label.

A snapshot proves what the official file contained when retrieved. It does not
prove historical membership or the publication/effective date of an undated CSV.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import re

from .quote_evidence import utc

MODEL = 'nse-constituents-2026-11-13-v1'
MAX_AGE_DAYS = 7  # explicit operational freshness limit, not an exchange rule
FILES = {
    'Total Market': 'ind_niftytotalmarket_list.csv',
    'IT': 'ind_niftyitlist.csv', 'Bank': 'ind_niftybanklist.csv',
    'Fin Service': 'ind_niftyfinancelist.csv', 'Auto': 'ind_niftyautolist.csv',
    'Pharma': 'ind_niftypharmalist.csv', 'FMCG': 'ind_niftyfmcglist.csv',
    'Metal': 'ind_niftymetallist.csv', 'Realty': 'ind_niftyrealtylist.csv',
    'Media': 'ind_niftymedialist.csv', 'Healthcare': 'ind_niftyhealthcarelist.csv',
    'Consumer Durables': 'ind_niftyconsumerdurableslist.csv',
    'Oil & Gas': 'ind_niftyoilgaslist.csv', 'Chemicals': 'ind_niftyChemicals_list.csv',
    'Cement': 'ind_NiftyCement_list.csv',
}
URLS = {name: 'https://archives.nseindia.com/content/indices/' + file for name, file in FILES.items()}
# NSE's legacy archive mirror does not publish this newly launched basket.
URLS['Cement'] = 'https://www.niftyindices.com/IndexConstituent/' + FILES['Cement']
SEED = Path(__file__).resolve().parents[1] / 'data' / 'sector_identity_snapshot.json'
_reader = None


def set_reader(reader):
    global _reader
    _reader = reader


def snapshot():
    """Read once per batch. No network on a decision or page request."""
    try:
        stored = _reader() if _reader else None
        if stored and stored.get('schema') == 1:
            return stored
        return json.loads(SEED.read_text())
    except Exception:
        # A failed store read cannot be called a current observation. The
        # bundled dated source remains useful only until its normal expiry.
        try:
            return json.loads(SEED.read_text())
        except (OSError, ValueError):
            return {}


def parse(raw, name, now=None, *, url=None):
    if name not in FILES or len(raw) > 2_000_000:
        raise ValueError('unsupported_source_or_oversized_file')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not {'Company Name', 'Industry', 'Symbol', 'Series', 'ISIN Code'}.issubset(reader.fieldnames or []):
        raise ValueError('constituent_columns_missing')
    rows, excluded = {}, []
    for r in reader:
        symbol = str(r.get('Symbol') or '').strip().upper()
        isin = str(r.get('ISIN Code') or '').strip().upper()
        industry = str(r.get('Industry') or '').strip()
        # Official baskets can temporarily contain demerger placeholders and
        # REIT units. Retain their exclusion evidence; do not map them to EQ.
        if isin.startswith('DUM') or r.get('Series') == 'RR':
            excluded.append({'symbol': symbol, 'isin': isin, 'series': r.get('Series'),
                             'reason': 'placeholder_or_non_equity_unit'})
            continue
        if (not re.fullmatch(r'[A-Z0-9&_-]+', symbol) or not re.fullmatch(r'IN[A-Z0-9]{10}', isin)
                or r.get('Series') not in ('EQ', 'BE', 'BZ') or not industry or not r.get('Company Name')):
            raise ValueError('invalid_constituent_row')
        ticker = symbol + '.NS'
        if ticker in rows:
            raise ValueError('duplicate_constituent')
        rows[ticker] = {'company': r['Company Name'].strip(), 'industry': industry, 'isin': isin, 'series': r['Series']}
    minimum = 600 if name == 'Total Market' else 5
    if not minimum <= len(rows) <= (1100 if name == 'Total Market' else 100):
        raise ValueError('incomplete_constituent_file')
    return {'rows': rows, 'url': url or URLS[name], 'sha256': hashlib.sha256(raw).hexdigest(),
            'observed_at': utc(now).isoformat(), 'publication_at': None, 'effective_at': None,
            'source_status': 'observed', 'count': len(rows), 'excluded': excluded}


def refresh(previous, now=None, fetcher=None):
    """Bounded independent sources. Failed source retains its previous timestamp."""
    now = utc(now)
    out = deepcopy(previous or {'schema': 1, 'model': MODEL, 'sources': {}})
    out.update(schema=1, model=MODEL, attempted_at=now.isoformat())
    sources = out.setdefault('sources', {})
    def one(name):
        if fetcher:
            raw = fetcher(URLS[name])
        else:
            import httpx
            # Stream with an explicit size ceiling (Content-Length is not trusted).
            with httpx.stream('GET', URLS[name], timeout=8.0, follow_redirects=False) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError('oversized_constituent_file')
                    chunks.append(chunk)
                raw = b''.join(chunks)
        return parse(raw, name, now)
    attempts = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='sector-identity') as pool:
        futures = {pool.submit(one, name): name for name in URLS}
        for future in as_completed(futures):
            name = futures[future]
            try:
                sources[name] = future.result()
                attempts[name] = {'status': 'observed', 'at': now.isoformat(), 'count': sources[name]['count']}
            except Exception as exc:
                attempts[name] = {'status': 'unavailable', 'at': now.isoformat(), 'error_type': type(exc).__name__,
                                  'retained_observation': sources.get(name, {}).get('observed_at')}
    out['attempts'] = attempts
    return out


def resolve(ticker, data, now=None, *, configured_label=None, instrument_isin=None):
    now = utc(now)
    base = {'ticker': ticker, 'status': 'unknown', 'reason': 'not_in_verified_sources',
            'industry': None, 'isin': None, 'sector_index': None, 'sector_status': 'unknown',
            'configured_label': configured_label, 'evidence': [], 'max_age_days': MAX_AGE_DAYS,
            'membership_basis': 'observed_current_constituents_not_historical_membership'}
    candidates = []
    for name, src in (data.get('sources') or {}).items():
        row = (src.get('rows') or {}).get(ticker)
        if not row or name not in FILES:
            continue
        at = utc(src.get('observed_at')) if src.get('observed_at') else None
        reason = ('source_time_unavailable' if at is None else 'source_observed_in_future' if at > now
                  else 'source_stale' if (now-at).total_seconds() > MAX_AGE_DAYS * 86400 else None)
        base['evidence'].append({'index': name, 'url': src.get('url'), 'sha256': src.get('sha256'),
                                 'observed_at': src.get('observed_at'), 'publication_at': src.get('publication_at'),
                                 'effective_at': src.get('effective_at'), 'status': reason or 'verified_observation'})
        if reason:
            base['reason'] = reason
        else:
            candidates.append((name, row, src))
    if not candidates:
        return base
    identities = {(r['isin'], r['industry']) for _, r, _ in candidates}
    if len(identities) != 1:
        base['reason'] = 'conflicting_official_identity'
        return base
    isin, industry = next(iter(identities))
    if instrument_isin and instrument_isin != isin:
        base['reason'] = 'instrument_isin_conflict'
        return base
    base.update(status='verified', reason='official_constituent_identity', isin=isin, industry=industry,
                company=candidates[0][1]['company'], instrument_match='matched' if instrument_isin else 'not_supplied')
    names = {name for name, _, _ in candidates if name != 'Total Market'}
    # Explicit narrower-basket choice, not a claim about weighting or historical constituents.
    if 'Bank' in names:
        names.discard('Fin Service')
    if 'Pharma' in names:
        names.discard('Healthcare')
    if len(names) == 1:
        base.update(sector_index=next(iter(names)), sector_status='verified', sector_reason='official_membership',
                    selection_rule='Bank before Financial Services; Pharma before Healthcare; otherwise unique membership')
    else:
        base['sector_reason'] = 'ambiguous_sector_membership' if names else 'no_verified_sector_index_membership'
    return base
