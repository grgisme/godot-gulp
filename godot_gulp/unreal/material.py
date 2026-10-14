"""Unreal materials to the converter's material model (materials.Mat), read from the editor graph, not guessed from
parameter names.

    Content(mounts, tex_dir).material(object_path) -> Mat     e.g. "/Game/Pack/Materials/MI_Brick.MI_Brick"

A MaterialInstanceConstant walks its Parent chain to the base Material, collecting scalar, vector and texture
parameter overrides (the nearest instance wins). The base Material's inputs (on its MaterialEditorOnlyData export in
UE 5.1+, on the Material itself before) are evaluated back through the graph:

- constants and parameters (Scalar, Vector, Constant, Constant2/3/4Vector, StaticBool), texture samples and texture
  parameters (TextureSample, TextureSampleParameter2D, TextureObject(Parameter) into a sample), TextureCoordinate
  tiling, Multiply, Add, Subtract, OneMinus, Lerp with a constant alpha, ComponentMask, AppendVector of constants,
  Reroute and named reroutes, and the input's own channel mask (packed ORM/MRA maps keep their channel)
- anything else (material functions, world-aligned or procedural nodes) is noted and the input left at its default

Unreal's UVs share glTF's top-left origin (no flip); its normal maps are DirectX (green down) and are flipped.
"""
import os

import numpy as np

from .. import materials as mt
from . import texture as tx
from .package import Package, UnrealError

INPUTS = ("BaseColor", "Metallic", "Specular", "Roughness", "Normal", "EmissiveColor", "Opacity", "OpacityMask",
          "AmbientOcclusion")
TEXTURE_SAMPLES = {"MaterialExpressionTextureSample", "MaterialExpressionTextureSampleParameter2D",
                   "MaterialExpressionTextureSampleParameter", "MaterialExpressionTextureSampleParameterSubUV"}
CHANNELS = {0: "rgb", 1: "r", 2: "g", 3: "b", 4: "a", 5: "rgba"}


class Const:
    """A constant of n components (1 to 4), held as four (a scalar repeated)."""
    def __init__(self, v):
        v = [float(x) for x in v] if isinstance(v, (list, tuple)) else [float(v)]
        self.n = max(1, min(4, len(v)))
        self.v = [v[0]] * 4 if len(v) == 1 else (v + [0.0, 0.0, 0.0, 1.0][len(v):])[:4]


class TexV:
    """A texture sample: its file, channel, UV transform and a constant factor applied to it."""
    def __init__(self, path, channel, xf, factor=(1.0, 1.0, 1.0, 1.0), normal=False, srgb=True, source=""):
        self.path, self.channel, self.xf, self.factor = path, channel, xf, list(factor)
        self.normal, self.srgb, self.source = normal, srgb, source


def _static_switches(props):
    """A material instance's static switch overrides -> [(name, bool)] (StaticParameters or the 5.x runtime set)."""
    out = []
    for key in ("StaticParameters", "StaticParametersRuntime", "EditorOnlyStaticParameters"):
        sp = props.get(key)
        if not isinstance(sp, dict):
            continue
        for s in sp.get("StaticSwitchParameters") or []:
            if isinstance(s, dict) and s.get("bOverride", True):
                n = (s.get("ParameterInfo") or {}).get("Name")
                if n:
                    out.append((n, bool(s.get("Value"))))
    return out


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


