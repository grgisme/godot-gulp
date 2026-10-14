"""COLLADA (.dae, 1.4 and 1.5) to glTF without Blender: read here with the standard library's XML parser.

    convert(src, dst, tex_dir=None) -> info

- the visual scene's node tree, each node's transform stack (matrix, translate, rotate, scale, in order), instanced
  library nodes; up_axis (Y, Z or X) and <unit> converted into glTF's space (Y up, metres) on nodes and vertices alike
- meshes: <triangles>, <polylist> and <polygons> (fanned), any input offsets, VERTEX/POSITION, NORMAL, the first
  TEXCOORD set (V flipped), COLOR; one primitive per bound material
- materials: profile_COMMON phong/blinn/lambert/constant: diffuse colour or texture (sampler -> surface -> image),
  emission, transparency, a bump/normal map from the common <extra> techniques; textures found beside the file when the
  stored path does not exist, and loose maps by material name like the FBX and USD readers
- skins: <controller><skin> (bind shape matrix, joints by sid/id/name, inverse bind matrices, vertex weights: the four
  strongest, normalised) as a glTF skin
- animations: every channel (whole-matrix targets and translate/rotate/scale members) re-sampled at the union of its
  node's key times and written as translation/rotation/scale (LINEAR)
"""
import math, os, re, urllib.parse
import xml.etree.ElementTree as ET

import numpy as np

from . import convert as gltf_out, materials as mt


def _strip(root):
    for e in root.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return root


def _floats(e):
    return np.array((e.text or "").split(), dtype=np.float64) if e is not None else np.zeros(0)


def _ints(e):
    return np.array((e.text or "").split(), dtype=np.int64) if e is not None else np.zeros(0, np.int64)


def _url(s):
    return (s or "").lstrip("#")


