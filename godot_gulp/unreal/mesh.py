"""Unreal static meshes from editor-saved packages: the LOD0 source model's FMeshDescription (UE 5.x), decoded here.

    static_mesh(content, object_path) -> (mesh dict for convert.write_gltf, notes)

FMeshDescription as UE 5 saves it (worked out from engine content, checked to parse to the exact end of every payload
and against the meshes' own normals; not from Epic source):

    element types: i32 count, then per type (Vertices, VertexInstances, UVs, Edges, Triangles, Polygons, PolygonGroups):
        FString name, i32 channels, per channel:
            allocated ids: TBitArray (i32 bits, words), i32 (unused here), i32 element count, i32 attribute count
            per attribute: FString name, i32 type, i32 values per element, i32 elements, i32 channels,
                per channel: i32, then FName values (i32 count, FStrings) or a bulk array (i32 size, i32 count, data)
                then the default value and i32 flags
    types: 0 Vector4f, 1 Vector3f, 2 Vector2f, 3 float, 4 int32, 5 bool (1 byte in arrays, 4 as the default), 6 FName

Unreal is left-handed Z-up in centimetres: a position (x, y, z) becomes (x, z, y) * 0.01, normals (x, z, y). That map
is a mirror and so reverses the handedness; Unreal's front faces are clockwise, so the triangle order is kept (the
check in decode() confirms it on every mesh: the faces agree with the stored normals). UVs share glTF's top-left origin.
"""
import struct

import numpy as np

from . import bulk
from .package import Reader, UnrealError

TYPE_SIZE = {0: 16, 1: 12, 2: 8, 3: 4, 4: 4, 5: 1}           # 6 FName (strings), 7 FTransform (80 bytes)
TYPE_DTYPE = {0: ("<f4", 4), 1: ("<f4", 3), 2: ("<f4", 2), 3: ("<f4", 1), 4: ("<i4", 1), 5: ("u1", 1)}
UE5_MAIN = None
MESH_DESCRIPTION_VIRTUALIZATION = 41


def parse_description(data):
    """FMeshDescription bytes -> {element type: {"count", "allocated" (bool array), attribute name: [channel arrays]}}.
    UE 5.8 saves write an FName channel's count twice; older ones once. Nothing in the payload says which, and the
    package's custom versions do not pin it down, so the older layout is tried first and the newer one when that does
    not parse to the exact end (a wrong layout cannot)."""
    results, errors = [], []
    for twice in (False, True):
        try:
            md, left = _parse_description(data, twice)
        except (UnrealError, ValueError, IndexError) as e:
            errors.append(str(e))
            continue
        if not left:
            return md
        results.append((left, md))
    if results and min(results, key=lambda x: x[0])[0] <= 4096:     # some saves leave stale buffer bytes after the end
        return min(results, key=lambda x: x[0])[1]
    raise UnrealError(errors[0] if errors else "mesh description: %d bytes left over" % min(results)[0])


