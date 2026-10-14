"""UsdPreviewSurface to glTF PBR for usd2gltf.py. Ported in part from migueldeicaza/godot-usd's
src/usd_materials.cpp (MIT, Copyright (c) 2026 Miguel de Icaza): the shader walk, the opacity-into-albedo
composite, the IOR to F0 rule. Better than it where glTF allows: rotation and per-texture transforms are kept (baked
into the UVs, or KHR_texture_transform), metallic/roughness/occlusion are packed into one ORM map, emissive above 1
keeps its energy (KHR_materials_emissive_strength), and every texture names its own UV set.

    describe(material_prim, uv_names) -> Mat      what the material needs: factors, texture slots, transforms
    Mat.bake_uv                                   the 3x3 UV transform (glTF space) to apply to its primitive's UVs
"""
import hashlib, math, os, re
import numpy as np
from pxr import Sdf, Usd, UsdShade

WRAP = {"repeat": 10497, "mirror": 33648, "clamp": 33071, "black": 33071, "useMetadata": 10497}
FLIP = np.array([[1, 0, 0], [0, -1, 1], [0, 0, 1]], dtype=np.float64)        # (u, v) -> (u, 1 - v), USD <-> glTF


class Tex:
    """One UsdUVTexture read: its file, channel, UV set and transform (glTF space)."""
    def __init__(self, path, channel, texcoord, xf, wrap_s, wrap_t, scale, bias, colour_space, source, invert=False, mul=1.0):
        self.path, self.channel, self.texcoord, self.xf = path, channel, texcoord, xf
        self.invert, self.mul = invert, mul          # a band read as 1 - value * mul (Unity smoothness to roughness)
        self.wrap_s, self.wrap_t, self.scale, self.bias, self.colour_space, self.source = wrap_s, wrap_t, scale, bias, colour_space, source


class Mat:
    def __init__(self, name):
        self.name = name
        self.base = [1.0, 1.0, 1.0, 1.0]
        self.metallic, self.roughness = 0.0, 0.5
        self.emissive = [0.0, 0.0, 0.0]
        self.opacity, self.cutoff = 1.0, None
        self.ior = None
        self.tex = {}                 # slot -> Tex: diffuse, opacity, metallic, roughness, occlusion, normal, emissive
        self.notes = []
        self.bake_uv = None           # 3x3: the transform baked into the primitive's UVs (one set), or None
        self.bake_texcoord = 0

    def key(self):
        return (self.name, tuple(sorted((k, t.texcoord) for k, t in self.tex.items())))


# ---- the UsdTransform2d, in glTF's UV space

def usd_transform2d(scale, rotation_deg, translation):
    """UsdTransform2d's matrix in USD's UV space: scale, then rotate counter-clockwise, then translate."""
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    S = np.diag([scale[0], scale[1], 1.0])
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    T = np.array([[1, 0, translation[0]], [0, 1, translation[1]], [0, 0, 1]])
    return T @ R @ S


def to_gltf_space(m_usd):
    """The same mapping with glTF's top-left origin on both sides (our UVs are written V-flipped)."""
    return FLIP @ m_usd @ FLIP


def khr_texture_transform(m):
    """Decompose a glTF-space affine into KHR_texture_transform (offset, rotation, scale): M = T * R(r) * S with
    R(r) = [[cos r, sin r], [-sin r, cos r]] as the extension defines it."""
    L = m[:2, :2]
    sx = math.hypot(L[0, 0], L[1, 0])
    r = math.atan2(-L[1, 0], L[0, 0])
    c, s = math.cos(r), math.sin(r)
    sy = (L[0, 1] * s + L[1, 1] * c)                    # R^T applied to the second column
    if np.linalg.det(L) < 0 and sy > 0:
        sy = -sy
    return {"offset": [float(m[0, 2]), float(m[1, 2])], "rotation": float(r), "scale": [float(sx), float(sy)]}


def apply_uv(m, uv):
    return (np.c_[uv, np.ones(len(uv))] @ m.T)[:, :2].astype(np.float32)


# ---- walking the shader graph

def _source(inp):   # ported from godot-usd (MIT): get_connected_shader, following interface inputs
    """(shader, output name) an input reads from, following interface connections; None for a constant."""
    if not inp or not inp.HasConnectedSource():
        return None
    srcs = inp.GetConnectedSources()[0] if hasattr(inp, "GetConnectedSources") else []
    for info in srcs:
        prim = info.source.GetPrim()
        sh = UsdShade.Shader(prim)
        if sh:
            return sh, str(info.sourceName)
        mat = UsdShade.Material(prim)                       # a material interface input: follow it on
        if mat:
            nxt = mat.GetInput(info.sourceName)
            if nxt:
                return _source(nxt)
    return None


