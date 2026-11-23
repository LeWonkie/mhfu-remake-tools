#!/usr/bin/env python3
"""Index or extract ATRAC3-in-WAVE resources from the MHFU DATA.BIN tail.

Run: python extract_mhfu_audio.py MHFU_DATA_RESTE.bin --out MHFU_AUDIO --extract
Without --extract, writes only a lightweight index.
This finds recognizable RIFF/WAVE resources without decrypting other game assets.
"""
import argparse
import csv
import struct
from pathlib import Path

SECTOR = 2048
TAIL_START_IN_ORIGINAL = 400 * 1024 * 1024

def identify(path):
    length = path.stat().st_size
    hits = []
    with path.open('rb') as fp:
        for off in range(0, length - 80, SECTOR):
            fp.seek(off)
            header = fp.read(80)
            if header[:4] != b'RIFF' or header[8:12] != b'WAVE':
                continue
            if header[12:16] != b'fmt ' or header[52:56] != b'fact':
                continue
            size = struct.unpack_from('<I',header,4)[0] + 8
            if size < 80 or off + size > length:
                continue
            fmt_size = struct.unpack_from('<I',header,16)[0]
            codec, nch, rate, bytespersec, align, bits = struct.unpack_from('<HHIIHH', header, 20)
            if fmt_size != 32 or codec != 0x0270 or nch not in (1,2) or rate != 44100:
                continue
            samples = struct.unpack_from('<I', header, 60)[0]
            hits.append((off, size, nch, rate, samples / rate, align))
    for a,b in zip(hits,hits[1:]):
        if a[0] + a[1] > b[0]:
            raise ValueError('Found overlapping RIFF streams; manual review required.')
    return hits

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='MHFU_DATA_RESTE.bin')
    parser.add_argument('--out', type=Path, default=Path('MHFU_AUDIO'))
    parser.add_argument('--extract', action='store_true', help='Also write the WAVE files (~183 MiB)')
    args = parser.parse_args()
    hits = identify(args.source)
    args.out.mkdir(parents=True,exist_ok=True)
    catalogue=args.out/'MHFU_audio_index.txt'
    with catalogue.open('w',encoding='utf8') as fp:
        print('MHFU DATA.BIN tail — ATRAC3/WAVE sector scan',file=fp)
        print('Source: '+str(args.source),file=fp)
        print('Number of validated RIFF/WAVE resources: '+str(len(hits)),file=fp)
        print('Offsets relative to original DATA.BIN assume the tail starts at byte 419430400.',file=fp)
        print('Audio file labels do NOT identify the sound contents.',file=fp)
        print('ID\tOFFSET_IN_TAIL\tOFFSET_IN_ORIGINAL\tSIZE_BYTES\tDURATION_SECONDS\tCHANNELS\tRATE_HZ\tBLOCK_ALIGN',file=fp)
        for j,(off,size,nch,rate,duration,align) in enumerate(hits,1):
            print(f'{j:03d}\t{off}\t{off+TAIL_START_IN_ORIGINAL}\t{size}\t{duration:.3f}\t{nch}\t{rate}\t{align}',file=fp)
    if args.extract:
        with args.source.open('rb') as src:
            for j,(off,size,*_) in enumerate(hits,1):
                src.seek(off)
                with (args.out/f'MHFU_audio_{j:03d}.wav').open('wb') as dst:
                    n=size
                    while n:
                        b=src.read(min(n,1<<20))
                        if not b:raise EOFError('Source shortened during extraction')
                        dst.write(b);n-=len(b)
    print(f'{len(hits)} ATRAC3/WAVE files indexed; catalogue: {catalogue}')
    if args.extract:print(f'Extracted {len(hits)} files in {args.out}')

if __name__=='__main__':main()
