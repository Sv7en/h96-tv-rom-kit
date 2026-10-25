#!/usr/bin/env python3
"""RKFW / RKAF (Rockchip update.img) unpack & repack tool.

Format notes, reverse-engineered and byte-validated against
RK3318_DC_R1_AP6330_10_20200707.0137.img (H96 Max Plus, Android 10):

RKFW header (0x66 bytes at file offset 0):
    +0x00 'RKFW'            magic
    +0x04 u32               header size (0x66)
    +0x08 u32               version (0x00050a00 = A10, 0x00000900 = A9)
    +0x0C..0x14             date/time fields (copied verbatim)
    +0x15 char[5]           product string, e.g. 'H223f'
    +0x1D u32               loader size; loader blob = file[0x66 : rkaf_off]
    +0x21 u32               RKAF region offset
    +0x25 u32               RKAF region size (incl. 4-byte trailer)
    trailer                 file ends with 32 ASCII chars = md5(file[:-32])

RKAF header (0x1000 bytes at rkaf_off):
    +0x00 'RKAF'            magic
    +0x04 u32               region size - 4
    +0x08 char[32]          product ('RK3328')
    ... version strings, embedded 'package-file' text ...
    entries: first entry name at first occurrence of b'bootloader\\0',
    stride 0x70 per entry:
        +0x00 char[32]      partition name
        +0x20 char[32]      package path, e.g. 'Image/boot.img'
        +0x34 char[4]       suffix blob 'bin'/'img'
        +0x5c u32           partition size on flash (512B LBAs)
        +0x60 u32           blob offset, relative to RKAF start, 0x800-aligned
        +0x64 u32           partition flash LBA (0xffffffff = n/a)
        +0x68 u32           blob size (LBAs)
    blobs live at RKAF+0x1000 ...
    last 4 bytes of the RKAF region: opaque trailer (algorithm unknown;
    the original bytes are carried over when repacking).
"""
import argparse
import hashlib
import json
import os
import struct
import sys

RKFW_HDR_SIZE = 0x66
RKAF_HDR_SIZE = 0x1000
ENTRY_STRIDE = 0x70


class Entry:
    def __init__(self, name, path, suffix, size_lba, blob_off, flash_lba,
                 blob_size_lba, hdr_off):
        self.name = name
        self.path = path
        self.suffix = suffix
        self.size_lba = size_lba
        self.blob_off = blob_off          # relative to RKAF start
        self.flash_lba = flash_lba
        self.blob_size_lba = blob_size_lba
        self.hdr_off = hdr_off            # entry name offset inside RKAF header

    @property
    def blob_bytes(self):
        return self.blob_size_lba * 512

    @property
    def blob_name(self):
        base = os.path.basename(self.path) if self.path else self.name
        if not base or base == 'RESERVED':
            base = self.name
        return base if '.' in base else '%s.%s' % (base, self.suffix or 'img')

    def to_dict(self):
        return dict(name=self.name, path=self.path, suffix=self.suffix,
                    size_lba=self.size_lba, blob_off=self.blob_off,
                    flash_lba=self.flash_lba, blob_size_lba=self.blob_size_lba,
                    blob_name=self.blob_name)


SPARSE_MAGIC = 0xED26FF3A


