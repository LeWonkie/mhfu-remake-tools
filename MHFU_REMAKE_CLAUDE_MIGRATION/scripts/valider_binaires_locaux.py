#!/usr/bin/env python3
"""Read-only local checks for game data. Never uploads game content."""
import argparse
from hashlib import sha256
from pathlib import Path

EXPECTED_DATA_SIZE = 782_028_800
HEAD_SIZE = 419_430_400
TAIL_SIZE = 362_598_400

def digest(path):
    h=sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,help='Complete DATA.BIN')
    p.add_argument('--head',type=Path,help='MHFU_DATA_400MiB.bin')
    p.add_argument('--tail',type=Path,help='MHFU_DATA_RESTE.bin')
    a=p.parse_args()
    if not (a.data or a.head or a.tail):
        p.error('Provide at least one input path')
    for kind,path,expected in [('DATA.BIN',a.data,EXPECTED_DATA_SIZE),('HEAD',a.head,HEAD_SIZE),('TAIL',a.tail,TAIL_SIZE)]:
        if path:
            if not path.is_file():
                print(f'{kind}: NOT FOUND: {path}')
                continue
            size=path.stat().st_size
            print(f'{kind}: {path} size={size}, expected={expected}, size_ok={size==expected}, sha256={digest(path)}')
    if a.data and a.head and a.tail and all(x.is_file() for x in [a.data,a.head,a.tail]):
        # Stream comparison; avoid copying huge files, and report mismatch offset.
        with a.data.open('rb') as whole:
            offset=0
            for part in [a.head,a.tail]:
                with part.open('rb') as fragment:
                    while True:
                        block=fragment.read(8*1024*1024)
                        if not block: break
                        if whole.read(len(block)) != block:
                            print(f'SPLIT DOES NOT MATCH COMPLETE DATA.BIN at or after byte {offset}')
                            return 1
                        offset += len(block)
            print(f'COMPARISON: exact concatenation for {offset} bytes: {whole.read(1)==b""}')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
