"""Offline candidate preparation and independent source/candidate verification.

This module cannot connect to MongoDB. A successful report proves file-level
preservation only, not source completeness, durable backups or a database restore.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile

from bson import BSON, json_util

from . import journal_archive_codec as codec

MAX_MANIFEST_LINE = 32 * 1024 * 1024
MAX_METADATA_BYTES = 2_000_000


class PreflightError(ValueError):
    pass


class _UniqueIds:
    """Bounded duplicate detection for an independent verification pass."""
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mars-journal-verify-')
        path = Path(self.tmp.name)/'ids.sqlite'
        with _private_file(path):
            pass
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('CREATE TABLE ids (hash TEXT PRIMARY KEY)')
        self.count = 0
        return self

    def add(self, value):
        try:
            self.db.execute('INSERT INTO ids VALUES (?)', (value,))
        except sqlite3.IntegrityError as exc:
            raise PreflightError('duplicate_original_id') from exc
        self.count += 1
        if self.count % 500 == 0:
            self.db.commit()

    def __exit__(self, *args):
        self.db.close()
        self.tmp.cleanup()


def iter_bson(stream):
    """Stream one bounded BSON frame at a time; reject partial/trailing input."""
    while True:
        prefix = stream.read(4)
        if not prefix:
            return
        if len(prefix) != 4:
            raise PreflightError('truncated_bson_prefix')
        size = struct.unpack('<i', prefix)[0]
        if not 5 <= size <= codec.MAX_BSON_BYTES:
            raise PreflightError('bson_frame_size_invalid')
        tail = stream.read(size - 4)
        if len(tail) != size - 4:
            raise PreflightError('truncated_bson_record')
        yield prefix + tail


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _bounded_read(path, limit):
    with Path(path).open('rb') as fh:
        raw = fh.read(limit + 1)
    if len(raw) > limit:
        raise PreflightError('artifact_oversized')
    return raw


def _manifest_lines(stream):
    while True:
        line = stream.readline(MAX_MANIFEST_LINE + 1)
        if not line:
            return
        if len(line) > MAX_MANIFEST_LINE or not line.endswith(b'\n'):
            raise PreflightError('manifest_line_invalid')
        yield line


def _details(number, source, candidate):
    original = codec.unpack_raw(source)
    if codec.unpack_raw(candidate) != original:
        raise PreflightError('original_byte_mismatch')
    doc = BSON(original).decode(codec_options=codec.OPTIONS)
    if '_id' not in doc:
        raise PreflightError('original_id_missing')
    return {'row': number, 'id': json.loads(json_util.dumps(doc['_id'],
                json_options=json_util.CANONICAL_JSON_OPTIONS)),
            'id_bson_sha256': _hash(BSON.encode({'_id': doc['_id']})),
            'source_sha256': _hash(source), 'source_bytes': len(source),
            'original_sha256': _hash(original), 'original_bytes': len(original),
            'candidate_sha256': _hash(candidate), 'candidate_bytes': len(candidate),
            'smaller': len(candidate) < len(source)}


def _private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')


def _write_json(path, value):
    with _private_file(path) as fh:
        fh.write((json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode())
        fh.flush(); os.fsync(fh.fileno())


def verify(source: Path, prepared: Path, *, _preparing=False):
    """Read-only check; compare every row, manifest entry and aggregate hash."""
    source, prepared = Path(source), Path(prepared)
    if (prepared/'FAILED.json').exists():
        raise PreflightError('preparation_incomplete')
    summary_raw = _bounded_read(prepared/'summary.json', MAX_METADATA_BYTES)
    summary = json.loads(summary_raw)
    if (summary.get('schema') != 1 or summary.get('format') != codec.FORMAT
            or summary.get('version') != codec.VERSION):
        raise PreflightError('summary_version_unsupported')
    if not _preparing:
        marker = json.loads(_bounded_read(prepared/'BYTE_VERIFIED.json', MAX_METADATA_BYTES))
        if (marker.get('status') != 'byte_verified_not_database_restored'
                or marker.get('summary_sha256') != _hash(summary_raw)):
            raise PreflightError('success_marker_mismatch')
    totals = {'rows': 0, 'source_bytes': 0, 'original_bytes': 0,
              'candidate_bytes': 0, 'smaller_records': 0, 'max_original_bytes': 0}
    sh, ch, mh = (hashlib.sha256() for _ in range(3))
    sentinel = object()
    with source.open('rb') as src, (prepared/'candidate.bson').open('rb') as candidate, (prepared/'manifest.jsonl').open('rb') as manifest, _UniqueIds() as seen:
        for number, (a,b,line) in enumerate(itertools.zip_longest(iter_bson(src), iter_bson(candidate), _manifest_lines(manifest), fillvalue=sentinel),1):
            if any(v is sentinel for v in (a,b,line)):
                raise PreflightError('source_candidate_manifest_count_mismatch')
            d = _details(number, a, b)
            seen.add(d['id_bson_sha256'])
            if json.loads(line) != d:
                raise PreflightError('manifest_record_mismatch')
            if len(b) > len(a):
                raise PreflightError('candidate_grew')
            sh.update(a); ch.update(b); mh.update(line)
            totals['rows'] += 1
            for key in ('source_bytes','original_bytes','candidate_bytes'):
                totals[key] += d[key]
            totals['smaller_records'] += int(d['smaller'])
            totals['max_original_bytes'] = max(totals['max_original_bytes'],d['original_bytes'])
    totals.update(source_sha256=sh.hexdigest(),candidate_sha256=ch.hexdigest(),manifest_sha256=mh.hexdigest())
    if totals != summary.get('verified_totals'):
        raise PreflightError('summary_totals_mismatch')
    metadata = summary.get('index_metadata')
    if metadata is not None:
        raw = _bounded_read(prepared/'source.metadata.json', MAX_METADATA_BYTES)
        if _hash(raw) != metadata.get('sha256'):
            raise PreflightError('index_metadata_hash_mismatch')
    return totals


def build(source: Path, prepared: Path, *, metadata: Path | None = None, expected_rows: int | None = None):
    """Build an inspectable candidate in a new private directory, then verify.

    Existing outputs cannot be overwritten. Incomplete candidates retain an
    explicit failure record and never receive a success marker. SQLite bounds
    the memory used for duplicate-ID detection independently of row count.
    """
    source, prepared = Path(source), Path(prepared)
    if expected_rows is not None and (type(expected_rows) is not int or expected_rows < 0):
        raise PreflightError('expected_count_invalid')
    before = source.stat()
    prepared.mkdir(mode=0o700)  # No exist_ok; never overwrite an existing backup.
    dbpath = prepared/'id-check.sqlite'
    totals = {'rows':0,'source_bytes':0,'original_bytes':0,'candidate_bytes':0,
              'smaller_records':0,'max_original_bytes':0}
    sh, ch, mh = (hashlib.sha256() for _ in range(3))
    conn = None
    try:
        with _private_file(dbpath):
            pass
        conn=sqlite3.connect(dbpath)
        conn.execute('PRAGMA cache_size=-2048')
        conn.execute('CREATE TABLE ids (hash TEXT PRIMARY KEY)')
        with source.open('rb') as src, _private_file(prepared/'candidate.bson') as out, _private_file(prepared/'manifest.jsonl') as manifest:
            for number, raw in enumerate(iter_bson(src),1):
                candidate=codec.pack_raw(raw)
                d=_details(number,raw,candidate)
                try:
                    conn.execute('INSERT INTO ids VALUES (?)',(d['id_bson_sha256'],))
                except sqlite3.IntegrityError as exc:
                    raise PreflightError('duplicate_original_id') from exc
                line=(json.dumps(d,ensure_ascii=False,separators=(',',':'))+'\n').encode()
                out.write(candidate); manifest.write(line)
                sh.update(raw); ch.update(candidate); mh.update(line)
                totals['rows'] += 1
                for key in ('source_bytes','original_bytes','candidate_bytes'):
                    totals[key] += d[key]
                totals['smaller_records'] += int(d['smaller'])
                totals['max_original_bytes']=max(totals['max_original_bytes'],d['original_bytes'])
                if number % 500 == 0:
                    conn.commit()
            for fh in (out,manifest):
                fh.flush(); os.fsync(fh.fileno())
        if expected_rows is not None and totals['rows'] != expected_rows:
            raise PreflightError('expected_count_mismatch')
        after=source.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino):
            raise PreflightError('source_changed')
        index_metadata=None
        if metadata is not None:
            raw=_bounded_read(metadata, MAX_METADATA_BYTES)
            if not isinstance(json.loads(raw),dict):
                raise PreflightError('index_metadata_not_object')
            with _private_file(prepared/'source.metadata.json') as fh:
                fh.write(raw); fh.flush(); os.fsync(fh.fileno())
            index_metadata={'sha256':_hash(raw),'bytes':len(raw),'database_restore':'not_run'}
        totals.update(source_sha256=sh.hexdigest(),candidate_sha256=ch.hexdigest(),manifest_sha256=mh.hexdigest())
        summary={'schema':1,'format':codec.FORMAT,'version':codec.VERSION,
                 'source_file':source.name,'verified_totals':totals,'index_metadata':index_metadata,
                 'source_completeness':'unknown; compare against a quiesced source inventory',
                 'database_restore':'not_run','durable_backup_copies':'not_verified',
                 'production_mutations':0}
        _write_json(prepared/'summary.json',summary)
        verified=verify(source,prepared,_preparing=True)  # Reopen files; do not trust in-memory objects.
        if verified != totals:
            raise PreflightError('independent_verification_failed')
        _write_json(prepared/'BYTE_VERIFIED.json',{'schema':1,'summary_sha256':_hash((prepared/'summary.json').read_bytes()),
                    'status':'byte_verified_not_database_restored','production_mutations':0})
        return summary
    except Exception as exc:
        _write_json(prepared/'FAILED.json',{'status':'incomplete','rows_processed':totals['rows'],
                    'error_type':type(exc).__name__,
                    'reason':str(exc) if isinstance(exc,(PreflightError,codec.ArchiveFormatError)) else 'preparation_failed',
                    'production_mutations':0})
        raise
    finally:
        if conn is not None:
            conn.close()
        # Retain this small private ID index with other preparation artifacts;
        # even failure cleanup never deletes an input or archive record.