class RkfwImage:
    def __init__(self, filename):
        self.filename = filename
        self.file_size = os.path.getsize(filename)
        with open(filename, 'rb') as fh:
            self.rkfw_hdr = fh.read(RKFW_HDR_SIZE)
            if self.rkfw_hdr[:4] != b'RKFW':
                raise ValueError('not an RKFW image: %r' % filename)
            self.loader_size = struct.unpack_from('<I', self.rkfw_hdr, 0x1D)[0]
            self.rkaf_off = struct.unpack_from('<I', self.rkfw_hdr, 0x21)[0]
            self.fw_size = struct.unpack_from('<I', self.rkfw_hdr, 0x25)[0]
            fh.seek(self.rkaf_off)
            self.rkaf_hdr = fh.read(RKAF_HDR_SIZE)
            if self.rkaf_hdr[:4] != b'RKAF':
                raise ValueError('RKAF magic not found at 0x%x' % self.rkaf_off)
            self.rkaf_size_field = struct.unpack_from('<I', self.rkaf_hdr, 4)[0]
            fh.seek(self.file_size - 32)
            self.md5_tail = fh.read(32)

        base = self.rkaf_hdr.find(b'bootloader\x00')
        if base < 0:
            raise ValueError('entry table not found in RKAF header')
        self.entries = []
        i = 0
        while base + (i + 1) * ENTRY_STRIDE <= RKAF_HDR_SIZE:
            raw = self.rkaf_hdr[base + i * ENTRY_STRIDE:
                                base + (i + 1) * ENTRY_STRIDE]
            name = raw[:32].split(b'\0')[0].decode('ascii', 'replace').strip()
            if not name:
                break
            path = raw[0x20:0x40].split(b'\0')[0].decode('ascii', 'replace').strip()
            suffix = raw[0x34:0x38].split(b'\0')[0].decode('ascii', 'replace').strip()
            size_lba, blob_off, flash_lba, blob_size_lba = struct.unpack_from(
                '<4I', raw, 0x5C)
            self.entries.append(Entry(name, path, suffix, size_lba, blob_off,
                                      flash_lba, blob_size_lba,
                                      base + i * ENTRY_STRIDE))
            i += 1

    def read_blob(self, entry):
        if entry.blob_off == 0 or entry.blob_size_lba == 0:
            return b''
        with open(self.filename, 'rb') as fh:
            fh.seek(self.rkaf_off + entry.blob_off)
            return fh.read(entry.blob_bytes)

    def blob_extent(self, entry):
        """(abs_start, abs_end) of a blob's real bytes in the file.

        Entry-declared blob sizes are unreliable (stock image: bootloader
        declares 47KB but holds a 190KB loader copy; parameter declares
        512B but holds ~1.2KB of text; super declares 587MB but the sparse
        stream runs to EOF). Derived truth instead:
        - blobs are packed in blob_off order; a blob ends where the next
          one begins (padding in between is zeros);
        - the last blob, when sparse, ends where its chunk stream ends.
        """
        with_blobs = [e for e in self.entries
                      if e.blob_off and e.blob_size_lba]
        if entry not in with_blobs:
            return (0, 0)
        with_blobs.sort(key=lambda e: e.blob_off)
        idx = with_blobs.index(entry)
        start = self.rkaf_off + entry.blob_off
        if idx + 1 < len(with_blobs):
            return (start, self.rkaf_off + with_blobs[idx + 1].blob_off)
        # last blob: sparse streams end where their chunks end
        with open(self.filename, 'rb') as fh:
            fh.seek(start)
            if struct.unpack('<I', fh.read(4))[0] == SPARSE_MAGIC:
                fh.seek(start)
                (magic, major, minor, file_hdr_sz, chunk_hdr_sz, blk_sz,
                 total_blks, total_chunks, _cs) = struct.unpack(
                    '<IHHHHIIII', fh.read(28))
                pos = file_hdr_sz
                for _ in range(total_chunks):
                    fh.seek(start + pos)
                    hdr = fh.read(12)
                    if len(hdr) < 12:
                        raise ValueError('sparse chunk header truncated')
                    _t, _r, _c, total_sz = struct.unpack('<HHII', hdr)
                    pos += total_sz
                return (start, start + pos)
        return (start, start + entry.blob_bytes)

    def read_loader(self):
        with open(self.filename, 'rb') as fh:
            fh.seek(RKFW_HDR_SIZE)
            return fh.read(self.loader_size)

    def info(self):
        lines = ['file: %s (0x%x bytes)' % (self.filename, self.file_size),
                 'rkaf @0x%x, fw size 0x%x, md5 tail %r' % (
                     self.rkaf_off, self.fw_size, self.md5_tail),
                 '%-13s %-10s %-11s %-10s %-11s' % (
                     'name', 'sizeLBA', 'blobOff', 'flashLBA', 'blobSizeLBA')]
        for e in self.entries:
            lines.append('%-13s %-10x %-11x %-10x %-11x' % (
                e.name, e.size_lba, e.blob_off,
                e.flash_lba & 0xffffffff, e.blob_size_lba))
        return '\n'.join(lines)

    def unpack(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, 'loader.bin'), 'wb') as fh:
            fh.write(self.read_loader())
        meta = dict(rkaf_off=self.rkaf_off, fw_size=self.fw_size,
                    file_size=self.file_size, entries=[])
        for e in self.entries:
            start, end = self.blob_extent(e)
            if start < end:
                with open(self.filename, 'rb') as src, \
                        open(os.path.join(outdir, e.blob_name), 'wb') as dst:
                    src.seek(start)
                    remaining = end - start
                    while remaining > 0:
                        chunk = src.read(min(1 << 24, remaining))
                        if not chunk:
                            break
                        dst.write(chunk)
                        remaining -= len(chunk)
            meta['entries'].append(e.to_dict())
        with open(os.path.join(outdir, 'entries.json'), 'w') as fh:
            json.dump(meta, fh, indent=2)
        with open(os.path.join(outdir, 'rkaf_header.bin'), 'wb') as fh:
            fh.write(self.rkaf_hdr)
        return meta

    def repack(self, outdir, replacements, out_path):
        """Rebuild update.img with replaced partition blobs.

        replacements: {partition_name: path_to_new_blob_file}

        Verified layout rules of the stock image:
        - regular blobs sit packed back-to-back (0x800-aligned gaps) before
          the super blob; the super sparse blob is LAST and runs to EOF
          (followed by zero padding and the 32-byte ASCII md5).
        - a replacement no larger than the original slot is written in
          place; otherwise it is appended after the last blob.
        - the super blob's entry blob_size_lba is not load-bearing
          (stock value already disagrees with the real stream length),
          so it is copied verbatim.
        """
        repl = dict(replacements)
        entry_by_name = {e.name: e for e in self.entries}
        for name in repl:
            if name not in entry_by_name:
                raise ValueError('unknown partition: %s' % name)

        extents = {e.name: self.blob_extent(e) for e in self.entries}
        orig_stream_end = max(end for _, end in extents.values())  # super's
        # everything between the last blob's end and the md5 tail — zero
        # padding plus a 4-byte opaque trailer (algorithm unidentified;
        # copied verbatim, see README risk notes)
        with open(self.filename, 'rb') as fh:
            fh.seek(orig_stream_end)
            tail_bytes = fh.read(self.file_size - 32 - orig_stream_end)
        append_cursor = None    # set when a replacement must be appended

        with open(out_path, 'wb') as out:
            out.write(self.rkfw_hdr)
            with open(self.filename, 'rb') as src:
                src.seek(RKFW_HDR_SIZE)
                remaining = self.rkaf_off - RKFW_HDR_SIZE
                while remaining > 0:
                    chunk = src.read(min(1 << 24, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)

            # blobs: unchanged ones copied verbatim at their real extents
            pending = []     # (abs_off, bytes-or-lazy, size)
            for e in self.entries:
                if e.blob_off == 0 or e.blob_size_lba == 0:
                    continue
                start, end = extents[e.name]
                if e.name in repl:
                    data = open(repl[e.name], 'rb').read()
                    slot = end - start
                    if e.name == 'super':
                        pending.append((start, data))       # last blob
                    elif len(data) <= slot:
                        pending.append((start, data + b'\0' * (slot - len(data))))
                    else:
                        if append_cursor is None:
                            append_cursor = (orig_stream_end
                                             + 0x7FF) & ~0x7FF
                        pending.append((append_cursor, data))
                        self._patch_entry(
                            e, append_cursor - self.rkaf_off,
                            (len(data) + 511) // 512)
                        append_cursor = (append_cursor + len(data)
                                         + 0x7FF) & ~0x7FF
                else:
                    pending.append((start, ('slice', start, end - start)))

            rkaf_hdr = bytearray(self.rkaf_hdr)
            for e in self.entries:   # apply any entry patches recorded
                if getattr(e, '_new_off', None) is not None:
                    struct.pack_into('<I', rkaf_hdr, e.hdr_off + 0x60,
                                     e._new_off)
                    struct.pack_into('<I', rkaf_hdr, e.hdr_off + 0x68,
                                     e._new_size_lba)
            out.write(bytes(rkaf_hdr))

            pending.sort(key=lambda p: p[0])
            with open(self.filename, 'rb') as src:
                for abs_off, item in pending:
                    out.seek(abs_off)
                    if isinstance(item, tuple):
                        _, s, n = item
                        src.seek(s)
                        left = n
                        while left > 0:
                            chunk = src.read(min(1 << 24, left))
                            if not chunk:
                                break
                            out.write(chunk)
                            left -= len(chunk)
                    else:
                        out.write(item)
                stream_end = out.tell()
            out.write(tail_bytes)
            total = out.tell()
            out.close()
            md5 = hashlib.md5()
            with open(out_path, 'rb') as fh:
                remaining = total
                while remaining > 0:
                    chunk = fh.read(min(1 << 24, remaining))
                    md5.update(chunk)
                    remaining -= len(chunk)
            with open(out_path, 'ab') as fh:
                fh.write(md5.hexdigest().encode('ascii'))
        return out_path

    def _patch_entry(self, entry, new_off, new_size_lba):
        entry._new_off = new_off
        entry._new_size_lba = new_size_lba


def main():
    ap = argparse.ArgumentParser(description='RKFW/RKAF update.img tool')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info', help='print partition entry table')
    p.add_argument('image')

    p = sub.add_parser('unpack', help='unpack all blobs to a directory')
    p.add_argument('image')
    p.add_argument('outdir')

    p = sub.add_parser('pack', help='repack with replaced partition blobs')
    p.add_argument('image')
    p.add_argument('--set', action='append', default=[],
                   metavar='NAME=FILE', help='replace blob of partition NAME')
    p.add_argument('-o', '--output', required=True)

    args = ap.parse_args()
    img = RkfwImage(args.image)
    if args.cmd == 'info':
        print(img.info())
    elif args.cmd == 'unpack':
        meta = img.unpack(args.outdir)
        print('unpacked %d partitions to %s' % (len(meta['entries']), args.outdir))
    elif args.cmd == 'pack':
        repl = {}
        for item in args.set:
            name, _, fn = item.partition('=')
            repl[name.strip()] = fn.strip()
        img.repack(None, repl, args.output)
        print('wrote %s' % args.output)


if __name__ == '__main__':
    main()