class Content:
    """Mount points (/Game -> a Content folder, /Engine, /<Plugin>) and a cache of decoded textures."""

    def __init__(self, mounts, tex_cache):
        self.mounts = dict(mounts)
        self.tex_cache = tex_cache
        self._pkgs = {}

    def file_of(self, object_path):
        pkg = object_path.split(".")[0].split(":")[0]
        for mount, root in sorted(self.mounts.items(), key=lambda kv: -len(kv[0])):
            if pkg == mount or pkg.startswith(mount + "/"):
                rel = pkg[len(mount):].lstrip("/")
                for ext in (".uasset", ".umap"):
                    p = os.path.join(root, rel + ext)
                    if os.path.isfile(p):
                        return p
        return ""

    def package(self, object_path):
        f = self.file_of(object_path)
        if not f:
            raise UnrealError("%s not found under the mounts" % object_path.split(".")[0])
        if f not in self._pkgs:
            self._pkgs[f] = Package.open(f)
        return self._pkgs[f]

    def export_of(self, pkg, object_path):
        name = object_path.split(".")[-1].split(":")[0]
        for i, e in enumerate(pkg.exports):
            if e.name == name and not e.outer:
                return i
        for i, e in enumerate(pkg.exports):
            if e.name == name:
                return i
        raise UnrealError("%s: no export %s" % (pkg.path, name))

    def texture_file(self, object_path):
        """The texture's source art as a PNG in the cache -> (path, info)."""
        pkg = self.package(object_path)
        i = self.export_of(pkg, object_path)
        st = os.stat(pkg.path)
        key = "%s_%x_%x" % (object_path.split(".")[-1], st.st_size, int(st.st_mtime))
        out = os.path.join(self.tex_cache, key + ".png")
        props, _r, _e = pkg.props(i)
        info = {"srgb": props.get("SRGB", True), "settings": str(props.get("CompressionSettings") or "").split("::")[-1]}
        if not os.path.isfile(out):
            im, _ = tx.source_image(pkg, i)
            if im.size[0] < 1 or im.size[1] < 1:
                raise UnrealError("texture %s is empty" % object_path)
            os.makedirs(self.tex_cache, exist_ok=True)
            tmp = "%s.%d.tmp" % (out, os.getpid())
            im.save(tmp, "PNG", compress_level=3)
            os.replace(tmp, out)
        return out, info

    # ---- materials

    def material(self, object_path):
        pkg = self.package(object_path)
        i = self.export_of(pkg, object_path)
        name = pkg.exports[i].name
        m = mt.Mat(name)
        params = {"scalar": {}, "vector": {}, "texture": {}, "static": {}}
        overrides = {}
        seen = 0
        while pkg.exports[i].class_name.startswith("MaterialInstance") and seen < 16:
            props, _r, _e = pkg.props(i)
            for kind, key in (("scalar", "ScalarParameterValues"), ("vector", "VectorParameterValues"),
                              ("texture", "TextureParameterValues")):
                for p in props.get(key) or []:
                    pn = ((p.get("ParameterInfo") or {}).get("Name") or p.get("ParameterName") or "") if isinstance(p, dict) else ""
                    if pn and pn not in params[kind]:
                        params[kind][pn] = p.get("ParameterValue")
            for sp in _static_switches(props):
                params["static"].setdefault(sp[0], sp[1])
            for k, v in (props.get("BasePropertyOverrides") or {}).items():
                overrides.setdefault(k, v)
            parent = props.get("Parent")
            if not parent:
                m.notes.append("material instance without a parent")
                return m
            ppath = parent if isinstance(parent, str) else parent.path
            if ppath.startswith("/"):
                pkg = self.package(ppath)
                i = self.export_of(pkg, ppath)
            else:
                i = self.export_of(pkg, ppath)
            seen += 1
        if pkg.exports[i].class_name != "Material":
            m.notes.append("base %s is a %s: not read" % (pkg.exports[i].name, pkg.exports[i].class_name))
            return m
        self._from_material(pkg, i, m, params, overrides)
        return m

    def _from_material(self, pkg, i, m, params, overrides):
        props, _r, _e = pkg.props(i)
        inputs = dict(props)
        eod = props.get("EditorOnlyData")
        if eod and eod.index > 0:                            # UE 5.1+: the inputs live on MaterialEditorOnlyData
            inputs.update(pkg.props(eod.index - 1)[0])
        ev = _Eval(self, pkg, params, m.notes)
        blend = str(overrides.get("BlendMode") or props.get("BlendMode") or "BLEND_Opaque").split("::")[-1]
        if overrides.get("TwoSided", props.get("TwoSided")):
            m.double_sided = True

        def get(name):
            inp = inputs.get(name)
            if not isinstance(inp, dict) or not inp.get("Expression"):
                return None
            v = ev.input(inp)
            if v is None:
                m.notes.append("%s: not evaluated (see the nodes above)" % name)
            return v

        bc = get("BaseColor")
        if isinstance(bc, Const):
            m.base = [bc.v[0], bc.v[1], bc.v[2], 1.0]
        elif isinstance(bc, TexV):
            m.tex["diffuse"] = self._tex(bc, "rgb" if bc.channel in ("rgb", "rgba") else bc.channel)
        for name, slot, attr in (("Metallic", "metallic", "metallic"), ("Roughness", "roughness", "roughness"),
                                 ("AmbientOcclusion", "occlusion", None)):
            v = get(name)
            if isinstance(v, Const) and attr:
                setattr(m, attr, float(np.clip(v.v[0], 0, 1)))
            elif isinstance(v, TexV):
                ch = v.channel if v.channel in ("r", "g", "b", "a") else "r"
                m.tex[slot] = self._tex(v, ch, scalar=True)
                if attr:
                    setattr(m, attr, 1.0)
        n = get("Normal")
        if isinstance(n, TexV):
            t = self._tex(n, "rgb")
            t.scale = (1.0, -1.0, 1.0, 1.0)                  # DirectX normal map: green down
            t.colour_space = "raw"
            m.tex["normal"] = t
        e = get("EmissiveColor")
        if isinstance(e, Const):
            m.emissive = [max(0.0, x) for x in e.v[:3]]
        elif isinstance(e, TexV):
            m.tex["emissive"] = self._tex(e, "rgb")
            m.emissive = [1.0, 1.0, 1.0]
        if blend == "BLEND_Masked":
            o = get("OpacityMask")
            m.cutoff = _f(overrides.get("OpacityMaskClipValue", props.get("OpacityMaskClipValue", 0.3333)), 0.3333)
            if isinstance(o, TexV):
                m.tex["opacity"] = self._tex(o, o.channel if o.channel in ("r", "g", "b", "a") else "a", scalar=True)
        elif blend in ("BLEND_Translucent", "BLEND_Additive", "BLEND_Modulate", "BLEND_AlphaComposite"):
            o = get("Opacity")
            if isinstance(o, Const):
                m.opacity = float(np.clip(o.v[0], 0, 1))
            elif isinstance(o, TexV):
                m.tex["opacity"] = self._tex(o, o.channel if o.channel in ("r", "g", "b", "a") else "a", scalar=True)
            if blend != "BLEND_Translucent":
                m.notes.append("blend mode %s shown as alpha blend" % blend)
        return m

    def _tex(self, v, channel, scalar=False):
        f = v.factor
        scale = (f[0], f[0], f[0], 1.0) if scalar else (f[0], f[1], f[2], f[3])
        t = mt.Tex(v.path, channel, 0, v.xf, 10497, 10497, scale, (0.0, 0.0, 0.0, 0.0), "auto", v.source)
        t.rel = None
        return t


