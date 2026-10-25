#!/usr/bin/env python3
"""Read-only ext4 directory lister for images inside a raw super image.

Windows-friendly alternative to mounting: walks the ext4 extent tree and
directory entries directly. Handles regular files, directories and
symlinks; requires extent-based inodes (make_ext4fs output, always true
for Rockchip OTA images).

Usage:
  python3 ext4ls.py <image> --offset 0x100000 /app
  python3 ext4ls.py <image> --offset 0x100000 / --tree --depth 2
"""
import argparse
import os
import struct

EXT4_EXTENTS_FL = 0x80000
EXT4_HTREE_FL = 0x1000


class Ext4:
    def __init__(self, path, offset=0):
        self.fh = open(path, 'rb')
        self.base = offset
        sb = self._read(offset + 1024, 1024)
        if struct.unpack_from('<H', sb, 0x38)[0] != 0xEF53:
            raise ValueError('no ext4 superblock at offset 0x%x' % offset)
        log_bs = struct.unpack_from('<I', sb, 0x18)[0]
        self.bs = 1024 << log_bs
        self.inodes = struct.unpack_from('<I', sb, 0)[0]
        self.ipg = struct.unpack_from('<I', sb, 0x28)[0]
        self.inode_size = struct.unpack_from('<H', sb, 0x58)[0] or 128
        incompat = struct.unpack_from('<I', sb, 0x60)[0]
        desc_size = struct.unpack_from('<H', sb, 0xFE)[0]
        self.desc_size = desc_size if (incompat & 0x80 and desc_size >= 64) else 32
        # block group descriptor table: block 1 for bs>1k
        bgdt_block = 1 if self.bs > 1024 else 2
        groups = (self.inodes + self.ipg - 1) // self.ipg
        bgdt = self._read(offset + bgdt_block * self.bs,
                          groups * self.desc_size)
        self.inode_tables = [
            struct.unpack_from('<I', bgdt, g * self.desc_size + 8)[0]
            for g in range(groups)]

    def _read(self, abs_off, n):
        self.fh.seek(abs_off)
        data = b''
        while len(data) < n:
            chunk = self.fh.read(n - len(data))
            if not chunk:
                raise IOError('short read at 0x%x' % abs_off)
            data += chunk
        return data

    def inode(self, n):
        group, idx = (n - 1) // self.ipg, (n - 1) % self.ipg
        addr = self.base + self.inode_tables[group] * self.bs + idx * self.inode_size
        raw = self._read(addr, self.inode_size)
        mode = struct.unpack_from('<H', raw, 0)[0]
        size = struct.unpack_from('<I', raw, 4)[0] | (
            struct.unpack_from('<I', raw, 0x6C)[0] << 32)
        flags = struct.unpack_from('<I', raw, 0x20)[0]
        links = struct.unpack_from('<H', raw, 0x1A)[0]
        return dict(num=n, mode=mode, size=size, flags=flags, links=links,
                    raw=raw)

    def _extents_of(self, inode):
        """[(logical_block, phys_block, len_blocks)] from the extent tree."""
        out = []

        def walk(block_abs, depth):
            node = self._read(self.base + block_abs * self.bs, self.bs)
            magic, entries, _max, dep = struct.unpack_from('<HHHH', node, 0)
            if magic != 0xF30A:
                raise ValueError('bad extent magic')
            for i in range(entries):
                off = 12 + i * 12
                if dep == 0:
                    lblock = struct.unpack_from('<I', node, off)[0]
                    elen = struct.unpack_from('<H', node, off + 4)[0] & 0x7FFF
                    phi = struct.unpack_from('<H', node, off + 6)[0]
                    plo = struct.unpack_from('<I', node, off + 8)[0]
                    out.append((lblock, (phi << 32) | plo, elen))
                else:
                    plo = struct.unpack_from('<I', node, off + 4)[0]
                    phi = struct.unpack_from('<H', node, off + 8)[0]
                    walk((phi << 32) | plo, dep - 1)

        if not (inode['flags'] & EXT4_EXTENTS_FL):
            raise ValueError('inode %d: not extent-based' % inode['num'])
        root = inode['raw'][40:100]
        magic, entries, _max, dep = struct.unpack_from('<HHHH', root, 0)
        if magic != 0xF30A:
            raise ValueError('inode %d: no extent header' % inode['num'])
        if dep == 0:
            for i in range(entries):
                off = 12 + i * 12
                lblock = struct.unpack_from('<I', root, off)[0]
                elen = struct.unpack_from('<H', root, off + 4)[0] & 0x7FFF
                phi = struct.unpack_from('<H', root, off + 6)[0]
                plo = struct.unpack_from('<I', root, off + 8)[0]
                out.append((lblock, (phi << 32) | plo, elen))
        else:
            for i in range(entries):
                off = 12 + i * 12
                plo = struct.unpack_from('<I', root, off + 4)[0]
                phi = struct.unpack_from('<H', root, off + 8)[0]
                walk((phi << 32) | plo, dep - 1)
        return sorted(out)

    def read_file(self, n):
        inode = self.inode(n)
        size = inode['size']
        extents = self._extents_of(inode)
        buf = bytearray()
        pos_block = 0
        for lblock, phys, elen in extents:
            if lblock > pos_block:
                buf += b'\0' * ((lblock - pos_block) * self.bs)
            buf += self._read(self.base + phys * self.bs, elen * self.bs)
            pos_block = lblock + elen
        return bytes(buf[:size])

    def listdir(self, n):
        data = self.read_file(n)
        out = []
        pos = 0
        while pos < len(data):
            ino, rec_len, name_len, ftype = struct.unpack_from(
                '<IHBB', data, pos)
            if rec_len < 8 or pos + rec_len > len(data):
                break
            if ino and name_len:
                name = data[pos + 8: pos + 8 + name_len].decode(
                    'utf-8', 'replace')
                if name not in ('.', '..'):
                    out.append((name, ino, ftype))
            pos += rec_len
        return out

    def lookup(self, path):
        cur = 2
        for part in [p for p in path.split('/') if p]:
            found = False
            for name, ino, ftype in self.listdir(cur):
                if name == part:
                    cur = ino
                    found = True
                    break
            if not found:
                raise FileNotFoundError(path)
        return cur

    def walk(self, n, path, depth=0, max_depth=6):
        for name, ino, ftype in self.listdir(n):
            p = path + '/' + name
            if ftype == 2:
                inode = self.inode(ino)
                print('%s%s/  [dir]' % ('  ' * depth, p))
                if depth < max_depth:
                    self.walk(ino, p, depth + 1, max_depth)
            else:
                inode = self.inode(ino)
                print('%s%-72s %10d  ino=%d' % ('  ' * depth, p, inode['size'], ino))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('path', nargs='?', default='/')
    ap.add_argument('--offset', default='0', help='partition offset in image')
    ap.add_argument('--tree', action='store_true')
    ap.add_argument('--depth', type=int, default=4)
    args = ap.parse_intermixed_args()
    fs = Ext4(args.image, int(args.offset, 0))
    ino = fs.lookup(args.path)
    if args.tree:
        fs.walk(ino, args.path.rstrip('/'), 0, args.depth)
    else:
        for name, i, t in fs.listdir(ino):
            inode = fs.inode(i)
            kind = 'dir' if t == 2 else 'link' if t == 7 else 'file'
            print('%-40s %-5s %10d  ino=%d' % (name, kind, inode['size'], i))


if __name__ == '__main__':
    main()
