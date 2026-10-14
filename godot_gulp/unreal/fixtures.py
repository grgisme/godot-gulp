"""Synthetic Unreal packages for the tests, written here byte by byte (no engine, no Epic content).

    b = Builder("/Game/Pack/M_Thing", ue5=1012)
    i = b.export("Material", "M_Thing", [(name, kind, value), ...])     # -> its package index (> 0)
    b.write(path)

Property kinds: int, float, bool, enum (enum name, value), name, obj (a package index, or an object path such as
"/Game/T/T_Brick.T_Brick", imported as `import_class`), struct (struct name, items), input (a material input: a
package index, output index, mask "rgb" or ""), color (LinearColor), structs (struct name, [items, ...]).
ue5 >= 1012 writes the UE 5.4+ property tags (type name trees), 1009 the older flat tags.

    texture(path, pixels (h, w, 4) uint8 RGBA, ue5=1012, compression="TSCF_UEDELTA"|"TSCF_PNG"|"TSCF_None")
    mesh_description(...), static_mesh(...): an FMeshDescription and a StaticMesh package around it
    sample(content_dir): a small project (textures, a material and an instance, a mesh, a level) for tests/conformance
"""
import hashlib, io, os, struct

import numpy as np

from . import texture as tx
from .package import TAG, UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME, UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION


class W:
    def __init__(self):
        self.b = bytearray()

    def add(self, fmt, *v):
        self.b += struct.pack("<" + fmt, *v)
        return self

    def fstring(self, s):
        if not s:
            return self.add("i", 0)
        e = s.encode("latin-1") + b"\0"
        self.add("i", len(e))
        self.b += e
        return self


