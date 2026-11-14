"""The proposed free-tier archive is measured offline; no production migration."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

from bson import BSON, ObjectId, Int64, Decimal128, Binary
import pytest

from web import journal_archive_codec as codec
from web import journal_archive_preflight as prep
from web.tests.test_journal_archive_codec_2026_11_14 import original


def source(tmp_path, raws=None):
    p=tmp_path/'journal.bson'
    p.write_bytes(b''.join(raws or [original(), BSON.encode({'_id':'tiny','action':'HOLD'})]))
    return p


def test_candidate_and_manifest_recover_every_byte_with_private_files(tmp_path,monkeypatch):
    import pymongo
    def forbidden(*a,**kw): raise AssertionError('preflight connected to Mongo')
    monkeypatch.setattr(pymongo,'MongoClient',forbidden)
    src=source(tmp_path); before=src.read_bytes(); out=tmp_path/'prepared'
    meta=tmp_path/'journal.metadata.json'; meta.write_text('{"indexes":[{"key":{"_id":1},"name":"_id_"}],"options":{}}')
    summary=prep.build(src,out,metadata=meta,expected_rows=2)
    assert prep.verify(src,out)==summary['verified_totals']
    assert src.read_bytes()==before
    assert summary['verified_totals']['rows']==2
    assert summary['verified_totals']['candidate_bytes'] < len(before)
    assert summary['database_restore']=='not_run' and summary['production_mutations']==0
    assert summary['durable_backup_copies']=='not_verified'
    assert 'unknown' in summary['source_completeness']
    with (out/'candidate.bson').open('rb') as fh:
        restored=b''.join(codec.unpack_raw(raw) for raw in prep.iter_bson(fh))
    assert restored==before
    assert (out/'source.metadata.json').read_bytes()==meta.read_bytes()
    assert out.stat().st_mode & 0o777 == 0o700
    for p in out.iterdir(): assert p.stat().st_mode & 0o077 == 0
    rows=[json.loads(x) for x in (out/'manifest.jsonl').read_text().splitlines()]
    assert rows[0]['id']=={'$oid':'000000000000000000000001'}
    assert rows[1]['smaller'] is False


def test_verifier_has_no_20000_row_export_cap(tmp_path):
    src=tmp_path/'many.bson'
    with src.open('wb') as fh:
        for i in range(20005): fh.write(BSON.encode({'_id':i,'action':'HOLD'}))
    result=prep.build(src,tmp_path/'out',expected_rows=20005)
    assert result['verified_totals']['rows']==20005
    assert prep.verify(src,tmp_path/'out')['rows']==20005


@pytest.mark.parametrize('damage',['truncated','bad_length','invalid_bson','duplicate_id','missing_id','expected_count'])
def test_failed_preparation_keeps_source_and_never_issues_success(tmp_path,damage):
    raw=original()
    data={'truncated':raw[:-2], 'bad_length':struct.pack('<i',codec.MAX_BSON_BYTES+1),
          'invalid_bson':b'\x05\0\0\0x', 'duplicate_id':raw+raw,
          'missing_id':BSON.encode({'evidence':{}}),'expected_count':raw}[damage]
    src=tmp_path/'source.bson'; src.write_bytes(data); out=tmp_path/'out'
    with pytest.raises((prep.PreflightError,codec.ArchiveFormatError)):
        prep.build(src,out,expected_rows=2 if damage=='expected_count' else None)
    assert src.read_bytes()==data
    assert (out/'FAILED.json').exists()
    assert not (out/'BYTE_VERIFIED.json').exists()
    with pytest.raises(prep.PreflightError): prep.verify(src,out)


@pytest.mark.parametrize('damage',['candidate','manifest','summary','metadata','extra_candidate','source','marker'])
def test_independent_recheck_rejects_modified_artifacts(tmp_path,damage):
    src=source(tmp_path); out=tmp_path/'out'; meta=tmp_path/'meta.json'; meta.write_text('{}')
    prep.build(src,out,metadata=meta)
    if damage=='candidate':
        p=out/'candidate.bson'; raw=p.read_bytes(); p.write_bytes(raw[:-1]+b'x')
    elif damage=='manifest':
        p=out/'manifest.jsonl'; p.write_bytes(p.read_bytes().replace(b'"row":1',b'"row":3'))
    elif damage=='summary':
        p=out/'summary.json'; d=json.loads(p.read_text()); d['verified_totals']['rows']=0;p.write_text(json.dumps(d))
    elif damage=='metadata': (out/'source.metadata.json').write_text('{"options":{"changed":true}}')
    elif damage=='extra_candidate':
        with (out/'candidate.bson').open('ab') as fh: fh.write(BSON.encode({'_id':'extra'}))
    elif damage=='source': src.write_bytes(src.read_bytes()+BSON.encode({'_id':'added-late'}))
    elif damage=='marker': (out/'BYTE_VERIFIED.json').write_text('{}')
    with pytest.raises((prep.PreflightError,codec.ArchiveFormatError)): prep.verify(src,out)


def test_existing_output_cannot_overwrite_backup(tmp_path):
    src=source(tmp_path); out=tmp_path/'out';out.mkdir(); sentinel=out/'important.bson'; sentinel.write_bytes(b'keep')
    with pytest.raises(FileExistsError): prep.build(src,out)
    assert sentinel.read_bytes()==b'keep' and not (out/'candidate.bson').exists()


def test_source_change_during_prepare_is_rejected(tmp_path,monkeypatch):
    src=source(tmp_path); original_pack=codec.pack_raw; changed=[]
    def replace(raw):
        if not changed:
            changed.append(True)
            with src.open('ab') as fh:fh.write(BSON.encode({'_id':'new'}))
        return original_pack(raw)
    monkeypatch.setattr(codec,'pack_raw',replace)
    with pytest.raises(prep.PreflightError,match='source_changed'):prep.build(src,tmp_path/'out')
    assert not (tmp_path/'out/BYTE_VERIFIED.json').exists()


def test_mixed_archive_and_unusual_bson_types_are_preserved(tmp_path):
    a=original(extra={'date_as_integer':Int64(1),'decimal':Decimal128('1.00'),'blob':Binary(b'abc',128)})
    b=BSON.encode({'_id':'other','value':None})
    src=source(tmp_path,[codec.pack_raw(a),b]); out=tmp_path/'out'
    result=prep.build(src,out)
    assert result['verified_totals']['source_bytes'] < result['verified_totals']['original_bytes']
    assert (out/'candidate.bson').read_bytes()==src.read_bytes()
    with (out/'candidate.bson').open('rb') as fh:assert b''.join(codec.unpack_raw(r) for r in prep.iter_bson(fh))==a+b


def test_cli_prepares_verifies_and_refuses_failed_input_without_reading_credentials(tmp_path):
    src=source(tmp_path); out=tmp_path/'out'; script=Path(__file__).resolve().parents[2]/'scripts/journal_archive_preflight.py'
    env={**os.environ,'MONGO_URL':'not-a-database-uri-fixture'}
    cmd=[sys.executable,str(script),'prepare','--source',str(src),'--directory',str(out),'--expected-rows','2']
    result=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=15)
    assert result.returncode==0 and json.loads(result.stdout)['status']=='byte_verified_not_database_restored'
    verify=subprocess.run([sys.executable,str(script),'verify','--source',str(src),'--directory',str(out)],capture_output=True,text=True,env=env,timeout=15)
    assert verify.returncode==0
    failed=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=15)
    assert failed.returncode==1 and 'not-a-database' not in failed.stderr


def test_empty_archive_is_explicitly_zero_rows_not_full_backup_proof(tmp_path):
    src=tmp_path/'empty.bson';src.write_bytes(b'')
    result=prep.build(src,tmp_path/'out',expected_rows=0)
    assert result['verified_totals']['rows']==0 and result['durable_backup_copies']=='not_verified'


def test_recheck_detects_duplicate_ids_even_with_consistent_hashes(tmp_path):
    src=source(tmp_path,[original()]);out=tmp_path/'out';prep.build(src,out)
    raw=src.read_bytes(); packed=(out/'candidate.bson').read_bytes()
    first=json.loads((out/'manifest.jsonl').read_text());second={**first,'row':2}
    manifest=(json.dumps(first)+'\n'+json.dumps(second)+'\n').encode()
    src.write_bytes(raw+raw);(out/'candidate.bson').write_bytes(packed+packed)
    (out/'manifest.jsonl').write_bytes(manifest)
    summary=json.loads((out/'summary.json').read_text());t=summary['verified_totals']
    for k in ('rows','source_bytes','original_bytes','candidate_bytes','smaller_records'):t[k]*=2
    t.update(source_sha256=hashlib.sha256(raw+raw).hexdigest(),candidate_sha256=hashlib.sha256(packed+packed).hexdigest(),manifest_sha256=hashlib.sha256(manifest).hexdigest())
    (out/'summary.json').write_text(json.dumps(summary))
    marker=json.loads((out/'BYTE_VERIFIED.json').read_text());marker['summary_sha256']=hashlib.sha256((out/'summary.json').read_bytes()).hexdigest();(out/'BYTE_VERIFIED.json').write_text(json.dumps(marker))
    with pytest.raises(prep.PreflightError,match='duplicate_original_id'):prep.verify(src,out)


@pytest.mark.parametrize('metadata',[b'[]',b'not json',b'x'*2_000_001])
def test_invalid_index_metadata_cannot_issue_success(tmp_path,metadata):
    src=source(tmp_path);meta=tmp_path/'meta.json';meta.write_bytes(metadata);out=tmp_path/'out'
    with pytest.raises(ValueError):prep.build(src,out,metadata=meta)
    assert (out/'FAILED.json').exists() and not (out/'BYTE_VERIFIED.json').exists()


@pytest.mark.parametrize('artifact',['manifest.jsonl','summary.json','BYTE_VERIFIED.json','source.metadata.json'])
def test_artifact_reads_are_bounded(tmp_path,monkeypatch,artifact):
    src=source(tmp_path);out=tmp_path/'out';meta=tmp_path/'meta.json';meta.write_text('{}')
    prep.build(src,out,metadata=meta)
    # Existing metadata is under 2 KiB; the replacement exceeds this independent bound.
    monkeypatch.setattr(prep,'MAX_METADATA_BYTES',2048)
    monkeypatch.setattr(prep,'MAX_MANIFEST_LINE',2048)
    (out/artifact).write_bytes(b'x'*2049)
    with pytest.raises(prep.PreflightError):prep.verify(src,out)
