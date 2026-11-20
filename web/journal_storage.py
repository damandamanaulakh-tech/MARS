"""Bounded, lossless journal I/O with retry-safe identities and mixed readers."""
from __future__ import annotations

from uuid import uuid4
from bson import BSON
from pymongo import UpdateOne
from .journal_archive_codec import OPTIONS, pack_raw, decode_record, ArchiveFormatError


def _ordered(value):
    if isinstance(value, dict):
        return {k: _ordered(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_ordered(v) for v in value]
    return value


def original(entry):
    # Assign once on the actual RAM/spill entry, before attempting any write.
    # Identical content can describe two distinct events; content hashes must
    # not collapse those events. JSON spill/retry preserves this string ID.
    if '_id' not in entry:
        entry['_id'] = 'mars-journal:' + uuid4().hex
    row = dict(entry)
    return bytes(BSON.encode(_ordered(row)))


def append(collection, entries):
    """Retry identical events without new copies; verify every acknowledgement.

    Each source entry receives a stable string ID. A conflicting identity or a partial
    write returns failure to the caller, which must retain RAM/spill evidence.
    """
    for start in range(0, len(entries), 100):
        expected = {}
        for entry in entries[start:start + 100]:
            raw = original(entry)
            key = BSON(raw).decode(codec_options=OPTIONS)['_id']
            if key in expected and expected[key] != raw:
                raise ArchiveFormatError('journal_identity_conflict')
            expected[key] = raw
        ops = [UpdateOne({'_id': key}, {'$setOnInsert': BSON(pack_raw(raw)).decode(codec_options=OPTIONS)}, upsert=True)
               for key, raw in expected.items()]
        if not collection.bulk_write(ops, ordered=False).acknowledged:
            return False
        actual = {row['_id']: bytes(BSON.encode(decode_record(row)))
                  for row in collection.find({'_id': {'$in': list(expected)}})}
        if actual != expected:
            raise ArchiveFormatError('journal_write_readback_mismatch')
    return True


def decoded(row):
    result = decode_record(row)
    result.pop('_id', None)
    return result


def iter_latest(collection, query, limit=20000):
    """Keep only IDs plus one 100-document batch, instead of 20k gate snapshots."""
    if not 1 <= limit <= 20000:
        raise ValueError('journal_limit_out_of_range')
    ids = [row['_id'] for row in collection.find(query, {'_id': 1}).sort([('ts', -1), ('_id', -1)]).limit(limit)]
    ids.reverse()
    for start in range(0, len(ids), 100):
        keys = ids[start:start + 100]
        rows = {row['_id']: row for row in collection.find({'_id': {'$in': keys}})}
        if len(rows) != len(keys):
            raise ArchiveFormatError('journal_changed_during_export')
        for key in keys:
            yield decoded(rows[key])


def decision_query(ticker='', mode=''):
    import re
    legacy = {'evidence.gates': {'$exists': True}}
    packed = {'_mars_journal_query.has_gates': True}
    for name, value in [('ticker', ticker), ('mode', mode)]:
        if value:
            condition = {'$regex': re.escape(value), '$options': 'i'} if name == 'ticker' else value
            legacy['evidence.' + name] = condition
            packed['_mars_journal_query.' + name] = condition
    return {'$or': [legacy, packed], 'pre_mars': {'$ne': True}}
