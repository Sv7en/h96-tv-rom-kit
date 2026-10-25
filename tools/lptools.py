#!/usr/bin/env python3
"""Locate ext4 partitions inside a raw (unsparse) super image.

Rockchip's super uses a custom LP metadata variant, so instead of trusting
it we scan for ext4 superblocks at 4K-aligned offsets and validate each hit
(magic, block size, sane block count). Partition identity comes from the
ext4 volume label where available ("vendor"/"product"/"odm"); the large
unlabeled one is system.

Verified against H96 Max Plus Android 10 super (0xc2800000 bytes):
    system  @0x100000  1837MB  label '/'
    vendor  @0x72f00000 113MB  label 'vendor'
    product @0x7a100000  305MB  label 'product'
    odm     @0x8d300000  544KB  label 'odm'
"""
import argparse
import json
import struct

EXT4_SB_OFF = 1024          # superblock offset inside filesystem
# canonical ext4 superblock field offsets
F_INODES = 0x00
F_BLOCKS = 0x04
F_FREE_BLOCKS = 0x0C
F_FREE_INODES = 0x10
F_LOG_BS = 0x18
F_MAGIC = 0x38
F_INCOMPAT = 0x60
F_RO_COMPAT = 0x64
F_UUID = 0x68
F_LABEL = 0x78


def parse_sb(sb, base_off):
    if len(sb) < 0x78 or struct.unpack_from('<H', sb, F_MAGIC)[0] != 0xEF53:
        return None
    log_bs = struct.unpack_from('<I', sb, F_LOG_BS)[0]
    if log_bs > 6:
        return None
    bs = 1024 << log_bs
    blocks = struct.unpack_from('<I', sb, F_BLOCKS)[0]
    if blocks == 0 or blocks > 1 << 28:
        return None
    free_blocks = struct.unpack_from('<I', sb, F_FREE_BLOCKS)[0]
    free_inodes = struct.unpack_from('<I', sb, F_FREE_INODES)[0]
    inodes = struct.unpack_from('<I', sb, F_INODES)[0]
    label = sb[F_LABEL:F_LABEL + 16].rstrip(b'\0').decode('ascii', 'replace')
    uuid = sb[F_UUID:F_UUID + 16].hex()
    return dict(offset=base_off, block_size=bs, blocks=blocks,
                size=blocks * bs, free_blocks=free_blocks,
                free_bytes=free_blocks * bs, inodes=inodes,
                free_inodes=free_inodes, label=label, uuid=uuid)


def scan(raw_path, limit_regions=None):
    """Scan 4K-aligned offsets for ext4 superblocks; skip image interiors."""
    found = []
    with open(raw_path, 'rb') as fh:
        import os
        total = os.path.getsize(raw_path)
        pos = 0x100000          # LP metadata area is always below 1MB
        while pos < total - EXT4_SB_OFF:
            fh.seek(pos + EXT4_SB_OFF)
            sb = fh.read(0x400)
            info = parse_sb(sb, pos)
            if info:
                found.append(info)
                pos = (pos + info['size'] + 0xFFF) & ~0xFFF
            else:
                pos += 0x1000
    # identify: labeled ones keep their name; the biggest unlabeled is system
    named = {}
    unnamed = []
    for info in found:
        name = info['label'] if info['label'] in (
            'system', 'vendor', 'product', 'odm') else None
        if name:
            named[name] = info
        else:
            unnamed.append(info)
    unnamed.sort(key=lambda i: -i['size'])
    if 'system' not in named and unnamed:
        named['system'] = unnamed.pop(0)
    for i, info in enumerate(unnamed):
        named.setdefault('fs%d' % i, info)
    return named


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('super_raw')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    parts = scan(args.super_raw)
    if args.json:
        print(json.dumps(parts, indent=2))
        return
    for name, p in sorted(parts.items(), key=lambda kv: kv[1]['offset']):
        print('%-8s @0x%08x size=%7dMB free=%7.1fMB inodes=%-6d free=%-5d label=%s' % (
            name, p['offset'], p['size'] >> 20, p['free_bytes'] / 1048576.0,
            p['inodes'], p['free_inodes'], p['label']))


if __name__ == '__main__':
    main()
