"""Lossless journal representation for mixed legacy/compressed journal storage.

Pure functions only. No database client, environment read or production writer.
Raw BSON is the preservation unit; JSON/CSV is never the backup representation.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import re
import struct
import zlib

from bson import BSON, Binary
from bson.codec_options import CodecOptions, DatetimeConversion
from bson.raw_bson import RawBSONDocument

MAX_BSON_BYTES = 16 * 1024 * 1024
MARKER = '_mars_journal_bson'
META = '_mars_journal_query'
FORMAT = 'mars.journal.raw-bson'
VERSION = 1
OPTIONS = CodecOptions(datetime_conversion=DatetimeConversion.DATETIME_AUTO)
RAW_OPTIONS = CodecOptions(document_class=RawBSONDocument,
                           datetime_conversion=DatetimeConversion.DATETIME_AUTO)
ENVELOPE_FIELDS = ('_id', 'ts', 'action', 'decision_id', 'trade_id', 'ticker', 'mode', 'pre_mars')


class ArchiveFormatError(ValueError):
    """Stored evidence is unavailable; callers must not treat it as empty."""


def _validated(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 5 <= len(raw) <= MAX_BSON_BYTES:
        raise ArchiveFormatError('bson_size_invalid')
    if struct.unpack('<i', raw[:4])[0] != len(raw) or raw[-1] != 0:
        raise ArchiveFormatError('bson_frame_invalid')
    try:
        return BSON(raw).decode(codec_options=OPTIONS)
    except Exception as exc:
        raise ArchiveFormatError('bson_invalid') from exc


def _envelope(original: Mapping) -> dict:
    if '_id' not in original:
        raise ArchiveFormatError('original_id_missing')
    result = {k: original[k] for k in ENVELOPE_FIELDS if k in original}
    evidence = original.get('evidence')
    metadata = {'has_gates': isinstance(evidence, Mapping) and 'gates' in evidence}
    if isinstance(evidence, Mapping):
        metadata.update({k: evidence[k] for k in ('ticker', 'mode') if k in evidence})
    result[META] = metadata
    return result


def _compressed(doc: Mapping) -> bool:
    marker = doc.get(MARKER)
    return isinstance(marker, Mapping) and marker.get('format') == FORMAT


def unpack_raw(raw: bytes) -> bytes:
    """Validate and recover the exact original bytes, or preserve a legacy row.

    A bounded decompressor refuses oversized output, incomplete streams and
    trailing streams/data. No unbounded flush is used. Metadata is authenticated
    against the recovered document before a query result can be trusted.
    """
    doc = _validated(raw)
    if not _compressed(doc):
        if MARKER in doc or META in doc:
            # A damaged/future envelope must not masquerade as an empty legacy
            # event. Reserved legacy collisions require explicit review too.
            raise ArchiveFormatError('unrecognized_archive_marker')
        return raw
    c = doc[MARKER]
    if set(c) != {'format', 'version', 'codec', 'raw_bytes', 'sha256', 'payload'}:
        raise ArchiveFormatError('codec_fields_invalid')
    if type(c['version']) is not int or c['version'] != VERSION or c['codec'] != 'zlib':
        raise ArchiveFormatError('codec_version_unsupported')
    length, digest, payload = c['raw_bytes'], c['sha256'], c['payload']
    if type(length) is not int or not 5 <= length <= MAX_BSON_BYTES:
        raise ArchiveFormatError('original_size_invalid')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ArchiveFormatError('original_hash_invalid')
    # BSON subtype zero decodes to bytes in PyMongo; explicit Binary(0) is
    # accepted for callers that have not gone through the wire decoder yet.
    if not isinstance(payload, (bytes, Binary)) or (isinstance(payload, Binary) and payload.subtype != 0):
        raise ArchiveFormatError('payload_type_invalid')
    try:
        stream = zlib.decompressobj()
        original = stream.decompress(bytes(payload), length + 1)
    except zlib.error as exc:
        raise ArchiveFormatError('payload_compression_invalid') from exc
    if (len(original) != length or not stream.eof or stream.unused_data or stream.unconsumed_tail):
        raise ArchiveFormatError('payload_size_or_stream_invalid')
    if not hmac.compare_digest(hashlib.sha256(original).hexdigest(), digest):
        raise ArchiveFormatError('original_hash_mismatch')
    decoded = _validated(original)
    if MARKER in decoded or META in decoded:
        raise ArchiveFormatError('nested_or_reserved_original')
    expected = _envelope(decoded)
    actual = {k: v for k, v in doc.items() if k != MARKER}
    # BSON comparison preserves absent/null, Int32/Int64, NaN and binary types.
    # Use the expected field order, but reject unknown or missing envelope keys.
    if set(actual) != set(expected) or BSON.encode({k: actual[k] for k in expected}) != BSON.encode(expected):
        raise ArchiveFormatError('query_envelope_mismatch')
    return original


def pack_raw(raw: bytes) -> bytes:
    """Return a smaller, verified envelope or the unchanged original bytes.

    Existing packed rows are validated and retained byte-for-byte. Reserved
    field collisions require review; no original key is discarded or renamed.
    """
    doc = _validated(raw)
    if _compressed(doc):
        unpack_raw(raw)
        return raw
    if MARKER in doc or META in doc:
        raise ArchiveFormatError('reserved_original_field')
    envelope = _envelope(doc)
    envelope[MARKER] = {'format': FORMAT, 'version': VERSION, 'codec': 'zlib',
        'raw_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'payload': Binary(zlib.compress(raw, level=9), subtype=0)}
    candidate = bytes(BSON.encode(envelope))
    if len(candidate) >= len(raw):
        return raw
    if unpack_raw(candidate) != raw:
        raise ArchiveFormatError('encode_verification_failed')
    return candidate


def decode_record(record: Mapping | RawBSONDocument) -> dict:
    """Compatibility reader, for both old rows and verified archive envelopes.

    All archive readers use this compatibility decoder. RawBSONDocument
    retains its exact input bytes; dict inputs were already decoded by the
    caller and cannot establish a raw-byte backup claim.
    """
    raw = record.raw if isinstance(record, RawBSONDocument) else bytes(BSON.encode(record))
    return _validated(unpack_raw(raw))
