"""Raw-BSON archive preparation; no database or production writes."""
from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
import struct
import zlib

from bson import BSON, Binary, Decimal128, Int64, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS
from bson.raw_bson import RawBSONDocument
import pytest

from web import journal_archive_codec as codec


def original(**extra):
    return BSON.encode({'_id': ObjectId('000000000000000000000001'),
        'ts': '2026-11-13T06:24:29+00:00', 'action': 'SCAN_DECISION',
        'ticker': 'AAA.NS', 'mode': 'intraday', 'decision_id': 'a'*32,
        'evidence': {'ticker':'AAA.NS', 'mode':'intraday', 'gates': {'data': {'state':'unavailable'}},
                     'repeated': 'preserve the complete explanation '*400}, **extra})


def test_exact_raw_bson_round_trip_preserves_types_unknowns_and_query_envelope():
    raw = original(unknown={'wide':Int64(1), 'decimal':Decimal128('1.2300'),
        'binary':Binary(b'\x01\x02', subtype=128), 'date':datetime(2020,1,1),
        'distant':DatetimeMS(2**62), 'regex':Regex('a.*','im'), 'ts':Timestamp(123,4),
        'nan':float('nan'), 'zero':-0.0, 'null':None})
    packed = codec.pack_raw(raw)
    assert len(packed) < len(raw)
    assert codec.unpack_raw(packed) == raw
    record = codec.decode_record(RawBSONDocument(packed, codec.RAW_OPTIONS))
    assert BSON.encode(record) == raw
    doc = BSON(packed).decode(codec_options=codec.OPTIONS)
    assert doc['_id'] == ObjectId('000000000000000000000001')
    assert doc[codec.META]['has_gates'] is True
    assert doc[codec.META]['ticker'] == 'AAA.NS'
    assert 'evidence' not in doc


def test_raw_unknown_duplicate_fields_survive_without_json_normalization():
    a=BSON.encode({'x':Int64(1)}); b=BSON.encode({'x':2})
    raw=original()
    body=raw[4:-1]+a[4:-1]+b[4:-1]
    raw=struct.pack('<i',len(body)+5)+body+b'\0'
    assert codec.unpack_raw(codec.pack_raw(raw)) == raw


def test_small_records_stay_original_and_pack_is_idempotent():
    tiny=BSON.encode({'_id':'small','action':'HOLD'})
    assert codec.pack_raw(tiny)==tiny
    packed=codec.pack_raw(original())
    assert codec.pack_raw(packed)==packed
    assert codec.decode_record(BSON(tiny).decode()) == {'_id':'small','action':'HOLD'}


def test_mapping_and_raw_document_decode_without_mutating_input():
    raw=original(); packed=BSON(codec.pack_raw(raw)).decode(); before=deepcopy(packed)
    assert BSON.encode(codec.decode_record(packed)) == raw
    assert packed == before
    legacy=BSON(raw).decode(); expected=deepcopy(legacy)
    assert codec.decode_record(legacy)==expected and legacy==expected


@pytest.mark.parametrize('mutation', ['version','codec','hash','length','payload','truncated',
    'trailing','bomb','wrong_id','wrong_metadata','extra_envelope','missing_metadata',
    'format','marker_type','missing_marker'])
def test_damaged_or_unsupported_envelope_is_explicitly_unavailable(mutation):
    d=BSON(codec.pack_raw(original())).decode(); c=d[codec.MARKER]
    if mutation=='version': c['version']=99
    elif mutation=='codec': c['codec']='unknown'
    elif mutation=='hash': c['sha256']='0'*64
    elif mutation=='length': c['raw_bytes']=1
    elif mutation=='payload': c['payload']='not binary'
    elif mutation=='truncated': c['payload']=Binary(bytes(c['payload'])[:-2])
    elif mutation=='trailing': c['payload']=Binary(bytes(c['payload'])+b'extra')
    elif mutation=='bomb': c['raw_bytes']=100; c['payload']=Binary(zlib.compress(b'x'*100000))
    elif mutation=='wrong_id': d['_id']='different'
    elif mutation=='wrong_metadata': d[codec.META]['ticker']='OTHER.NS'
    elif mutation=='extra_envelope': d['new_field']='unverified'
    elif mutation=='missing_metadata': del d[codec.META]
    elif mutation=='format': c['format']='mars.journal.raw-bson-future'
    elif mutation=='marker_type': d[codec.MARKER]='damaged marker'
    elif mutation=='missing_marker': del d[codec.MARKER]
    with pytest.raises(codec.ArchiveFormatError): codec.unpack_raw(BSON.encode(d))


@pytest.mark.parametrize('bad', [b'', b'1234', struct.pack('<i',100)+b'\0', b'\x05\0\0\0x'])
def test_invalid_bson_is_not_a_successful_empty_archive(bad):
    with pytest.raises(codec.ArchiveFormatError): codec.pack_raw(bad)


def test_reserved_key_and_missing_id_are_not_silently_rewritten():
    raw=original(**{codec.MARKER: {'unrelated_legacy_value':True}})
    with pytest.raises(codec.ArchiveFormatError): codec.pack_raw(raw)
    with pytest.raises(codec.ArchiveFormatError): codec.unpack_raw(raw)
    with pytest.raises(codec.ArchiveFormatError): codec.pack_raw(BSON.encode({'message':'no primary key'}))


def test_size_limit_applies_before_decode_and_decompression(monkeypatch):
    raw=original(); packed=codec.pack_raw(raw)
    monkeypatch.setattr(codec,'MAX_BSON_BYTES',len(raw)-1)
    with pytest.raises(codec.ArchiveFormatError): codec.pack_raw(raw)
    with pytest.raises(codec.ArchiveFormatError): codec.unpack_raw(packed)


def test_envelope_preserves_absent_vs_null_and_query_identity():
    a=original(pre_mars=None, trade_id='t'*32)
    d=BSON(codec.pack_raw(a)).decode()
    assert 'pre_mars' in d and d['pre_mars'] is None and d['trade_id']=='t'*32
    b=BSON(codec.pack_raw(original())).decode()
    assert 'pre_mars' not in b
    assert codec.unpack_raw(BSON.encode(d))==a
