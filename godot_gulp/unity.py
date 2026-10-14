"""Unity to glTF, headless: a .unitypackage or a project folder (Assets/ with .meta files) in, one .gltf per prefab,
scene and model out. No Unity, no Godot editor.

Read from the files Unity writes:
- .unitypackage: a gzip tar of <guid>/asset, <guid>/asset.meta, <guid>/pathname (first line: the asset path)
- Unity YAML (.prefab, .unity, .mat, .asset): documents headed `--- !u!<classID> &<fileID>` (`stripped` for objects
  that live in another prefab); bodies are plain YAML
- GameObject (1), Transform (4), MeshFilter (33), MeshRenderer (23), PrefabInstance (1001) with m_Modifications
  (position, rotation, scale, name, materials), stripped objects followed through m_CorrespondingSourceObject
- materials: Standard, URP Lit and HDRP Lit property names (see material())
- meshes: OBJ (read here) and FBX (the vendored ufbx), with the model .meta's globalScale/useFileScale and
  internalIDToNameTable (fileID -> mesh name)

Coordinates: Unity is left-handed (X right, Y up, Z forward); glTF is right-handed. Positions convert as (-x, y, z),
rotations as (x, -y, -z, w), scale unchanged (the mirror conjugation every converter in our survey uses:
docs/research/unity-importers.md). Meshes are read straight into glTF's space, so they need no mirror; a placement
that mirrors (a negative scale) is baked into its own copy with its winding fixed.
"""
import io, math, os, re, tarfile, tempfile

import numpy as np
import yaml

from . import convert, materials as mt

HEADER = re.compile(r"^--- !u!(\d+) &(-?\d+)( stripped)?[^\n]*$", re.M)
HEX_KEYS = re.compile(r"^(\s*(?:_typelessdata|m_IndexBuffer|m_Heights)): ?([0-9a-fA-F]*)\s*$", re.M)
S = np.diag([-1.0, 1.0, 1.0, 1.0])          # Unity <-> glTF: the X mirror
STANDARD, URP, HDRP = "standard", "urp", "hdrp"


# ---- Unity YAML