class _Eval:
    def __init__(self, content, pkg, params, notes, bound=None, depth=0):
        self.c, self.pkg, self.params, self.notes = content, pkg, params, notes
        self.bound = bound or {}                       # a material function's inputs: input Id -> value
        self.depth = depth

    def note(self, s):
        if s not in self.notes:
            self.notes.append(s)

    def input(self, inp):
        """An FExpressionInput dict -> Const / TexV / None, its channel mask applied."""
        ref = inp.get("Expression")
        if not ref or ref.index <= 0:
            return None
        v = self.node(ref.index - 1, int(inp.get("OutputIndex", 0) or 0))
        if v is not None and inp.get("Mask"):
            chans = "".join(c for c, k in zip("rgba", ("MaskR", "MaskG", "MaskB", "MaskA")) if inp.get(k))
            v = _mask(v, chans)
        return v

    def node(self, i, out=0):
        self.depth += 1
        try:
            if self.depth > 64:
                raise UnrealError("material graph too deep")
            return self._node(i, out)
        finally:
            self.depth -= 1

    def _call(self, p, out, name):
        return _call_impl(self, p, out, name)

    def _in(self, props, key, default=None):
        inp = props.get(key)
        if isinstance(inp, dict) and inp.get("Expression"):
            return self.input(inp)
        return default

    def _node(self, i, out):
        e = self.pkg.exports[i]
        cls = e.class_name.replace("MaterialExpression", "")
        p, _r, _e = self.pkg.props(i)
        pname = p.get("ParameterName")
        if cls in ("ScalarParameter", "StaticBoolParameter"):
            return Const(_f(self.params["scalar"].get(pname, p.get("DefaultValue", 0.0))))
        if cls == "Constant":
            return Const(_f(p.get("R", 0.0)))
        if cls == "StaticBool":
            return Const(1.0 if p.get("Value") else 0.0)
        if cls == "FunctionOutput":
            return self._in(p, "A")
        if cls in ("VectorParameter", "DoubleVectorParameter"):
            v = self.params["vector"].get(pname, p.get("DefaultValue", (0.0, 0.0, 0.0, 1.0)))
            v = Const(list(v))
            return _mask(v, CHANNELS.get(out, "rgba")) if out else v
        if cls in ("Constant3Vector", "Constant4Vector"):
            v = Const(list(p.get("Constant", (0.0, 0.0, 0.0, 1.0))))
            return _mask(v, CHANNELS.get(out, "rgba")) if out else v
        if cls == "Constant2Vector":
            return Const([_f(p.get("R")), _f(p.get("G")), 0.0, 1.0])
        if cls in ("Reroute",):
            return self._in(p, "Input")
        if cls in ("FeatureLevelSwitch", "QualitySwitch", "ShadingPathSwitch", "ShaderStageSwitch"):
            return self._in(p, "Default")
        if cls in ("StaticSwitchParameter", "StaticSwitch"):
            on = self.params["static"].get(pname, p.get("DefaultValue", False)) if pname else p.get("DefaultValue", False)
            if cls == "StaticSwitch" and isinstance(p.get("Value"), dict) and p["Value"].get("Expression"):
                v = self._in(p, "Value")
                on = bool(v.v[0]) if isinstance(v, Const) else on
            return self._in(p, "A" if on else "B")
        if cls == "FunctionInput":
            fid = p.get("Id")
            if fid in self.bound:
                return self.bound[fid]
            return self._in(p, "Preview") if p.get("bUsePreviewValueAsDefault") or "Preview" in p else None
        if cls == "MaterialFunctionCall":
            return self._call(p, out, e.name)
        if cls == "VertexColor":
            self.note("%s: vertex colour input not carried into the material" % e.name)
            return None
        if cls == "Divide":
            a = self._in(p, "A", Const(_f(p.get("ConstA", 1.0), 1.0)))
            b = self._in(p, "B", Const(_f(p.get("ConstB", 1.0), 1.0)))
            if isinstance(b, Const) and all(abs(x) > 1e-9 for x in b.v):
                inv = Const([1.0 / x for x in b.v])
                inv.n = b.n
                return _arith("Multiply", a, inv, self, e.name)
            self.note("%s: division by a texture not representable" % e.name)
            return a
        if cls == "ConstantBiasScale":
            a = self._in(p, "Input")
            if isinstance(a, Const):
                bias, scale = _f(p.get("Bias", 0.0)), _f(p.get("Scale", 1.0), 1.0)
                return Const([(x + bias) * scale for x in a.v])
            if isinstance(a, TexV) and abs(_f(p.get("Bias", 0.0))) < 1e-6:
                return _arith("Multiply", a, Const(_f(p.get("Scale", 1.0), 1.0)), self, e.name)
            self.note("%s: bias on a texture not representable" % e.name)
            return a
        if cls == "NamedRerouteUsage":
            d = p.get("Declaration")
            return self.node(d.index - 1, 0) if d and d.index > 0 else None
        if cls == "NamedRerouteDeclaration":
            return self._in(p, "Input")
        if cls in {c.replace("MaterialExpression", "") for c in TEXTURE_SAMPLES}:
            tex = p.get("Texture")
            tpath = tex.path if tex else ""
            if pname and pname in self.params["texture"]:
                o = self.params["texture"][pname]
                tpath = o.path if hasattr(o, "path") else (o or tpath)
            obj = self._in(p, "TextureObject")
            if isinstance(obj, str):
                tpath = obj
            if not tpath:
                self.note("%s: no texture" % e.name)
                return None
            xf = np.eye(3)
            uv = self._in(p, "Coordinates")
            if isinstance(uv, Const) and getattr(uv, "uv", False):
                xf = uv.xf
            elif uv is not None:
                self.note("%s: UVs from a graph the converter does not follow: plain UV0" % e.name)
            try:
                path, info = self.c.texture_file(tpath)
            except (UnrealError, OSError) as ex:
                self.note("texture %s: %s" % (tpath, ex))
                return None
            st = str(p.get("SamplerType") or "").split("::")[-1]
            return TexV(path, CHANNELS.get(out, "rgb"), xf, normal=st == "SAMPLERTYPE_Normal" or info["settings"] == "TC_Normalmap",
                        srgb=info["srgb"], source="unreal:" + tpath)
        if cls in ("TextureObject", "TextureObjectParameter"):
            tex = p.get("Texture")
            tpath = tex.path if tex else ""
            if pname and pname in self.params["texture"]:
                o = self.params["texture"][pname]
                tpath = o.path if hasattr(o, "path") else (o or tpath)
            return tpath or None
        if cls == "TextureCoordinate":
            c = Const([0.0, 0.0, 0.0, 1.0])
            c.uv, c.xf = True, np.diag([_f(p.get("UTiling", 1.0), 1.0), _f(p.get("VTiling", 1.0), 1.0), 1.0])
            if int(p.get("CoordinateIndex", 0) or 0):
                self.note("%s: UV%d used as UV0" % (e.name, p.get("CoordinateIndex")))
            return c
        if cls in ("Multiply", "Add", "Subtract"):
            a = self._in(p, "A", Const(_f(p.get("ConstA", 0.0 if cls != "Multiply" else 1.0))))
            b = self._in(p, "B", Const(_f(p.get("ConstB", 1.0 if cls == "Multiply" else 1.0))))
            return _arith(cls, a, b, self, e.name)
        if cls == "OneMinus":
            a = self._in(p, "Input")
            if isinstance(a, Const):
                return Const([1.0 - x for x in a.v[:3]] + [a.v[3]])
            self.note("%s: 1 - texture not representable: the texture kept as is" % e.name)
            return a
        if cls == "LinearInterpolate":
            a = self._in(p, "A", Const(_f(p.get("ConstA", 0.0))))
            b = self._in(p, "B", Const(_f(p.get("ConstB", 1.0))))
            t = self._in(p, "Alpha", Const(_f(p.get("ConstAlpha", 0.5))))
            if isinstance(t, Const):
                if isinstance(a, Const) and isinstance(b, Const):
                    k = t.v[0]
                    return Const([x * (1 - k) + y * k for x, y in zip(a.v, b.v)])
                return b if t.v[0] >= 0.5 else a
            self.note("%s: lerp by a texture: its A input kept" % e.name)
            return a if a is not None else b
        if cls == "ComponentMask":
            a = self._in(p, "Input")
            chans = "".join(c for c, k in zip("rgba", "RGBA") if p.get(k))
            return _mask(a, chans) if a is not None else None
        if cls == "AppendVector":
            a, b = self._in(p, "A"), self._in(p, "B")
            if isinstance(a, Const) and isinstance(b, Const):
                return Const((a.v[:max(1, a.n)] + b.v[:max(1, b.n)] + [0.0, 0.0, 0.0, 1.0])[:4])
            self.note("%s: append of textures not representable" % e.name)
            return None
        if cls in ("Saturate", "Clamp", "Desaturation", "Power", "SquareRoot", "Abs"):
            a = self._in(p, "Input") or self._in(p, "Base")
            if a is not None and cls not in ("Saturate", "Clamp"):
                self.note("%s (%s) skipped: its input used as is" % (e.name, cls))
            return a
        self.note("%s: node %s not evaluated" % (e.name, cls))
        return None