def _value(inp, default):
    """The input's own value, or the material interface value it is connected to."""
    if not inp:
        return default
    v = inp.Get()
    if v is None and inp.HasConnectedSource():
        for info in inp.GetConnectedSources()[0]:
            mat = UsdShade.Material(info.source.GetPrim())
            if mat and mat.GetInput(info.sourceName):
                v = mat.GetInput(info.sourceName).Get()
    return default if v is None else v


def _id(sh):
    v = sh.GetIdAttr().Get() if sh else None
    return str(v) if v else ""


def _uv_reader(sh_st, uv_names):
    """Follow a texture's st input: (texcoord index, transform in glTF space, note)."""
    xf = np.eye(3)
    note = ""
    src = _source(sh_st)
    if src and _id(src[0]) == "UsdTransform2d":
        t = src[0]
        xf = to_gltf_space(usd_transform2d(_value(t.GetInput("scale"), (1.0, 1.0)), float(_value(t.GetInput("rotation"), 0.0)),
                                           _value(t.GetInput("translation"), (0.0, 0.0))))
        src = _source(t.GetInput("in"))
    name = "st"
    if src and _id(src[0]).startswith("UsdPrimvarReader"):
        name = str(_value(src[0].GetInput("varname"), "st"))
    if name not in uv_names:
        note = "UV set %s not on the mesh: TEXCOORD_0 used" % name
        return 0, xf, note
    return uv_names.index(name), xf, note


RESOLVER = None      # (material name, slot, authored path) -> a file, when the authored one does not resolve (pack quirks)
SIBLING_AO = True    # an X_occlusion / X_AO file beside the base colour X_BC is the occlusion map when the graph has none
                     # (UsdPreviewSurface exporters often drop AO: a pack can ship many such maps, none wired)
_BASE_RE = re.compile(r"_(BC|basecolor|base_color|albedo|diffuse)(\.[A-Za-z]+)$", re.I)


def sibling_ao(base_path):
    m = _BASE_RE.search(base_path)
    if not m:
        return ""
    stem = base_path[:m.start()]
    for suffix in ("_occlusion", "_Occlusion", "_AO", "_ao"):
        for ext in (m.group(2), ".png", ".jpg"):
            if os.path.isfile(stem + suffix + ext):
                return stem + suffix + ext
    return ""


def _texture(inp, uv_names, slot, notes, mat_name=""):
    src = _source(inp)
    if not src:
        return None
    sh, out = src
    if _id(sh) != "UsdUVTexture":
        notes.append("%s: %s source not supported" % (slot, _id(sh) or "unknown"))
        return None
    f = sh.GetInput("file")
    asset = f.Get() if f else None
    path = asset.resolvedPath if isinstance(asset, Sdf.AssetPath) else ""
    if not path and RESOLVER:
        path = RESOLVER(mat_name, slot, getattr(asset, "path", "") or "") or ""
        if path:
            notes.append("%s: resolved by name: %s" % (slot, os.path.basename(path)))
    if not path:
        notes.append("%s: texture %s does not resolve" % (slot, getattr(asset, "path", asset)))
        return None
    tc, xf, n = _uv_reader(sh.GetInput("st"), uv_names)
    if n:
        notes.append("%s: %s" % (slot, n))
    return Tex(path, out, tc, xf, WRAP.get(str(_value(sh.GetInput("wrapS"), "useMetadata")), 10497),
               WRAP.get(str(_value(sh.GetInput("wrapT"), "useMetadata")), 10497),
               tuple(_value(sh.GetInput("scale"), (1.0, 1.0, 1.0, 1.0))), tuple(_value(sh.GetInput("bias"), (0.0, 0.0, 0.0, 0.0))),
               str(_value(sh.GetInput("sourceColorSpace"), "auto")), str(sh.GetPath()))


def surface_of(material):
    out = material.GetSurfaceOutput()
    src = _source(out) if out else None
    if not src:
        for o in material.GetOutputs():                 # a renderContext-specific surface
            if o.GetBaseName() == "surface":
                src = _source(o)
                if src:
                    break
    return src[0] if src else None