def _rot(axis, deg):
    x, y, z = np.asarray(axis, dtype=np.float64) / (np.linalg.norm(axis) or 1.0)
    a = math.radians(deg)
    c, s, t = math.cos(a), math.sin(a), 1 - math.cos(a)
    R = np.eye(4)
    R[:3, :3] = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                 [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                 [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
    return R


def _element_matrix(kind, v):
    M = np.eye(4)
    if kind == "matrix":
        return np.asarray(v, dtype=np.float64).reshape(4, 4)          # row-major, column vectors
    if kind == "translate":
        M[:3, 3] = v[:3]
    elif kind == "scale":
        M = np.diag(list(v[:3]) + [1.0])
    elif kind == "rotate":
        M = _rot(v[:3], v[3])
    elif kind == "lookat":
        eye, at, up = np.array(v[:3]), np.array(v[3:6]), np.array(v[6:9])
        f = at - eye
        f /= np.linalg.norm(f) or 1.0
        s = np.cross(f, up)
        s /= np.linalg.norm(s) or 1.0
        u = np.cross(s, f)
        M[:3, :3] = np.stack([s, u, -f], axis=1)
        M[:3, 3] = eye
    return M


def decompose(M):
    """4x4 (column vectors) -> translation, rotation quaternion (x, y, z, w), scale."""
    t = M[:3, 3].copy()
    A = M[:3, :3]
    sc = np.linalg.norm(A, axis=0)
    if np.linalg.det(A) < 0:
        sc[0] = -sc[0]
    R = A / np.where(np.abs(sc) < 1e-12, 1.0, sc)
    tr = np.trace(R)
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = [(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s]
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s]
    q = np.array(q)
    return t, q / (np.linalg.norm(q) or 1.0), sc


class Doc:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.base = os.path.dirname(self.path)
        self.root = _strip(ET.parse(path).getroot())
        self.ids = {e.get("id"): e for e in self.root.iter() if e.get("id")}
        asset = self.root.find("asset")
        up = (asset.findtext("up_axis") if asset is not None else None) or "Y_UP"
        unit = asset.find("unit") if asset is not None else None
        u = float(unit.get("meter", "1")) if unit is not None else 1.0
        C = {"Y_UP": np.eye(3), "Z_UP": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0.0]]),
             "X_UP": np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.0]])}.get(up.strip().upper(), np.eye(3))
        self.A = np.eye(4)
        self.A[:3, :3] = C * u                               # into glTF space: rotate the up axis, scale to metres
        self.A_inv = np.linalg.inv(self.A)
        self.C = C
        self.notes = []

    def conj(self, M):
        return self.A @ M @ self.A_inv

    # ---- sources

    def source(self, sid):
        """A <source> -> (n, stride) float array (or names for Name_array/IDREF_array)."""
        s = self.ids.get(_url(sid))
        if s is None:
            raise ValueError("source %s not found" % sid)
        if s.tag == "vertices":
            pos = next((i for i in s.findall("input") if i.get("semantic") == "POSITION"), None)
            return self.source(pos.get("source"))
        acc = s.find("technique_common/accessor")
        stride = int(acc.get("stride", "1")) if acc is not None else 1
        for tag in ("Name_array", "IDREF_array", "SIDREF_array"):
            na = s.find(tag)
            if na is not None:
                return (na.text or "").split()
        fa = s.find("float_array")
        if fa is None:
            fa = s.find("int_array")
        a = _floats(fa)
        return a.reshape(-1, stride) if stride > 1 else a

    # ---- materials

    def image_path(self, image_id):
        img = self.ids.get(image_id)
        if img is None:
            return ""
        init = img.find("init_from")
        txt = (init.findtext("ref") if init is not None and init.find("ref") is not None else (init.text if init is not None else "")) or ""
        txt = urllib.parse.unquote(txt.strip())
        if txt.startswith("file://"):
            txt = txt[7:].lstrip("/") if re.match(r"^file:///[A-Za-z]:", txt) else txt[7:]
        cands = [txt, os.path.join(self.base, txt), os.path.join(self.base, os.path.basename(txt.replace("\\", "/"))),
                 os.path.join(self.base, "textures", os.path.basename(txt.replace("\\", "/")))]
        return next((p for p in cands if p and os.path.isfile(p)), "")

    def texture(self, effect, tex_el):
        """<texture texture="sampler sid"> -> image file path (sampler2D -> surface -> image, or the image id itself)."""
        ref = tex_el.get("texture", "")
        params = {p.get("sid"): p for p in effect.iter("newparam")}
        img = ref
        p = params.get(ref)
        if p is not None and p.find("sampler2D") is not None:
            src = p.find("sampler2D/source")
            inst = p.find("sampler2D/instance_image")
            if inst is not None:
                img = _url(inst.get("url"))
            elif src is not None:
                surf = params.get((src.text or "").strip())
                if surf is not None and surf.find("surface/init_from") is not None:
                    img = (surf.find("surface/init_from").text or "").strip()
        return self.image_path(img)

    def material(self, mat_id, cache):
        if mat_id in cache:
            return cache[mat_id]
        me = self.ids.get(mat_id)
        name = (me.get("name") or mat_id) if me is not None else (mat_id or "material")
        m = mt.Mat(name)
        T = lambda path, ch: mt.Tex(path, ch, 0, np.eye(3), 10497, 10497, (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0),
                                    "auto", "dae:" + os.path.basename(path))
        eff = self.ids.get(_url(me.find("instance_effect").get("url"))) if me is not None and me.find("instance_effect") is not None else None
        tech = None
        if eff is not None:
            tc = eff.find("profile_COMMON/technique")
            if tc is not None:
                tech = next((t for t in tc if t.tag in ("phong", "blinn", "lambert", "constant")), None)
        if tech is not None:
            d = tech.find("diffuse")
            if d is not None and d.find("color") is not None:
                c = _floats(d.find("color"))
                m.base = [float(c[0]), float(c[1]), float(c[2]), 1.0]
            elif d is not None and d.find("texture") is not None:
                p = self.texture(eff, d.find("texture"))
                if p:
                    m.tex["diffuse"] = T(p, "rgb")
                else:
                    m.notes.append("diffuse texture %s not found" % d.find("texture").get("texture"))
            e = tech.find("emission/color")
            if e is not None:
                m.emissive = [float(x) for x in _floats(e)[:3]]
            tr = tech.find("transparency/float")
            if tr is not None and tech.find("transparent") is not None:
                a = float(_floats(tr)[0]) if len(_floats(tr)) else 1.0
                mode = tech.find("transparent").get("opaque", "A_ONE")
                op = a if mode in ("A_ONE", "RGB_ONE") else 1.0 - a
                if op < 0.999:
                    m.opacity = max(0.0, min(1.0, op))
            sh = tech.find("shininess/float")
            if sh is not None and tech.tag in ("phong", "blinn"):
                s = float(_floats(sh)[0]) if len(_floats(sh)) else 0.0
                m.roughness = float(np.clip(math.sqrt(2.0 / (max(s, 0.0) + 2.0)), 0.05, 1.0)) if s > 0 else 0.9
            else:
                m.roughness = 0.9
            bump = next((b for b in eff.iter("bump")), None)
            if bump is not None and bump.find("texture") is not None:
                p = self.texture(eff, bump.find("texture"))
                if p:
                    m.tex["normal"] = T(p, "rgb")
        if not m.tex.get("diffuse") and mt.RESOLVER:
            for slot in ("diffuse", "normal", "roughness", "metallic", "occlusion"):
                if slot in m.tex:
                    continue
                p = mt.RESOLVER(m.name, slot, "")
                if p:
                    m.notes.append("%s: resolved by name: %s" % (slot, os.path.basename(p)))
                    m.tex[slot] = T(p, "r" if slot in ("roughness", "metallic", "occlusion") else "rgb")
                    if slot in ("roughness", "metallic"):
                        setattr(m, slot, 1.0)
        for t in m.tex.values():
            t.rel = None
        cache[mat_id] = m
        return m

    # ---- geometry

    def geometry(self, geom_id, bind, mats, bake=None, skin=None):
        """A <geometry> -> mesh dict (positions in glTF space; bake: a 4x4 applied first, e.g. the bind shape)."""
        g = self.ids.get(geom_id)
        mesh = g.find("mesh") if g is not None else None
        if mesh is None:
            return None
        vert_el = mesh.find("vertices")
        vert_inputs = {i.get("semantic"): i.get("source") for i in vert_el.findall("input")} if vert_el is not None else {}
        prims = []
        for prim in mesh:
            if prim.tag not in ("triangles", "polylist", "polygons"):
                continue
            inputs = prim.findall("input")
            if not inputs:
                continue
            stride = max(int(i.get("offset", "0")) for i in inputs) + 1
            if prim.tag == "polygons":
                polys = [_ints(p) for p in prim.findall("p")]
                vc = np.array([len(p) // stride for p in polys], dtype=np.int64)
                idx = np.concatenate(polys) if polys else np.zeros(0, np.int64)
            else:
                idx = _ints(prim.find("p"))
                n_corner = len(idx) // stride
                vc = _ints(prim.find("vcount")) if prim.tag == "polylist" else np.full(n_corner // 3, 3, np.int64)
            idx = idx.reshape(-1, stride)
            # fan each polygon into triangles: corner indices into idx
            starts = np.concatenate([[0], np.cumsum(vc)[:-1]]) if len(vc) else np.zeros(0, np.int64)
            tri = [(s, s + k, s + k + 1) for s, n in zip(starts, vc) for k in range(1, n - 1)]
            if not tri:
                continue
            corners = np.array(tri, dtype=np.int64).reshape(-1)
            attrs = {}
            vid = None
            for inp in inputs:
                sem, off = inp.get("semantic"), int(inp.get("offset", "0"))
                ii = idx[corners, off]
                if sem == "VERTEX":
                    vid = ii
                    P = self.source(vert_inputs.get("POSITION"))
                    attrs["POSITION"] = P[ii]
                    if "NORMAL" in vert_inputs:
                        attrs["NORMAL"] = self.source(vert_inputs["NORMAL"])[ii]
                    if "TEXCOORD" in vert_inputs:
                        attrs["TEXCOORD_0"] = self.source(vert_inputs["TEXCOORD"])[ii][:, :2]
                elif sem == "NORMAL" and "NORMAL" not in attrs:
                    attrs["NORMAL"] = self.source(inp.get("source"))[ii]
                elif sem == "TEXCOORD" and "TEXCOORD_0" not in attrs and inp.get("set", "0") in ("0", "1") :
                    attrs["TEXCOORD_0"] = self.source(inp.get("source"))[ii][:, :2]
                elif sem == "COLOR" and "COLOR_0" not in attrs:
                    c = self.source(inp.get("source"))[ii]
                    attrs["COLOR_0"] = np.c_[c[:, :3], c[:, 3] if c.shape[1] > 3 else np.ones(len(c))]
            if "POSITION" not in attrs:
                continue
            P = np.c_[attrs["POSITION"][:, :3], np.ones(len(attrs["POSITION"]))]
            M = self.A @ (bake if bake is not None else np.eye(4))
            attrs["POSITION"] = (P @ M.T)[:, :3].astype(np.float32)
            if "NORMAL" in attrs:
                N = attrs["NORMAL"][:, :3] @ np.linalg.inv(M[:3, :3])
                attrs["NORMAL"] = (N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)).astype(np.float32)
                if np.linalg.det(M[:3, :3]) < 0:
                    attrs["POSITION"] = attrs["POSITION"].reshape(-1, 3, 3)[:, ::-1].reshape(-1, 3)
                    attrs["NORMAL"] = attrs["NORMAL"].reshape(-1, 3, 3)[:, ::-1].reshape(-1, 3)
            if "TEXCOORD_0" in attrs:
                uv = attrs["TEXCOORD_0"].copy()
                uv[:, 1] = 1.0 - uv[:, 1]
                attrs["TEXCOORD_0"] = uv.astype(np.float32)
            if "COLOR_0" in attrs:
                if np.allclose(attrs["COLOR_0"], 1.0):
                    del attrs["COLOR_0"]
                else:
                    attrs["COLOR_0"] = np.clip(attrs["COLOR_0"], 0, 1).astype(np.float32)
            if skin is not None and vid is not None:
                attrs["JOINTS_0"], attrs["WEIGHTS_0"] = skin[0][vid], skin[1][vid]
            sym = prim.get("material", "")
            mat = self.material(bind.get(sym, sym), mats)
            prims.append({"path": sym, "attrs": attrs, "mat": mat})
        if not prims:
            return None
        return {"name": g.get("name") or geom_id, "path": geom_id, "uv_sets": ["st"], "subsets": 0, "prims": prims}


def _bind(inst):
    return {im.get("symbol"): _url(im.get("target")) for im in inst.iter("instance_material")}


def _weights(doc, skin_el, n_joints):
    """<vertex_weights> -> per-position-index (JOINTS_0, WEIGHTS_0): the four strongest, normalised."""
    vw = skin_el.find("vertex_weights")
    count = int(vw.get("count", "0"))
    inputs = vw.findall("input")
    stride = max(int(i.get("offset", "0")) for i in inputs) + 1
    jo = next(int(i.get("offset", "0")) for i in inputs if i.get("semantic") == "JOINT")
    wi = next(i for i in inputs if i.get("semantic") == "WEIGHT")
    wo, wsrc = int(wi.get("offset", "0")), np.asarray(doc.source(wi.get("source"))).reshape(-1)
    vc = _ints(vw.find("vcount"))
    v = _ints(vw.find("v")).reshape(-1, stride)
    J = np.zeros((count, 4), np.float32)
    W = np.zeros((count, 4), np.float32)
    k = 0
    for i, n in enumerate(vc):
        pairs = sorted(((wsrc[v[k + j, wo]], v[k + j, jo]) for j in range(n)), reverse=True)[:4]
        k += n
        pairs = [(w, j) for w, j in pairs if j >= 0 and w > 0]
        tot = sum(w for w, _ in pairs) or 1.0
        for c, (w, j) in enumerate(pairs):
            J[i, c], W[i, c] = j, w / tot
    return J, W


def convert(src, dst, tex_dir=None):
    doc = Doc(src)
    mats = {}
    scene = doc.root.find("scene/instance_visual_scene")
    vs = doc.ids.get(_url(scene.get("url"))) if scene is not None else doc.root.find("library_visual_scenes/visual_scene")
    if vs is None:
        raise ValueError("no visual scene")
    key_of = {}                # node element -> tree key
    by_sid, by_id, by_name = {}, {}, {}
    trees, skins, anim_nodes = [], [], {}
    counter = [0]

    def node(e, parent_key=None):
        k = counter[0]
        counter[0] += 1
        key_of[id(e)] = k
        for d, a in ((by_sid, e.get("sid")), (by_id, e.get("id")), (by_name, e.get("name"))):
            if a:
                d.setdefault(a, k)
        stack = [(c.get("sid"), c.tag, _floats(c)) for c in e if c.tag in ("matrix", "translate", "rotate", "scale", "lookat")]
        L = np.eye(4)
        for _sid, kind, v in stack:
            L = L @ _element_matrix(kind, v)
        t = {"key": k, "name": e.get("name") or e.get("id") or "node", "local": doc.conj(L), "children": [], "stack": stack,
             "elem_id": e.get("id")}
        for inst in e.findall("instance_geometry"):
            m = doc.geometry(_url(inst.get("url")), _bind(inst), mats)
            if m is not None:
                t["children"].append({"key": "g%d_%d" % (k, len(t["children"])), "name": m["name"], "local": np.eye(4),
                                      "mesh": m, "children": []})
        for inst in e.findall("instance_controller"):
            skins.append((inst, k))
        for c in e.findall("node"):
            t["children"].append(node(c, k))
        for inst in e.findall("instance_node"):
            ln = doc.ids.get(_url(inst.get("url")))
            if ln is not None:
                t["children"].append(node(ln, k))
        if e.get("id"):
            anim_nodes[e.get("id")] = t
        return t

    for e in vs.findall("node"):
        trees.append(node(e))
    # skins: the mesh is placed at the scene root (glTF ignores a skinned mesh node's transform)
    for inst, host in skins:
        ctrl = doc.ids.get(_url(inst.get("url")))
        sk = ctrl.find("skin") if ctrl is not None else None
        if sk is None:
            doc.notes.append("controller %s is not a skin (morph?): left out" % inst.get("url"))
            continue
        bsm = _floats(sk.find("bind_shape_matrix")).reshape(4, 4) if sk.find("bind_shape_matrix") is not None else np.eye(4)
        jin = {i.get("semantic"): i.get("source") for i in sk.findall("joints/input")}
        names = doc.source(jin["JOINT"])
        ibms = np.asarray(doc.source(jin["INV_BIND_MATRIX"])).reshape(-1, 4, 4)
        keys = []
        for nm in names:
            k = by_sid.get(nm, by_id.get(nm, by_name.get(nm)))
            if k is None:
                doc.notes.append("joint %s not found in the scene" % nm)
                k = host
            keys.append(k)
        J, W = _weights(doc, sk, len(names))
        m = doc.geometry(_url(sk.get("source")), _bind(inst), mats, bake=bsm, skin=(J, W))
        if m is None:
            continue
        m["skin_keys"] = keys
        m["ibm"] = [doc.conj(B) for B in ibms]
        trees.append({"key": "skin%d" % len(skins), "name": m["name"], "local": np.eye(4), "mesh": m, "children": []})
    anims = _animations(doc, anim_nodes)
    for t in _walk(trees):
        t.pop("stack", None)
        t.pop("elem_id", None)
    info = gltf_out.write_gltf(dst, [], tex_dir, trees, anims)
    info.update(variants={}, layers=[os.path.abspath(src)])
    info["material_notes"] = doc.notes + info["material_notes"]
    return info


def _walk(trees):
    for t in trees:
        yield t
        yield from _walk(t["children"])


def _animations(doc, anim_nodes):
    """Every channel re-sampled per node at the union of its key times -> one glTF animation (TRS, LINEAR)."""
    by_node = {}
    for ch in doc.root.iter("channel"):
        target = ch.get("target", "")
        nid, _, rest = target.partition("/")
        if nid not in anim_nodes:
            continue
        smp = doc.ids.get(_url(ch.get("source")))
        if smp is None:
            continue
        ins = {i.get("semantic"): i.get("source") for i in smp.findall("input")}
        t = np.asarray(doc.source(ins["INPUT"])).reshape(-1)
        v = np.asarray(doc.source(ins["OUTPUT"]))
        sid, member = re.match(r"([^.(]*)(.*)", rest).groups()
        by_node.setdefault(nid, []).append((sid, member, t, v.reshape(len(t), -1)))
    chans = []
    for nid, lst in by_node.items():
        node = anim_nodes[nid]
        times = np.unique(np.concatenate([c[2] for c in lst]))
        T, R, S = [], [], []
        for tm in times:
            stack = [(sid, kind, np.array(vals, dtype=np.float64)) for sid, kind, vals in node["stack"]]
            for sid, member, kt, kv in lst:
                val = np.array([np.interp(tm, kt, kv[:, j]) for j in range(kv.shape[1])])
                for k2, (esid, kind, vals) in enumerate(stack):
                    if esid != sid:
                        continue
                    if not member:
                        vals[:len(val)] = val
                    elif member.startswith("("):
                        ij = [int(x) for x in re.findall(r"\d+", member)]
                        vals[ij[0] * 4 + ij[1] if len(ij) == 2 else ij[0]] = val[0]
                    else:
                        m = member.lstrip(".").upper()
                        pos = {"X": 0, "Y": 1, "Z": 2, "ANGLE": 3, "W": 3}.get(m)
                        if pos is not None:
                            vals[pos] = val[0]
            L = np.eye(4)
            for _sid, kind, vals in stack:
                L = L @ _element_matrix(kind, vals)
            tt, q, s = decompose(doc.conj(L))
            if R and np.dot(R[-1], q) < 0:                 # keep the quaternion on one hemisphere
                q = -q
            T.append(tt)
            R.append(q)
            S.append(s)
        t0 = float(times[0]) if len(times) else 0.0
        for path, vals in (("translation", T), ("rotation", R), ("scale", S)):
            vals = np.asarray(vals)
            if len(vals) > 1 and np.ptp(vals, axis=0).max() > 1e-7:
                chans.append({"key": node["key"], "path": path, "times": list(times - t0), "values": vals.tolist()})
    return [{"name": os.path.splitext(os.path.basename(doc.path))[0], "channels": chans}] if chans else []
