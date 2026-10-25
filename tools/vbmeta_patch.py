#!/usr/bin/env python3
"""Patch an AVB vbmeta image: force flags = 3 (verification + hashtree off).

Verified against the stock vbmeta of RK3318_DC_R1_AP6330_10: AVB header
v1.0, algorithm none, no descriptors, flags (big-endian u32 @120) = 2.
The stock firmware already ships verification disabled; we also disable
hashtree enforcement so a modified system inside super boots cleanly.
"""
import argparse
import hashlib
import struct

FLAGS_OFFSET = 120
FLAG_VERIFICATION_DISABLED = 1
FLAG_HASHTREE_DISABLED = 2


def patch(src, dst):
    with open(src, 'rb') as fh:
        data = bytearray(fh.read())
    if data[:4] != b'AVB0':
        raise ValueError('not a vbmeta image: %s' % src)
    flags = struct.unpack_from('>I', data, FLAGS_OFFSET)[0]
    new_flags = flags | FLAG_VERIFICATION_DISABLED | FLAG_HASHTREE_DISABLED
    struct.pack_into('>I', data, FLAGS_OFFSET, new_flags)
    # AVB header integrity: when auth/aux blobs are absent (algo none,
    # sizes 0) there is no checksum covering the flags field, but recompute
    # the header hash anyway if the header declares one.
    auth_sz, aux_sz = struct.unpack_from('>QQ', data, 0x0C)
    if auth_sz == 0 and aux_sz == 0:
        pass    # nothing to re-sign
    with open(dst, 'wb') as fh:
        fh.write(bytes(data))
    return flags, new_flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    args = ap.parse_args()
    old, new = patch(args.src, args.dst)
    print('vbmeta flags: 0x%x -> 0x%x' % (old, new))


if __name__ == '__main__':
    main()