def parse_docs(text):
    """{fileID: (classID, stripped, type name, body)} for one Unity YAML file."""
    out = {}
    heads = list(HEADER.finditer(text))
    for i, h in enumerate(heads):
        body = text[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        body = HEX_KEYS.sub(r'\1: "\2"', body)            # Unity writes hex blobs bare: YAML would read some as numbers
        data = yaml.safe_load(body) or {}
        (kind, value), = data.items() if isinstance(data, dict) and len(data) == 1 else (("", data),)
        out[int(h.group(2))] = (int(h.group(1)), bool(h.group(3)), kind, value or {})
    return out


def _ref(v):
    """{fileID, guid} -> (fileID, guid or "") ; None for an empty reference."""
    if not isinstance(v, dict) or not v.get("fileID"):
        return None
    return int(v["fileID"]), str(v.get("guid") or "")


def _plist(v):
    """Unity's serialized property lists ([{name: value}, ...] or {name: value}) as one dict."""
    if isinstance(v, dict):
        return v
    out = {}
    for item in v or []:
        if isinstance(item, dict):
            out.update(item)
    return out


# ---- the asset database

class Assets:
    """guid -> file path and its .meta, from a .unitypackage (extracted to a temp folder) or a project folder."""

    def __init__(self, src):
        self.by_guid, self.meta, self.root = {}, {}, src
        if os.path.isfile(src):
            self.root = tempfile.mkdtemp(prefix="godot-gulp-unity-")
            self._from_package(src)
        else:
            self._from_folder(src)
        self._docs = {}

    def _from_package(self, pkg):
        entries = {}
        with tarfile.open(pkg, "r:*") as tf:
            for m in tf.getmembers():
                parts = m.name.strip("./").split("/")
                if len(parts) == 2 and m.isfile():
                    entries.setdefault(parts[0], {})[parts[1]] = tf.extractfile(m).read()
        for guid, e in entries.items():
            if "pathname" not in e:
                continue
            rel = e["pathname"].decode("utf-8", "replace").splitlines()[0].strip()
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                continue                                    # never write outside the extract folder
            if "asset.meta" in e:
                self.meta[guid] = yaml.safe_load(e["asset.meta"].decode("utf-8", "replace")) or {}
            if "asset" in e:
                path = os.path.join(self.root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(e["asset"])
                self.by_guid[guid] = path

    def _from_folder(self, root):
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if f.endswith(".meta"):
                    try:
                        meta = yaml.safe_load(open(os.path.join(dp, f), encoding="utf-8", errors="replace")) or {}
                    except yaml.YAMLError:
                        continue
                    guid = str(meta.get("guid", ""))
                    path = os.path.join(dp, f[:-5])
                    if guid and os.path.exists(path):
                        self.by_guid[guid], self.meta[guid] = path, meta

    def path(self, guid):
        return self.by_guid.get(guid, "")

    def docs(self, guid):
        if guid not in self._docs:
            p = self.path(guid)
            self._docs[guid] = parse_docs(open(p, encoding="utf-8", errors="replace").read()) if p else {}
        return self._docs[guid]

    def of_kind(self, exts):
        return sorted((g, p) for g, p in self.by_guid.items() if p.lower().endswith(exts))


# ---- materials

def srgb_to_linear(c):
    c = max(0.0, float(c))
    if c > 1.0:
        return c ** 2.2                                 # HDR colours (emission): the plain power curve
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def material(assets, guid, cache):
    """A .mat as our material model (materials.Mat), named for the report. Standard: _MainTex/_Color,
    _MetallicGlossMap (R metallic, A smoothness x _GlossMapScale; albedo A when _SmoothnessTextureChannel is 1),
    _Metallic/_Glossiness, _BumpMap/_BumpScale, _OcclusionMap (G), _EmissionMap/_EmissionColor, _Mode + _Cutoff.
    URP Lit: _BaseMap/_BaseColor, _Smoothness, _Surface, _AlphaClip. HDRP Lit: _MaskMap (R metallic, G occlusion,
    A smoothness), _NormalMap/_NormalScale. Roughness = 1 - smoothness; colours sRGB to linear; the base map's tiling
    and offset are baked into the UVs (one transform: Unity's materials tile every map with it)."""
    if guid in cache:
        return cache[guid]
    docs = assets.docs(guid)
    body = next((b for _c, _s, kind, b in docs.values() if kind == "Material"), None)
    name = os.path.splitext(os.path.basename(assets.path(guid)))[0] or "unity material"
    m = mt.Mat(name)
    cache[guid] = m
    if body is None:
        m.notes.append("not a Unity material file: default material")
        return m
    sp = body.get("m_SavedProperties", {})
    texs, floats, cols = _plist(sp.get("m_TexEnvs")), _plist(sp.get("m_Floats")), _plist(sp.get("m_Colors"))
    keywords = str(body.get("m_ShaderKeywords", "")) + " " + " ".join(body.get("m_ValidKeywords", []) or [])

    def tex(*names):
        for n in names:
            t = texs.get(n) or {}
            r = _ref(t.get("m_Texture"))
            if r and assets.path(r[1]):
                sc, of = t.get("m_Scale") or {}, t.get("m_Offset") or {}
                return assets.path(r[1]), (float(sc.get("x", 1)), float(sc.get("y", 1))), (float(of.get("x", 0)), float(of.get("y", 0)))
        return None

    def col(*names, default=(1, 1, 1, 1)):
        for n in names:
            c = cols.get(n)
            if isinstance(c, dict):
                return [float(c.get(k, d)) for k, d in zip("rgba", default)]
        return list(default)

    def flt(*names, default=0.0):
        for n in names:
            if n in floats:
                return float(floats[n])
        return default

    pipe = HDRP if "_MaskMap" in texs or "_NormalMap" in texs else URP if "_BaseMap" in texs or "_BaseColor" in cols else STANDARD
    base_t = tex("_BaseColorMap", "_BaseMap", "_MainTex")
    lead_xf = mt.to_gltf_space(mt.usd_transform2d(base_t[1], 0.0, base_t[2])) if base_t else np.eye(3)
    T = lambda path, ch, **kw: mt.Tex(path, ch, 0, lead_xf, 10497, 10497, (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0), "auto",
                                      "unity:" + os.path.basename(path), **kw)
    c = col("_BaseColor", "_Color")
    m.base = [srgb_to_linear(c[0]), srgb_to_linear(c[1]), srgb_to_linear(c[2]), c[3]]
    if base_t:
        m.tex["diffuse"] = T(base_t[0], "rgb")
        m.tex["diffuse"].scale = (m.base[0], m.base[1], m.base[2], 1.0)       # the colour tints the map
    # metallic, roughness, occlusion
    if pipe == HDRP:
        mask = tex("_MaskMap")
        m.metallic, m.roughness = flt("_Metallic", default=0.0), 1.0 - flt("_Smoothness", default=0.5)
        if mask:
            m.tex["metallic"], m.tex["occlusion"] = T(mask[0], "r"), T(mask[0], "g")
            m.tex["roughness"] = T(mask[0], "a", invert=True)
            m.metallic = m.roughness = 1.0
            m.notes.append("HDRP mask map repacked to ORM (R metallic, G occlusion, A smoothness inverted); its remap ranges are not applied")
    else:
        mg = tex("_MetallicGlossMap")
        smooth = flt("_GlossMapScale", "_Smoothness", default=1.0) if mg else flt("_Glossiness", "_Smoothness", default=0.5)
        from_albedo = int(flt("_SmoothnessTextureChannel", default=0)) == 1 and base_t
        m.metallic = 1.0 if mg else flt("_Metallic", default=0.0)
        m.roughness = 1.0 - smooth
        if mg:
            m.tex["metallic"] = T(mg[0], "r")
        if mg or from_albedo:
            m.tex["roughness"] = T((base_t if from_albedo else mg)[0], "a", invert=True, mul=smooth)
            m.roughness = 1.0
        occ = tex("_OcclusionMap")
        if occ:
            m.tex["occlusion"] = T(occ[0], "g")
    n = tex("_NormalMap", "_BumpMap")
    if n:
        bs = flt("_NormalScale", "_BumpScale", default=1.0)
        m.tex["normal"] = T(n[0], "rgb")
        m.tex["normal"].scale = (2.0 * bs, 2.0 * bs, 2.0, 1.0)   # Unity's maps are green-up like glTF's: no flip
    # emission (Standard/URP: the keyword turns it on)
    e = col("_EmissionColor", "_EmissiveColor", default=(0, 0, 0, 1))
    if "_EMISSION" in keywords or pipe == HDRP:
        m.emissive = [srgb_to_linear(x) for x in e[:3]]
        et = tex("_EmissionMap", "_EmissiveColorMap")
        if et:
            m.tex["emissive"] = T(et[0], "rgb")
            m.tex["emissive"].scale = (m.emissive[0], m.emissive[1], m.emissive[2], 1.0)
    # alpha
    mode = int(flt("_Mode", default=0))
    if flt("_AlphaClip", "_AlphaCutoffEnable", default=0) or (pipe == STANDARD and mode == 1):
        m.cutoff = flt("_Cutoff", "_AlphaCutoff", default=0.5)
    elif flt("_Surface", "_SurfaceType", default=0) == 1 or (pipe == STANDARD and mode in (2, 3)):
        m.opacity = c[3]
        if base_t:
            m.tex["opacity"] = T(base_t[0], "a")
    if m.cutoff is not None and base_t:
        m.tex["opacity"] = T(base_t[0], "a")
    m.bake_uv, m.bake_texcoord = lead_xf, 0
    for t in m.tex.values():
        t.rel = None
    m.notes.append("Unity %s material" % pipe)
    return m


# ---- meshes

def _obj(path, scale):
    """OBJ: v/vt/vn/f with usemtl groups -> [(group name, attrs)] in the file's own (glTF) space."""
    V, VT, VN = [], [], []
    groups, cur = {}, "default"
    for line in open(path, encoding="utf-8", errors="replace"):
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            V.append([float(x) for x in p[1:4]])
        elif p[0] == "vt":
            VT.append([float(x) for x in p[1:3]])
        elif p[0] == "vn":
            VN.append([float(x) for x in p[1:4]])
        elif p[0] == "usemtl":
            cur = p[1] if len(p) > 1 else "default"
        elif p[0] == "f":
            corners = []
            for c in p[1:]:
                ix = (c.split("/") + ["", ""])[:3]
                corners.append(tuple(int(i) - 1 if i and int(i) > 0 else (int(i) if i else None) for i in ix))
            for k in range(1, len(corners) - 1):
                groups.setdefault(cur, []).extend([corners[0], corners[k], corners[k + 1]])
    out = []
    V, VT, VN = np.array(V or [[0, 0, 0]]), np.array(VT or [[0, 0]]), np.array(VN or [[0, 1, 0]])
    for g, cs in groups.items():
        P = V[[c[0] for c in cs]] * scale
        a = {"POSITION": P.astype(np.float32)}
        if all(c[1] is not None for c in cs) and len(VT) > 0:
            uv = VT[[c[1] for c in cs]].copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            a["TEXCOORD_0"] = uv.astype(np.float32)
        if all(c[2] is not None for c in cs) and len(VN) > 0:
            a["NORMAL"] = VN[[c[2] for c in cs]].astype(np.float32)
        else:
            t = P.reshape(-1, 3, 3)
            fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
            fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
            a["NORMAL"] = np.repeat(fn, 3, axis=0).astype(np.float32)
        out.append((g, a))
    return out


def _fbx(path, scale, space="world"):
    """FBX through the vendored ufbx (fbx.scene: glTF's space, right-handed, Y up, metres):
    [(node|material name, attrs)] per mesh node, baked to world (the whole model) or in its node's own space ("local":
    a Unity Mesh sub-asset, which a prefab's GameObject places with its own transform)."""
    from . import fbx
    sc = fbx.scene(path)
    nodes = sc["nodes"]
    world = {}

    def to_world(i):
        if i not in world:
            n = nodes[i]
            L = np.array(n["local"]).reshape(4, 4)
            world[i] = L if n["parent"] < 0 else to_world(n["parent"]) @ L
        return world[i]
    out = []
    for i, n in enumerate(nodes):
        if n["mesh"] < 0:
            continue
        md = sc["meshes"][n["mesh"]]
        M = (to_world(i) if space == "world" else np.eye(4)) @ np.array(n["geometry"]).reshape(4, 4)
        for mi, a in fbx.mesh_parts(md, M, False):
            a["POSITION"] = (a["POSITION"] * scale).astype(np.float32)
            a.pop("COLOR_0", None)
            name = sc["materials"][mi]["name"] if 0 <= mi < len(sc["materials"]) else "default"
            out.append((n["name"] + "|" + name, a))
    return out


def model_parts(assets, guid, cache, space="world"):
    """A model asset as {mesh name: [(part name, attrs)]} ; the .meta's scale applied; space as in _fbx."""
    if (guid, space) in cache:
        return cache[(guid, space)]
    path, meta = assets.path(guid), assets.meta.get(guid, {})
    mi = meta.get("ModelImporter", {}) or {}
    meshes = mi.get("meshes", {}) or {}
    scale = float(meshes.get("globalScale", 1.0) or 1.0)
    parts = _fbx(path, scale, space) if path.lower().endswith(".fbx") else _obj(path, scale) if path.lower().endswith(".obj") else []
    out = {}
    for name, attrs in parts:
        out.setdefault(name.split("|")[0], []).append((name.split("|")[-1], attrs))
    cache[(guid, space)] = out
    return out


BUILTIN = "0000000000000000e000000000000000"   # Unity's built-in extra resources (the primitive meshes)


def _builtin(file_id):
    """Unity's built-in Cube (10202), Plane (10209, 10 m) and Quad (10210), in glTF space; None for the others."""
    def box():
        f = []
        for axis in range(3):
            for sgn in (-1, 1):
                n = np.zeros(3); n[axis] = sgn
                u, v = np.roll(np.eye(3), 1, 0)[axis], np.roll(np.eye(3), 2, 0)[axis]
                if sgn < 0:
                    u = -u
                c = n * 0.5
                q = [c - u * .5 - v * .5, c + u * .5 - v * .5, c + u * .5 + v * .5, c - u * .5 + v * .5]
                f.append((q, n))
        return f
    def quad(size, normal):
        h = size / 2.0
        if normal == "y":
            return [([np.array([-h, 0, h]), np.array([h, 0, h]), np.array([h, 0, -h]), np.array([-h, 0, -h])], np.array([0, 1.0, 0]))]
        return [([np.array([h, -h, 0]), np.array([-h, -h, 0]), np.array([-h, h, 0]), np.array([h, h, 0])], np.array([0, 0, -1.0]))]
    faces = {10202: box, 10209: lambda: quad(10.0, "y"), 10210: lambda: quad(1.0, "z")}.get(int(file_id))
    if faces is None:
        return None
    P, N, T = [], [], []
    for q, n in faces():
        for i in (0, 1, 2, 0, 2, 3):
            P.append(q[i]); N.append(n)
        T += [[0, 1], [1, 1], [1, 0], [0, 1], [1, 0], [0, 0]]
    P = np.array(P, dtype=np.float32)
    tri = P.reshape(-1, 3, 3)
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    flip = (np.einsum("ij,ij->i", fn, np.array(N, dtype=np.float32).reshape(-1, 3, 3)[:, 0]) < 0)
    order = np.arange(len(P)).reshape(-1, 3)
    order[flip] = order[flip][:, ::-1]                         # every face wound outward (glTF: counter-clockwise)
    order = order.reshape(-1)
    return [("builtin", {"POSITION": P[order], "NORMAL": np.array(N, dtype=np.float32)[order],
                         "TEXCOORD_0": np.array(T, dtype=np.float32)[order]})]


_FMT = {0: ("<f4", 4), 1: ("<f2", 2), 2: ("u1", 1), 3: ("i1", 1), 4: ("<u2", 2), 5: ("<i2", 2), 6: ("<u4", 4), 7: ("<i4", 4)}


def _asset_mesh(assets, guid, file_id):
    """A Unity Mesh (class 43) in a text .asset: m_VertexData (channels over streams, each stream 16-byte aligned,
    hex in _typelessdata), m_IndexBuffer (hex, 16 or 32 bit) and m_SubMeshes -> [(submesh, attrs)] in glTF space
    (Unity's left-handed data mirrored in X, winding reversed, V flipped)."""
    docs = assets.docs(guid)
    body = None
    for fid, (cls, _s, kind, b) in docs.items():
        if kind == "Mesh" and (fid == int(file_id) or body is None):
            body = b
    if body is None:
        return []
    vd = body.get("m_VertexData", {}) or {}
    n = int(vd.get("m_VertexCount", 0))
    chans = vd.get("m_Channels", []) or []
    raw = bytes.fromhex(str(vd.get("_typelessdata", "")))
    streams = {}
    for ci, ch in enumerate(chans):
        if int(ch.get("dimension", 0)):
            streams.setdefault(int(ch.get("stream", 0)), []).append((ci, ch))
    attrs, offset = {}, 0
    for si in sorted(streams):
        stride = max(int(ch["offset"]) + _FMT[int(ch["format"])][1] * int(ch["dimension"]) for _ci, ch in streams[si])
        block = np.frombuffer(raw, dtype=np.uint8, count=stride * n, offset=offset).reshape(n, stride)
        for ci, ch in streams[si]:
            dt, size = _FMT[int(ch["format"])]
            dim, off = int(ch["dimension"]), int(ch["offset"])
            col = np.ascontiguousarray(block[:, off:off + size * dim]).view(dt).reshape(n, dim).astype(np.float64)
            if dt == "u1":
                col = col / 255.0
            attrs[ci] = col
        offset += stride * n
        offset = (offset + 15) // 16 * 16
    if 0 not in attrs:
        return []
    # skin: weights (channel 12) and bone indices (13) in the vertex data, else the older m_Skin list
    skin_w = skin_j = None
    if 12 in attrs and 13 in attrs:
        skin_w, skin_j = _four(attrs[12]), _four(attrs[13])
    elif body.get("m_Skin"):
        sk = body["m_Skin"]
        skin_w = np.array([[float(v.get("weight[%d]" % i, 0)) for i in range(4)] for v in sk])
        skin_j = np.array([[float(v.get("boneIndex[%d]" % i, 0)) for i in range(4)] for v in sk])
    if skin_w is not None:
        tot = skin_w.sum(axis=1, keepdims=True)
        skin_w = np.where(tot > 0, skin_w / np.maximum(tot, 1e-12), np.array([1.0, 0, 0, 0]))
    ib = bytes.fromhex(str(body.get("m_IndexBuffer", "")))
    idx = np.frombuffer(ib, dtype="<u4" if int(body.get("m_IndexFormat", 0)) == 1 else "<u2").astype(np.int64)
    out = []
    for k, sm in enumerate(body.get("m_SubMeshes", []) or []):
        isz = 4 if int(body.get("m_IndexFormat", 0)) == 1 else 2
        first = int(sm.get("firstByte", 0)) // isz
        tri = idx[first:first + int(sm.get("indexCount", 0))] + int(sm.get("baseVertex", 0))
        tri = tri.reshape(-1, 3)[:, ::-1].reshape(-1)               # the X mirror reverses the winding
        a = {"POSITION": (attrs[0][tri, :3] * np.array([-1, 1, 1])).astype(np.float32)}
        if 1 in attrs:
            a["NORMAL"] = (attrs[1][tri, :3] * np.array([-1, 1, 1])).astype(np.float32)
        if 4 in attrs:
            uv = attrs[4][tri, :2].copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            a["TEXCOORD_0"] = uv.astype(np.float32)
        if 3 in attrs:
            a["COLOR_0"] = attrs[3][tri, :4].astype(np.float32)
        if skin_w is not None:
            a["WEIGHTS_0"], a["JOINTS_0"] = skin_w[tri].astype(np.float32), skin_j[tri].astype(np.float32)
        out.append(("submesh%d" % k, a))
    return out


def _four(a):
    out = np.zeros((len(a), 4))
    out[:, :min(4, a.shape[1])] = a[:, :4]
    return out


def bind_poses(assets, guid, file_id):
    """A Mesh .asset's m_BindPose (Unity Matrix4x4 eRC, column vectors), one per bone."""
    meshes = [(fid, b) for fid, (cls, _s, kind, b) in assets.docs(guid).items() if kind == "Mesh"]
    for fid, b in sorted(meshes, key=lambda m: m[0] != int(file_id)):      # the named mesh, else the first
        if True:
            return [np.array([[float(m.get("e%d%d" % (r, c), 1.0 if r == c else 0.0)) for c in range(4)] for r in range(4)])
                    for m in b.get("m_BindPose", []) or []]
    return []


SPLAT_MAX = int(os.environ.get("GODOT_GULP_SPLAT_MAX", "4096"))     # the baked terrain texture's largest side
SPLAT_TEXELS_PER_TILE = 256                                           # aimed for, under SPLAT_MAX


def _texture2d(body):
    """A Unity Texture2D written as text (RGBA32 or ARGB32, the first mip) -> float RGBA (h, w, 4) in 0..1, rows from
    v = 0 (Unity's bottom) up; None for other formats."""
    w, h, fmt = int(body.get("m_Width", 0)), int(body.get("m_Height", 0)), int(body.get("m_TextureFormat", 0))
    raw = body.get("_typelessdata", "")
    data = bytes.fromhex(raw) if isinstance(raw, str) else b""
    if fmt not in (4, 5) or w < 1 or h < 1 or len(data) < w * h * 4:
        return None
    a = np.frombuffer(data[:w * h * 4], dtype=np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0
    return a[..., [1, 2, 3, 0]] if fmt == 5 else a


def _bilinear(img, v, u):
    """img (h, w, c) sampled at fractional rows v, columns u (arrays, clamped)."""
    h, w = img.shape[:2]
    v, u = np.clip(v, 0, h - 1), np.clip(u, 0, w - 1)
    v0, u0 = np.floor(v).astype(int), np.floor(u).astype(int)
    v1, u1 = np.minimum(v0 + 1, h - 1), np.minimum(u0 + 1, w - 1)
    fv, fu = (v - v0)[..., None], (u - u0)[..., None]
    return (img[v0, u0] * (1 - fu) + img[v0, u1] * fu) * (1 - fv) + (img[v1, u0] * (1 - fu) + img[v1, u1] * fu) * fv


def bake_splat(assets, docs, sdb, size, notes):
    """Unity's splat blend baked into one base colour texture over the whole terrain: each terrain layer's diffuse,
    tiled by its m_TileSize/m_TileOffset like Unity's terrain shader, weighted by the alphamaps (four layers per RGBA
    alphamap, weights normalised), blended in linear space. -> the PNG path (cached by its inputs), or "" """
    import hashlib
    from PIL import Image
    alphas = []
    for ref in sdb.get("m_AlphaTextures", []) or []:
        r = _ref(ref)
        d = (assets.docs(r[1]) if r and r[1] else docs).get(r[0]) if r else None
        a = _texture2d(d[3]) if d and d[2] == "Texture2D" else None
        if a is None:
            notes.append("terrain: alphamap %s unreadable (only RGBA32/ARGB32 text textures are read)" % (r,))
            return ""
        alphas.append(a)
    if not alphas:
        return ""
    W = np.concatenate(alphas, axis=-1)                                 # (h, w, 4 * alphamaps): one channel per layer
    layers = []
    for i, lr in enumerate(sdb.get("m_TerrainLayers", []) or []):
        r = _ref(lr)
        ld = next((b for _c, _s, kind, b in assets.docs(r[1]).values() if kind == "TerrainLayer"), None) if r and r[1] else None
        dref = _ref((ld or {}).get("m_DiffuseTexture"))
        path = assets.path(dref[1]) if dref else ""
        if not path or i >= W.shape[-1]:
            notes.append("terrain: layer %d has no diffuse texture (or no alphamap channel); left out of the blend" % i)
            continue
        ts, to = (ld.get("m_TileSize") or {}), (ld.get("m_TileOffset") or {})
        layers.append((i, path, (max(1e-6, float(ts.get("x", 15))), max(1e-6, float(ts.get("y", 15)))),
                       (float(to.get("x", 0)), float(to.get("y", 0)))))
    if not layers:
        return ""
    tiles = max(size[0] / min(t[0] for _i, _p, t, _o in layers), size[1] / min(t[1] for _i, _p, t, _o in layers))
    R = int(min(SPLAT_MAX, max(W.shape[0], W.shape[1], 64, round(tiles * SPLAT_TEXELS_PER_TILE))))
    key = hashlib.sha1(W.tobytes() + repr((R, size, [(i, os.path.getsize(p), os.path.getmtime(p), t, o)
                                                     for i, p, t, o in layers])).encode()).hexdigest()[:16]
    out = os.path.join(tempfile.gettempdir(), "godot-gulp-splat", key + ".png")
    if os.path.isfile(out):
        return out
    s = (np.arange(R) + 0.5) / R                                        # terrain UV of each output texel, 0..1
    out_rows = []
    for c0 in range(0, R, 512):                                         # rows in bands: memory stays small at 4096
        t = s[c0:c0 + 512]                                              # output row r is Unity v = t (z), from z = 0
        V, U = np.meshgrid(t, s, indexing="ij")
        wts = _bilinear(W, V * W.shape[0] - 0.5, U * W.shape[1] - 0.5)
        acc = np.zeros(V.shape + (3,), dtype=np.float32)
        tot = np.zeros(V.shape + (1,), dtype=np.float32)
        for i, path, tile, off in layers:
            img = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
            img = np.where(img <= 0.04045, img / 12.92, ((img + 0.055) / 1.055) ** 2.4)      # sRGB to linear
            h, w = img.shape[:2]
            tu = (U * size[0] + off[0]) / tile[0] % 1.0
            tv = (V * size[1] + off[1]) / tile[1] % 1.0
            sample = _bilinear(img, (1.0 - tv) * h - 0.5, tu * w - 0.5)                 # image rows run top down
            acc += sample * wts[..., i:i + 1]
            tot += wts[..., i:i + 1]
        lin = acc / np.maximum(tot, 1e-6)
        out_rows.append(np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(np.clip(lin, 0, 1), 1 / 2.4) - 0.055))
    img = np.concatenate(out_rows, axis=0)[::-1]                        # PNG top row = glTF v 0 = Unity's far edge
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = "%s.%d.tmp" % (out, os.getpid())
    Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB").save(tmp, "PNG", compress_level=3)
    os.replace(tmp, out)
    notes.append("terrain: %d layers blended by the alphamaps into one %d px texture (%.1f px per metre)" % (
        len(layers), R, R / max(size)))
    return out


def terrain_parts(assets, guid, cache):
    """A TerrainData (heightmap: int16 samples over m_Resolution^2, m_Scale per sample; height = sample / 32766 *
    scale.y) -> one grid mesh in glTF space, and the first terrain layer as its material (its diffuse tiled every
    m_TileSize metres); with more than one layer the splat blend baked into one texture (bake_splat).
    -> (parts, material, notes)"""
    docs = assets.docs(guid)
    td = next((b for _c, _s, kind, b in docs.values() if kind == "TerrainData"), None)
    if td is None:
        return [], None, ["terrain data %s not found" % guid]
    hm = td.get("m_Heightmap", {}) or {}
    n = int(hm.get("m_Resolution", 0) or hm.get("m_Width", 0) or 0)
    raw = hm.get("m_Heights", "")
    H = (np.frombuffer(bytes.fromhex(raw), dtype="<i2") if isinstance(raw, str) else np.array(raw or [], dtype=np.int16)).astype(np.float64)
    if n < 2 or len(H) < n * n:
        return [], None, ["terrain heightmap unreadable (resolution %s, %d samples)" % (n, len(H))]
    sc = hm.get("m_Scale", {}) or {}
    sx, sy, sz = float(sc.get("x", 1)), float(sc.get("y", 1)), float(sc.get("z", 1))
    H = H[:n * n].reshape(n, n) / 32766.0 * sy                  # rows are z, columns x
    zz, xx = np.mgrid[0:n, 0:n]
    P = np.stack([xx * sx, H, zz * sz], axis=-1).reshape(-1, 3)
    UVg = np.stack([xx / (n - 1.0), zz / (n - 1.0)], axis=-1).reshape(-1, 2)
    q = (zz[:-1, :-1] * n + xx[:-1, :-1]).reshape(-1)
    # two triangles a cell, wound for Unity's left-handed front (up); the X mirror below reverses them for glTF
    tri = np.stack([q, q + n, q + 1, q + 1, q + n, q + n + 1], axis=-1).reshape(-1)
    tri = tri.reshape(-1, 3)[:, ::-1].reshape(-1)
    pos = P[tri] * np.array([-1.0, 1.0, 1.0])
    t3 = pos.reshape(-1, 3, 3)
    fn = np.cross(t3[:, 1] - t3[:, 0], t3[:, 2] - t3[:, 0])
    acc = np.zeros((len(P), 3))
    np.add.at(acc, tri.reshape(-1, 3), fn[:, None, :].repeat(3, axis=1))
    N = acc[tri]
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    uv = UVg[tri].copy()
    uv[:, 1] = 1.0 - uv[:, 1]
    attrs = {"POSITION": pos.astype(np.float32), "NORMAL": N.astype(np.float32), "TEXCOORD_0": uv.astype(np.float32)}
    notes, mat = [], None
    sdb = td.get("m_SplatDatabase", {}) or {}
    layers = sdb.get("m_TerrainLayers", []) or []
    if len(layers) > 1:
        baked = bake_splat(assets, docs, sdb, ((n - 1) * sx, (n - 1) * sz), notes)
        if baked:
            mat = mt.Mat(td.get("m_Name") or "terrain")
            mat.tex["diffuse"] = mt.Tex(baked, "rgb", 0, np.eye(3), 33071, 33071, (1.0, 1.0, 1.0, 1.0),
                                        (0.0, 0.0, 0.0, 0.0), "auto", "unity:terrain splat")
            mat.tex["diffuse"].rel = None
            mat.roughness, mat.metallic = 0.9, 0.0
            return [("terrain", attrs)], mat, notes
        notes.append("terrain: %d layers; the first is tiled over it (the splat blend could not be baked)" % len(layers))
    lref = _ref(layers[0]) if layers else None
    if lref and lref[1]:
        ld = next((b for _c, _s, kind, b in assets.docs(lref[1]).values() if kind == "TerrainLayer"), None)
        dref = _ref((ld or {}).get("m_DiffuseTexture"))
        if dref and assets.path(dref[1]):
            ts, to = (ld.get("m_TileSize") or {}), (ld.get("m_TileOffset") or {})
            tile = (max(1e-6, float(ts.get("x", 15))), max(1e-6, float(ts.get("y", 15))))
            size = ((n - 1) * sx, (n - 1) * sz)
            xf = mt.to_gltf_space(mt.usd_transform2d((size[0] / tile[0], size[1] / tile[1]), 0.0,
                                                     (float(to.get("x", 0)) / tile[0], float(to.get("y", 0)) / tile[1])))
            mat = mt.Mat(os.path.splitext(os.path.basename(assets.path(lref[1])))[0] or "terrain")
            mat.tex["diffuse"] = mt.Tex(assets.path(dref[1]), "rgb", 0, xf, 10497, 10497, (1.0, 1.0, 1.0, 1.0),
                                        (0.0, 0.0, 0.0, 0.0), "auto", "unity:terrain layer")
            mat.tex["diffuse"].rel = None
            mat.bake_uv, mat.bake_texcoord = xf, 0
            mat.roughness, mat.metallic = 0.9, 0.0
    return [("terrain", attrs)], mat, notes


_ID_MAPS = {}


def fileid_map(path):
    """{(model asset path, fileID): mesh name} from a .tsv Unity itself wrote (model, class, name, fileID), e.g. through
    the Unity CLI: Unity 2019.3+ hashes these ids, and the map gives the exact names (GODOT_GULP_UNITY_IDS, --fileid-map)."""
    if path not in _ID_MAPS:
        m = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh.read().splitlines()[1:]:
                f = line.split("\t")
                if len(f) >= 4 and f[1] == "Mesh":
                    try:
                        m[(f[0], int(f[3]))] = f[2]
                    except ValueError:
                        pass
        _ID_MAPS[path] = m
    return _ID_MAPS[path]


def _plain(name):
    """A GameObject name as Unity's duplicates leave it, compared without case ("Door (1)" -> "door")."""
    return re.sub(r"\s*\(\d+\)$", "", name or "").lower()


def mesh_by_fileid(assets, guid, file_id, cache, node_name=None, notes=None):
    """The mesh a MeshFilter names: a built-in primitive, a Mesh .asset, or a model whose .meta maps fileIDs to names
    (internalIDToNameTable, fileIDToRecycleName); a model with one mesh needs no map. Unity 2019.3+ leaves the map out
    and names a model's meshes by a hash of their names: then a fileID map Unity wrote gives the name, else the mesh is
    the model's node of the renderer's own GameObject name (Unity names each mesh after its FBX node, and a prefab made
    from a model keeps those names), else the model's first mesh, with a note."""
    if guid == BUILTIN:
        return _builtin(file_id) or []
    if assets.path(guid).lower().endswith(".asset"):
        key = (guid, int(file_id))
        if key not in cache:
            cache[key] = _asset_mesh(assets, guid, file_id)
        return cache[key]
    parts = model_parts(assets, guid, cache, "local")       # a Mesh sub-asset: its GameObject's transform places it
    if len(parts) == 1:
        return next(iter(parts.values()))
    meta = assets.meta.get(guid, {}).get("ModelImporter", {}) or {}
    names = {}
    for e in meta.get("internalIDToNameTable", []) or []:
        first = e.get("first", {}) or {}
        for _cls, fid in first.items():
            names[int(fid)] = e.get("second")
    for fid, n in (meta.get("fileIDToRecycleName", {}) or {}).items():
        names[int(fid)] = n
    n = names.get(int(file_id))
    if n in parts:
        return parts[n]
    ids = os.environ.get("GODOT_GULP_UNITY_IDS")
    if ids:
        ap = assets.path(guid).replace("\\", "/")
        ap = ap[ap.rfind("/Assets/") + 1:] if "/Assets/" in ap else ap        # an extracted path -> Unity's "Assets/..."
        fm = fileid_map(ids)
        n = fm.get((ap, int(file_id)))
        if n in parts:
            return parts[n]
        if n is None and any(k[0] == ap for k in fm):      # Unity lists this model, and no mesh with that id:
            if notes is not None:                           # a broken reference, which Unity draws as nothing
                notes.append("%s: mesh fileID %s is not in the model (Unity lists no such mesh): left out, as Unity does"
                             % (ap.split("/")[-1], file_id))
            return []
    if node_name in parts:
        return parts[node_name]
    plain = {_plain(k): k for k in parts}
    if _plain(node_name) in plain:
        return parts[plain[_plain(node_name)]]
    if notes is not None:
        notes.append("%s: mesh fileID %s not mapped (no name table, no node named %s): the model's first mesh used"
                     % (assets.path(guid).split("/")[-1], file_id, node_name))
    return next(iter(parts.values()), [])


# ---- prefabs and scenes

def _trs(t):
    """A Transform's local matrix in Unity space (column vectors): T * R * S."""
    p, q, s = t.get("m_LocalPosition", {}) or {}, t.get("m_LocalRotation", {}) or {}, t.get("m_LocalScale", {}) or {}
    x, y, z, w = (float(q.get(k, d)) for k, d in zip("xyzw", (0, 0, 0, 1)))
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0      # Unity normalizes on load: an override of one component
    x, y, z, w = x / n, y / n, z / n, w / n                  # (x set, w kept) leaves an unnormalized quaternion
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R @ np.diag([float(s.get(k, 1)) for k in "xyz"])
    M[:3, 3] = [float(p.get(k, 0)) for k in "xyz"]
    return M


class Node:
    def __init__(self, name, local):
        self.name, self.local, self.children = name, local, []
        self.mesh = None             # (model guid, mesh fileID)
        self.skinned = False         # a SkinnedMeshRenderer: a glTF skin when its bones and weights are known
        self.bones = []              # Transform fileIDs (this file's, or stripped ones) in bone order
        self.lod = 0                 # the LODGroup level this node's renderer belongs to (0: drawn)
        self.terrain = None          # (TerrainData fileID, guid) of a Terrain component
        self.controller = ""         # an Animator's AnimatorController guid
        self.materials = []          # material guids, in submesh order


def build(assets, guid, depth=0):
    """A prefab or scene as a list of root Nodes, nested PrefabInstances expanded with their overrides applied."""
    if depth > 16:
        return [], {}
    docs = assets.docs(guid)
    nodes = {}                       # Transform fileID -> Node
    go_tf = {}                       # GameObject fileID -> Transform fileID
    for fid, (cls, stripped, kind, b) in docs.items():
        if kind in ("Transform", "RectTransform") and not stripped:
            nodes[fid] = Node("", _trs(b))
            go = _ref(b.get("m_GameObject"))
            if go:
                go_tf[go[0]] = fid
    comp_node = {}                   # renderer fileID -> Node (for LODGroups)
    for fid, (cls, stripped, kind, b) in docs.items():
        if kind == "GameObject" and not stripped and fid in go_tf:
            nodes[go_tf[fid]].name = str(b.get("m_Name", ""))
        if kind in ("MeshFilter", "MeshRenderer", "SkinnedMeshRenderer") and not stripped:
            go = _ref(b.get("m_GameObject"))
            node = nodes.get(go_tf.get(go[0])) if go else None
            if node is None:
                continue
            comp_node[fid] = node
            if kind in ("MeshFilter", "SkinnedMeshRenderer"):
                r = _ref(b.get("m_Mesh"))
                node.mesh = r if r and r[1] else None
                node.skinned = kind == "SkinnedMeshRenderer"
            if kind in ("MeshRenderer", "SkinnedMeshRenderer"):
                node.materials = [(_ref(x) or (0, ""))[1] for x in b.get("m_Materials", []) or []]
            if kind == "SkinnedMeshRenderer":
                node.bones = [(_ref(x) or (0, ""))[0] for x in b.get("m_Bones", []) or []]
    for fid, (cls, stripped, kind, b) in docs.items():
        if kind == "Animator" and not stripped:
            go = _ref(b.get("m_GameObject"))
            node = nodes.get(go_tf.get(go[0])) if go else None
            ctl = _ref(b.get("m_Controller"))
            if node is not None and ctl:
                node.controller = ctl[1]
        if kind == "Terrain" and not stripped:
            go = _ref(b.get("m_GameObject"))
            node = nodes.get(go_tf.get(go[0])) if go else None
            if node is not None:
                node.terrain = _ref(b.get("m_TerrainData"))
    # LOD groups: one level is drawn at a time; the coarser levels' renderers are marked and left out of the glTF
    # (Godot makes its own LODs on import; the levels are named in the report)
    for fid, (cls, stripped, kind, b) in docs.items():
        if kind == "LODGroup" and not stripped:
            for level, lod in enumerate(b.get("m_LODs", []) or []):
                for r in lod.get("renderers", []) or []:
                    ref = _ref((r or {}).get("renderer"))
                    if ref and ref[0] in comp_node and level > 0:
                        comp_node[ref[0]].lod = level
    # nested prefabs: expand each instance, apply its modifications, hang it under its parent
    by_source = {}                   # (instance fileID, source object fileID) -> Node in the expansion
    for fid, (cls, stripped, kind, b) in docs.items():
        if kind != "PrefabInstance":
            continue
        src = _ref(b.get("m_SourcePrefab"))
        if not src:
            continue
        roots, index = build(assets, src[1], depth + 1)
        mod = b.get("m_Modification", {}) or {}
        for m in mod.get("m_Modifications", []) or []:
            tgt = _ref(m.get("target"))
            node = index.get(tgt[0]) if tgt else None
            if node is None:
                continue
            _modify(node, str(m.get("propertyPath", "")), m.get("value"), _ref(m.get("objectReference")))
        for sfid, node in index.items():
            by_source[(fid, sfid)] = node
        parent = _ref(mod.get("m_TransformParent"))
        holder = ("instance", fid, roots, parent[0] if parent else 0)
        nodes.setdefault(("instances",), []).append(holder)
    # stripped objects in this file stand for objects inside an instance
    stripped_map = {}
    for fid, (cls, stripped, kind, b) in docs.items():
        if stripped:
            inst, src = _ref(b.get("m_PrefabInstance")), _ref(b.get("m_CorrespondingSourceObject"))
            if inst and src and (inst[0], src[0]) in by_source:
                stripped_map[fid] = by_source[(inst[0], src[0])]
    instances = nodes.pop(("instances",), [])

    def resolve(f):
        return nodes.get(f) or stripped_map.get(f)
    roots = []
    for fid, (cls, stripped, kind, b) in docs.items():
        if kind in ("Transform", "RectTransform") and not stripped:
            father = _ref(b.get("m_Father"))
            parent = resolve(father[0]) if father else None
            (parent.children if parent else roots).append(nodes[fid])
    for _tag, _ifid, iroots, parent_fid in instances:
        parent = resolve(parent_fid) if parent_fid else None
        (parent.children if parent else roots).extend(iroots)
    index = dict(nodes)
    index.update(stripped_map)
    for (go_fid, tf_fid) in go_tf.items():
        index[go_fid] = nodes[tf_fid]                      # a modification may target the GameObject (m_Name)
    for (inst, sfid), node in by_source.items():
        pass
    return roots, index


def _modify(node, path, value, objref):
    comp = {"x": 0, "y": 1, "z": 2, "w": 3}
    try:
        v = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        v = None
    m = re.match(r"m_Local(Position|Rotation|Scale)\.([xyzw])$", path)
    if m and v is not None:
        # rebuild the local TRS from the current matrix, set the one component, put it back
        pos, rot, scl = _decompose(node.local)
        target = {"Position": pos, "Rotation": rot, "Scale": scl}[m.group(1)]
        target[comp[m.group(2)]] = v
        node.local = _compose(pos, rot, scl)
    elif path == "m_Name" and value is not None:
        node.name = str(value)
    elif path.startswith("m_Materials.Array.data[") and objref:
        i = int(re.search(r"\[(\d+)\]", path).group(1))
        while len(node.materials) <= i:
            node.materials.append("")
        node.materials[i] = objref[1]


def _decompose(M):
    pos = list(M[:3, 3])
    sx, sy, sz = (float(np.linalg.norm(M[:3, i])) for i in range(3))
    R = M[:3, :3] / np.array([sx or 1, sy or 1, sz or 1])
    if np.linalg.det(R) < 0:
        sx, R = -sx, R * np.array([-1, 1, 1])
    w = math.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    if w > 1e-6:
        x, y, z = (R[2, 1] - R[1, 2]) / (4 * w), (R[0, 2] - R[2, 0]) / (4 * w), (R[1, 0] - R[0, 1]) / (4 * w)
    else:
        x = math.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2
        y = (R[0, 1] + R[1, 0]) / (4 * x) if x else 0.0
        z = (R[0, 2] + R[2, 0]) / (4 * x) if x else 0.0
        w = (R[2, 1] - R[1, 2]) / (4 * x) if x else 1.0
    return pos, [x, y, z, w], [sx, sy, sz]


def _compose(pos, rot, scl):
    return _trs({"m_LocalPosition": dict(zip("xyz", pos)), "m_LocalRotation": dict(zip("xyzw", rot)),
                 "m_LocalScale": dict(zip("xyz", scl))})


# ---- to glTF

def _hermite(keys, t):
    """Unity's curve at t: Hermite between keyframes (time, value, inSlope, outSlope), clamped at the ends."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0, _i0, o0), (t1, v1, i1, _o1) in zip(keys, keys[1:]):
        if t <= t1:
            d = t1 - t0
            u = (t - t0) / d if d > 0 else 0.0
            h00, h10, h01, h11 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u, -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
            return h00 * v0 + h10 * d * o0 + h01 * v1 + h11 * d * i1
    return keys[-1][1]


def _euler_quat(x, y, z):
    """Unity Euler degrees (applied Z, then X, then Y) to a quaternion (x, y, z, w) in Unity space."""
    def q(axis, deg):
        h = math.radians(deg) / 2
        v = [0.0, 0.0, 0.0]
        v[axis] = math.sin(h)
        return np.array(v + [math.cos(h)])
    def mul(a, b):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return np.array([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                         aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz])
    return mul(mul(q(1, y), q(0, x)), q(2, z))


def controller_clips(assets, controller_guid, animator_node):
    """The clips an AnimatorController's states play, as glTF channels on the animator's node tree (curve paths are
    child names from the Animator's GameObject). Position, quaternion rotation and scale curves keep their keys and
    slopes (CUBICSPLINE: glTF tangents are Unity's slopes); Euler curves are sampled at 30 fps into quaternions."""
    out, seen = [], set()
    for _fid, (_c, _s, kind, b) in assets.docs(controller_guid).items():
        if kind != "AnimatorState":
            continue
        mo = _ref(b.get("m_Motion"))
        if not mo or not mo[1] or mo[1] in seen or not assets.path(mo[1]).lower().endswith(".anim"):
            continue
        seen.add(mo[1])
        clip = next((cb for _f, (_cc, _ss, k, cb) in assets.docs(mo[1]).items() if k == "AnimationClip"), None)
        if clip is None:
            continue

        def target(path):
            node = animator_node
            for part in [p for p in str(path or "").split("/") if p]:
                node = next((c for c in node.children if c.name == part), None)
                if node is None:
                    return None
            return node
        chans = []
        for key, gpath, n, conv in (("m_PositionCurves", "translation", 3, np.array([-1, 1, 1])),
                                    ("m_RotationCurves", "rotation", 4, np.array([1, -1, -1, 1])),
                                    ("m_ScaleCurves", "scale", 3, np.array([1, 1, 1]))):
            for c in clip.get(key, []) or []:
                node = target(c.get("path"))
                kf = ((c.get("curve") or {}).get("m_Curve")) or []
                if node is None or not kf:
                    continue
                comp = "xyzw"[:n]
                vec = lambda d: np.array([float((d or {}).get(k, 0)) for k in comp])
                chans.append({"key": id(node), "path": gpath, "times": [float(k.get("time", 0)) for k in kf],
                              "values": [vec(k.get("value")) * conv for k in kf],
                              "in": [vec(k.get("inSlope")) * conv for k in kf], "out": [vec(k.get("outSlope")) * conv for k in kf]})
        for c in clip.get("m_EulerCurves", []) or []:
            node = target(c.get("path"))
            kf = ((c.get("curve") or {}).get("m_Curve")) or []
            if node is None or not kf:
                continue
            comps = [[(float(k.get("time", 0)), float((k.get("value") or {}).get(a, 0)), float((k.get("inSlope") or {}).get(a, 0)),
                       float((k.get("outSlope") or {}).get(a, 0))) for k in kf] for a in "xyz"]
            t0, t1 = comps[0][0][0], comps[0][-1][0]
            times = list(np.linspace(t0, t1, max(2, int(round((t1 - t0) * 30)) + 1)))
            quats = [_euler_quat(*(_hermite(cc, t) for cc in comps)) * np.array([1, -1, -1, 1]) for t in times]
            chans.append({"key": id(node), "path": "rotation", "times": times, "values": quats})
        if chans:
            out.append((str(clip.get("m_Name", "") or os.path.basename(assets.path(mo[1]))), chans))
    return out


def _bake(attrs, M):
    """A copy of attrs with M (column vectors, glTF space) applied; winding reversed when M mirrors."""
    a = dict(attrs)
    a["POSITION"] = ((np.c_[attrs["POSITION"], np.ones(len(attrs["POSITION"]))] @ M.T)[:, :3]).astype(np.float32)
    if "NORMAL" in attrs:
        N = attrs["NORMAL"] @ np.linalg.inv(M[:3, :3])
        a["NORMAL"] = (N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)).astype(np.float32)
    if np.linalg.det(M[:3, :3]) < 0:
        order = np.arange(len(a["POSITION"])).reshape(-1, 3)[:, ::-1].reshape(-1)
        a = {k: v[order] for k, v in a.items()}
    return a


def convert_asset(assets, guid, dst, caches=None):
    """One prefab, scene or model to dst (.gltf). -> info dict (meshes, tris, materials, notes)."""
    caches = caches if caches is not None else {"mat": {}, "model": {}}
    path = assets.path(guid)
    nodes_out, tree, anims = [], [], []
    notes = []
    default = mt.Mat("default")

    def make_mesh(name, parts, mats):
        prims = []
        for i, (pname, attrs) in enumerate(parts):
            mg = mats[i] if i < len(mats) else (mats[-1] if mats else "")
            prims.append({"path": name, "attrs": attrs, "mat": material(assets, mg, caches["mat"]) if mg else default})
        return {"name": name, "path": name, "prims": prims, "uv_sets": ["st"], "subsets": 0}

    def mesh_dict(name, parts, mats, M_gltf, skin=None):
        prims = []
        for i, (pname, attrs) in enumerate(parts):
            mg = mats[i] if i < len(mats) else (mats[-1] if mats else "")
            mat = material(assets, mg, caches["mat"]) if mg else default
            prims.append({"path": name, "attrs": attrs, "mat": mat})
        m = {"name": name, "path": name, "prims": prims, "uv_sets": ["st"], "subsets": 0}
        if skin is not None:                                 # glTF ignores a skinned mesh's node transform
            m["skin"] = skin
            nodes_out.append((m, None))
            return
        if np.linalg.det(M_gltf[:3, :3]) < 0:            # a mirroring placement: baked, its winding fixed here
            for p in prims:
                p["attrs"] = _bake(p["attrs"], M_gltf)
            nodes_out.append((m, None))
        else:
            nodes_out.append((m, M_gltf.T))               # write_gltf takes row-vector matrices

    if path.lower().endswith((".fbx", ".obj")):
        meta = assets.meta.get(guid, {}).get("ModelImporter", {}) or {}
        remap = {}
        for e in meta.get("externalObjects", []) or []:
            first, second = e.get("first", {}) or {}, _ref(e.get("second"))
            if first.get("type", "").endswith("Material") and second:
                remap[first.get("name")] = second[1]
        for name, parts in model_parts(assets, guid, caches["model"]).items():
            mesh_dict(name, parts, [remap.get(p[0], "") for p in parts], np.eye(4))
    else:
        roots, index = build(assets, guid)
        world = {}

        def worlds(node, parent):
            world[id(node)] = parent @ node.local
            for c in node.children:
                worlds(c, world[id(node)])
        for r in roots:
            worlds(r, np.eye(4))

        def strip_skin(parts):
            return [(n, {k: v for k, v in a.items() if k not in ("JOINTS_0", "WEIGHTS_0")}) for n, a in parts]

        def tnode(node):
            """The glTF node tree in glTF space (local = S L S), meshes in their node's space."""
            t = {"key": id(node), "name": node.name or "node", "local": S @ node.local @ S, "children": []}
            W = S @ world[id(node)] @ S
            if node.terrain and node.terrain[1]:
                parts, tmat, tnotes = terrain_parts(assets, node.terrain[1], caches["model"])
                notes.extend(tnotes)
                if parts:
                    t["mesh"] = {"name": node.name or "terrain", "path": "terrain", "uv_sets": ["st"], "subsets": 0,
                                 "prims": [{"path": "terrain", "attrs": parts[0][1], "mat": tmat or default}]}
            if node.mesh and node.lod > 0:
                notes.append("%s: LOD%d left out (LOD0 kept; Godot makes its own LODs)" % (node.name or "mesh", node.lod))
            elif node.mesh:
                parts = mesh_by_fileid(assets, node.mesh[1], node.mesh[0], caches["model"], node.name, notes)
                if not parts:
                    notes.append("%s: mesh %s not found" % (node.name, node.mesh[1]))
                elif node.skinned and node.bones and all("JOINTS_0" in a for _n, a in parts):
                    binds = bind_poses(assets, node.mesh[1], node.mesh[0])
                    bones = [index.get(f) for f in node.bones]
                    if len(binds) == len(bones) and all(b is not None for b in bones):
                        m = make_mesh(node.name or "mesh", parts, node.materials)
                        m["skin_keys"], m["ibm"] = [id(b) for b in bones], [S @ B @ S for B in binds]
                        t["mesh"] = m
                    else:
                        notes.append("%s: bones or bind poses do not resolve: drawn in its bind pose" % node.name)
                        t["mesh"] = make_mesh(node.name or "mesh", strip_skin(parts), node.materials)
                elif np.linalg.det(W[:3, :3]) < 0:            # a mirroring placement: baked on its own root
                    m = make_mesh(node.name or "mesh", strip_skin(parts), node.materials)
                    for p in m["prims"]:
                        p["attrs"] = _bake(p["attrs"], W)
                    nodes_out.append((m, None))
                else:
                    t["mesh"] = make_mesh(node.name or "mesh", strip_skin(parts), node.materials)
            t["children"] = [tnode(c) for c in node.children]
            return t
        tree = [tnode(r) for r in roots]

        def all_nodes(ns):
            for n in ns:
                yield n
                yield from all_nodes(n.children)
        for node in all_nodes(roots):
            if node.controller:
                for name, chans in controller_clips(assets, node.controller, node):
                    anims.append({"name": name, "channels": chans})
    info = convert.write_gltf(dst, nodes_out, None, tree, anims)
    info["material_notes"] = sorted(set(info.get("material_notes", []) + notes))
    return info


def convert_source(src, out, only=()):
    """Every prefab, scene and model in a .unitypackage or project folder -> <out>/<asset path>.gltf. -> rows."""
    import fnmatch
    assets = Assets(src)
    caches = {"mat": {}, "model": {}}
    rows = []
    for guid, path in assets.of_kind((".prefab", ".unity", ".fbx", ".obj")):
        rel = os.path.relpath(path, assets.root).replace(os.sep, "/")
        if only and not any(fnmatch.fnmatch(rel, g) for g in only):
            continue
        dst = os.path.join(out, os.path.splitext(rel)[0] + ".gltf")
        row = {"id": rel, "gltf_rel": os.path.splitext(rel)[0] + ".gltf", "ok": False}
        try:
            info = convert_asset(assets, guid, dst, caches)
            row.update(ok=info["meshes"] > 0, meshes=info["meshes"], tris=info["tris"], verts=info["verts"],
                       w_m=info["size"][0], d_m=info["size"][1], h_m=info["size"][2], materials=info["materials"],
                       notes="; ".join(info["material_notes"]))
            if not info["meshes"]:
                row["error"] = "no meshes"
            with open(dst) as f:
                import json
                gl = json.load(f)
            row["expect_albedo"] = sum(1 for m in gl.get("meshes", []) for p in m["primitives"]
                                       if "baseColorTexture" in gl["materials"][p["material"]].get("pbrMetallicRoughness", {}))
        except Exception as e:
            row["error"] = "%s: %s" % (type(e).__name__, e)
        rows.append(row)
    return rows