def describe(material_prim, uv_names, display_colour=None):
    """A Mat for a UsdShade.Material prim (None: the default material, tinted by a constant displayColor)."""
    m = Mat(material_prim.GetName() if material_prim else "default")
    if display_colour is not None:
        m.base[:3] = [float(x) for x in display_colour]
    if not material_prim:
        return m
    ps = surface_of(UsdShade.Material(material_prim))
    if ps is None or _id(ps) != "UsdPreviewSurface":
        m.notes.append("surface %s is not a UsdPreviewSurface: base colour only" % (_id(ps) if ps else "missing"))
        return m
    g = ps.GetInput
    m.base[:3] = [float(x) for x in _value(g("diffuseColor"), (0.18, 0.18, 0.18))]
    m.metallic = float(_value(g("metallic"), 0.0))
    m.roughness = float(_value(g("roughness"), 0.5))
    m.emissive = [float(x) for x in _value(g("emissiveColor"), (0.0, 0.0, 0.0))]
    m.opacity = float(_value(g("opacity"), 1.0))
    th = float(_value(g("opacityThreshold"), 0.0))
    m.cutoff = th if th > 0 else None
    if g("ior") and g("ior").GetAttr().HasAuthoredValue():
        m.ior = float(_value(g("ior"), 1.5))
    if int(_value(g("useSpecularWorkflow"), 0)):
        m.notes.append("specular workflow: approximated as metallic 0 (glTF has no UsdPreviewSurface specularColor)")
        m.metallic = 0.0
    for slot, name in (("diffuse", "diffuseColor"), ("opacity", "opacity"), ("metallic", "metallic"), ("roughness", "roughness"),
                       ("occlusion", "occlusion"), ("normal", "normal"), ("emissive", "emissiveColor")):
        t = _texture(g(name), uv_names, slot, m.notes, m.name)
        if t:
            m.tex[slot] = t
    d = m.tex.get("diffuse")
    if SIBLING_AO and d is not None and "occlusion" not in m.tex:
        ao = sibling_ao(d.path)
        if ao:
            m.tex["occlusion"] = Tex(ao, "r", d.texcoord, d.xf, d.wrap_s, d.wrap_t, (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0),
                                     "raw", d.source + " (sibling AO)")
            m.notes.append("occlusion: sibling map by name: %s" % os.path.basename(ao))
    # the transform baked into the UVs: the base colour's (else the first texture's); the rest keep theirs as
    # KHR_texture_transform relative to it, and say so when they differ (Godot's material has one UV transform)
    lead = m.tex.get("diffuse") or next(iter(m.tex.values()), None)
    if lead is not None:
        m.bake_uv, m.bake_texcoord = lead.xf, lead.texcoord
        for slot, t in m.tex.items():
            # on the baked set its transform is relative to the bake; on another set (left as authored) it is its own
            rel = np.linalg.inv(lead.xf) @ t.xf if t.texcoord == lead.texcoord else t.xf
            t.rel = None if np.allclose(rel, np.eye(3), atol=1e-6) else rel
            if t.rel is not None:
                m.notes.append("%s: its own UV transform or set, kept as KHR_texture_transform (one UV transform in Godot)" % slot)
    return m


# ---- images

_KEYS = {}


def file_key(path):
    """Content hash of a source file, once per process per (path, size, mtime): one 15 MB 4K map shared by 40 meshes
    is read once, and two identical copies in two pack folders still dedupe to one texture."""
    st = os.stat(path)
    k = (path, st.st_size, st.st_mtime)
    if k not in _KEYS:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _KEYS[k] = h.hexdigest()[:12]
    return _KEYS[k]


def spec_key(*parts):
    """A built image's name from what it is built from (sources by content, channels, the operation): built once per
    pack, and skipped without decoding anything when it already exists."""
    flat = [file_key(p) if isinstance(p, str) and os.path.isfile(p) else repr(p) for p in parts]
    return hashlib.sha1("|".join(flat).encode()).hexdigest()[:12]


CHANNEL = {"r": 0, "g": 1, "b": 2, "a": 3}


