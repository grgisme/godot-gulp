"""One USD file to glTF without Blender: OpenUSD (pxr) reads the stage, this writes .gltf + .bin directly.

    godot-gulp file <in.usd[a|c|z]> <out.gltf> [--variant set=value ...] [--json]

Geometry: every Mesh (instance proxies realised), faces triangulated (holeIndices dropped, left-handed and
mirrored transforms re-wound), normals (authored, any interpolation, indexed; else area-weighted), every UV set
(st first, then the rest by name: TEXCOORD_0..n, V flipped to glTF's top-left origin), displayColor/displayOpacity as
COLOR_0 when it varies, one primitive per materialBind GeomSubset, metersPerUnit and upAxis to metres, Y up. The
stage's own variant selections, or --variant. Materials: materials.py. Instancing: one mesh per prototype, a node per
instance.

Prints one JSON line of what it wrote (meshes, tris, verts, size, uv_sets, subsets, variants) with --json.
"""
import argparse, json, os
import numpy as np
from pxr import Sdf, Usd, UsdGeom, UsdShade
from . import materials as usdmat

F32, U32, U16 = 5126, 5125, 5123          # glTF componentType
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


# ---- reading a mesh

def _flat(pv, n_points, counts, time):
    """A primvar as one value per face corner (faceVarying order), whatever its interpolation; None if unusable."""
    if not pv or not pv.HasAuthoredValue():
        return None
    vals = pv.ComputeFlattened(time)
    if vals is None or len(vals) == 0:
        return None
    a = np.array(vals, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    interp = pv.GetInterpolation()
    n_corners = int(counts.sum())
    if interp == UsdGeom.Tokens.faceVarying:
        return a if len(a) == n_corners else None
    if interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
        return None if len(a) != n_points else "vertex", a     # mapped through the face indices by the caller
    if interp == UsdGeom.Tokens.uniform:
        return np.repeat(a, counts, axis=0) if len(a) == len(counts) else None
    if interp == UsdGeom.Tokens.constant:
        return np.repeat(a[:1], n_corners, axis=0)
    return None


def _corners(pv, n_points, counts, indices, time):
    r = _flat(pv, n_points, counts, time)
    if isinstance(r, tuple):
        return r[1][indices]
    return r


def uv_primvars(mesh):
    """The texture-coordinate primvars, "st" first then by name (glTF TEXCOORD_n order)."""
    api = UsdGeom.PrimvarsAPI(mesh)
    out = []
    for pv in api.GetPrimvarsWithValues():
        tn = pv.GetTypeName()
        role = tn.role if hasattr(tn, "role") else ""
        if tn in UV_TYPES or role == "TextureCoordinate":
            out.append(pv)
    out.sort(key=lambda p: (p.GetPrimvarName() != "st", str(p.GetPrimvarName())))
    return out


UV_TYPES = (Sdf.ValueTypeNames.TexCoord2f, Sdf.ValueTypeNames.TexCoord2d, Sdf.ValueTypeNames.Float2, Sdf.ValueTypeNames.Double2)


def _tri_fans(counts, holes):
    """(face index, corner offset of v0, of v1, of v2) for each triangle; holes dropped, fans per polygon."""
    tris = []
    off = 0
    hole = set(int(h) for h in holes)
    for f, c in enumerate(counts):
        c = int(c)
        if c >= 3 and f not in hole:
            for k in range(1, c - 1):
                tris.append((f, off, off + k, off + k + 1))
        off += c
    return np.array(tris, dtype=np.int64).reshape(-1, 4)


def read_mesh(prim, xf, time):
    """One mesh in world space (metres, Y up applied by the caller's xf): a dict of numpy arrays per primitive."""
    m = UsdGeom.Mesh(prim)
    pts = np.array(m.GetPointsAttr().Get(time) or [], dtype=np.float64)
    counts = np.array(m.GetFaceVertexCountsAttr().Get(time) or [], dtype=np.int64)
    idx = np.array(m.GetFaceVertexIndicesAttr().Get(time) or [], dtype=np.int64)
    if len(pts) == 0 or len(counts) == 0 or len(idx) != counts.sum():
        return None
    holes = m.GetHoleIndicesAttr().Get(time) or []
    tris = _tri_fans(counts, holes)
    if len(tris) == 0:
        return None
    left = m.GetOrientationAttr().Get(time) == UsdGeom.Tokens.leftHanded
    mat = np.array(xf, dtype=np.float64).reshape(4, 4)        # Gf row-vector convention: p' = p @ M
    flip = left != (np.linalg.det(mat[:3, :3]) < 0)            # a mirror and a left-handed mesh cancel
    if flip:
        tris = tris[:, [0, 1, 3, 2]]
    corner = tris[:, 1:].reshape(-1)                            # face-corner index of every triangle corner
    face_of = np.repeat(tris[:, 0], 3)
    p_world = np.c_[pts, np.ones(len(pts))] @ mat
    P = p_world[idx[corner], :3]

    # normals: primvars:normals over the normals attribute; area-weighted per point when neither is authored
    api = UsdGeom.PrimvarsAPI(m)
    nc = None
    npv = api.GetPrimvar("normals")
    if npv and npv.HasAuthoredValue():
        nc = _corners(npv, len(pts), counts, idx, time)
    elif m.GetNormalsAttr().HasAuthoredValue():
        nv = np.array(m.GetNormalsAttr().Get(time), dtype=np.float64)
        it = m.GetNormalsInterpolation()
        if it == UsdGeom.Tokens.faceVarying and len(nv) == counts.sum():
            nc = nv
        elif it in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying) and len(nv) == len(pts):
            nc = nv[idx]
        elif it == UsdGeom.Tokens.uniform and len(nv) == len(counts):
            nc = np.repeat(nv, counts, axis=0)
    if nc is not None:
        N = nc[corner] @ np.linalg.inv(mat[:3, :3]).T           # normals by the inverse transpose (authored normals are
                                                                # directions: a left-handed mesh re-winds, its normals stay)
    else:
        tri_p = P.reshape(-1, 3, 3)
        fn = np.cross(tri_p[:, 1] - tri_p[:, 0], tri_p[:, 2] - tri_p[:, 0])   # area-weighted, already wound outward
        acc = np.zeros((len(pts), 3))
        np.add.at(acc, idx[corner].reshape(-1, 3), fn[:, None, :].repeat(3, axis=1))
        N = acc[idx[corner]]
    ln = np.linalg.norm(N, axis=1, keepdims=True)
    N = np.where(ln > 1e-12, N / np.maximum(ln, 1e-12), np.array([0.0, 1.0, 0.0]))

    attrs = {"POSITION": P.astype(np.float32), "NORMAL": N.astype(np.float32)}
    uv_names = []
    for i, pv in enumerate(uv_primvars(m)):
        st = _corners(pv, len(pts), counts, idx, time)
        if st is None or st.shape[1] < 2:
            continue
        uv = st[corner][:, :2].copy()
        uv[:, 1] = 1.0 - uv[:, 1]                               # USD's origin is bottom-left, glTF's top-left
        attrs["TEXCOORD_%d" % len(uv_names)] = uv.astype(np.float32)
        uv_names.append(str(pv.GetPrimvarName()))

    col = api.GetPrimvar("displayColor")
    opa = api.GetPrimvar("displayOpacity")
    c = _corners(col, len(pts), counts, idx, time) if col and col.GetInterpolation() != UsdGeom.Tokens.constant else None
    o = _corners(opa, len(pts), counts, idx, time) if opa and opa.GetInterpolation() != UsdGeom.Tokens.constant else None
    if c is not None or o is not None:
        n = len(corner)
        rgb = c[corner][:, :3] if c is not None else np.ones((n, 3))
        a = o[corner][:, :1] if o is not None else np.ones((n, 1))
        attrs["COLOR_0"] = np.c_[rgb, a].astype(np.float32)

    # one primitive per materialBind subset (faces left over form one more)
    subsets = UsdGeom.Subset.GetGeomSubsets(m, UsdGeom.Tokens.face, "materialBind") if hasattr(UsdGeom.Subset, "GetGeomSubsets") else []
    groups = []
    taken = np.zeros(len(counts), dtype=bool)
    for s in subsets:
        fids = np.array(s.GetIndicesAttr().Get(time) or [], dtype=np.int64)
        fids = fids[(fids >= 0) & (fids < len(counts))]
        taken[fids] = True
        groups.append((s.GetPrim(), np.isin(face_of[::3], fids)))
    rest = ~taken[face_of[::3]]
    if rest.any():
        groups.append((prim, rest))
    const_colour = None
    if col and col.HasAuthoredValue() and col.GetInterpolation() == UsdGeom.Tokens.constant:
        v = col.Get(time)
        const_colour = list(v[0]) if v is not None and len(v) else None
    prims = []
    for bind, tri_mask in groups:
        sel = np.repeat(tri_mask, 3)
        if not sel.any():
            continue
        mp = UsdShade.MaterialBindingAPI(bind).ComputeBoundMaterial()[0]
        prims.append({"path": str(bind.GetPath()), "attrs": {k: v[sel] for k, v in attrs.items()},
                      "material": mp.GetPrim() if mp else None, "colour": const_colour})
    return {"name": prim.GetName(), "path": str(prim.GetPath()), "prims": prims, "uv_sets": uv_names,
            "subsets": len(subsets)}


