"""FBX (and OBJ) to glTF without Blender: ufbx (vendored C, MIT or public domain) loads the file into glTF's space
(right-handed, Y up, metres; the geometry adjusted, not a root fix-up) and godot_gulp/_ufbxdump.c hands the whole
scene over as plain data in one call (the ufbx Python binding 0.0.x corrupts memory: a node's children list read
before the scene's node list segfaults).

    convert(src, dst, tex_dir=None) -> info ;  scene(src) -> the dumped scene (dict)
    MODEL_EXTS: the files it reads (FBX binary and ASCII, OBJ with its .mtl)

- the node hierarchy with each node's local transform; a mesh's geometric transform baked into its vertices
- meshes: one primitive per material part, polygons triangulated by ufbx, normals (generated when missing), the first
  UV set (V flipped), vertex colour
- materials: ufbx's PBR view (base colour and factor, roughness, metalness, normal map, emission and factor,
  opacity, occlusion), texture files resolved next to the FBX when the stored path does not exist
- skins: each skin cluster a joint (its bone node), geometry_to_bone the inverse bind matrix, per-vertex weights
  (strongest four, normalised) as WEIGHTS_0/JOINTS_0
- animation: every animation stack baked by ufbx into translation/rotation/scale keys (LINEAR), times from 0
"""
import json, os

import numpy as np

from . import convert as gltf_out, materials as mt

MODEL_EXTS = (".fbx", ".obj")
ROLE_SLOTS = ("diffuse", "normal", "roughness", "metallic", "occlusion")      # the ones resolve_by_name knows


def scene(src):
    try:
        from . import _ufbxdump
    except ImportError:
        raise RuntimeError("the FBX reader is not built: reinstall godot-gulp with a C compiler available")
    return json.loads(_ufbxdump.dump(os.path.abspath(src)))


def _find(names, base):
    """A texture file: as stored, else beside the FBX or in its textures/ (exporters store other machines' paths)."""
    for name in [n for n in names if n]:
        for p in (name, os.path.join(base, name), os.path.join(base, os.path.basename(name.replace("\\", "/"))),
                  os.path.join(base, "textures", os.path.basename(name.replace("\\", "/")))):
            if os.path.isfile(p):
                return p
    return ""


def material(md, base):
    m = mt.Mat(md.get("name") or "material")
    T = lambda path, ch: mt.Tex(path, ch, 0, np.eye(3), 10497, 10497, (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0), "auto",
                                "fbx:" + os.path.basename(path))
    f = (md.get("base_factor") or [1.0])[0]
    if md.get("base"):
        m.base = [md["base"][0] * f, md["base"][1] * f, md["base"][2] * f, 1.0]
    if md.get("roughness"):
        m.roughness = float(md["roughness"][0])
    if md.get("metalness"):
        m.metallic = float(md["metalness"][0])
    if md.get("emission"):
        ef = (md.get("emission_factor") or [1.0])[0]
        m.emissive = [md["emission"][i] * ef for i in range(3)]
    if md.get("opacity") and md["opacity"][0] < 0.999:
        m.opacity = float(md["opacity"][0])
    for slot, key, ch in (("diffuse", "base_tex", "rgb"), ("roughness", "roughness_tex", "r"), ("metallic", "metalness_tex", "r"),
                          ("normal", "normal_tex", "rgb"), ("emissive", "emission_tex", "rgb"), ("opacity", "opacity_tex", "a"),
                          ("occlusion", "ao_tex", "r")):
        names = md.get(key)
        if not names:
            # nothing wired (packs often ship the maps loose): the pack's resolver, by material name (engine.job sets it)
            p = mt.RESOLVER(m.name, slot, "") if mt.RESOLVER and slot in ROLE_SLOTS else ""
            if p:
                m.notes.append("%s: resolved by name: %s" % (slot, os.path.basename(p)))
                m.tex[slot] = T(p, ch)
                if slot in ("roughness", "metallic"):
                    setattr(m, slot, 1.0)
            continue
        p = _find(names, base)
        if not p:
            m.notes.append("%s texture %s not found" % (slot, names[1] or names[0]))
            continue
        m.tex[slot] = T(p, ch)
        if slot == "diffuse" and md.get("base") and not all(abs(x - 0.8) < 1e-6 for x in md["base"][:3]):
            m.tex[slot].scale = (m.base[0], m.base[1], m.base[2], 1.0)      # 0.8 grey is FBX's untouched default
        if slot in ("roughness", "metallic"):
            setattr(m, slot, 1.0)
    for t in m.tex.values():
        t.rel = None
    return m