class Builder:
    def __init__(self, package_name, ue5=1012, ue4=518, editor_version=37):
        self.package_name, self.ue5, self.ue4 = package_name, ue5, ue4      # ue4: the UE4 version of a ue5=0 save
        self.editor_version = editor_version               # FEditorObjectVersion of a ue5=0 save (under 28: raw meshes)
        self.names, self.idx = [], {}
        self.imports = []                  # (class package, class, outer, name)
        self.exports = []                  # dicts
        self.payloads = []                 # (hash20, compressed buffer bytes)
        for s in ("None", "/Script/CoreUObject", "/Script/Engine", "Class", "Package"):
            self.n(s)
        self._engine = self._import("/Script/CoreUObject", "Package", 0, "/Script/Engine")

    def n(self, s):
        if s not in self.idx:
            self.idx[s] = len(self.names)
            self.names.append(s)
        return self.idx[s]

    def _import(self, cp, cn, outer, name):
        for k, im in enumerate(self.imports):
            if im == (cp, cn, outer, name):
                return -k - 1
        for s in (cp, cn, name):
            self.n(s)
        self.imports.append((cp, cn, outer, name))
        return -len(self.imports)

    def class_ref(self, cls):
        return self._import("/Script/CoreUObject", "Class", self._engine, cls)

    def object_import(self, path, cls):
        pkg, obj = path.split(".")[0], path.split(".")[-1]
        p = self._import("/Script/CoreUObject", "Package", 0, pkg)
        return self._import("/Script/Engine", cls, p, obj)

    def export(self, cls, name, props, outer=0, tail=b""):
        """tail: bytes written after the tagged properties and the object guid (class-specific serialisation)."""
        self.n(name)
        self.exports.append({"cls": self.class_ref(cls), "name": name, "props": props, "outer": outer, "tail": tail})
        return len(self.exports)

    def payload(self, raw):
        """Adds raw bytes to the package trailer as an uncompressed FCompressedBuffer -> FEditorBulkData bytes."""
        cb = struct.pack(">IIBBBBIQQ", 0xB7756362, 0, 0, 0, 0, 0, 1, len(raw), len(raw) + 64) + bytes(32) + raw
        h20 = hashlib.sha1(cb).digest()
        self.payloads.append((h20, cb))
        return struct.pack("<I", 1 << 9) + bytes(16) + h20 + struct.pack("<q", len(cb))

    # ---- properties

    def _name(self, w, s):
        w.add("ii", self.n(s), 0)

    def _tag(self, w, name, type_, size, tree, legacy_extra=b"", flags=0):
        index = 0
        if name.endswith("]") and "[" in name:                 # "Translation[1]": a static array's element
            name, index = name[:name.index("[")], int(name[name.index("[") + 1:-1])
        self._name(w, name)
        if self.ue5 >= UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
            for nm, c in tree:
                self._name(w, nm)
                w.add("i", c)
            w.add("iB", size, flags | (1 if index else 0))
            if index:
                w.add("i", index)
        else:
            self._name(w, type_)
            w.add("ii", size, index)
            w.b += legacy_extra
            w.add("B", 0)
            if self.ue5 >= UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION:
                w.add("B", 0)

    def _ref(self, v, cls="Texture2D"):
        return v if isinstance(v, int) else self.object_import(v, cls)

    def _value(self, kind, v):
        """-> (type name, type tree, legacy tag extra, value bytes, tag flags)"""
        w = W()
        if kind == "int":
            return "IntProperty", [("IntProperty", 0)], b"", struct.pack("<i", v), 0
        if kind == "float":
            return "FloatProperty", [("FloatProperty", 0)], b"", struct.pack("<f", v), 0
        if kind == "name":
            self._name(w, v)
            return "NameProperty", [("NameProperty", 0)], b"", bytes(w.b), 0
        if kind == "obj":
            ref = self._ref(v)
            return "ObjectProperty", [("ObjectProperty", 0)], b"", struct.pack("<i", ref), 0
        if kind == "enum":
            enum, value = v
            ex = W()
            self._name(ex, enum)
            self._name(w, value)
            return "EnumProperty", [("EnumProperty", 2), (enum, 1), ("/Script/Engine", 0), ("ByteProperty", 0)], bytes(ex.b), bytes(w.b), 0
        if kind in ("vec", "rot"):                     # Vector / Rotator: doubles from UE 5.0 (large world coordinates)
            body = struct.pack("<3d", *v) if self.ue5 >= 1004 else struct.pack("<3f", *v)
            return self._struct_head("Vector" if kind == "vec" else "Rotator", body, native=True)
        if kind == "fchannel":                         # FMovieSceneFloatChannel: {times, values, default, tick}
            c = W()
            c.add("BB", 0, 0).add("ii", 4, len(v["times"])).add("%di" % len(v["times"]), *v["times"])
            c.add("ii", 28, len(v["values"]))
            for x in v["values"]:
                c.add("f", x).add("ffff", 0, 0, 0, 0).add("B", 0).b += bytes(3)
                c.add("BB", 0, 0).b += bytes(2)                   # linear, auto tangent
            c.add("f", v.get("default", 0.0)).add("i", 1 if "default" in v else 0).add("ii", int(v.get("tick", 30)), 1).add("i", 0)
            return self._struct_head("MovieSceneFloatChannel", bytes(c.b), native=True)
        if kind == "color":
            return self._struct_head("LinearColor", struct.pack("<4f", *v), native=True)
        if kind == "input":
            ref, out, mask = v
            w.add("ii", ref, out)
            self._name(w, "None")
            w.add("iiiii", 1 if mask else 0, *[1 if c in mask else 0 for c in "rgba"])
            return self._struct_head("ExpressionInput", bytes(w.b), native=True)
        if kind == "struct":
            sname, items = v
            return self._struct_head(sname, self.props(items, False), native=False)
        if kind == "structs":
            sname, rows = v
            w.add("i", len(rows))
            body = b"".join(self.props(r, False) for r in rows)
            if self.ue5 >= UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
                w.b += body
            else:                                          # the inner struct's tag, once for the array
                inner = W()
                ex = W()
                self._name(ex, sname)
                ex.b += bytes(16)
                self._tag(inner, "Values", "StructProperty", len(body), None, legacy_extra=bytes(ex.b))
                w.b += inner.b + body
            ex = W()
            self._name(ex, "StructProperty")
            return "ArrayProperty", [("ArrayProperty", 1), ("StructProperty", 1), (sname, 1), ("/Script/Engine", 0)], bytes(ex.b), bytes(w.b), 0
        raise ValueError(kind)

    def _struct_head(self, sname, body, native):
        ex = W()
        self._name(ex, sname)
        ex.b += bytes(16)
        return "StructProperty", [("StructProperty", 1), (sname, 1), ("/Script/Engine", 0)], bytes(ex.b), body, 8 if native else 0

    def props(self, items, top=True):
        w = W()
        if top and self.ue5 >= UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION:
            w.add("B", 0)
        for name, kind, v in items:
            if kind == "bool":
                if self.ue5 >= UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
                    self._tag(w, name, "BoolProperty", 0, [("BoolProperty", 0)], flags=0x10 if v else 0)
                else:
                    self._tag(w, name, "BoolProperty", 0, None, legacy_extra=bytes([1 if v else 0]))
                continue
            type_, tree, extra, body, flags = self._value(kind, v)
            self._tag(w, name, type_, len(body), tree, legacy_extra=extra, flags=flags)
            w.b += body
        self._name(w, "None")
        return bytes(w.b)

    # ---- the file

    def write(self, path):
        ue5 = self.ue5
        bodies = []
        for e in self.exports:
            body = W()
            body.b += self.props(e["props"], True)
            body.add("i", 0)                               # no object guid
            body.b += e["tail"]
            bodies.append(bytes(body.b))
        self.n(self.package_name)

        def summary(name_off, imp_off, exp_off, toc, total):
            s = W()
            if ue5:
                s.add("I", TAG).add("i", -8).add("i", 864).add("i", 522).add("i", ue5).add("i", 0)
                s.add("i", 1).b += struct.pack("<4I", 0x697DD581, 0xE64F41AB, 0xAA4A51EC, 0xBEB7B628)
                s.add("i", 51)                             # FUE5MainStreamObjectVersion
            else:                                          # a UE4 save (4.24 = 518): legacy -7, no UE5 version
                s.add("I", TAG).add("i", -7).add("i", 864).add("i", self.ue4).add("i", 0)
                s.add("i", 1).b += struct.pack("<4I", 0xE4B068ED, 0xF49442E9, 0xA231DA0B, 0x2E46BB41)
                s.add("i", self.editor_version)            # FEditorObjectVersion
            s.add("i", total).fstring(self.package_name).add("I", 0)
            s.add("ii", len(self.names), name_off)
            if ue5 >= 1008:
                s.add("ii", 0, 0)
            s.fstring("")
            s.add("ii", 0, 0)
            s.add("ii", len(self.exports), exp_off).add("ii", len(self.imports), imp_off)
            s.add("i", 0).add("ii", 0, 0).add("i", 0).add("i", 0)
            # the package guid; the persistent guid from UE4 518 (and every UE5); the owner's too in UE4 518-519
            s.b += bytes(32 if ue5 else 48 if 518 <= self.ue4 < 520 else 32 if self.ue4 >= 520 else 16)
            s.add("i", 0)
            for _ in range(2):
                s.add("HHHI", 5, 4, 0, 0).fstring("")
            s.add("I", 0).add("i", 0).add("I", 0).add("i", 0)
            s.add("i", 0).add("q", 0).add("i", 0).add("i", 0).add("ii", 0, 0)
            if ue5 >= 1001:
                s.add("i", 0)
            if ue5 >= 1002:
                s.add("q", toc)
            if ue5 >= 1009:
                s.add("i", -1)
            return bytes(s.b)

        head = len(summary(0, 0, 0, 0, 0))
        t = W()
        for s in self.names:
            t.fstring(s).add("I", 0)
        imp_off = head + len(t.b)
        for cp, cn, outer, nm in self.imports:
            self._name(t, cp), self._name(t, cn)
            t.add("i", outer)
            self._name(t, nm)
            if ue5:                                        # the package name (UE4 520+; a 4.24 save has none)
                self._name(t, "None")
            if ue5 >= 1003:
                t.add("i", 0)
        exp_off = head + len(t.b)
        exp_len = 112 if ue5 >= 1010 else 96 if ue5 >= 1006 else 104
        data_off = exp_off + exp_len * len(self.exports)
        ex = W()
        off = data_off
        for e, body in zip(self.exports, bodies):
            ex.add("iiii", e["cls"], e.get("super", 0), 0, e["outer"])
            self._name(ex, e["name"])
            ex.add("I", 0x1 | 0x8).add("qq", len(body), off).add("iii", 0, 0, 0)
            if ue5 < 1005:
                ex.b += bytes(16)                          # package guid
            if ue5 >= 1006:
                ex.add("i", 0)                             # is inherited
            ex.add("I", 0).add("ii", 0, 1)
            if ue5 >= 1003:
                ex.add("i", 0)                             # generate public hash
            ex.b += bytes(20)
            if ue5 >= 1010:
                ex.add("qq", 0, 0)
            off += len(body)
        assert len(ex.b) == exp_len * len(self.exports)
        toc = off
        tr = W()
        if self.payloads:
            entries = W()
            rel = 0
            for h20, cb in self.payloads:
                entries.b += h20
                entries.add("qQQ", rel, len(cb), len(cb)).add("HH", 0, 0).add("B", 0)
                rel += len(cb)
            header_len = 8 + 4 + 4 + 8 + 4 + len(entries.b)
            tr.add("QiIQ", 0xD1C43B2E80A5F697, 2, header_len, rel).add("i", len(self.payloads))
            tr.b += entries.b + b"".join(cb for _h, cb in self.payloads)
            tr.add("QQI", 0x29BFCA045138DE76, len(tr.b) + 20, TAG)
        else:
            toc = -1
        out = summary(head + 0, imp_off, exp_off, toc, data_off) + bytes(t.b) + bytes(ex.b) + b"".join(bodies) + bytes(tr.b)
        with open(path, "wb") as f:
            f.write(out)
        return path


