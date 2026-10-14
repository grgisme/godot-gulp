"""Blender .blend files to glTF without Blender: the container, the SDNA type tables and the datablocks are read here
(written from the format's documented facts: docs/research/blend-readers.md; no Blender code).

    BlendFile(path)                      the container: blocks, SDNA, pointers, struct views
    convert(src, dst, tex_dir=None)      -> info ; raises NeedsBlender for what only Blender can evaluate

Supported: Blender 2.80 to 5.x files (gzip and zstd), little-endian 64-bit; meshes in all three on-disk eras (the
MVert/MEdge/MPoly/MLoop structs of 2.8 to 3.6, the named attribute layers of 4.0 to 5.0, the AttributeStorage of 5.1+),
UV maps, material slots (object or data), Principled BSDF values and Image Texture maps (external or packed), the
object tree with its transforms. Only the base mesh is in a .blend: an object whose modifier stack changes its shape
(mirror, subdivision, geometry nodes, booleans...) raises NeedsBlender, so the caller can hand the file to Blender.
"""
import gzip, os, struct

import numpy as np

BASIC = {"char": ("b", 1), "uchar": ("B", 1), "short": ("h", 2), "ushort": ("H", 2), "int": ("i", 4), "uint": ("I", 4),
         "float": ("f", 4), "double": ("d", 8), "int64_t": ("q", 8), "uint64_t": ("Q", 8), "int8_t": ("b", 1),
         "uint8_t": ("B", 1), "int16_t": ("h", 2), "uint16_t": ("H", 2), "int32_t": ("i", 4), "uint32_t": ("I", 4),
         "long": ("i", 4), "ulong": ("I", 4), "bool": ("?", 1)}


class BlendError(Exception):
    pass


class NeedsBlender(BlendError):
    """The file holds something only Blender can evaluate (a shape-changing modifier, geometry nodes...)."""


class Field:
    __slots__ = ("name", "type", "offset", "size", "ptr", "dims", "elem")

    def __init__(self, name, type_, offset, size, ptr, dims, elem):
        self.name, self.type, self.offset, self.size, self.ptr, self.dims, self.elem = name, type_, offset, size, ptr, dims, elem


class Struct:
    def __init__(self, name, size, fields):
        self.name, self.size = name, size
        self.fields = {f.name: f for f in fields}


class Block:
    __slots__ = ("code", "size", "old", "sdna", "count", "pos")

    def __init__(self, code, size, old, sdna, count, pos):
        self.code, self.size, self.old, self.sdna, self.count, self.pos = code, size, old, sdna, count, pos


def _bare(name):
    """'*next' -> 'next', 'co[3]' -> 'co', '(*func)()' -> 'func'"""
    n = name.lstrip("*").replace("(*", "").split("[")[0].split(")")[0]
    return n.lstrip("*")


def _dims(name):
    out, s = [], name
    while "[" in s:
        a, s = s.split("[", 1)
        b, s = s.split("]", 1)
        out.append(int(b))
    return out


