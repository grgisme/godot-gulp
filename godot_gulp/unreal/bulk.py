"""Bulk payloads of editor-saved packages: FEditorBulkData (UE5: the payload in the package trailer, an
FCompressedBuffer), the legacy FByteBulkData (UE4: inline or at the end of the file, maybe zlib), and the decoders.

    editor_bulk(pkg, r) -> bytes or raises UnrealError ; byte_bulk(pkg, r) -> bytes

Oodle is proprietary: a payload compressed with it needs the user's own Oodle library (GODOT_GULP_OODLE=path to
liboo2core*.so); it is never bundled. LZ4 needs the lz4 package. The layouts follow CUE4Parse (Apache-2.0).
"""
import ctypes, os, struct, zlib

from .package import Reader, UnrealError

TRAILER_HEADER_TAG = 0xD1C43B2E80A5F697
TRAILER_FOOTER_TAG = 0x29BFCA045138DE76
CB_MAGIC = 0xB7756362
STORED_IN_TRAILER = 1 << 9
IS_VIRTUALIZED = 1 << 0
REFERENCES_LEGACY_FILE = 1 << 2
LEGACY_FILE_COMPRESSED = 1 << 3

BULK_PAYLOAD_AT_END = 1 << 0
BULK_ZLIB = 1 << 1
BULK_INLINE = 1 << 6
BULK_SEPARATE_FILE = 1 << 8
BULK_OPTIONAL = 1 << 11
BULK_SIZE64 = 1 << 13
BULK_DUPLICATE = 1 << 14
BULK_BAD_DATA_VERSION = 1 << 15
BULK_NO_OFFSET_FIXUP = 1 << 16


def trailer(pkg):
    """The package trailer's lookup table: {payload hash (hex): (absolute offset, compressed size)}."""
    if getattr(pkg, "_trailer", None) is not None:
        return pkg._trailer
    out = {}
    if pkg.payload_toc > 0:
        r = Reader(pkg.data, pkg.payload_toc)
        start = r.pos
        if r.u64() != TRAILER_HEADER_TAG:
            raise UnrealError("package trailer: bad header tag")
        version, header_len, _payloads_len = r.i32(), r.u32(), r.u64()
        legacy_mode = r.u8() if version < 1 else 0
        for _ in range(r.i32()):
            h, off, csize, _raw = r.raw(20).hex(), r.i64(), r.u64(), r.u64()
            if version >= 2:
                r.u16(), r.u16()
            mode = r.u8() if version >= 1 else (legacy_mode if off != -1 else 2)
            if mode == 0:
                out[h] = (start + header_len + off, csize)
            elif mode == 1:
                out[h] = ("referenced", csize)
            else:
                out[h] = ("virtualized", csize)
    pkg._trailer = out
    return out


def editor_bulk(pkg, r):
    """FEditorBulkData at r -> the payload's bytes (decompressed)."""
    flags, _guid, h, size = r.u32(), r.raw(16), r.raw(20).hex(), r.i64()
    if flags & STORED_IN_TRAILER:
        where = trailer(pkg).get(h)
        if where is None:
            raise UnrealError("editor bulk data: payload %s not in the package trailer" % h[:12])
        if where[0] in ("referenced", "virtualized"):
            raise UnrealError("editor bulk data is %s (not stored in this file)" % where[0])
        off = where[0]
    else:
        off = r.i64()
        if flags & (IS_VIRTUALIZED | REFERENCES_LEGACY_FILE) and off < 0:
            raise UnrealError("editor bulk data is virtualized or in a legacy sidecar file")
    if size == 0:
        return b""
    if off < 0:
        raise UnrealError("editor bulk data has no payload in this file")
    if flags & REFERENCES_LEGACY_FILE:
        raw = pkg.data[off:off + size]
        return zlib.decompress(raw) if flags & LEGACY_FILE_COMPRESSED else raw
    return compressed_buffer(pkg.data, off)


