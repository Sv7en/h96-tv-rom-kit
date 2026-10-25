#!/usr/bin/env python3
"""Android sparse image (simg) <-> raw image converter, pure Python.

Chunk types: 0xCAC1 raw, 0xCAC2 crc32, 0xCAC3 fill, 0xCAC4 dont-care.
"""
import argparse
import os
import struct

SPARSE_MAGIC = 0xED26FF3A
CHUNK_TYPE_RAW = 0xCAC1
CHUNK_TYPE_FILL = 0xCAC3
CHUNK_TYPE_DONT_CARE = 0xCAC4


class SparseReader:
    """Reader over a sparse image; exposes logical runs of raw data."""

    def __init__(self, filename):
        self.fh = open(filename, 'rb')
        hdr = self.fh.read(28)
        (magic, self.major, self.minor, file_hdr_sz, chunk_hdr_sz,
         self.blk_sz, self.total_blks, self.total_chunks,
         self.checksum) = struct.unpack('<IHHHHIIII', hdr)
        if magic != SPARSE_MAGIC:
            raise ValueError('not a sparse image: %s' % filename)
        self.chunks = []   # (logical_off, data_off, length)
        pos = file_hdr_sz
        logical = 0
        for _ in range(self.total_chunks):
            self.fh.seek(pos)
            ctype, _, blk_cnt, total_sz = struct.unpack('<HHII', self.fh.read(12))
            data_off = pos + chunk_hdr_sz
            if ctype == CHUNK_TYPE_RAW:
                self.chunks.append((logical, data_off, blk_cnt * self.blk_sz))
            logical += blk_cnt * self.blk_sz
            pos += total_sz
        self.logical_size = logical

    def read_run(self, index):
        return self.chunks[index]

    def read_at(self, logical):
        """Read blk_sz bytes at a logical offset (must land in a raw run)."""
        for start, data_off, length in self.chunks:
            if start <= logical < start + length:
                self.fh.seek(data_off + (logical - start))
                return self.fh.read(self.blk_sz)
        return b'\0' * self.blk_sz

    def close(self):
        self.fh.close()


def unsparse(src, dst):
    r = SparseReader(src)
    with open(dst, 'wb') as out:
        pos = 0
        for logical, data_off, length in r.chunks:
            if logical > pos:
                out.write(b'\0' * (logical - pos))
            r.fh.seek(data_off)
            remaining = length
            while remaining > 0:
                chunk = r.fh.read(min(1 << 24, remaining))
                if not chunk:
                    raise IOError('short read in sparse data')
                out.write(chunk)
                remaining -= len(chunk)
            pos = logical + length
        if pos < r.logical_size:
            out.write(b'\0' * (r.logical_size - pos))
    return r.logical_size


def sparse(src, dst, blk_sz=4096):
    """Convert raw image to sparse, skipping all-zero blocks.

    Zero regions become DONT_CARE chunks (zero-length payloads that still
    advance the logical block cursor); non-zero runs become RAW chunks.
    Chunk block counts must sum to total_blks in the file header.
    """
    total = os.path.getsize(src)
    total_blks = (total + blk_sz - 1) // blk_sz
    chunk_hdrs = 0
    cursor_blks = 0          # logical blocks accounted for so far
    with open(src, 'rb') as fh, open(dst, 'wb') as out:
        out.write(struct.pack('<IHHHHIIII', SPARSE_MAGIC, 1, 0, 28, 12,
                              blk_sz, total_blks, 0, 0))
        run = None    # [count, header_pos]

        def close_run():
            nonlocal run, chunk_hdrs
            if run is None:
                return
            end = out.tell()
            out.seek(run[1])
            out.write(struct.pack('<HHII', CHUNK_TYPE_RAW, 0, run[0],
                                  12 + run[0] * blk_sz))
            out.seek(end)
            run = None

        def emit_gap(blocks):
            nonlocal chunk_hdrs, cursor_blks
            if blocks <= 0:
                return
            out.write(struct.pack('<HHII', CHUNK_TYPE_DONT_CARE, 0,
                                  blocks, 12))
            chunk_hdrs += 1
            cursor_blks += blocks

        for base in range(0, total, blk_sz):
            fh.seek(base)
            blk = fh.read(blk_sz)
            if len(blk) < blk_sz:
                blk = blk + b'\0' * (blk_sz - len(blk))
            idx = base // blk_sz
            if any(blk):
                if run is None:
                    close_run()
                    emit_gap(idx - cursor_blks)
                    run = [0, out.tell()]
                    out.write(struct.pack('<HHII', CHUNK_TYPE_RAW, 0, 0, 0))
                    chunk_hdrs += 1
                out.write(blk)
                run[0] += 1
                cursor_blks += 1
            else:
                close_run()
        close_run()
        emit_gap(total_blks - cursor_blks)
        end = out.tell()
        out.seek(20)
        out.write(struct.pack('<I', chunk_hdrs))
        out.seek(end)
    return chunk_hdrs


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('unsparse'); p.add_argument('src'); p.add_argument('dst')
    p = sub.add_parser('sparse'); p.add_argument('src'); p.add_argument('dst')
    p.add_argument('-b', '--block', type=int, default=4096)
    args = ap.parse_args()
    if args.cmd == 'unsparse':
        print('raw size: %d' % unsparse(args.src, args.dst))
    else:
        print('chunks: %d' % sparse(args.src, args.dst, args.block))


if __name__ == '__main__':
    main()