class BlendFile:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        with open(path, "rb") as f:
            data = f.read()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        elif data[:4] == b"\x28\xb5\x2f\xfd":
            data = _zstd(data)
        if data[:7] != b"BLENDER":
            raise BlendError("not a .blend file")
        self.data = data
        if data[7:9].isdigit():                              # format 1 (Blender 5.0+): BLENDER17-01v0502
            hsize = int(data[7:9])
            if data[9:10] != b"-" or data[12:13] != b"v":
                raise BlendError("unknown .blend header %r" % data[:hsize])
            self.version = int(data[13:17])
            self.ptr, self.bhead = 8, "large"
            pos = hsize
        else:                                                # format 0: BLENDER-v402
            self.ptr = {b"_": 4, b"-": 8}.get(data[7:8])
            if data[8:9] != b"v":
                raise BlendError("big-endian .blend files are not supported")
            if self.ptr != 8:
                raise BlendError("32-bit .blend files are not supported")
            self.version = int(data[9:12])
            self.bhead = "small"
            pos = 12
        if self.version < 280:
            raise BlendError("Blender %d.%d file: only 2.80 and later are read" % (self.version // 100, self.version % 100))
        self.blocks, self.by_old = [], {}
        while pos < len(data):
            if self.bhead == "large":
                code, sdna, old, size, count = struct.unpack_from("<4siQqq", data, pos)
                pos += 32
            else:
                code, size, old, sdna, count = struct.unpack_from("<4siQii", data, pos)
                pos += 24
            b = Block(code.rstrip(b"\0").decode("latin-1"), size, old, sdna, count, pos)
            if b.code == "ENDB":
                break
            self.blocks.append(b)
            if old:
                self.by_old.setdefault(old, b)
            pos += size
        dna = next((b for b in self.blocks if b.code == "DNA1"), None)
        if dna is None:
            raise BlendError("no DNA1 block")
        self._sdna(dna)

    # ---- SDNA

    def _sdna(self, b):
        d, p = self.data, b.pos
        base = b.pos                                         # the sections are 4-aligned from the block's start
        align = lambda q: base + ((q - base + 3) & ~3)
        if d[p:p + 8] != b"SDNANAME":
            raise BlendError("bad DNA1 block")
        p += 8

        def strings(p):
            n = struct.unpack_from("<i", d, p)[0]
            p += 4
            out = []
            for _ in range(n):
                e = d.index(b"\0", p)
                out.append(d[p:e].decode("latin-1"))
                p = e + 1
            return out, align(p)

        names, p = strings(p)
        if d[p:p + 4] != b"TYPE":
            raise BlendError("bad DNA1 TYPE")
        types, p = strings(p + 4)
        if d[p:p + 4] != b"TLEN":
            raise BlendError("bad DNA1 TLEN")
        tlen = struct.unpack_from("<%dh" % len(types), d, p + 4)
        p = align(p + 4 + 2 * len(types))
        if d[p:p + 4] != b"STRC":
            raise BlendError("bad DNA1 STRC")
        n = struct.unpack_from("<i", d, p + 4)[0]
        p += 8
        self.structs, self.struct_index = [], {}
        for k in range(n):
            t, nf = struct.unpack_from("<hh", d, p)
            p += 4
            off, fields = 0, []
            for _ in range(nf):
                ft, fn = struct.unpack_from("<hh", d, p)
                p += 4
                name = names[fn]
                ptr = name.startswith("*") or name.startswith("(*")
                dims = _dims(name)
                count = int(np.prod(dims)) if dims else 1
                elem = self.ptr if ptr else tlen[ft]
                size = elem * count
                fields.append(Field(_bare(name), types[ft], off, size, ptr, dims, elem))
                off += size
            self.structs.append(Struct(types[t], tlen[t], fields))
            self.struct_index[types[t]] = k

    # ---- access

    def block(self, ptr):
        return self.by_old.get(ptr) if ptr else None

    def view(self, ptr_or_block, struct_name=None, index=0):
        """A struct view of the block a pointer names (or a block), as its SDNA struct or the given one."""
        b = ptr_or_block if isinstance(ptr_or_block, Block) else self.block(ptr_or_block)
        if b is None:
            return None
        st = self.structs[self.struct_index[struct_name]] if struct_name else self.structs[b.sdna]
        return View(self, st, b.pos + index * st.size)

    def views(self, ptr, struct_name=None):
        """Every element of the struct array a pointer names."""
        b = self.block(ptr)
        if b is None:
            return []
        st = self.structs[self.struct_index[struct_name]] if struct_name else self.structs[b.sdna]
        n = b.size // st.size if st.size else 0
        return [View(self, st, b.pos + k * st.size) for k in range(n)]

    def raw(self, ptr, dtype, count=None):
        """The raw array a pointer names (plain data blocks carry no type: the caller says what it holds)."""
        b = self.block(ptr)
        if b is None:
            return None
        dt = np.dtype(dtype)
        n = b.size // dt.itemsize if count is None else count
        return np.frombuffer(self.data, dtype=dt, count=n, offset=b.pos)

    def bytes_of(self, ptr):
        b = self.block(ptr)
        return bytes(self.data[b.pos:b.pos + b.size]) if b else b""

    def listbase(self, lb, struct_name=None):
        """The elements of a ListBase (first .. via next)."""
        out, p, seen = [], lb["first"], set()
        while p and p not in seen and len(out) < 100000:
            seen.add(p)
            v = self.view(p, struct_name)
            if v is None:
                break
            out.append(v)
            p = v.get("next", 0)
        return out

    def ids(self, code):
        """Every ID datablock of a two-letter code ('ME', 'OB', 'MA', 'IM', ...)."""
        return [View(self, self.structs[b.sdna], b.pos) for b in self.blocks if b.code == code]


class View:
    __slots__ = ("f", "st", "pos")

    def __init__(self, f, st, pos):
        self.f, self.st, self.pos = f, st, pos

    @property
    def type(self):
        return self.st.name

    def has(self, name):
        return name in self.st.fields

    def get(self, name, default=None):
        fl = self.st.fields.get(name)
        if fl is None:
            return default
        return self._value(fl)

    def __getitem__(self, name):
        fl = self.st.fields.get(name)
        if fl is None:
            raise KeyError("%s.%s" % (self.st.name, name))
        return self._value(fl)

    def _value(self, fl):
        d, p = self.f.data, self.pos + fl.offset
        if fl.ptr:
            if fl.dims:
                return list(struct.unpack_from("<%dQ" % (fl.size // 8), d, p))
            return struct.unpack_from("<Q", d, p)[0]
        if fl.type in BASIC:
            c, s = BASIC[fl.type]
            if fl.type == "char" and fl.dims:                # a C string
                raw = d[p:p + fl.size]
                return raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            if fl.dims:
                a = np.frombuffer(d, dtype="<" + c, count=fl.size // s, offset=p)
                return a.reshape(fl.dims) if len(fl.dims) > 1 else a
            return struct.unpack_from("<" + c, d, p)[0]
        k = self.f.struct_index.get(fl.type)
        if k is None:
            return None
        st = self.f.structs[k]
        if fl.dims:
            return [View(self.f, st, p + i * st.size) for i in range(fl.size // st.size)]
        return View(self.f, st, p)

    def id_name(self):
        idv = self.get("id")
        return idv["name"][2:] if idv is not None else ""


def _zstd(data):
    try:
        import zstandard
    except ImportError:
        try:
            from compression import zstd                     # Python 3.14+
            return zstd.decompress(data)
        except ImportError:
            raise BlendError("a zstd-compressed .blend (Blender 3.0+): pip install zstandard")
    out = bytearray()
    with zstandard.ZstdDecompressor().stream_reader(data) as r:
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            out += chunk
    return bytes(out)


# ---- datablocks

CD_MVERT, CD_MEDGE, CD_MLOOPUV, CD_MPOLY, CD_MLOOP = 0, 3, 16, 25, 26
CD_PROP_INT32, CD_PROP_FLOAT3, CD_PROP_FLOAT2, CD_PROP_BOOL, CD_PROP_INT32_2D = 11, 48, 49, 50, 46
CD_PROP_BYTE_COLOR, CD_PROP_COLOR = 17, 47
ATTR_DTYPE = {0: ("?", 1), 1: ("i1", 1), 2: ("<i2", 2), 3: ("<i4", 1), 4: ("<i4", 2), 5: ("<f4", 1), 6: ("<f4", 2),
              7: ("<f4", 3), 8: ("<f4", 16), 9: ("u1", 4), 10: ("<f4", 4), 11: ("<f4", 4), 13: ("<f4", 4)}
DOMAIN = {0: "point", 1: "edge", 2: "face", 3: "corner"}
OB_EMPTY, OB_MESH = 0, 1
OB_NEEDS_BLENDER = {2: "curve", 3: "surface", 4: "text", 5: "metaball", 27: "hair curves", 29: "point cloud",
                    30: "volume", 26: "grease pencil", 31: "grease pencil"}
# modifiers that leave the base mesh's shape as it is (or only touch normals / runtime topology): imported as is
HARMLESS_MODIFIERS = {"TriangulateModifierData", "WeightedNormalModifierData", "EdgeSplitModifierData",
                      "CollisionModifierData", "ParticleSystemModifierData",
                      "ClothModifierData", "SoftbodyModifierData"}   # simulations: at rest, the base mesh
OB_HIDE_RENDER = 1 << 2      # Object.restrictflag: hidden from render (rig widgets, helpers): not exported, not judged
C3 = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])          # Blender Z up -> glTF Y up: (x, z, -y)
C4 = np.eye(4)
C4[:3, :3] = C3


def _field_array(bf, ptr, struct_name, field):
    """One field of every element of a struct array (strided numpy view)."""
    b = bf.block(ptr)
    if b is None:
        return None
    st = bf.structs[bf.struct_index[struct_name]] if struct_name else bf.structs[b.sdna]
    fl = st.fields[field]
    n = b.size // st.size
    c, s = BASIC[fl.type]
    per = fl.size // s
    a = np.ndarray((n, per), dtype="<" + c, buffer=bf.data, offset=b.pos + fl.offset, strides=(st.size, s))
    return a[:, 0].copy() if per == 1 else a.copy()


def _cd_layers(bf, cd):
    """A CustomData's layers -> [(type, name, data ptr)]"""
    out = []
    n = cd["totlayer"]
    if not n or not cd["layers"]:
        return out
    for lay in bf.views(cd["layers"], "CustomDataLayer")[:n]:
        out.append((lay["type"], lay["name"], lay["data"]))
    return out


def mesh_arrays(bf, me, notes):
    """A Mesh -> {"pos" (V,3), "offsets" (F+1), "corner_vert" (L), "uv" (L,2) or None, "mat" (F), "smooth" (F bool)}
    from whichever on-disk era the file is: AttributeStorage (5.1+), named layers (4.0+), legacy structs (2.8-3.6)."""
    nv, nf, nl = me["totvert"], me["totpoly"], me["totloop"]
    attrs = {}
    st = me.get("attribute_storage")
    if st is not None and st["dna_attributes_num"]:
        for a in bf.views(st["dna_attributes"], "Attribute")[:st["dna_attributes_num"]]:
            name = bf.bytes_of(a["name"]).split(b"\0", 1)[0].decode("utf-8", "replace")
            dt = ATTR_DTYPE.get(a["data_type"])
            if dt is None:
                continue
            dom = DOMAIN.get(a["domain"])
            size = {"point": nv, "edge": me["totedge"], "face": nf, "corner": nl}.get(dom, 0)
            if a["storage_type"] == 0:
                arr = bf.view(a["data"], "AttributeArray")
                vals = bf.raw(arr["data"], dt[0])
                if arr["is_single"]:
                    vals = np.tile(vals[:dt[1]], size)
            else:                                            # single: one value for the whole domain
                sv = bf.view(a["data"])
                vals = bf.raw(sv["data"] if sv is not None and sv.has("data") else a["data"], dt[0])
                vals = np.tile(vals[:dt[1]], size)
            vals = vals[:size * dt[1]]
            attrs[(dom, name)] = vals.reshape(size, dt[1]) if dt[1] > 1 else vals
    else:
        for dom, cdname in (("point", "vdata"), ("edge", "edata"), ("face", "pdata"), ("corner", "ldata")):
            size = {"point": nv, "edge": me["totedge"], "face": nf, "corner": nl}[dom]
            for t, name, ptr in _cd_layers(bf, me[cdname]):
                if t == CD_MVERT:
                    attrs[("point", "position")] = _field_array(bf, ptr, "MVert", "co")
                elif t == CD_MPOLY:
                    attrs[("face", "_loopstart")] = _field_array(bf, ptr, "MPoly", "loopstart")
                    attrs[("face", "_totloop")] = _field_array(bf, ptr, "MPoly", "totloop")
                    attrs[("face", "material_index")] = _field_array(bf, ptr, "MPoly", "mat_nr").astype(np.int32)
                    attrs[("face", "_smooth")] = (_field_array(bf, ptr, "MPoly", "flag") & 1).astype(bool)
                elif t == CD_MLOOP:
                    attrs[("corner", ".corner_vert")] = _field_array(bf, ptr, "MLoop", "v").astype(np.int64)
                elif t == CD_MLOOPUV:
                    attrs.setdefault(("corner", "_uv"), _field_array(bf, ptr, "MLoopUV", "uv"))
                elif t == CD_PROP_FLOAT3:
                    attrs[(dom, name)] = bf.raw(ptr, "<f4", size * 3).reshape(size, 3)
                elif t == CD_PROP_FLOAT2:
                    attrs[(dom, name)] = bf.raw(ptr, "<f4", size * 2).reshape(size, 2)
                elif t == CD_PROP_INT32:
                    attrs[(dom, name)] = bf.raw(ptr, "<i4", size)
                elif t == CD_PROP_INT32_2D:
                    attrs[(dom, name)] = bf.raw(ptr, "<i4", size * 2).reshape(size, 2)
                elif t == CD_PROP_BOOL:
                    attrs[(dom, name)] = bf.raw(ptr, "?", size)
    pos = attrs.get(("point", "position"))
    cv = attrs.get(("corner", ".corner_vert"))
    if pos is None or cv is None:
        raise BlendError("mesh %s: no positions or corners found" % me.id_name())
    if ("face", "_loopstart") in attrs:                      # legacy: faces from MPoly
        ls, tl = attrs[("face", "_loopstart")], attrs[("face", "_totloop")]
        order = np.argsort(ls)
        if not (np.diff(ls[order]) == tl[order][:-1]).all() or not (order == np.arange(len(order))).all():
            notes.append("mesh %s: faces out of corner order" % me.id_name())
        offsets = np.concatenate([ls, [ls[-1] + tl[-1]]]) if len(ls) else np.zeros(1, np.int64)
        smooth = attrs[("face", "_smooth")]
    else:
        offsets = bf.raw(me["poly_offset_indices"], "<i4", nf + 1) if nf else np.zeros(1, np.int32)
        sharp = attrs.get(("face", "sharp_face"))
        smooth = ~sharp.astype(bool) if sharp is not None else np.ones(nf, bool)
    uv = attrs.get(("corner", "_uv"))
    if uv is None:
        uvs = [k for k in attrs if k[0] == "corner" and not k[1].startswith(".") and np.ndim(attrs[k]) == 2
               and attrs[k].shape[1] == 2]
        uv = attrs[uvs[0]] if uvs else None
    mat = attrs.get(("face", "material_index"))
    return {"pos": np.asarray(pos, np.float64), "offsets": np.asarray(offsets, np.int64), "corner_vert": np.asarray(cv, np.int64),
            "uv": None if uv is None else np.asarray(uv, np.float64),
            "mat": np.zeros(nf, np.int32) if mat is None else np.asarray(mat, np.int32), "smooth": np.asarray(smooth, bool)}


def _triangulate(poly):
    """Corner positions of one face (n, 3) -> triangles as index triples: a fan when convex, else ear clipping."""
    n = len(poly)
    if n == 3:
        return [(0, 1, 2)]
    nrm = np.zeros(3)
    for i in range(n):                                        # Newell's normal
        a, b = poly[i], poly[(i + 1) % n]
        nrm += [(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])]
    ax = int(np.argmax(np.abs(nrm)))
    p2 = poly[:, list([(1, 2), (2, 0), (0, 1)][ax])].astype(np.float64)   # the plane across the dominant axis, cyclic
    if nrm[ax] < 0:
        p2[:, 0] = -p2[:, 0]                                  # seen from the side the face points to

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    convex = all(cross(p2[i], p2[(i + 1) % n], p2[(i + 2) % n]) >= -1e-12 for i in range(n))
    if convex:
        return [(0, k, k + 1) for k in range(1, n - 1)]
    idx, out, guard = list(range(n)), [], 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[(k - 1) % m], idx[k], idx[(k + 1) % m]
            if cross(p2[i0], p2[i1], p2[i2]) <= 1e-12:
                continue
            if any(j not in (i0, i1, i2) and cross(p2[i0], p2[i1], p2[j]) >= 0 and cross(p2[i1], p2[i2], p2[j]) >= 0
                   and cross(p2[i2], p2[i0], p2[j]) >= 0 for j in idx):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        out.append(tuple(idx))
    elif len(idx) > 3:                                       # degenerate: fan the rest
        out += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
    return out


def mesh_primitives(arr, slot_count):
    """-> {material slot: attrs} in Blender space (positions, normals from the smooth flags, UV0 V-flipped)."""
    pos, off, cv = arr["pos"], arr["offsets"], arr["corner_vert"]
    nf = len(off) - 1
    fn = np.zeros((nf, 3))
    tris, tri_face = [], []
    for f in range(nf):
        a, b = off[f], off[f + 1]
        if b - a < 3:
            continue
        poly = pos[cv[a:b]]
        for t in _triangulate(poly):
            tris.append((a + t[0], a + t[1], a + t[2]))
            tri_face.append(f)
        for i in range(b - a):                                # Newell: area-weighted face normal
            p, q = poly[i], poly[(i + 1) % (b - a)]
            fn[f] += [(p[1] - q[1]) * (p[2] + q[2]), (p[2] - q[2]) * (p[0] + q[0]), (p[0] - q[0]) * (p[1] + q[1])]
    if not tris:
        return {}
    tris = np.array(tris, np.int64)
    tri_face = np.array(tri_face, np.int64)
    vn = np.zeros((len(pos), 3))
    corner_face = np.repeat(np.arange(nf), np.diff(off))
    sm = arr["smooth"][corner_face]
    np.add.at(vn, cv[sm], fn[corner_face[sm]])
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    fnn = fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    out = {}
    mats = np.clip(arr["mat"], 0, max(0, slot_count - 1))
    for slot in sorted(set(mats[tri_face].tolist())):
        sel = tris[mats[tri_face] == slot].reshape(-1)
        faces = np.repeat(tri_face[mats[tri_face] == slot], 3)
        n = np.where(arr["smooth"][faces][:, None], vn[cv[sel]], fnn[faces])
        a = {"POSITION": pos[cv[sel]], "NORMAL": n}
        if arr["uv"] is not None and len(arr["uv"]) >= len(cv):
            uv = arr["uv"][sel].copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            a["TEXCOORD_0"] = uv
        out[slot] = a
    return out


def object_matrix(ob):
    """An Object's world matrix (Blender space, column vectors): the saved obmat when the file has it, else composed
    from loc/rot/scale (rotmode) and the parent chain with parentinv."""
    if ob.has("obmat"):
        return np.asarray(ob["obmat"], np.float64).T.copy()     # stored as [column][row]
    return None


def _local(ob):
    import math
    loc, size, mode = ob["loc"], ob["size"], ob["rotmode"]
    if mode == 0:
        w, x, y, z = ob["quat"] / (np.linalg.norm(ob["quat"]) or 1.0)
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    elif mode == -1:
        ax, ang = np.asarray(ob["rotAxis"], np.float64), ob["rotAngle"]
        ax = ax / (np.linalg.norm(ax) or 1.0)
        K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
        R = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
    else:
        rx, ry, rz = ob["rot"]
        Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
        Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
        Rz = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
        order = {1: "XYZ", 2: "XZY", 3: "YXZ", 4: "YZX", 5: "ZXY", 6: "ZYX"}.get(mode, "XYZ")
        R = np.eye(3)
        for c in order:                                       # "XYZ": X first, so R = Rz Ry Rx
            R = {"X": Rx, "Y": Ry, "Z": Rz}[c] @ R
    M = np.eye(4)
    M[:3, :3] = R @ np.diag(size)
    M[:3, 3] = loc
    return M


def world_matrices(bf, obs):
    """{object pointer: world matrix} for every object."""
    by_ptr = {o_ptr: o for o_ptr, o in obs}
    out = {}

    def w(p, depth=0):
        if p in out:
            return out[p]
        o = by_ptr[p]
        M = object_matrix(o)
        if M is None:
            M = _local(o)
            par = o["parent"]
            if par in by_ptr and depth < 64:
                M = w(par, depth + 1) @ np.asarray(o["parentinv"], np.float64).T @ M
        out[p] = M
        return M
    for p, _o in obs:
        w(p)
    return out


# ---- materials

def _listbase_ptrs(bf, lb, struct_name=None):
    """[(pointer, view)] along a ListBase."""
    out, p, seen = [], lb["first"], set()
    while p and p not in seen and len(out) < 100000:
        seen.add(p)
        v = bf.view(p, struct_name)
        if v is None:
            break
        out.append((p, v))
        p = v.get("next", 0)
    return out


class _Graph:
    def __init__(self, bf, tree):
        self.bf = bf
        self.nodes = {p: v for p, v in _listbase_ptrs(bf, tree["nodes"], "bNode")}
        self.links = [v for _p, v in _listbase_ptrs(bf, tree["links"], "bNodeLink")]
        self.sockets = {}
        for p, n in self.nodes.items():
            for sp, s in _listbase_ptrs(bf, n["inputs"], "bNodeSocket"):
                self.sockets[sp] = (p, s, "in")
            for sp, s in _listbase_ptrs(bf, n["outputs"], "bNodeSocket"):
                self.sockets[sp] = (p, s, "out")

    def inputs(self, node_ptr):
        return {s["identifier"]: (sp, s) for sp, (np_, s, io) in self.sockets.items() if np_ == node_ptr and io == "in"}

    def link_into(self, socket_ptr):
        """(from node pointer, from socket view) linked into an input socket, or None."""
        for l in self.links:
            if l["tosock"] == socket_ptr and l["fromnode"] in self.nodes:
                fs = self.sockets.get(l["fromsock"])
                return l["fromnode"], (fs[1] if fs else None)
        return None

    def value(self, s):
        dv = s["default_value"]
        if not dv:
            return None
        idn = s["idname"]
        if "Color" in idn:
            return list(self.bf.view(dv, "bNodeSocketValueRGBA")["value"])
        if "Float" in idn:
            return float(self.bf.view(dv, "bNodeSocketValueFloat")["value"])
        if "Vector" in idn:
            return list(self.bf.view(dv, "bNodeSocketValueVector")["value"])
        return None


def _image_file(bf, img, cache_dir, notes):
    """An Image -> a file path: its packed bytes written out, or its external path (// = beside the .blend)."""
    name = img.id_name()
    path = img["name"]
    pf = img.get("packedfile") or 0
    if not pf and img.has("packedfiles") and img["packedfiles"]["first"]:
        ipf = bf.view(img["packedfiles"]["first"], "ImagePackedFile")
        pf = ipf["packedfile"] if ipf is not None else 0
    if pf:
        p = bf.view(pf, "PackedFile")
        data = bf.bytes_of(p["data"])[:p["size"]]
        ext = os.path.splitext(path)[1].lower() or (".png" if data[:4] == b"\x89PNG" else ".jpg")
        os.makedirs(cache_dir, exist_ok=True)
        import hashlib
        out = os.path.join(cache_dir, "%s_%s%s" % (os.path.splitext(name)[0].replace("/", "_"),
                                                     hashlib.sha1(data).hexdigest()[:8], ext))
        if not os.path.isfile(out):
            with open(out, "wb") as f:
                f.write(data)
        return out
    base = os.path.dirname(bf.path)
    rel = path[2:] if path.startswith("//") else path
    rel = rel.replace("\\", "/")
    for c in (os.path.join(base, rel), rel, os.path.join(base, os.path.basename(rel)),
              os.path.join(base, "textures", os.path.basename(rel))):
        if c and os.path.isfile(c):
            return c
    notes.append("image %s (%s) not found" % (name, path))
    return ""


PRINCIPLED = {"base": ("Base Color",), "metallic": ("Metallic",), "roughness": ("Roughness",), "alpha": ("Alpha",),
              "normal": ("Normal",), "emission": ("Emission Color", "Emission"), "emission_strength": ("Emission Strength",)}


def material(bf, ma, cache_dir):
    """A Material -> materials.Mat: the Principled BSDF behind the material output (values and image maps), else the
    viewport colour."""
    from . import materials as mt
    m = mt.Mat(ma.id_name() or "material")
    m.base = [ma["r"], ma["g"], ma["b"], ma.get("a", 1.0)]
    m.roughness, m.metallic = float(ma.get("roughness", 0.5)), float(ma.get("metallic", 0.0))
    T = lambda path, ch: mt.Tex(path, ch, 0, np.eye(3), 10497, 10497, (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0), "auto",
                                "blend:" + os.path.basename(path))
    tree = bf.view(ma["nodetree"]) if ma.get("nodetree") else None
    if tree is None:
        return m
    g = _Graph(bf, tree)
    out = next((p for p, n in g.nodes.items() if n["idname"] == "ShaderNodeOutputMaterial" and n["flag"] & 2), None) or \
        next((p for p, n in g.nodes.items() if n["idname"] == "ShaderNodeOutputMaterial"), None)
    if out is None:
        return m
    surf = g.inputs(out).get("Surface")
    src = g.link_into(surf[0]) if surf else None
    if src is None or g.nodes[src[0]]["idname"] != "ShaderNodeBsdfPrincipled":
        if src is not None:
            m.notes.append("surface is %s, not a Principled BSDF: the viewport colour is used" % g.nodes[src[0]]["idname"])
        return m
    ins = g.inputs(src[0])

    def inp(key):
        return next((ins[k] for k in PRINCIPLED[key] if k in ins), None)

    def image_of(sock_ptr, depth=0):
        """(image path, channel) behind an input: an Image Texture, maybe through a Normal Map or a Separate Color."""
        lk = g.link_into(sock_ptr)
        if lk is None or depth > 4:
            return None
        node = g.nodes[lk[0]]
        idn = node["idname"]
        if idn == "ShaderNodeTexImage":
            img = bf.view(node["id"]) if node["id"] else None
            if img is None:
                return None
            p = _image_file(bf, img, cache_dir, m.notes)
            fs = lk[1]["identifier"] if lk[1] is not None else "Color"
            return (p, "a" if fs == "Alpha" else "rgb") if p else None
        if idn in ("ShaderNodeNormalMap",):
            c = g.inputs(lk[0]).get("Color")
            return image_of(c[0], depth + 1) if c else None
        if idn in ("ShaderNodeSeparateColor", "ShaderNodeSeparateRGB"):
            c = g.inputs(lk[0]).get("Color") or g.inputs(lk[0]).get("Image")
            r = image_of(c[0], depth + 1) if c else None
            ch = {"Red": "r", "R": "r", "Green": "g", "G": "g", "Blue": "b", "B": "b"}.get(lk[1]["identifier"] if lk[1] is not None else "", "r")
            return (r[0], ch) if r else None
        m.notes.append("%s fed by %s: not followed" % ("an input", idn))
        return None

    b = inp("base")
    if b:
        v = g.value(b[1])
        if v is not None:
            m.base = [v[0], v[1], v[2], 1.0]
        im = image_of(b[0])
        if im:
            m.tex["diffuse"] = T(im[0], "rgb")
            m.base = [1.0, 1.0, 1.0, 1.0]
    for key, slot in (("metallic", "metallic"), ("roughness", "roughness")):
        s = inp(key)
        if not s:
            continue
        v = g.value(s[1])
        if v is not None:
            setattr(m, slot, float(v))
        im = image_of(s[0])
        if im:
            m.tex[slot] = T(im[0], im[1] if im[1] in ("r", "g", "b", "a") else "r")
            setattr(m, slot, 1.0)
    a = inp("alpha")
    if a:
        v = g.value(a[1])
        im = image_of(a[0])
        if im:
            m.tex["opacity"] = T(im[0], im[1] if im[1] in ("r", "g", "b", "a") else "a")
        elif v is not None and v < 0.999:
            m.opacity = float(v)
    n = inp("normal")
    if n:
        im = image_of(n[0])
        if im:
            t = T(im[0], "rgb")
            t.colour_space = "raw"
            m.tex["normal"] = t
    e, es = inp("emission"), inp("emission_strength")
    strength = g.value(es[1]) if es else 1.0
    if e:
        v = g.value(e[1])
        im = image_of(e[0])
        if im:
            m.tex["emissive"] = T(im[0], "rgb")
            m.emissive = [strength or 1.0] * 3
        elif v is not None and strength:
            m.emissive = [c * strength for c in v[:3]]
    for t in m.tex.values():
        t.rel = None
    return m


# ---- objects, the modifier policy, the glTF

def _modifiers(bf, ob):
    out = []
    for _p, md in _listbase_ptrs(bf, ob["modifiers"]):
        head = md.get("modifier")
        mode = head["mode"] if head is not None else 3
        name = head["name"] if head is not None else md.type
        out.append((md.type, name, mode, md))
    return out


def check_modifiers(bf, ob):
    """Raise NeedsBlender when an enabled modifier changes the object's shape; -> notes for the harmless ones."""
    notes = []
    for typ, name, mode, md in _modifiers(bf, ob):
        if not mode & 3:                                       # disabled in viewport and render
            continue
        if typ in HARMLESS_MODIFIERS:
            notes.append("%s: modifier %s (%s) left out" % (ob.id_name(), name, typ.replace("ModifierData", "")))
            continue
        if typ == "ArmatureModifierData":                      # the reader keeps no skin: Blender keeps skin and actions
            raise NeedsBlender("%s: skinned to an armature (modifier %s)" % (ob.id_name(), name))
        if typ == "NodesModifierData":
            grp = bf.view(md["node_group"]) if md.get("node_group") else None
            gname = grp.id_name() if grp is not None else ""
            if gname.startswith(("Smooth by Angle", "Auto Smooth")):
                notes.append("%s: %s (normals only) left out: smooth shading kept" % (ob.id_name(), gname))
                continue
            raise NeedsBlender("%s: geometry nodes modifier %s (%s)" % (ob.id_name(), name, gname or "no group"))
        raise NeedsBlender("%s: modifier %s (%s)" % (ob.id_name(), name, typ.replace("ModifierData", "")))
    return notes


def convert(src, dst, tex_dir=None, modifiers="refuse"):
    """modifiers: "refuse" (raise NeedsBlender for a shape-changing modifier) or "ignore" (the base meshes, noted)."""
    from . import convert as gltf_out, materials as mt
    bf = BlendFile(src)
    notes = []
    obs = [(b.old, View(bf, bf.structs[b.sdna], b.pos)) for b in bf.blocks if b.code == "OB"]
    hidden = {p for p, ob in obs if (ob.get("restrictflag") or 0) & OB_HIDE_RENDER}
    for p, ob in obs:
        if p in hidden:
            notes.append("%s: hidden from render, left out" % ob.id_name())
            continue
        if ob["type"] in OB_NEEDS_BLENDER:
            raise NeedsBlender("%s is a %s object (no mesh in the file)" % (ob.id_name(), OB_NEEDS_BLENDER[ob["type"]]))
        if ob["type"] == OB_MESH:
            try:
                notes += check_modifiers(bf, ob)
            except NeedsBlender as e:
                if modifiers != "ignore":
                    raise
                notes.append("%s: ignored, the base mesh kept" % e)
    world = world_matrices(bf, obs)
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(dst)), ".blend-images")
    mats, meshes = {}, {}

    def mat_of(ptr):
        if ptr not in mats:
            v = bf.view(ptr) if ptr else None
            mats[ptr] = material(bf, v, cache_dir) if v is not None else mt.Mat("default")
        return mats[ptr]

    nodes = {}
    for p, ob in obs:
        n = {"key": p, "name": ob.id_name() or "object", "children": [], "W": world[p]}
        if ob["type"] == OB_MESH and ob["data"] and p not in hidden:
            me = bf.view(ob["data"])
            slots = []
            me_mats = bf.raw(me["mat"], "<u8", me["totcol"]) if me["mat"] and me["totcol"] else []
            ob_mats = bf.raw(ob["mat"], "<u8", ob["totcol"]) if ob["mat"] and ob["totcol"] else []
            bits = bf.raw(ob["matbits"], "u1", ob["totcol"]) if ob["matbits"] and ob["totcol"] else []
            for i in range(max(me["totcol"], ob["totcol"])):
                use_ob = i < len(bits) and bits[i] and i < len(ob_mats)
                slots.append(int(ob_mats[i]) if use_ob else (int(me_mats[i]) if i < len(me_mats) else 0))
            key = (ob["data"], tuple(slots))
            if key not in meshes:
                arr = mesh_arrays(bf, me, notes)
                prims = []
                for slot, a in mesh_primitives(arr, max(1, len(slots))).items():
                    a = {"POSITION": (a["POSITION"] @ C3.T).astype(np.float32),
                         "NORMAL": (a["NORMAL"] @ C3.T).astype(np.float32),
                         **({"TEXCOORD_0": a["TEXCOORD_0"].astype(np.float32)} if "TEXCOORD_0" in a else {})}
                    prims.append({"path": me.id_name(), "attrs": a, "mat": mat_of(slots[slot] if slot < len(slots) else 0)})
                meshes[key] = {"name": me.id_name(), "path": me.id_name(), "uv_sets": ["st"], "subsets": 0,
                               "prims": prims} if prims else None
            n["mesh"] = meshes[key]
        nodes[p] = (n, ob["parent"])
    roots = []
    for p, (n, par) in nodes.items():
        parent = nodes.get(par)
        Wg = C4 @ n["W"] @ C4.T
        Pg = C4 @ parent[0]["W"] @ C4.T if parent else np.eye(4)
        n["local"] = np.linalg.inv(Pg) @ Wg
        (parent[0]["children"] if parent else roots).append(n)
    for n, _par in nodes.values():
        n.pop("W", None)
    # keep only mesh objects and their ancestors (cameras, lights and bare empties do not go to the glTF)
    def keep(n):
        n["children"] = [c for c in n["children"] if keep(c)]
        return n.get("mesh") is not None or bool(n["children"])
    roots = [r for r in roots if keep(r)]
    info = gltf_out.write_gltf(dst, [], tex_dir, roots)
    info.update(variants={}, layers=[os.path.abspath(src)])
    info["material_notes"] = sorted(set(notes)) + info["material_notes"]
    info["blender_version"] = "%d.%d" % (bf.version // 100, bf.version % 100)
    return info


# ---- the fallback: Blender itself, for what only Blender can evaluate

BLENDER_EXPORT = """
import bpy, sys
dst = sys.argv[sys.argv.index("--") + 1]
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLTF_SEPARATE", export_apply=True, export_yup=True)
"""


def _gltf_info(dst):
    """The write_gltf-style summary of a glTF another tool wrote (counts and world bounds of its meshes)."""
    import json
    from . import report
    with open(dst) as f:
        g = json.load(f)
    tris = verts = 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for m in g.get("meshes", []):
        for p in m["primitives"]:
            a = g["accessors"][p["attributes"]["POSITION"]]
            verts += a["count"]
            tris += (g["accessors"][p["indices"]]["count"] if "indices" in p else a["count"]) // 3
            if "min" in a:
                lo, hi = np.minimum(lo, a["min"]), np.maximum(hi, a["max"])
    size = (hi - lo).tolist() if np.isfinite(lo).all() else [0, 0, 0]
    return {"meshes": sum(1 for n in g.get("nodes", []) if "mesh" in n), "unique_meshes": len(g.get("meshes", [])),
            "tris": tris, "verts": verts, "size": [size[0], size[2], size[1]],
            "material_names": [m.get("name", "") for m in g.get("materials", [])], "texture_sources": [],
            "uv_sets": [], "subsets": 0, "materials": len(g.get("materials", [])), "textures": len(g.get("images", [])),
            "animations": len(g.get("animations", [])), "material_notes": []}


def convert_or_blender(src, dst, tex_dir=None):
    """convert(); on NeedsBlender, headless Blender (GODOT_GULP_BLENDER) exports it with its modifiers applied and the
    reason goes in the notes. Without a Blender the NeedsBlender error stands (its message names the cause)."""
    import subprocess, tempfile
    try:
        return convert(src, dst, tex_dir)
    except NeedsBlender as e:
        blender = os.environ.get("GODOT_GULP_BLENDER", "")
        if not blender or not os.access(blender, os.X_OK):
            raise NeedsBlender("%s; set GODOT_GULP_BLENDER to a Blender binary for the automatic fallback" % e)
        reason = str(e)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(BLENDER_EXPORT)
        script = f.name
    try:
        r = subprocess.run([blender, "-b", "--factory-startup", os.path.abspath(src), "--python", script, "--",
                            os.path.abspath(dst)], capture_output=True, text=True, timeout=int(os.environ.get("GODOT_GULP_BLENDER_TIMEOUT", "600")))
    finally:
        os.unlink(script)
    if r.returncode != 0 or not os.path.isfile(dst):
        raise BlendError("Blender fallback failed (%s): %s" % (reason, (r.stderr or r.stdout)[-400:]))
    info = _gltf_info(dst)
    info.update(variants={}, layers=[os.path.abspath(src)])
    info["material_notes"] = ["converted by Blender (headless): %s" % reason]
    return info
