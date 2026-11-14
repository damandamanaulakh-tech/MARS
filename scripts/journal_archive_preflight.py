"""Prepare or verify an offline BSON archive candidate. Never accesses MongoDB."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.journal_archive_preflight import build, verify


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('prepare','verify'))
    parser.add_argument('--source',type=Path,required=True,help='Uncompressed raw .bson file from a verified export')
    parser.add_argument('--directory',type=Path,required=True,help='New private output directory, or existing candidate to verify')
    parser.add_argument('--metadata',type=Path,help='Optional native export index/options metadata; preserved verbatim')
    parser.add_argument('--expected-rows',type=int,help='Optional independently measured source count')
    args=parser.parse_args(argv)
    if args.operation=='verify' and (args.metadata is not None or args.expected_rows is not None):
        parser.error('metadata/expected-rows apply only to prepare')
    try:
        result=(build(args.source,args.directory,metadata=args.metadata,expected_rows=args.expected_rows)
                if args.operation=='prepare' else verify(args.source,args.directory))
    except Exception as exc:
        # Do not print credentials, document contents or raw server messages.
        print(json.dumps({'status':'failed','error_type':type(exc).__name__}),file=sys.stderr)
        return 1
    print(json.dumps({'status':'byte_verified_not_database_restored','result':result},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
