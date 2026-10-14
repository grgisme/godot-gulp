"""Texture2D from an editor-saved package: the source art (lossless, what the artist imported), not cooked mips.

    source_image(pkg, i) -> (PIL image, info) ; info: format, size, srgb, compression settings, notes

UTexture::Serialize after the tagged properties: FStripDataFlags, then (editor data kept) the source as FEditorBulkData
(UE5, FUE5MainStreamObjectVersion >= TextureSourceVirtualization) or a legacy FByteBulkData. The Source struct property
(FTextureSource) says the size, slices, mips, layer format and whether the payload is PNG or JPEG.
"""
import io

import numpy as np
from PIL import Image

from . import bulk
from .package import UnrealError


TEXTURE_SOURCE_VIRTUALIZATION = 42
VIRTUALIZED_BULK_DATA_UNIQUE_GUIDS = 51


def _guid(a, b, c, d):
    import struct
    return struct.pack("<4I", a, b, c, d).hex()


UE5_MAIN = _guid(0x697DD581, 0xE64F41AB, 0xAA4A51EC, 0xBEB7B628)

BPP = {"TSF_G8": 1, "TSF_BGRA8": 4, "TSF_BGRE8": 4, "TSF_RGBA16": 8, "TSF_RGBA16F": 8, "TSF_RGBA8": 4, "TSF_RGBE8": 4,
       "TSF_G16": 2, "TSF_RGBA32F": 16, "TSF_R16F": 2, "TSF_R32F": 4}


UEDELTA_BLOCK = 1 << 17          # bytes: a block is as many whole rows as fit


def undelta(data, row_bytes, h):
    """TSCF_UEDELTA (UE 5.6+ source compression, before Oodle): blocks of as many whole rows as fit in 128 KiB; in each
    block the first row is stored as is and every later byte as the difference (mod 256) from the byte one row above.
    Worked out from the engine's own content (780 textures: decoded images smooth, no seam at the block starts; a
    160 px wide one settles whole rows over whole bytes), not from Epic source."""
    import numpy as np
    a = np.frombuffer(data, dtype=np.uint8).reshape(h, row_bytes)
    k = max(1, UEDELTA_BLOCK // row_bytes)
    out = np.empty_like(a)
    for y0 in range(0, h, k):
        out[y0:y0 + k] = np.cumsum(a[y0:y0 + k], axis=0, dtype=np.uint8)
    return out.tobytes()


def _enum(v):
    s = str(v or "")
    return s.split("::")[-1]


def source_image(pkg, i):
    props, r, end = pkg.props(i)
    src = props.get("Source") or {}
    if not isinstance(src, dict):
        raise UnrealError("texture %s: no Source (editor data stripped: a cooked package)" % pkg.exports[i].name)
    global_strip, _class_strip = r.u8(), r.u8()
    if global_strip & 1:
        raise UnrealError("texture %s: editor data stripped" % pkg.exports[i].name)
    if pkg.custom_version(UE5_MAIN) >= TEXTURE_SOURCE_VIRTUALIZATION:
        data = bulk.editor_bulk(pkg, r)
    else:
        data = bulk.byte_bulk(pkg, r)
    w, h = int(src.get("SizeX", 0)), int(src.get("SizeY", 0))
    fmt = _enum(src.get("Format")) or "TSF_BGRA8"
    comp = _enum(src.get("CompressionFormat"))
    if src.get("bPNGCompressed"):
        comp = "TSCF_PNG"
    info = {"name": pkg.exports[i].name, "size": (w, h), "format": fmt, "compression": comp or "TSCF_None",
            "srgb": props.get("SRGB", True), "settings": _enum(props.get("CompressionSettings")) or "TC_Default",
            "slices": int(src.get("NumSlices", 1) or 1), "notes": []}
    if info["slices"] > 1 or int(src.get("NumLayers", 1) or 1) > 1:
        info["notes"].append("%d slices / layers: the first is kept" % max(info["slices"], int(src.get("NumLayers", 1) or 1)))
    if comp in ("TSCF_PNG", "TSCF_JPEG"):
        im = Image.open(io.BytesIO(data))
        im.load()
        if fmt == "TSF_BGRA8" and im.mode in ("RGB", "RGBA"):
            # Unreal compresses the BGRA bytes as they are, so the PNG's red holds blue: swap back. Checked on a
            # vendor pack's 190 coloured PNG sources against the vendor's own texture files (all swapped, none not); JPEG
            # sources (one seen) are taken to be written the same way
            ch = im.split()
            im = Image.merge(im.mode, (ch[2], ch[1], ch[0]) + ch[3:])
        return im, info
    n = BPP.get(fmt)
    if not n:
        raise UnrealError("texture %s: source format %s" % (info["name"], fmt))
    if len(data) < w * h * n:
        raise UnrealError("texture %s: %d bytes for %dx%d %s" % (info["name"], len(data), w, h, fmt))
    if comp == "TSCF_UEDELTA":
        data = undelta(data[:w * h * n], w * n, h)
    elif comp not in ("", "TSCF_None"):
        raise UnrealError("texture %s: source compression %s" % (info["name"], comp))
    a = np.frombuffer(data[:w * h * n], dtype=np.uint8)
    if fmt == "TSF_G8":
        return Image.fromarray(a.reshape(h, w), "L"), info
    if fmt in ("TSF_BGRA8",):
        return Image.fromarray(a.reshape(h, w, 4)[..., [2, 1, 0, 3]], "RGBA"), info
    if fmt == "TSF_RGBA8":
        return Image.fromarray(a.reshape(h, w, 4), "RGBA"), info
    if fmt == "TSF_G16":
        g = np.frombuffer(data[:w * h * 2], dtype="<u2").reshape(h, w)
        return Image.fromarray((g >> 8).astype(np.uint8), "L"), info
    if fmt == "TSF_RGBA16":
        c = np.frombuffer(data[:w * h * 8], dtype="<u2").reshape(h, w, 4)
        return Image.fromarray((c >> 8).astype(np.uint8), "RGBA"), info
    # HDR sources: clamped to 0..1 and stored as 8-bit sRGB (glTF has no HDR texture); said in the notes
    if fmt == "TSF_RGBA16F":
        f = np.frombuffer(data[:w * h * 8], dtype="<f2").reshape(h, w, 4).astype(np.float32)
    elif fmt == "TSF_RGBA32F":
        f = np.frombuffer(data[:w * h * 16], dtype="<f4").reshape(h, w, 4)
    elif fmt in ("TSF_R16F", "TSF_R32F"):
        g = np.frombuffer(data[:w * h * n], dtype="<f2" if n == 2 else "<f4").reshape(h, w).astype(np.float32)
        f = np.stack([g, g, g, np.ones_like(g)], -1)
    elif fmt in ("TSF_BGRE8", "TSF_RGBE8"):
        q = a.reshape(h, w, 4).astype(np.float32)
        rgb = q[..., [2, 1, 0]] if fmt == "TSF_BGRE8" else q[..., :3]
        e = np.ldexp(1.0, (q[..., 3] - 136).astype(np.int32))[..., None]
        f = np.concatenate([rgb * e, np.ones((h, w, 1), np.float32)], -1)
    else:
        raise UnrealError("texture %s: source format %s" % (info["name"], fmt))
    info["notes"].append("HDR source (%s) clamped to 8-bit" % fmt)
    lin = np.clip(f[..., :3], 0, 1)
    srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    out = np.concatenate([srgb, np.clip(f[..., 3:4], 0, 1)], -1)
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA"), info