class GltfMaterials:
    """Writes Mats into a glTF dict: images copied (or built: ORM, alpha) into <out dir>/textures, deduped by content."""

    def __init__(self, gl, out_dir, tex_dir=None):
        from PIL import Image          # noqa: F401  (fail early when Pillow is missing)
        self.gl, self.dir = gl, tex_dir or os.path.join(out_dir, "textures")     # one folder a pack shares, or beside the gltf
        self.rel = os.path.relpath(self.dir, out_dir).replace(os.sep, "/")
        self.sources = set()          # every source file used: the cache's dependencies
        self.by_key, self.images, self.samplers = {}, {}, {}
        gl.setdefault("images", []), gl.setdefault("textures", []), gl.setdefault("samplers", []), gl.setdefault("materials", [])

    # images
    def _register(self, name):
        if name not in self.images:
            self.gl["images"].append({"uri": "%s/%s" % (self.rel, name)})
            self.images[name] = len(self.gl["images"]) - 1
        return self.images[name]

    def _built(self, key, stem, make):
        """An image made by make() -> PIL image, named by its inputs: made once, reused when the file exists. Written
        to a temp file and renamed, so parallel workers never see half a PNG."""
        name = "%s_%s.png" % (key, stem)
        dst = os.path.join(self.dir, name)
        if not os.path.exists(dst):
            os.makedirs(self.dir, exist_ok=True)
            tmp = "%s.%d.tmp" % (dst, os.getpid())
            make().save(tmp, "PNG", compress_level=1)          # Godot recompresses on import: speed over size here
            os.replace(tmp, dst)
        return self._register(name)

    def _copy(self, path):
        """A source texture as is (png/jpg), else converted to png."""
        self.sources.add(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            from PIL import Image
            return self._built(spec_key("png", path), os.path.splitext(os.path.basename(path))[0], lambda: Image.open(path))
        name = "%s_%s" % (file_key(path), os.path.basename(path))
        dst = os.path.join(self.dir, name)
        if not os.path.exists(dst):
            import shutil
            os.makedirs(self.dir, exist_ok=True)
            tmp = "%s.%d.tmp" % (dst, os.getpid())
            shutil.copyfile(path, tmp)
            os.replace(tmp, dst)
        return self._register(name)

    def _sampler(self, t):
        k = (t.wrap_s, t.wrap_t)
        if k not in self.samplers:
            self.gl["samplers"].append({"magFilter": 9729, "minFilter": 9987, "wrapS": t.wrap_s, "wrapT": t.wrap_t})
            self.samplers[k] = len(self.gl["samplers"]) - 1
        return self.samplers[k]

    def _texinfo(self, image, t, extra=None):
        self.gl["textures"].append({"source": image, "sampler": self._sampler(t)})
        info = {"index": len(self.gl["textures"]) - 1, "texCoord": t.texcoord}
        if getattr(t, "rel", None) is not None:
            info.setdefault("extensions", {})["KHR_texture_transform"] = dict(khr_texture_transform(t.rel), texCoord=t.texcoord)
            self._used("KHR_texture_transform")
        if extra:
            info.update(extra)
        return info

    def _used(self, ext):
        u = self.gl.setdefault("extensionsUsed", [])
        if ext not in u:
            u.append(ext)

    def _band(self, t):
        """A texture's channel as one 8-bit band, with Unity's smoothness rule applied when asked (1 - value * mul)."""
        b = self._chan(t.path, t.channel)
        if getattr(t, "mul", 1.0) != 1.0:
            m = t.mul
            b = b.point(lambda v: int(round(min(255, max(0, v * m)))))
        if getattr(t, "invert", False):
            b = b.point(lambda v: 255 - v)
        return b

    def _chan(self, path, out):
        from PIL import Image
        self.sources.add(path)
        im = Image.open(path)
        bands = im.getbands()
        if out in CHANNEL and len(bands) > CHANNEL[out]:
            return im.getchannel(CHANNEL[out])
        return im.convert("L")

    # materials
    def index(self, m):
        k = m.key()
        if k in self.by_key:
            return self.by_key[k]
        from PIL import Image
        pbr = {"baseColorFactor": [float(x) for x in m.base[:3]] + [float(m.opacity)]}
        mat = {"name": m.name, "pbrMetallicRoughness": pbr}
        d, o = m.tex.get("diffuse"), m.tex.get("opacity")
        if o is not None:
            # ported from godot-usd (MIT): make_opacity_composited_albedo; glTF has no opacity map
            def alpha():
                base = Image.open(d.path).convert("RGBA") if d else Image.new("RGBA", Image.open(o.path).size, (255, 255, 255, 255))
                a = self._chan(o.path, o.channel)
                base.putalpha(a if a.size == base.size else a.resize(base.size, Image.BILINEAR))
                return base
            self.sources.update(t.path for t in (d, o) if t)
            key = spec_key("alpha", d.path if d else None, o.path, o.channel)
            pbr["baseColorTexture"] = self._texinfo(self._built(key, "albedo_alpha", alpha), d or o)
            pbr["baseColorFactor"][3] = 1.0
        elif d is not None:
            pbr["baseColorTexture"] = self._texinfo(self._copy(d.path), d)
        if d is not None:
            pbr["baseColorFactor"][:3] = [float(x) for x in d.scale[:3]]   # a connected input ignores its constant
            if any(abs(b) > 1e-6 for b in d.bias[:3]):
                m.notes.append("diffuse: texture bias %s not representable in glTF: dropped" % (d.bias[:3],))
        if getattr(m, "double_sided", False):
            mat["doubleSided"] = True
        if m.cutoff is not None:
            mat["alphaMode"], mat["alphaCutoff"] = "MASK", float(m.cutoff)
        elif o is not None or m.opacity < 1.0:
            mat["alphaMode"] = "BLEND"
        # metallic, roughness, occlusion: one ORM map (R occlusion, G roughness, B metallic) when any is a texture
        mt, rt, ot = m.tex.get("metallic"), m.tex.get("roughness"), m.tex.get("occlusion")
        pbr["metallicFactor"], pbr["roughnessFactor"] = float(m.metallic), float(m.roughness)
        if mt or rt or ot:
            def orm():
                srcs = [t for t in (ot, rt, mt) if t]
                size = max((Image.open(t.path).size for t in srcs), key=lambda s: s[0] * s[1])
                def band(t, fill):
                    if not t:
                        return Image.new("L", size, fill)
                    b = self._band(t)
                    return b if b.size == size else b.resize(size, Image.BILINEAR)
                return Image.merge("RGB", (band(ot, 255), band(rt, 255), band(mt, 255)))
            self.sources.update(t.path for t in (ot, rt, mt) if t)
            key = spec_key("orm", *[(t.path, t.channel, getattr(t, "invert", False), getattr(t, "mul", 1.0)) if t else None
                                    for t in (ot, rt, mt)])
            img = self._built(key, "orm", orm)
            lead = rt or mt or ot
            if mt or rt:
                pbr["metallicRoughnessTexture"] = self._texinfo(img, lead)
                if mt:
                    pbr["metallicFactor"] = float(mt.scale[0])
                if rt:
                    pbr["roughnessFactor"] = float(rt.scale[0])
            if ot:
                mat["occlusionTexture"] = self._texinfo(img, ot)
        n = m.tex.get("normal")
        if n is not None:
            flip_g = n.scale[1] < 0                              # a DirectX (green down) map: flip to glTF's green up
            if flip_g:
                def flipped():
                    r, g, b = Image.open(n.path).convert("RGB").split()
                    return Image.merge("RGB", (r, g.point(lambda v: 255 - v), b))
                m.notes.append("normal: green flipped (DirectX map) to glTF's convention")
                self.sources.add(n.path)
                idx = self._built(spec_key("normal_flip_g", n.path), "normal", flipped)
            else:
                idx = self._copy(n.path)
            strength = abs(float(n.scale[0])) / 2.0 if n.scale[0] else 1.0     # UsdUVTexture scale 2, bias -1 is unit strength
            mat["normalTexture"] = self._texinfo(idx, n, {"scale": strength} if abs(strength - 1.0) > 1e-3 else None)
        e = m.tex.get("emissive")
        em = [float(x) for x in (e.scale[:3] if e else m.emissive)]
        peak = max(em) if em else 0.0
        if e is not None and peak > 0:      # a zero factor: no texture either (Godot's importer adds the texture regardless)
            mat["emissiveTexture"] = self._texinfo(self._copy(e.path), e)
        if peak > 0:
            if peak > 1.0:                                        # glTF's factor stops at 1: the rest is the strength
                mat["emissiveFactor"] = [x / peak for x in em]
                mat.setdefault("extensions", {})["KHR_materials_emissive_strength"] = {"emissiveStrength": peak}
                self._used("KHR_materials_emissive_strength")
            else:
                mat["emissiveFactor"] = em
        if m.ior is not None and abs(m.ior - 1.5) > 1e-3:
            mat.setdefault("extensions", {})["KHR_materials_ior"] = {"ior": m.ior}
            self._used("KHR_materials_ior")
        if m.notes:
            mat["extras"] = {"usd_notes": m.notes}
        self.gl["materials"].append(mat)
        self.by_key[k] = len(self.gl["materials"]) - 1
        return self.by_key[k]