def _parse_description(data, name_count_twice):
    """-> (description, leftover bytes that are not zero padding)"""
    r = Reader(data)
    out = {}
    for _ in range(r.i32()):
        tname = r.fstring()
        nch = r.i32()
        if not 0 <= nch <= 64:
            raise UnrealError("mesh description: %s has %d channels" % (tname, nch))
        el = {}
        for _c in range(nch):
            nbits = r.i32()
            words = np.frombuffer(r.raw(4 * ((nbits + 31) // 32)), dtype="<u4")
            alloc = np.unpackbits(words.view(np.uint8), bitorder="little")[:nbits].astype(bool)
            r.i32()
            count = r.i32()
            el = {"count": count, "allocated": alloc}
            for _a in range(r.i32()):
                aname, ty, extent, _nel, nchan = r.fstring().strip(), r.i32(), r.i32(), r.i32(), r.i32()   # "Position " is saved with a space
                chans = []
                for _k in range(nchan):
                    if extent == 0:                             # an array per element (skin weights): chunked
                        chans.append(_array_channel(r, ty, _nel))
                        continue
                    r.i32()
                    if ty == 7:                                 # FTransform: 10 doubles (quat xyzw, translation, scale)
                        n = r.i32()
                        chans.append(np.frombuffer(r.raw(80 * n), dtype="<f8").reshape(-1, 10))
                        continue
                    if ty == 6:
                        n = r.i32()
                        if name_count_twice and r.i32() != n:
                            raise UnrealError("mesh description: FName channel counts differ")
                        chans.append([r.fstring() for _ in range(n)])
                        continue
                    esize, n = r.i32(), r.i32()
                    raw = r.raw(esize * n)
                    if ty not in TYPE_DTYPE:
                        chans.append(None)
                        continue
                    dt, w = TYPE_DTYPE[ty]
                    a = np.frombuffer(raw, dtype=dt)
                    per = w * extent
                    chans.append(a.reshape(-1, per) if per > 1 else a)
                if extent == 0:
                    r.raw(8)                                    # the default: an empty array
                elif ty == 7:
                    r.raw(80)
                elif ty == 6:
                    r.fstring()
                elif ty == 5:
                    r.raw(4)
                elif ty in TYPE_SIZE:
                    r.raw(TYPE_SIZE[ty])                         # one value, whatever the extent
                else:
                    raise UnrealError("mesh description: attribute %s has unknown type %d" % (aname, ty))
                r.i32()                                         # flags
                el[aname] = chans
        out[tname] = el
    left = len(data) - r.pos if r.pos != len(data) and any(data[r.pos:]) else 0     # zero padding is fine
    return out, left


def _array_channel(r, ty, n_elements):
    """A per-element array attribute (TArrayAttribute; UE 5.x skin weights): i32 chunks, each i32 values + the values,
    i32 elements + their offsets into the chunk's values (not in element order) + their counts + their capacities;
    then i32 element total.
    -> a list, per element, of its values (int32 for skin weights: weight in the low 16 bits, bone in the high 16)."""
    dt = TYPE_DTYPE.get(ty, ("<i4", 1))[0]
    size = TYPE_SIZE.get(ty, 4)
    out = []
    for _ in range(r.i32()):
        nv = r.i32()
        vals = np.frombuffer(r.raw(size * nv), dtype=dt)
        ne = r.i32()
        offs = np.frombuffer(r.raw(4 * ne), dtype="<i4")
        counts = np.frombuffer(r.raw(4 * ne), dtype="<i4")
        r.raw(4 * ne)                                    # capacities
        out.extend(vals[o:o + c] for o, c in zip(offs, counts))
    total = r.i32()
    if total != len(out) or total != n_elements:
        raise UnrealError("mesh description: array attribute has %d elements, %d expected" % (len(out), n_elements))
    return out


def decode(md, notes):
    """-> [(polygon group index, slot name, attrs)] in glTF space, one entry per polygon group."""
    vi = md["VertexInstances"]
    tri = md["Triangles"]
    pos = md["Vertices"]["Position"][0].astype(np.float64)
    vidx = vi["VertexIndex"][0]
    corners = tri["VertexInstanceIndex"][0].reshape(-1, 3)
    groups = tri["PolygonGroupIndex"][0]
    live = tri["allocated"][:len(corners)] if len(tri["allocated"]) >= len(corners) else np.ones(len(corners), bool)
    names = (md.get("PolygonGroups", {}).get("ImportedMaterialSlotName") or [[]])[0]
    nrm = vi.get("Normal", [None])[0]
    uvs = vi.get("TextureCoordinate") or []
    col = vi.get("Color", [None])[0]
    swap = [0, 2, 1]
    P = pos[:, swap] * 0.01
    out = []
    agree = disagree = 0
    for g in sorted(set(groups[live].tolist())):
        sel = corners[live & (groups == g)].reshape(-1)
        a = {"POSITION": P[vidx[sel]].astype(np.float32)}
        if nrm is not None and len(nrm) >= len(vidx):
            n = nrm[sel][:, swap].astype(np.float64)
            a["NORMAL"] = (n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)).astype(np.float32)
            t = a["POSITION"].reshape(-1, 3, 3)
            fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
            dots = (fn * a["NORMAL"].reshape(-1, 3, 3).sum(1)).sum(1)
            agree += int((dots > 0).sum())
            disagree += int((dots < 0).sum())
        for k, ch in enumerate(uvs[:2]):
            if ch is not None and len(ch) >= len(vidx):
                a["TEXCOORD_%d" % k] = ch[sel].astype(np.float32)
        if col is not None and len(col) >= len(vidx) and not np.allclose(col[sel], 1.0):
            a["COLOR_0"] = np.clip(col[sel], 0, 1).astype(np.float32)
        out.append((g, names[g] if g < len(names) else "", a))
    if disagree > agree:
        notes.append("faces disagree with the stored normals (%d of %d): winding suspect" % (disagree, agree + disagree))
    return out


EDITOR_OBJECT_VERSION = "ed68b0e4e94294f40bda31a241bb462e"     # FEditorObjectVersion E4B068ED-F494-42E9-A231-DA0B2E46BB41
STATIC_MESH_DEPRECATED_RAW_MESH = 28


def _ue4_source_bytes(pkg, i):
    """UE 4.2x: LOD0's FMeshDescriptionBulkData in UStaticMesh's native data (after the properties: strip flags,
    bCooked, BodySetup, NavCollision, HighResSourceMeshName and CRC, LightingGuid, sockets, then per source model
    bIsValid and a legacy FByteBulkData). Older saves (UE 4.19-4.23) keep each source model's FRawMeshBulkData there
    instead: -> ("raw", bytes) for parse_raw_mesh, else ("description", bytes) for parse_description_4."""
    from . import bulk
    _props, r, _end = pkg.props(i)
    if r.raw(2)[0] & 1:
        raise UnrealError("editor data stripped (a cooked mesh)")
    if r.i32():
        raise UnrealError("a cooked mesh")
    r.i32(), r.i32()                                     # BodySetup, NavCollision
    r.fstring(), r.u32()                                 # high-res source mesh name and CRC
    r.raw(16)                                            # lighting guid
    r.raw(4 * r.i32())                                   # sockets
    if pkg.custom_version(EDITOR_OBJECT_VERSION) < STATIC_MESH_DEPRECATED_RAW_MESH:
        return "raw", bulk.byte_bulk(pkg, r)             # LOD0's FRawMeshBulkData (no bIsValid before 4.24)
    if not r.i32():
        raise UnrealError("LOD0 has no source mesh")
    return "description", bulk.byte_bulk(pkg, r)


MAX_MESH_TEXTURE_COORDS = 8


def parse_raw_mesh(data):
    """A UE 4.19-4.23 FRawMesh (RawMesh.cpp operator<<: version, licensee version, face material indices, smoothing masks,
    vertex positions, wedge indices, wedge tangents X/Y/Z, 8 texture coordinate channels, wedge colours, then the
    material index remap) into the shape parse_description() returns, one vertex instance per wedge."""
    r = Reader(data)
    r.i32(), r.i32()                                           # raw mesh version, licensee version

    def arr(fmt, width):
        n = r.i32()
        if n < 0 or n * width > len(data) - r.pos:
            raise UnrealError("raw mesh: array of %d" % n)
        return np.frombuffer(r.raw(n * width), dtype=fmt).copy() if n else np.zeros(0, fmt)

    face_mat = arr("<i4", 4)
    arr("<u4", 4)                                              # smoothing masks: the wedge normals carry the shading
    pos = arr("<f4", 12).reshape(-1, 3)
    wedge = arr("<u4", 4).astype(np.int64)
    arr("<f4", 12)                                             # tangent X
    arr("<f4", 12)                                             # tangent Y
    nrm = arr("<f4", 12).reshape(-1, 3)
    uvs = [arr("<f4", 8).reshape(-1, 2) for _ in range(MAX_MESH_TEXTURE_COORDS)]
    col = arr("u1", 4).reshape(-1, 4)                          # FColor: B, G, R, A
    n = len(wedge)
    if n % 3 or len(face_mat) != n // 3 or (len(wedge) and wedge.max() >= len(pos)):
        raise UnrealError("raw mesh: %d wedges, %d faces, %d positions" % (n, len(face_mat), len(pos)))
    vi = {"count": n, "allocated": np.ones(n, bool), "VertexIndex": [wedge]}
    if len(nrm) == n:
        vi["Normal"] = [nrm]
    vi["TextureCoordinate"] = [u for u in uvs if len(u) == n] or []
    if len(col) == n:
        vi["Color"] = [col[:, [2, 1, 0, 3]].astype(np.float32) / 255.0]
    return {"Vertices": {"count": len(pos), "allocated": np.ones(len(pos), bool), "Position": [pos]},
            "VertexInstances": vi,
            "Triangles": {"count": n // 3, "allocated": np.ones(n // 3, bool), "VertexInstanceIndex": [np.arange(n)],
                          "PolygonGroupIndex": [face_mat]},
            "PolygonGroups": {"count": 0, "allocated": np.zeros(0, bool)}}


def _description_bytes(pkg, export_index):
    """The FMeshDescriptionBulkData on a StaticMeshDescriptionBulkData export -> raw FMeshDescription bytes."""
    _props, r, end = pkg.props(export_index)
    return bulk.editor_bulk(pkg, r)


def static_mesh(content, object_path, mat_fn=None):
    """-> (mesh dict, notes). mat_fn(material object path or None, slot name) -> materials.Mat for each section."""
    from .. import materials as mt
    pkg = content.package(object_path)
    i = content.export_of(pkg, object_path)
    if pkg.exports[i].class_name != "StaticMesh":
        raise UnrealError("%s is a %s" % (object_path, pkg.exports[i].class_name))
    props, _r, _e = pkg.props(i)
    notes = []
    models = props.get("SourceModels") or []
    if not models:
        raise UnrealError("%s: no source models (a cooked mesh)" % object_path)
    ref = models[0].get("StaticMeshDescriptionBulkData") if isinstance(models[0], dict) else None
    if ref and ref.index > 0:
        md = parse_description(_description_bytes(pkg, ref.index - 1))
    elif pkg.ue5 == 0:
        kind, data = _ue4_source_bytes(pkg, i)
        md = parse_raw_mesh(data) if kind == "raw" else parse_description_4(data)
        if kind == "raw":
            notes.append("source: a UE 4.19-4.23 raw mesh")
    else:
        raise UnrealError("%s: LOD0 has no mesh description export" % object_path)
    if len(models) > 1:
        notes.append("LOD0 kept; %d coarser LODs left out (Godot makes its own)" % (len(models) - 1))
    if props.get("NaniteSettings", {}).get("bEnabled") if isinstance(props.get("NaniteSettings"), dict) else False:
        notes.append("Nanite mesh: the full-detail source kept")
    slots = props.get("StaticMaterials") or []
    by_name = {}
    for k, s in enumerate(slots):
        if isinstance(s, dict):
            by_name.setdefault(s.get("ImportedMaterialSlotName") or "", k)
            by_name.setdefault(s.get("MaterialSlotName") or "", k)
    sim = {}
    for key, v in (props.get("SectionInfoMap") or {}).get("Map", []) if isinstance(props.get("SectionInfoMap"), dict) else []:
        if isinstance(v, dict) and key < 1 << 16:                # LOD0 sections: keys 0..n (LOD << 16 | section)
            sim[key] = v.get("MaterialIndex", key)
    prims = []
    for g, gname, attrs in decode(md, notes):
        slot = by_name.get(gname, sim.get(g, g))
        mpath = None
        if 0 <= slot < len(slots) and isinstance(slots[slot], dict) and slots[slot].get("MaterialInterface"):
            mpath = slots[slot]["MaterialInterface"].path
        mat = mat_fn(mpath, gname) if mat_fn else mt.Mat(gname or "material")
        prims.append({"path": gname, "attrs": attrs, "mat": mat, "slot": slot})
    name = pkg.exports[i].name
    return {"name": name, "path": object_path, "uv_sets": ["st"], "subsets": 0, "prims": prims}, notes


# ---- skeletal meshes (UE 5.x: LOD0's mesh description carries the skin weights and the bones)

S4 = np.array([[0.01, 0, 0, 0], [0, 0, 0.01, 0], [0, 0.01, 0, 0], [0, 0, 0, 1.0]])
S4_INV = np.linalg.inv(S4)


def quat_matrix(x, y, z, w):
    """A unit quaternion -> 3x3 rotation for column vectors (Unreal's FQuat rotates v as q v q*, the usual way)."""
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def transform_matrix(t10):
    """An FTransform (quat xyzw, translation, scale; Unreal space, cm) -> its 4x4 in glTF space (S M S^-1)."""
    M = np.eye(4)
    M[:3, :3] = quat_matrix(*t10[:4]) @ np.diag(t10[7:10])
    M[:3, 3] = t10[4:7]
    return S4 @ M @ S4_INV


def bones(md):
    """-> [(name, parent index, local 4x4 in glTF space)] from the mesh description's bones element, or []."""
    key = next((k for k in md if k.startswith("Bones")), None)
    if key is None:
        return []
    b = md[key]
    names, parents, pose = b["Name"][0], b["ParentIndex"][0], b["Pose"][0]
    return [(names[i], int(parents[i]), transform_matrix(pose[i])) for i in range(len(names))]


def skin_attrs(md, vidx, sel):
    """JOINTS_0 / WEIGHTS_0 for the corners sel (vertex instances): each vertex's four strongest bones, normalised."""
    sw = md["Vertices"]["SkinWeights"][0]
    J = np.zeros((len(sw), 4), np.float32)
    W = np.zeros((len(sw), 4), np.float32)
    for v, vals in enumerate(sw):
        if not len(vals):
            continue
        vals = np.asarray(vals, dtype=np.int64)
        bone, wt = vals >> 16, (vals & 0xFFFF) / 65535.0
        top = np.argsort(-wt)[:4]
        tot = wt[top].sum() or 1.0
        J[v, :len(top)], W[v, :len(top)] = bone[top], wt[top] / tot
    corner_v = vidx[sel]
    return J[corner_v], W[corner_v]


def skeletal_materials(pkg, r, end, n_bones):
    """The material slots in USkeletalMesh's native data after its properties: FStripDataFlags, the imported bounds
    (FBoxSphereBounds, doubles from UE 5.0), then TArray<FSkeletalMaterial>, then the reference skeleton (its bone
    count first). A slot's size depends on the engine version, so it is found: the size at which every slot's
    material reference and names resolve and the next value is the skeleton's bone count.
    -> [(imported slot name, slot name, material object path or None)]"""
    b = pkg.data
    o = r.pos + 2 + (56 if pkg.ue5 >= 1004 else 28)
    n = struct.unpack_from("<i", b, o)[0]
    o += 4
    if not 0 <= n <= 256:
        raise UnrealError("skeletal mesh: %d material slots" % n)

    def name(off):
        k, num = struct.unpack_from("<ii", b, off)
        return pkg.names[k] if 0 <= k < len(pkg.names) and num >= 0 else None

    for size in (52, 48, 56, 44, 60, 64, 40):
        if o + n * size + 4 > end:
            continue
        slots, ok = [], True
        for k in range(n):
            e = o + k * size
            ref = struct.unpack_from("<i", b, e)[0]
            slot = name(e + 4)
            if slot is None or (ref and pkg.ref(ref) is None):
                ok = False
                break
            imported = name(e + 16) if size >= 48 and struct.unpack_from("<i", b, e + 12)[0] in (0, 1) else None
            slots.append((imported or slot, slot, pkg.object_path(ref) if ref else None))
        if ok and struct.unpack_from("<i", b, o + n * size)[0] == n_bones:
            return slots
    raise UnrealError("skeletal mesh: material slots not found (an unknown slot layout)")


def skeletal_mesh(content, object_path, mat_fn=None):
    """-> (mesh dict, bones [(name, parent, local)], notes). The mesh dict's primitives carry JOINTS_0/WEIGHTS_0;
    its "ibm" holds each bone's inverse bind matrix (glTF space)."""
    from .. import materials as mt
    pkg = content.package(object_path)
    i = content.export_of(pkg, object_path)
    if pkg.exports[i].class_name != "SkeletalMesh":
        raise UnrealError("%s is a %s" % (object_path, pkg.exports[i].class_name))
    props, _r, _e = pkg.props(i)
    notes = []
    models = props.get("SourceModels") or []
    ref = (models[0].get("MeshDescriptionBulkData") or models[0].get("SkeletalMeshDescriptionBulkData")) if models and isinstance(models[0], dict) else None
    if not ref or ref.index <= 0:
        raise UnrealError("%s: no LOD0 mesh description (saved before UE 5.1? the older ImportedModel layout is not "
                          "read yet)" % object_path)
    md = parse_description(_description_bytes(pkg, ref.index - 1))
    if len(models) > 1:
        notes.append("LOD0 kept; %d coarser LODs left out (Godot makes its own)" % (len(models) - 1))
    bl = bones(md)
    if not bl:
        raise UnrealError("%s: no bones in the mesh description" % object_path)
    world = []
    for name, parent, L in bl:
        world.append((world[parent] @ L) if parent >= 0 else L)
    pr, rr, end = pkg.props(i)
    try:
        slots = skeletal_materials(pkg, rr, end, len(bl))
    except UnrealError as e:
        slots = []
        notes.append(str(e))
    by_name = {}
    for k, (imported, slot, _m) in enumerate(slots):
        by_name.setdefault(imported, k)
        by_name.setdefault(slot, k)
    vidx = md["VertexInstances"]["VertexIndex"][0]
    corners = md["Triangles"]["VertexInstanceIndex"][0].reshape(-1, 3)
    groups = md["Triangles"]["PolygonGroupIndex"][0]
    prims = []
    for g, gname, attrs in decode(md, notes):
        sel = corners[groups == g].reshape(-1)
        attrs["JOINTS_0"], attrs["WEIGHTS_0"] = skin_attrs(md, vidx, sel)
        slot = by_name.get(gname, g)
        mpath = slots[slot][2] if 0 <= slot < len(slots) else None
        mat = mat_fn(mpath, gname) if mat_fn else mt.Mat(gname or "material")
        prims.append({"path": gname, "attrs": attrs, "mat": mat, "slot": slot})
    m = {"name": pkg.exports[i].name, "path": object_path, "uv_sets": ["st"], "subsets": 0, "prims": prims,
         "ibm": [np.linalg.inv(W) for W in world]}
    return m, bl, notes


# ---- UE 4.2x mesh descriptions (FMeshDescriptionBulkData in a legacy FByteBulkData; packs saved by 4.24-4.27)

def _bits(r):
    n = r.i32()
    words = np.frombuffer(r.raw(4 * ((n + 31) // 32)), dtype="<u4")
    return np.unpackbits(words.view(np.uint8), bitorder="little")[:n].astype(bool)


def _attribute_set(r, name_count_twice=False):
    """A UE 4.2x TAttributesSet: i32 elements, i32 attributes, each: FString name, i32 type, i32 elements, i32 channels,
    per channel a bulk array (i32 size, i32 count, data) or FNames, then the default and i32 flags."""
    n_el, out = r.i32(), {}
    for _ in range(r.i32()):
        name, ty, _nel, nch = r.fstring().strip(), r.i32(), r.i32(), r.i32()
        chans = []
        for _c in range(nch):
            if ty == 6:
                chans.append([r.fstring() for _ in range(r.i32())])
                continue
            esize, n = r.i32(), r.i32()
            raw = r.raw(esize * n)
            dt, w = TYPE_DTYPE[ty]
            a = np.frombuffer(raw, dtype=dt)
            chans.append(a.reshape(-1, w) if w > 1 else a)
        if ty == 6:
            r.fstring()
        elif ty == 5:
            r.raw(4)
        elif ty in TYPE_SIZE:
            r.raw(TYPE_SIZE[ty])
        else:
            raise UnrealError("mesh description (4.2x): attribute %s has unknown type %d" % (name, ty))
        r.i32()
        out[name] = chans
    return n_el, out


def parse_description_4(data):
    """A UE 4.2x FMeshDescription (worked out from real 4.24 packs; checked to parse to the end) into the same shape
    parse_description() returns, so decode() reads both."""
    r = Reader(data)
    vbits = _bits(r)
    vibits = _bits(r)
    vi_vertex = np.frombuffer(r.raw(4 * int(vibits.sum())), dtype="<i4")
    ebits = _bits(r)
    r.raw(8 * int(ebits.sum()))                                # edges: two vertex ids each
    pbits = _bits(r)
    poly = np.frombuffer(r.raw(8 * int(pbits.sum())), dtype="<i4").reshape(-1, 2)
    _bits(r)                                                   # polygon groups (no payload)
    sets = [_attribute_set(r) for _ in range(5)]
    tbits = _bits(r)
    tri = np.frombuffer(r.raw(16 * int(tbits.sum())), dtype="<i4").reshape(-1, 4)
    if len(data) - r.pos > 64:
        raise UnrealError("mesh description (4.2x): %d bytes left over" % (len(data) - r.pos))

    def dense(bits, values):
        """Items are stored for the set bits only: back to their ids."""
        out = np.zeros((len(bits),) + values.shape[1:], values.dtype)
        out[bits] = values
        return out

    vi_vertex = dense(vibits, vi_vertex)
    poly_group = dense(pbits, poly[:, 1]) if len(poly) else np.zeros(0, np.int32)   # (0, group): checked on real multi-slot 4.24 meshes
    md = {"Vertices": dict(sets[0][1], count=len(vbits), allocated=vbits),
          "VertexInstances": dict(sets[1][1], count=len(vibits), allocated=vibits, VertexIndex=[vi_vertex]),
          "Triangles": {"count": len(tbits), "allocated": tbits, "VertexInstanceIndex": [dense(tbits, tri[:, :3]).reshape(-1)],
                        "PolygonGroupIndex": [poly_group[dense(tbits, tri[:, 3])]]},
          "PolygonGroups": dict(sets[4][1], count=0, allocated=np.zeros(0, bool))}
    return md