def _call_impl(ev, p, out, name):
    """A MaterialFunctionCall: the callee's FunctionOutput (by the call's output index) evaluated in the function's
    package, its FunctionInputs bound to the caller's values."""
    fn = p.get("MaterialFunction")
    if not fn or not getattr(fn, "path", ""):
        ev.note("%s: no material function" % name)
        return None
    outs = p.get("FunctionOutputs") or []
    if out >= len(outs):
        ev.note("%s: output %d of %d" % (name, out, len(outs)))
        return None
    want = outs[out].get("ExpressionOutputId")
    bound = {}
    for fi in p.get("FunctionInputs") or []:
        inp = fi.get("Input") or {}
        if inp.get("Expression"):
            bound[fi.get("ExpressionInputId")] = ev.input(inp)
    try:
        fpkg = ev.c.package(fn.path)
    except UnrealError as ex:
        ev.note("%s: %s" % (name, ex))
        return None
    sub = _Eval(ev.c, fpkg, ev.params, ev.notes, bound, ev.depth)
    for j, e in enumerate(fpkg.exports):
        if e.class_name == "MaterialExpressionFunctionOutput":
            fp = fpkg.props(j)[0]
            if fp.get("Id") == want:
                return sub._in(fp, "A")
    ev.note("%s: output %s not found in %s" % (name, want, fn.path))
    return None