def weld(attrs):
    """De-duplicate identical corners: (attributes of unique vertices, uint32 indices)."""
    keys = sorted(attrs)
    stacked = np.concatenate([attrs[k].reshape(len(attrs[k]), -1) for k in keys], axis=1)
    uniq, inverse = np.unique(stacked, axis=0, return_inverse=True)
    out, col = {}, 0
    for k in keys:
        w = attrs[k].reshape(len(attrs[k]), -1).shape[1]
        out[k] = np.ascontiguousarray(uniq[:, col:col + w], dtype=np.float32)
        col += w
    return out, inverse.reshape(-1).astype(np.uint32)


# ---- the stage

def root_matrix(stage):
    """metersPerUnit and upAxis to glTF: metres, Y up (row-vector convention, applied after the prim's own world)."""
    s = UsdGeom.GetStageMetersPerUnit(stage) or 0.01
    m = np.eye(4) * s
    m[3, 3] = 1.0
    if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
        r = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)   # (x, y, z) -> (x, z, -y)
        m = m @ r
    return m


def open_stage(path, variants=()):
    stage = Usd.Stage.Open(path)
    if stage is None:
        raise RuntimeError("cannot open %s" % path)
    for sel in variants:                                   # "set=value" on the default prim, else the first prim that has it
        vset, _, val = sel.partition("=")
        targets = [stage.GetDefaultPrim()] if stage.GetDefaultPrim() else []
        targets += [p for p in stage.Traverse() if p.GetVariantSets().HasVariantSet(vset)]
        for p in targets:
            if p and p.GetVariantSets().HasVariantSet(vset):
                p.GetVariantSets().GetVariantSet(vset).SetVariantSelection(val)
                break
        else:
            raise RuntimeError("no variant set %s on %s" % (vset, path))
    return stage