def mesh_parts(md, G, skinned):
    """A dumped mesh as [(material index, attrs)]; G (column vectors) baked unless skinned."""
    out = []
    W = J = None
    if skinned and md.get("skin"):
        ws = md["skin"]["weights"]
        W, J = np.zeros((len(ws), 4)), np.zeros((len(ws), 4))
        for v, flat in enumerate(ws):
            pairs = sorted(((flat[i + 1], flat[i]) for i in range(0, len(flat), 2)), reverse=True)[:4]
            tot = sum(w for w, _c in pairs) or 1.0
            for k, (w, c) in enumerate(pairs):
                W[v, k], J[v, k] = w / tot, c
    for part in md["parts"]:
        P = np.array(part["pos"], dtype=np.float64).reshape(-1, 3)
        if not len(P):
            continue
        P = (np.c_[P, np.ones(len(P))] @ G.T)[:, :3]
        a = {"POSITION": P.astype(np.float32)}
        if part["nrm"]:
            N = np.array(part["nrm"]).reshape(-1, 3) @ np.linalg.inv(G[:3, :3])
            a["NORMAL"] = (N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)).astype(np.float32)
        if part["uv"]:
            uv = np.array(part["uv"]).reshape(-1, 2)
            uv[:, 1] = 1.0 - uv[:, 1]
            a["TEXCOORD_0"] = uv.astype(np.float32)
        if part["col"]:
            a["COLOR_0"] = np.array(part["col"]).reshape(-1, 4).astype(np.float32)
        if W is not None:
            vert = np.array(part["vert"], dtype=np.int64)
            a["WEIGHTS_0"], a["JOINTS_0"] = W[vert].astype(np.float32), J[vert].astype(np.float32)
        out.append((part["material"], a))
    return out


def convert(src, dst, tex_dir=None):
    sc = scene(src)
    base = os.path.dirname(os.path.abspath(src))
    mats = {i: material(m, base) for i, m in enumerate(sc["materials"])}
    default = mt.Mat("default")
    kids = {}
    for i, n in enumerate(sc["nodes"]):
        kids.setdefault(n["parent"], []).append(i)

    def tnode(i):
        n = sc["nodes"][i]
        t = {"key": i, "name": n["name"] or "node", "local": np.array(n["local"]).reshape(4, 4), "children": []}
        if n["mesh"] >= 0:
            md = sc["meshes"][n["mesh"]]
            skinned = bool(md.get("skin"))
            parts = mesh_parts(md, np.eye(4) if skinned else np.array(n["geometry"]).reshape(4, 4), skinned)
            if parts:
                m = {"name": n["name"] or "mesh", "path": n["name"] or "mesh", "uv_sets": ["st"], "subsets": 0,
                     "prims": [{"path": n["name"], "attrs": a, "mat": mats.get(mi, default)} for mi, a in parts]}
                if skinned:
                    cl = md["skin"]["clusters"]
                    m["skin_keys"] = [c["bone"] if c["bone"] >= 0 else i for c in cl]
                    m["ibm"] = [np.array(c["geometry_to_bone"]).reshape(4, 4) for c in cl]
                t["mesh"] = m
        t["children"] = [tnode(c) for c in kids.get(i, [])]
        return t
    roots = [i for i, n in enumerate(sc["nodes"]) if n["root"]]
    tree = [tnode(c) for r in roots for c in kids.get(r, [])]
    anims = []
    for a in sc["anims"]:
        t0 = float(a.get("begin", 0.0))
        chans = []
        for bn in a["nodes"]:
            for key, path in (("t", "translation"), ("r", "rotation"), ("s", "scale")):
                ks = bn[key]
                if len(ks) > 1 and any(k[1:] != ks[0][1:] for k in ks):     # ufbx bakes untouched channels too
                    chans.append({"key": bn["node"], "path": path, "times": [k[0] - t0 for k in ks], "values": [k[1:] for k in ks]})
        if chans:
            anims.append({"name": a["name"] or "take", "channels": chans})
    info = gltf_out.write_gltf(dst, [], tex_dir, tree, anims)
    info.update(variants={}, layers=[os.path.abspath(src)])
    return info