def _mask(v, chans):
    if not chans:
        return v
    if isinstance(v, Const):
        idx = ["rgba".index(c) for c in chans]
        return Const([v.v[k] for k in idx])
    if isinstance(v, TexV):
        if len(chans) == 1:
            return TexV(v.path, chans if v.channel in ("rgb", "rgba") else v.channel, v.xf, v.factor, v.normal, v.srgb, v.source)
        return v
    return v


def _arith(op, a, b, ev, name):
    if a is None or b is None:
        return a if b is None else b
    if isinstance(a, Const) and isinstance(b, Const):
        if getattr(a, "uv", False) or getattr(b, "uv", False):     # UV * tiling: scale the UV transform
            uv, k = (a, b) if getattr(a, "uv", False) else (b, a)
            if op == "Multiply":
                c = Const(uv.v)
                c.uv, c.xf = True, uv.xf @ np.diag([k.v[0], k.v[1] if k.n > 1 else k.v[0], 1.0])
                return c
            if op == "Add":
                c = Const(uv.v)
                off = np.eye(3)
                off[0, 2], off[1, 2] = k.v[0], (k.v[1] if k.n > 1 else k.v[0])
                c.uv, c.xf = True, off @ uv.xf
                return c
        f = {"Multiply": lambda x, y: x * y, "Add": lambda x, y: x + y, "Subtract": lambda x, y: x - y}[op]
        out = Const([f(x, y) for x, y in zip(a.v, b.v)])
        out.n = max(a.n, b.n)
        return out
    t, k = (a, b) if isinstance(a, TexV) else (b, a)
    if op == "Multiply" and isinstance(t, TexV) and isinstance(k, Const):
        fac = [x * (k.v[0] if k.n == 1 else y) for x, y in zip(t.factor, k.v)]
        return TexV(t.path, t.channel, t.xf, fac, t.normal, t.srgb, t.source)
    if isinstance(t, TexV) and isinstance(k, Const) and op in ("Add", "Subtract") and all(abs(x) < 1e-6 for x in k.v[:3]):
        return t
    ev.note("%s: %s of two textures (or a texture offset) not representable: the first kept" % (name, op))
    return t if isinstance(t, TexV) else a