def variant_sets(stage):
    out = {}
    for p in stage.Traverse():
        vs = p.GetVariantSets()
        for n in vs.GetNames():
            out["%s:%s" % (p.GetPath(), n)] = {"names": vs.GetVariantSet(n).GetVariantNames(),
                                               "selected": vs.GetVariantSet(n).GetVariantSelection()}
    return out


def _mat(cache, prim):
    return np.array(cache.GetLocalToWorldTransform(prim), dtype=np.float64).reshape(4, 4)


def _drawn(prim, time):
    img = UsdGeom.Imageable(prim)
    if not img:
        return True
    return img.ComputeVisibility(time) != UsdGeom.Tokens.invisible and img.ComputePurpose() not in (UsdGeom.Tokens.guide, UsdGeom.Tokens.proxy)


def convert(src, dst, variants=(), tex_dir=None):
    """Every drawn mesh as glTF nodes. Plain meshes are baked into world space. A mesh under an instanceable prim's
    prototype or a PointInstancer prototype is written once, in its prototype's space, and placed by one node per
    instance (step c): smaller and faster than realising copies. An instance whose placement mirrors is baked on its
    own (its winding fixed here, not left to the reader)."""
    stage = open_stage(src, variants)
    time = Usd.TimeCode.Default()
    root = root_matrix(stage)
    cache = UsdGeom.XformCache(time)
    nodes = []                        # (mesh dict, node matrix or None)
    shared = {}                       # (prototype mesh path) -> mesh dict in prototype space

    def place(mesh_prim, local, node):
        """`local`: mesh to prototype space; `node`: prototype to glTF world. Shared unless the node mirrors."""
        if np.linalg.det(node[:3, :3]) < 0:
            m = read_mesh(mesh_prim, local @ node, time)
            if m and m["prims"]:
                nodes.append((m, None))
            return
        k = str(mesh_prim.GetPath())
        if k not in shared:
            shared[k] = read_mesh(mesh_prim, local, time)
        if shared[k] and shared[k]["prims"]:
            nodes.append((shared[k], node))

    def meshes_under(top):
        it = iter(Usd.PrimRange(top, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)))
        for q in it:
            if not _drawn(q, time):
                it.PruneChildren()
                continue
            if q.IsA(UsdGeom.Mesh):
                yield q

    it = iter(Usd.PrimRange(stage.GetPseudoRoot()))
    for prim in it:
        if not _drawn(prim, time):
            it.PruneChildren()
            continue
        if prim.IsInstance():                                   # an instanceable reference: its prototype once
            it.PruneChildren()
            proto = prim.GetPrototype()
            w_inst = _mat(cache, prim)
            for pm in meshes_under(proto):
                proxy = stage.GetPrimAtPath(prim.GetPath().AppendPath(pm.GetPath().MakeRelativePath(proto.GetPath())))
                local = _mat(cache, proxy) @ np.linalg.inv(w_inst)
                place(pm, local, w_inst @ root)
            continue
        if prim.IsA(UsdGeom.PointInstancer):
            it.PruneChildren()                                  # its prototypes draw only where it places them
            pi = UsdGeom.PointInstancer(prim)
            protos = pi.GetPrototypesRel().GetTargets()
            ids = pi.GetProtoIndicesAttr().Get(time) or []
            # every instance's transform (IgnoreMask): the masked ones are skipped below, so ids and transforms stay aligned
            xfs = pi.ComputeInstanceTransformsAtTime(time, time, UsdGeom.PointInstancer.IncludeProtoXform,
                                                     UsdGeom.PointInstancer.IgnoreMask)
            mask = pi.ComputeMaskAtTime(time) or [True] * len(ids)
            w_pi = _mat(cache, prim)
            for i, (pid, xf) in enumerate(zip(ids, xfs)):
                if not mask[i] or pid >= len(protos):
                    continue
                proto = stage.GetPrimAtPath(protos[pid])
                if not proto:
                    continue
                w_proto = _mat(cache, proto)
                node = np.array(xf, dtype=np.float64).reshape(4, 4) @ w_pi @ root
                for pm in meshes_under(proto):
                    place(pm, _mat(cache, pm) @ np.linalg.inv(w_proto), node)
            continue
        if prim.IsA(UsdGeom.Mesh):
            m = read_mesh(prim, _mat(cache, prim) @ root, time)
            if m and m["prims"]:
                nodes.append((m, None))
    info = write_gltf(dst, nodes, tex_dir)
    info["variants"] = variant_sets(stage)
    info["layers"] = sorted({l.realPath for l in stage.GetUsedLayers() if l.realPath})
    return info, stage