def compressed_buffer(data, off):
    """FCompressedBuffer (big-endian header, then one raw block or block sizes and blocks)."""
    if off + 64 > len(data):
        raise UnrealError("compressed buffer past the end")
    magic, _crc, method, _compressor, _level, exp, count, raw_size, comp_size = struct.unpack_from(">IIBBBBIQQ", data, off)
    if magic != CB_MAGIC:
        raise UnrealError("compressed buffer: bad magic %08x" % magic)
    p = off + 64
    if method == 0:
        return bytes(data[p:p + raw_size])
    sizes = struct.unpack_from(">%dI" % count, data, p)
    p += 4 * count
    block = 1 << exp
    out = bytearray()
    for i, cs in enumerate(sizes):
        want = min(block, raw_size - i * block)
        chunk = data[p:p + cs]
        p += cs
        if cs == want:                                    # a block that would not compress is stored raw
            out += chunk
        elif method == 4:
            out += _lz4(chunk, want)
        elif method == 3:
            out += _oodle(chunk, want)
        else:
            raise UnrealError("compressed buffer method %d" % method)
    return bytes(out)


def _lz4(chunk, size):
    try:
        import lz4.block
    except ImportError:
        raise UnrealError("an LZ4 payload needs the lz4 package (pip install lz4)")
    return lz4.block.decompress(chunk, uncompressed_size=size)


_OODLE = None


def _oodle(chunk, size):
    global _OODLE
    path = os.environ.get("GODOT_GULP_OODLE", "")
    if not path:
        raise UnrealError("an Oodle-compressed payload: set GODOT_GULP_OODLE to your own Oodle library (not bundled)")
    if _OODLE is None:
        _OODLE = ctypes.CDLL(path)
        _OODLE.OodleLZ_Decompress.restype = ctypes.c_int64
    out = ctypes.create_string_buffer(size)
    n = _OODLE.OodleLZ_Decompress(chunk, ctypes.c_int64(len(chunk)), out, ctypes.c_int64(size),
                                  0, 0, 0, None, 0, None, None, None, 0, 3)
    if n != size:
        raise UnrealError("Oodle decompression failed (%d of %d bytes)" % (n, size))
    return out.raw


def byte_bulk(pkg, r):
    """The legacy FByteBulkData at r -> bytes (inline, or at the end of this file; zlib chunks decompressed)."""
    flags = r.u32()
    if flags & BULK_SIZE64:
        count, size = r.i64(), r.i64()
    else:
        count, size = r.i32(), r.u32()
    off = r.i64()
    if not flags & BULK_NO_OFFSET_FIXUP:
        off += pkg.bulk_data_start
    if flags & BULK_BAD_DATA_VERSION:
        r.u16()
    if flags & BULK_DUPLICATE:
        r.u32()
        r.i64() if flags & BULK_SIZE64 else r.u32()
        r.i64()
    if size <= 0:
        return b""
    if flags & BULK_INLINE:
        raw = r.raw(size)
    elif flags & (BULK_SEPARATE_FILE | BULK_OPTIONAL):
        raise UnrealError("bulk data in a separate .ubulk/.uptnl file")
    else:
        raw = pkg.data[off:off + size]
    if flags & BULK_ZLIB:
        return _zlib_chunks(raw)
    return bytes(raw)


def _zlib_chunks(raw):
    """FArchive::SerializeCompressed: a package tag, a block size, a summary, the chunk table, then the chunks."""
    r = Reader(raw)
    tag = r.u64()
    if tag & 0xFFFFFFFF != 0x9E2A83C1:
        raise UnrealError("compressed bulk data: bad tag")
    _block, comp_total, raw_total = r.i64(), r.i64(), r.i64()
    chunks, got = [], 0
    while got < raw_total:
        cs, us = r.i64(), r.i64()
        chunks.append(cs)
        got += us
    out = bytearray()
    for cs in chunks:
        out += zlib.decompress(r.raw(cs))
    return bytes(out)