def _delta(raw, row_bytes, h):
    a = np.frombuffer(raw, dtype=np.uint8).reshape(h, row_bytes)
    k = max(1, tx.UEDELTA_BLOCK // row_bytes)
    out = a.copy()
    for y0 in range(0, h, k):
        blk = a[y0:y0 + k]
        out[y0 + 1:y0 + k] = blk[1:] - blk[:-1]
    return out.tobytes()


def texture(path, pixels, ue5=1012, compression="TSCF_UEDELTA", name="T_Test", package="", settings=None):
    h, w = pixels.shape[:2]
    bgra = np.ascontiguousarray(pixels[..., [2, 1, 0, 3]]).tobytes()
    if compression == "TSCF_PNG":
        from PIL import Image
        f = io.BytesIO()
        # what Unreal writes for a BGRA8 source: the B, G, R, A bytes stored as the PNG's R, G, B, A
        Image.fromarray(np.ascontiguousarray(pixels[..., [2, 1, 0, 3]]), "RGBA").save(f, "PNG")
        raw = f.getvalue()
    elif compression == "TSCF_UEDELTA":
        raw = _delta(bgra, w * 4, h)
    else:
        raw = bgra
    b = Builder(package or "/Game/" + name, ue5)
    src = [("SizeX", "int", w), ("SizeY", "int", h), ("NumSlices", "int", 1), ("NumMips", "int", 1),
           ("Format", "enum", ("ETextureSourceFormat", "TSF_BGRA8")),
           ("CompressionFormat", "enum", ("ETextureSourceCompressionFormat", compression))]
    props = [("Source", "struct", ("TextureSource", src)), ("SRGB", "bool", settings is None)]
    if settings:
        props.append(("CompressionSettings", "enum", ("TextureCompressionSettings", settings)))
    b.export("Texture2D", name, props, tail=bytes([0, 0]) + b.payload(raw))
    return b.write(path)


def mesh_description(positions, corners, normals, uvs, groups, group_names, skin=None, bones=None):
    """FMeshDescription bytes (the UE 5 layout mesh.py reads): positions (n, 3) in cm; corners (t, 3) vertex indices,
    one vertex instance per corner; per-corner normals and UVs (t * 3 rows); groups: a polygon group per triangle.
    skin: per vertex [(bone, weight 0..1)]; bones: [(name, parent, (qx, qy, qz, qw, tx, ty, tz, sx, sy, sz))] (a skeletal
    mesh's description: SkinWeights on Vertices, a Bones element; the UE 5.8 layout, chunks of 256 elements)."""
    w = W()

    def fs(s):
        w.fstring(s)

    def bits(n):
        w.add("i", n)
        words = (n + 31) // 32
        for k in range(words):
            nb = min(32, n - 32 * k)
            w.add("I", (1 << nb) - 1 if nb < 32 else 0xFFFFFFFF)

    def attr(name, ty, extent, n, chans, default, flags):
        fs(name)
        w.add("iiii", ty, extent, n, len(chans))
        for data in chans:
            w.add("i", 1)
            if ty == 6:
                w.add("i", len(data))
                for s in data:
                    fs(s)
            else:
                esize = {0: 16, 1: 12, 2: 8, 3: 4, 4: 4, 5: 1}[ty]
                w.add("ii", esize, len(data))           # values of esize bytes: elements x extent
                w.b += np.ascontiguousarray(data).tobytes()
        if ty == 6:
            fs(default)
        else:
            w.b += default
        w.add("i", flags)

    def element(name, n, attrs):
        fs(name)
        w.add("i", 1)
        bits(n)
        w.add("iii", 0, n, len(attrs))
        for a in attrs:
            attr(*a)

    t = len(corners)
    pos = np.asarray(positions, "<f4")
    vi_vertex = np.asarray(corners, "<i4").reshape(-1)
    w.add("i", 8 if bones else 7)
    if skin is None:
        element("Vertices", len(pos), [("Position", 1, 1, len(pos), [pos], bytes(12), 0x21)])
    else:
        fs("Vertices")
        w.add("i", 1)
        bits(len(pos))
        w.add("iii", 0, len(pos), 2)
        attr("Position", 1, 1, len(pos), [pos], bytes(12), 0x21)
        fs("SkinWeights")
        w.add("iiii", 4, 0, len(pos), 1)                     # int32 values, an array per element, one channel
        chunks = [list(range(k, min(k + 256, len(pos)))) for k in range(0, len(pos), 256)]
        w.add("i", len(chunks))
        for ch in chunks:
            vals, offs, counts = [], [], []
            for v in reversed(ch):                           # offsets need not follow the element order
                offs.append(len(vals))
                counts.append(len(skin[v]))
                vals += [(b << 16) | int(round(wt * 65535)) for b, wt in skin[v]]
            offs, counts = offs[::-1], counts[::-1]
            w.add("i", len(vals)).add("%dI" % len(vals), *vals)
            w.add("i", len(ch)).add("%di" % len(ch), *offs).add("%di" % len(ch), *counts).add("%di" % len(ch), *counts)
        w.add("i", len(pos))
        w.b += bytes(8)
        w.add("i", 0x20)
    element("VertexInstances", 3 * t, [
        ("VertexIndex", 4, 1, 3 * t, [vi_vertex], struct.pack("<i", -1), 0x30),
        ("TextureCoordinate", 2, 1, 3 * t, [np.asarray(uvs, "<f4")], bytes(8), 0x21),
        ("Normal", 1, 1, 3 * t, [np.asarray(normals, "<f4")], bytes(12), 0x22)])
    element("UVs", 0, [("UVCoordinate", 2, 1, 0, [np.zeros((0, 2), "<f4")], bytes(8), 0x21)])
    element("Edges", 0, [])
    tri = np.arange(3 * t, dtype="<i4").reshape(t, 3)
    element("Triangles", t, [("VertexInstanceIndex", 4, 3, t, [tri.reshape(-1)], struct.pack("<i", -1), 0x30),
                             ("PolygonGroupIndex", 4, 1, t, [np.asarray(groups, "<i4")], struct.pack("<i", -1), 0x30)])
    element("Polygons", t, [("PolygonGroupIndex", 4, 1, t, [np.asarray(groups, "<i4")], struct.pack("<i", -1), 0x30)])
    element("PolygonGroups", len(group_names), [("ImportedMaterialSlotName", 6, 1, len(group_names), [list(group_names)], "None", 0x20)])
    if bones:
        fs("BonesElementName")
        w.add("i", 1)
        bits(len(bones))
        w.add("iii", 0, len(bones), 3)
        attr("Name", 6, 1, len(bones), [[b[0] for b in bones]], "None", 0x20)
        attr("ParentIndex", 4, 1, len(bones), [np.asarray([b[1] for b in bones], "<i4")], struct.pack("<i", -1), 0x20)
        fs("Pose")
        w.add("iiiiii", 7, 1, len(bones), 1, 1, len(bones))
        for b in bones:
            w.add("10d", *b[2])
        w.add("10d", 0, 0, 0, 1, 0, 0, 0, 1, 1, 1)
        w.add("i", 0x20)
    return bytes(w.b)


def skeletal_mesh(path, md_bytes, slots, n_bones, ue5=1012, name="SK_Test", package="", skeleton=None):
    """A SkeletalMesh package (UE 5.1+ layout): LOD0's mesh description on its own export, the material slots in the
    native data after the properties (strip flags, bounds, TArray<FSkeletalMaterial> of 52-byte slots), then the bone
    count of the reference skeleton. slots: [(slot name, material object path or None)]."""
    b = Builder(package or "/Game/" + name, ue5)
    b.exports.append(None)
    bd = b.export("SkeletalMeshDescriptionBulkData", "SkeletalMeshDescriptionBulkData_0", [], outer=1,
                  tail=b.payload(md_bytes) + bytes(16) + struct.pack("<i", 0))
    tail = W()
    tail.add("BB", 0, 0).add("7d", 0, 0, 0, 100, 100, 100, 173).add("i", len(slots))
    for sname, mpath in slots:
        ref = b.object_import(mpath, "Material") if mpath else 0
        tail.add("i", ref).add("ii", b.n(sname), 0).add("i", 1).add("ii", b.n(sname), 0)
        tail.add("ii", 1, 0).add("4f", 100, 0, 0, 0).add("i", 0)
    tail.add("i", n_bones)
    b.n(name)
    b.exports[0] = {"cls": b.class_ref("SkeletalMesh"), "name": name, "outer": 0, "tail": bytes(tail.b),
                    "props": [("SourceModels", "structs", ("SkeletalMeshSourceModel", [[("MeshDescriptionBulkData", "obj", bd)]]))]
                    + ([("Skeleton", "obj", b.object_import(skeleton, "Skeleton"))] if skeleton else [])}
    return b.write(path)


def anim_sequence(path, skeleton, controls, ue5=1012, name="A_Test", package="", tick=30):
    """An AnimSequence package (UE 5.2+ layout): its keys on a MovieSceneControlRigParameterSection.
    controls: {control name: {"Translation[i]" / "Rotation[i]" / "Scale[i]": (frames, values)}}; unlisted channels get
    a default (0, or 1 for scale)."""
    b = Builder(package or "/Game/" + name, ue5)
    b.exports.append(None)
    rows = []
    for cname, chans in controls.items():
        row = [("ParameterName", "name", cname)]
        for k in ("Translation", "Rotation", "Scale"):
            for j in range(3):
                key = k if j == 0 else "%s[%d]" % (k, j)
                fr, vals = chans.get("%s[%d]" % (k, j), ([], []))
                v = {"times": list(fr), "values": list(vals), "tick": tick}
                if not fr:
                    v["default"] = 1.0 if k == "Scale" else 0.0
                row.append((key, "fchannel", v))
        rows.append(row)
    b.export("MovieSceneControlRigParameterSection", "MovieSceneControlRigParameterSection_0",
             [("TransformParameterNamesAndCurves", "structs", ("EnumParameterNameAndCurve", rows))], outer=1)
    b.n(name)
    b.exports[0] = {"cls": b.class_ref("AnimSequence"), "name": name, "outer": 0, "tail": b"",
                    "props": [("Skeleton", "obj", b.object_import(skeleton, "Skeleton"))]}
    return b.write(path)


def static_mesh(path, md_bytes, slots, ue5=1012, name="SM_Test", package=""):
    """A StaticMesh package: LOD0's mesh description on a StaticMeshDescriptionBulkData export (payload in the trailer).
    slots: [(slot name, material object path or None)]."""
    b = Builder(package or "/Game/" + name, ue5)
    b.exports.append(None)
    tail = b.payload(md_bytes) + bytes(16) + struct.pack("<i", 0)
    bd = b.export("StaticMeshDescriptionBulkData", "StaticMeshDescriptionBulkData_0", [], outer=1, tail=tail)
    mats = []
    for sname, mpath in slots:
        row = [("MaterialSlotName", "name", sname), ("ImportedMaterialSlotName", "name", sname)]
        if mpath:
            row.insert(0, ("MaterialInterface", "obj", b.object_import(mpath, "Material")))
        mats.append(row)
    b.n(name)
    b.exports[0] = {"cls": b.class_ref("StaticMesh"), "name": name, "outer": 0, "tail": b"",
                    "props": [("SourceModels", "structs", ("StaticMeshSourceModel", [[("StaticMeshDescriptionBulkData", "obj", bd)]])),
                              ("StaticMaterials", "structs", ("StaticMaterial", mats))]}
    return b.write(path)


# ---- a small sample project: materials and textures, a mesh, a level

def material_pack(d, ue5=1012):
    """/Game/T: three textures; /Game/F/MF_Half: a function halving its input; /Game/M/M_Brick: a material reading
    them; /Game/M/MI_Brick: an instance overriding the roughness and the base texture."""
    os.makedirs(os.path.join(d, "T"))
    os.makedirs(os.path.join(d, "F"))
    os.makedirs(os.path.join(d, "M"))
    px = lambda c: np.tile(np.array(c, np.uint8), (4, 4, 1))
    texture(os.path.join(d, "T", "T_Brick.uasset"), px((200, 50, 40, 255)), ue5, "TSCF_None", "T_Brick", "/Game/T/T_Brick")
    texture(os.path.join(d, "T", "T_Moss.uasset"), px((40, 160, 40, 255)), ue5, "TSCF_None", "T_Moss", "/Game/T/T_Moss")
    texture(os.path.join(d, "T", "T_ORM.uasset"), px((255, 128, 0, 255)), ue5, "TSCF_None", "T_ORM", "/Game/T/T_ORM",
                settings="TC_Masks")
    texture(os.path.join(d, "T", "T_N.uasset"), px((128, 100, 255, 255)), ue5, "TSCF_None", "T_N", "/Game/T/T_N",
                settings="TC_Normalmap")
    f = Builder("/Game/F/MF_Half", ue5)
    fn = f.export("MaterialFunction", "MF_Half", [])
    c = f.export("MaterialExpressionConstant", "Half", [("R", "float", 0.5)], outer=fn)
    fi = f.export("MaterialExpressionFunctionInput", "In", [("Id", "name", "IN1")], outer=fn)
    mu = f.export("MaterialExpressionMultiply", "Mul", [("A", "input", (fi, 0, "")), ("B", "input", (c, 0, ""))], outer=fn)
    f.export("MaterialExpressionFunctionOutput", "Out", [("A", "input", (mu, 0, "")), ("Id", "name", "OUT1")], outer=fn)
    f.write(os.path.join(d, "F", "MF_Half.uasset"))

    b = Builder("/Game/M/M_Brick", ue5)
    m = 1                                                     # the material is the first export
    b.exports.append(None)
    eod_props = []
    tex = b.export("MaterialExpressionTextureSampleParameter2D", "BaseTex",
                   [("Texture", "obj", "/Game/T/T_Brick.T_Brick"), ("ParameterName", "name", "Base")], outer=m)
    tint = b.export("MaterialExpressionVectorParameter", "Tint",
                    [("DefaultValue", "color", (0.5, 0.5, 0.5, 1.0)), ("ParameterName", "name", "Tint")], outer=m)
    mul = b.export("MaterialExpressionMultiply", "Mul", [("A", "input", (tex, 0, "")), ("B", "input", (tint, 0, ""))], outer=m)
    orm = b.export("MaterialExpressionTextureSample", "ORM", [("Texture", "obj", "/Game/T/T_ORM.T_ORM")], outer=m)
    rough = b.export("MaterialExpressionScalarParameter", "Rough",
                     [("DefaultValue", "float", 0.9), ("ParameterName", "name", "Roughness")], outer=m)
    rmul = b.export("MaterialExpressionMultiply", "RMul", [("A", "input", (orm, 0, "g")), ("B", "input", (rough, 0, ""))], outer=m)
    nrm = b.export("MaterialExpressionTextureSample", "N", [("Texture", "obj", "/Game/T/T_N.T_N")], outer=m)
    one = b.export("MaterialExpressionConstant", "One", [("R", "float", 0.8)], outer=m)
    call = b.export("MaterialExpressionMaterialFunctionCall", "Call", [
        ("MaterialFunction", "obj", b.object_import("/Game/F/MF_Half.MF_Half", "MaterialFunction")),
        ("FunctionInputs", "structs", ("FunctionExpressionInput", [[("ExpressionInputId", "name", "IN1"),
                                                                    ("Input", "input", (one, 0, ""))]])),
        ("FunctionOutputs", "structs", ("FunctionExpressionOutput", [[("ExpressionOutputId", "name", "OUT1")]]))], outer=m)
    eod = b.export("MaterialEditorOnlyData", "M_BrickEditorOnlyData", [
        ("BaseColor", "input", (mul, 0, "")), ("Roughness", "input", (rmul, 0, "")), ("Normal", "input", (nrm, 0, "")),
        ("Metallic", "input", (call, 0, ""))], outer=m)
    b.n("M_Brick")
    b.exports[0] = {"cls": b.class_ref("Material"), "name": "M_Brick", "outer": 0, "tail": b"",
                    "props": [("EditorOnlyData", "obj", eod), ("TwoSided", "bool", True),
                              ("BlendMode", "enum", ("EBlendMode", "BLEND_Masked")), ("OpacityMaskClipValue", "float", 0.4)]}
    b.write(os.path.join(d, "M", "M_Brick.uasset"))

    i = Builder("/Game/M/MI_Brick", ue5)
    info = lambda n: ("ParameterInfo", "struct", ("MaterialParameterInfo", [("Name", "name", n)]))
    i.export("MaterialInstanceConstant", "MI_Brick", [
        ("Parent", "obj", i.object_import("/Game/M/M_Brick.M_Brick", "Material")),
        ("ScalarParameterValues", "structs", ("ScalarParameterValue", [[info("Roughness"), ("ParameterValue", "float", 0.5)]])),
        ("TextureParameterValues", "structs", ("TextureParameterValue", [[info("Base"),
                                                ("ParameterValue", "obj", i.object_import("/Game/T/T_Moss.T_Moss", "Texture2D"))]]))])
    i.write(os.path.join(d, "M", "MI_Brick.uasset"))
def level(d, ue5=1012):
    """/Game/Maps/L_Test: a mesh actor at (100, 200, 300) cm yawed 90 degrees with a child component 100 cm along its
    X; an instanced component with two instances 50 cm along X and along Y; a hidden actor."""
    os.makedirs(os.path.join(d, "Maps"), exist_ok=True)
    b = Builder("/Game/Maps/L_Test", ue5)
    b.exports.append(None)                                     # 1: the World, filled below
    lvl = b.export("Level", "PersistentLevel", [], outer=1)
    mesh = "/Game/Meshes/SM_Box.SM_Box"
    a = b.export("StaticMeshActor", "Box_0", [("ActorLabel", "name", "Box")], outer=lvl)
    root = b.export("StaticMeshComponent", "Root", [("StaticMesh", "obj", b.object_import(mesh, "StaticMesh")),
                                                    ("RelativeLocation", "vec", (100.0, 200.0, 300.0)),
                                                    ("RelativeRotation", "rot", (0.0, 90.0, 0.0))], outer=a)
    b.export("StaticMeshComponent", "Child", [("StaticMesh", "obj", b.object_import(mesh, "StaticMesh")),
                                              ("RelativeLocation", "vec", (100.0, 0.0, 0.0)), ("AttachParent", "obj", root)], outer=a)
    i = b.export("Actor", "Rocks_0", [], outer=lvl)
    fmt = "<16d" if ue5 >= 1004 else "<16f"
    m = lambda t: list(np.eye(4)[:3].reshape(-1)) + [t[0], t[1], t[2], 1.0]
    inst = struct.pack("<ii", 0, 0) + struct.pack("<ii", 128 if ue5 >= 1004 else 64, 2) + struct.pack(fmt, *m((50, 0, 0))) + struct.pack(fmt, *m((0, 50, 0)))
    b.export("InstancedStaticMeshComponent", "Rocks", [("StaticMesh", "obj", b.object_import(mesh, "StaticMesh"))], outer=i, tail=inst)
    h = b.export("StaticMeshActor", "Hidden_0", [("bHidden", "bool", True)], outer=lvl)
    b.export("StaticMeshComponent", "Root", [("StaticMesh", "obj", b.object_import(mesh, "StaticMesh"))], outer=h)
    b.n("L_Test")
    b.exports[0] = {"cls": b.class_ref("World"), "name": "L_Test", "outer": 0, "tail": b"", "props": []}
    b.write(os.path.join(d, "Maps", "L_Test.umap"))
def mesh_asset(d, ue5=1012):
    """/Game/Meshes/SM_Box: two triangles in Unreal space (cm, Z up), front faces as Unreal winds them, facing +Z;
    the first in slot Stone (M_Brick), the second in slot Bare (no material)."""
    os.makedirs(os.path.join(d, "Meshes"), exist_ok=True)
    pos = [(0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 50)]
    corners = [(0, 2, 1), (0, 3, 2)]                          # the second stands up along X = 0
    normals = [(0, 0, 1)] * 3 + [(1, 0, 0)] * 3
    uvs = [(0, 0), (0, 1), (1, 0), (0, 0), (0, 1), (1, 1)]
    md = mesh_description(pos, corners, normals, uvs, [0, 1], ["Stone", "Bare"])
    static_mesh(os.path.join(d, "Meshes", "SM_Box.uasset"), md, [("Stone", "/Game/M/M_Brick.M_Brick"), ("Bare", None)],
                    ue5, "SM_Box", "/Game/Meshes/SM_Box")


def skeletal_asset(d, ue5=1012):
    """/Game/Meshes/SK_Arm: 300 vertices (two skin-weight chunks), two triangles; bones Root (origin) and Arm (50 cm up,
    yawed 90 degrees); vertex 0 on Root, 1 split half and half, 2 on Arm, 3 a quarter Root, 299 (second chunk) on Arm."""
    import math
    os.makedirs(os.path.join(d, "Meshes"), exist_ok=True)
    pos = [(0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 50)] + [(1, 1, 1)] * 295 + [(0, 50, 50)]
    corners = [(0, 2, 1), (299, 3, 2)]
    normals = [(0, 0, 1)] * 3 + [(1, 0, 0)] * 3
    uvs = [(0, 0)] * 6
    skin = [[(0, 1.0)], [(0, 0.5), (1, 0.5)], [(1, 1.0)], [(1, 0.75), (0, 0.25)]] + [[(0, 1.0)]] * 295 + [[(1, 1.0)]]
    s = math.sqrt(0.5)
    bones = [("Root", -1, (0, 0, 0, 1, 0, 0, 0, 1, 1, 1)), ("Arm", 0, (0, 0, s, s, 0, 0, 50, 1, 1, 1))]
    md = mesh_description(pos, corners, normals, uvs, [0, 0], ["Skin"], skin, bones)
    skeletal_mesh(os.path.join(d, "Meshes", "SK_Arm.uasset"), md, [("Skin", "/Game/M/M_Brick.M_Brick")], 2, ue5,
                  "SK_Arm", "/Game/Meshes/SK_Arm", skeleton="/Game/Meshes/SK_Arm_Skeleton.SK_Arm_Skeleton")
    # a take: the arm raised 100 cm and yawed 90 degrees more over a second (30 frames)
    anim_sequence(os.path.join(d, "Meshes", "A_Wave.uasset"), "/Game/Meshes/SK_Arm_Skeleton.SK_Arm_Skeleton",
                  {"arm_CONTROL": {"Translation[2]": ([0, 30], [50, 150]), "Rotation[2]": ([0, 30], [90, 180])}},
                  ue5, "A_Wave", "/Game/Meshes/A_Wave")


def sample(d, ue5=1012):
    """The whole sample under d (a Content folder mounted as /Game)."""
    material_pack(d, ue5)
    mesh_asset(d, ue5)
    skeletal_asset(d, ue5)
    level(d, ue5)
    return d


def mesh_description_4(positions, corners, normals, uvs, groups, group_names):
    """A UE 4.24 FMeshDescription (the layout mesh.parse_description_4 reads): element arrays (vertices; vertex
    instances with their vertex; edges, none here; polygons with their group; groups), five attribute sets, then the
    triangles (three vertex instances and the polygon each)."""
    w = W()
    t = len(corners)

    def bits(n):
        w.add("i", n)
        for k in range((n + 31) // 32):
            nb = min(32, n - 32 * k)
            w.add("I", (1 << nb) - 1 if nb < 32 else 0xFFFFFFFF)

    def aset(n, attrs):
        w.add("ii", n, len(attrs))
        for name, ty, data, default in attrs:
            w.fstring(name).add("iii", ty, n, 1)
            if ty == 6:
                w.add("i", len(data))
                for x in data:
                    w.fstring(x)
                w.fstring(default)
            else:
                a = np.ascontiguousarray(data)
                w.add("ii", {0: 16, 1: 12, 2: 8, 3: 4, 4: 4}[ty], len(a))
                w.b += a.tobytes() + default
            w.add("i", 1)

    pos = np.asarray(positions, "<f4")
    bits(len(pos))
    bits(3 * t)
    w.b += np.asarray(corners, "<i4").reshape(-1).tobytes()
    bits(0)
    bits(t)
    for g in groups:
        w.add("ii", 0, g)
    bits(len(group_names))
    aset(len(pos), [("Position", 1, pos, bytes(12))])
    aset(3 * t, [("TextureCoordinate", 2, np.asarray(uvs, "<f4"), bytes(8)), ("Normal", 1, np.asarray(normals, "<f4"), bytes(12))])
    aset(0, [])
    aset(t, [])
    aset(len(group_names), [("ImportedMaterialSlotName", 6, list(group_names), "None")])
    bits(t)
    for k in range(t):
        w.add("iiii", 3 * k, 3 * k + 1, 3 * k + 2, k)
    w.add("ii", t, 0)
    return bytes(w.b)


def raw_mesh(positions, wedges, face_materials, normals, uvs):
    """FRawMesh bytes (UE 4.19-4.23): positions in cm; wedges: three vertex ids per face; per-wedge normals and UVs."""
    w = W()
    w.add("ii", 1, 0)                                     # RAW_MESH_VER (zero-triangle sections removed), licensee 0
    w.add("i", len(face_materials)).b += struct.pack("<%di" % len(face_materials), *face_materials)
    w.add("i", len(face_materials)).b += bytes(4 * len(face_materials))                  # smoothing masks
    w.add("i", len(positions)).b += struct.pack("<%df" % (3 * len(positions)), *[c for p in positions for c in p])
    w.add("i", len(wedges)).b += struct.pack("<%dI" % len(wedges), *wedges)
    for _ in range(2):                                    # tangents X and Y: none
        w.add("i", 0)
    w.add("i", len(normals)).b += struct.pack("<%df" % (3 * len(normals)), *[c for n in normals for c in n])
    w.add("i", len(uvs)).b += struct.pack("<%df" % (2 * len(uvs)), *[c for u in uvs for c in u])
    for _ in range(7):                                    # the other texture coordinate channels: empty
        w.add("i", 0)
    w.add("i", 0)                                         # wedge colours: none
    w.add("i", 0)                                         # material index to import index
    return bytes(w.b)


def static_mesh_4(path, md_bytes, slots, name="SM_Old", package="", ue4=518, raw=False):
    """A StaticMesh as UE 4.24 saves it: LOD0's mesh description in the native data after the properties, a legacy
    FByteBulkData stored inline and zlib-compressed (FArchive::SerializeCompressed chunks)."""
    import zlib
    b = Builder(package or "/Game/" + name, 0, ue4, 27 if raw else 37)   # raw: an FRawMesh (md_bytes from raw_mesh)
    comp = zlib.compress(md_bytes)
    chunks = struct.pack("<qqqq", TAG, 131072, len(comp), len(md_bytes)) + struct.pack("<qq", len(comp), len(md_bytes)) + comp
    tail = W()
    tail.add("BB", 0, 0).add("i", 0).add("ii", 0, 0).fstring("").add("I", 0)
    tail.b += bytes(16)
    tail.add("i", 0)                                      # no sockets
    if not raw:
        tail.add("i", 1)                                  # LOD0 valid (4.24's bIsValid; raw meshes have none)
    tail.add("I", (1 << 6) | (1 << 1)).add("iI", len(md_bytes), len(chunks)).add("q", 0)   # inline, zlib
    tail.b += chunks + bytes(16)                          # the payload, then the guid
    mats = []
    for sname, mpath in slots:
        row = [("MaterialSlotName", "name", sname), ("ImportedMaterialSlotName", "name", sname)]
        if mpath:
            row.insert(0, ("MaterialInterface", "obj", b.object_import(mpath, "Material")))
        mats.append(row)
    b.export("StaticMesh", name, [("SourceModels", "structs", ("StaticMeshSourceModel", [[("ScreenSize", "float", 1.0)]])),
                                  ("StaticMaterials", "structs", ("StaticMaterial", mats))], tail=bytes(tail.b))
    return b.write(path)


def blueprint(path, name, mesh=None, location=None, parent_class=None, ue5=1012, package=""):
    """A Blueprint package: its generated class <name>_C (super: parent_class, an object path, or none) and a
    StaticMeshComponent template StaticMesh_GEN_VARIABLE carrying the mesh and/or a relative location."""
    b = Builder(package or "/Game/" + name, ue5)
    b.exports.append(None)
    props = []
    if mesh:
        props.append(("StaticMesh", "obj", b.object_import(mesh, "StaticMesh")))
    if location:
        props.append(("RelativeLocation", "vec", location))
    b.export("StaticMeshComponent", "StaticMesh_GEN_VARIABLE", props, outer=1)
    b.n(name + "_C")
    b.exports[0] = {"cls": b.class_ref("BlueprintGeneratedClass"), "name": name + "_C", "outer": 0, "tail": b"", "props": []}
    if parent_class:
        b.exports[0]["super"] = b.object_import(parent_class, "BlueprintGeneratedClass")
    return b.write(path)


def bp_level(d, ue5=1012):
    """/Game/Props/BP_Base (the mesh SM_Box, 100 cm up) and its child BP_Kid (only a new location: 200 cm along X);
    /Game/Maps/L_BP: one BP_Kid actor at (0, 0, 300) cm whose component stores nothing but its attach parent."""
    os.makedirs(os.path.join(d, "Props"), exist_ok=True)
    os.makedirs(os.path.join(d, "Maps"), exist_ok=True)
    blueprint(os.path.join(d, "Props", "BP_Base.uasset"), "BP_Base", mesh="/Game/Meshes/SM_Box.SM_Box",
              location=(0.0, 0.0, 100.0), ue5=ue5, package="/Game/Props/BP_Base")
    blueprint(os.path.join(d, "Props", "BP_Kid.uasset"), "BP_Kid", location=(200.0, 0.0, 0.0),
              parent_class="/Game/Props/BP_Base.BP_Base_C", ue5=ue5, package="/Game/Props/BP_Kid")
    b = Builder("/Game/Maps/L_BP", ue5)
    b.exports.append(None)
    lvl = b.export("Level", "PersistentLevel", [], outer=1)
    cls = b.object_import("/Game/Props/BP_Kid.BP_Kid_C", "BlueprintGeneratedClass")
    b.exports.append({"cls": cls, "name": "BP_Kid_C_1", "outer": lvl, "tail": b"", "props": [("ActorLabel", "name", "Kid")]})
    actor = len(b.exports)
    b.n("BP_Kid_C_1")
    root = b.export("SceneComponent", "DefaultSceneRoot", [("RelativeLocation", "vec", (0.0, 0.0, 300.0))], outer=actor)
    b.export("StaticMeshComponent", "StaticMesh", [("AttachParent", "obj", root)], outer=actor)
    b.n("L_BP")
    b.exports[0] = {"cls": b.class_ref("World"), "name": "L_BP", "outer": 0, "tail": b"", "props": []}
    b.write(os.path.join(d, "Maps", "L_BP.umap"))