def write_gltf(dst, nodes, tex_dir=None, tree=None, anims=None):
    """nodes: (mesh dict, 4x4 row-vector node matrix or None): flat roots. A mesh dict that appears more than once is
    one glTF mesh. tree: hierarchical roots, each {"key", "name", "local" (4x4, column vectors, glTF space),
    "mesh" (a mesh dict or None), "children": [...]}; a mesh dict with "skin_keys" and "ibm" is skinned on those
    nodes. anims: [{"name", "channels": [{"key", "path" (translation|rotation|scale), "times", "values",
    "in", "out"}]}] (in/out tangents present: CUBICSPLINE, else LINEAR)."""
    gl = {"asset": {"version": "2.0", "generator": "godot-gulp (OpenUSD %s)" % ".".join(map(str, Usd.GetVersion()))},
          "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "accessors": [], "bufferViews": [],
          "materials": [], "buffers": []}
    blob = bytearray()
    mats = usdmat.GltfMaterials(gl, os.path.dirname(os.path.abspath(dst)), tex_dir)
    described = {}
    typ = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}

    def view(data, target):
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data.tobytes())
        bv = {"buffer": 0, "byteOffset": off, "byteLength": data.nbytes}
        if target is not None:
            bv["target"] = target
        gl["bufferViews"].append(bv)
        return len(gl["bufferViews"]) - 1

    def accessor(arr, ctype, kind, target, minmax=False):
        a = {"bufferView": view(arr, target), "componentType": ctype, "count": int(len(arr)), "type": kind}
        if minmax:
            a["min"] = [float(x) for x in arr.min(axis=0)]
            a["max"] = [float(x) for x in arr.max(axis=0)]
        gl["accessors"].append(a)
        return len(gl["accessors"]) - 1

    def write_mesh(m):
        """-> (glTF mesh index, every welded position, tris, verts)"""
        gm = {"name": m["name"], "primitives": []}
        allpos, t, v = [], 0, 0
        for p in m["prims"]:
            if p.get("mat") is not None:                 # a material built by another reader (Unity .mat, ...)
                mat = p["mat"]
                described.setdefault(("mat", id(mat)), mat)
            else:
                mp = p.get("material")
                dk = (str(mp.GetPath()) if mp else "", tuple(m["uv_sets"]), tuple(p.get("colour") or ()))
                if dk not in described:
                    described[dk] = usdmat.describe(mp, m["uv_sets"], None if mp else p.get("colour"))
                mat = described[dk]
            a = dict(p["attrs"])
            uvk = "TEXCOORD_%d" % mat.bake_texcoord
            if mat.bake_uv is not None and uvk in a and not np.allclose(mat.bake_uv, np.eye(3)):
                a[uvk] = usdmat.apply_uv(mat.bake_uv, a[uvk])     # the transform baked: every reader, Godot included
            attrs, ind = weld(a)
            prim = {"attributes": {}, "indices": accessor(ind, U32, "SCALAR", ELEMENT_ARRAY_BUFFER), "material": mats.index(mat)}
            for k in sorted(attrs):
                if k.startswith("JOINTS_"):              # joint indices: unsigned short, as glTF requires
                    prim["attributes"][k] = accessor(np.ascontiguousarray(np.round(attrs[k]), dtype=np.uint16), U16, "VEC4", ARRAY_BUFFER)
                else:
                    prim["attributes"][k] = accessor(attrs[k], F32, typ[attrs[k].shape[1]], ARRAY_BUFFER, minmax=(k == "POSITION"))
            gm["primitives"].append(prim)
            allpos.append(attrs["POSITION"])
            t += len(ind) // 3
            v += len(attrs["POSITION"])
        gl["meshes"].append(gm)
        return len(gl["meshes"]) - 1, np.concatenate(allpos).astype(np.float64), t, v

    tris = verts = 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    written = {}
    unique = []
    for m, node in nodes:
        if id(m) not in written:
            written[id(m)] = write_mesh(m)
            unique.append(m)
        gi, pos, t, v = written[id(m)]
        tris, verts = tris + t, verts + v
        n = {"name": m["name"], "mesh": gi}
        world = pos
        if m.get("skin"):                                   # joints placed at their bind-time world, plus the IBMs
            sk = m["skin"]
            first = len(gl["nodes"])
            for jname, J in sk["joints"]:
                gl["nodes"].append({"name": jname, "matrix": [float(x) for x in np.asarray(J).T.reshape(-1)]})
                gl["scenes"][0]["nodes"].append(len(gl["nodes"]) - 1)
            ibm = np.array([np.asarray(B).T.reshape(-1) for B in sk["ibm"]], dtype=np.float32)   # column-major MAT4
            gl.setdefault("skins", []).append({"joints": list(range(first, first + len(sk["joints"]))),
                                               "inverseBindMatrices": accessor(ibm, F32, "MAT4", None)})
            gl["accessors"][-1].pop("min", None)
            n["skin"] = len(gl["skins"]) - 1
        if node is not None:
            n["matrix"] = [float(x) for x in node.reshape(-1)]   # row-vector M flattened by rows is glTF's column-major M^T
            world = (np.c_[pos, np.ones(len(pos))] @ node)[:, :3]
        gl["nodes"].append(n)
        gl["scenes"][0]["nodes"].append(len(gl["nodes"]) - 1)
        if len(world):
            lo, hi = np.minimum(lo, world.min(axis=0)), np.maximum(hi, world.max(axis=0))

    key_idx, pending = {}, []

    def emit(t, parent_world, into):
        nonlocal tris, verts, lo, hi
        L = np.asarray(t.get("local", np.eye(4)), dtype=np.float64)
        n = {"name": t.get("name", "")}
        if not np.allclose(L, np.eye(4)):
            n["matrix"] = [float(x) for x in L.T.reshape(-1)]          # column vectors -> glTF column-major
        idx = len(gl["nodes"])
        gl["nodes"].append(n)
        into.append(idx)
        key_idx[t["key"]] = idx
        world = parent_world @ L
        m = t.get("mesh")
        if m is not None:
            if id(m) not in written:
                written[id(m)] = write_mesh(m)
                unique.append(m)
            gi, pos, tt, vv = written[id(m)]
            tris, verts = tris + tt, verts + vv
            n["mesh"] = gi
            wp = (np.c_[pos, np.ones(len(pos))] @ world.T)[:, :3]
            if len(wp):
                lo, hi = np.minimum(lo, wp.min(axis=0)), np.maximum(hi, wp.max(axis=0))
            if m.get("skin_keys"):
                pending.append((idx, m))
        kids = []
        for c in t.get("children", []):
            emit(c, world, kids)
        if kids:
            n["children"] = kids
    tree_meshes = 0
    for r in tree or []:
        emit(r, np.eye(4), gl["scenes"][0]["nodes"])

    def count(t):
        return (1 if t.get("mesh") is not None else 0) + sum(count(c) for c in t.get("children", []))
    tree_meshes = sum(count(r) for r in tree or [])
    for idx, m in pending:
        joints = [key_idx[k] for k in m["skin_keys"]]
        ibm = np.array([np.asarray(B).T.reshape(-1) for B in m["ibm"]], dtype=np.float32)
        gl.setdefault("skins", []).append({"joints": joints, "inverseBindMatrices": accessor(ibm, F32, "MAT4", None)})
        gl["nodes"][idx]["skin"] = len(gl["skins"]) - 1
    for a in anims or []:
        ga = {"name": a.get("name", "clip"), "samplers": [], "channels": []}
        for ch in a["channels"]:
            if ch["key"] not in key_idx or not len(ch["times"]):
                continue
            t_in = np.asarray(ch["times"], dtype=np.float32)
            vals = np.asarray(ch["values"], dtype=np.float32)
            if ch.get("in") is not None:
                out = np.stack([np.asarray(ch["in"], dtype=np.float32), vals, np.asarray(ch["out"], dtype=np.float32)], axis=1)
                out, interp = out.reshape(-1, vals.shape[1]), "CUBICSPLINE"
            else:
                out, interp = vals, "LINEAR"
            ia = accessor(t_in.reshape(-1, 1)[:, 0], F32, "SCALAR", None)
            gl["accessors"][ia]["min"], gl["accessors"][ia]["max"] = [float(t_in.min())], [float(t_in.max())]
            oa = accessor(out, F32, {3: "VEC3", 4: "VEC4"}[vals.shape[1]], None)
            ga["samplers"].append({"input": ia, "output": oa, "interpolation": interp})
            ga["channels"].append({"sampler": len(ga["samplers"]) - 1, "target": {"node": key_idx[ch["key"]], "path": ch["path"]}})
        if ga["channels"]:
            gl.setdefault("animations", []).append(ga)

    binname = os.path.splitext(os.path.basename(dst))[0] + ".bin"
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(os.path.join(os.path.dirname(os.path.abspath(dst)), binname), "wb") as f:
        f.write(blob)
    gl["buffers"].append({"uri": binname, "byteLength": len(blob)})
    for k in ("images", "textures", "samplers"):
        if not gl.get(k):
            gl.pop(k, None)
    with open(dst, "w") as f:
        json.dump(gl, f, separators=(",", ":"))
    size = (hi - lo).tolist() if (nodes or tree_meshes) else [0, 0, 0]
    notes = sorted({"%s: %s" % (d.name, n) for d in described.values() for n in d.notes})
    return {"meshes": len(nodes) + tree_meshes, "unique_meshes": len(unique), "tris": int(tris), "verts": int(verts),
            "animations": len(gl.get("animations", [])),
            "size": [size[0], size[2], size[1]],                 # (width, depth, height): the report's order
            "uv_sets": sorted({n for m in unique for n in m["uv_sets"]}), "subsets": sum(m["subsets"] for m in unique),
            "materials": len(gl["materials"]), "textures": len(gl.get("images", [])), "material_notes": notes,
            "material_names": [m["name"] for m in gl["materials"]], "texture_sources": sorted(mats.sources)}
