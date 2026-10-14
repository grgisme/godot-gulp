"""Tagged properties (every editor-saved object, most UE4 cooked ones): the property tag (the UE 5.4+ type name tree or
the older flat tag), then the value. Each value is bounded by its tag's size, so one value this reader does not
understand comes back as Raw and the next property still lines up.

    read_tagged(pkg, reader, end) -> Props (name -> value; arrays as lists, structs as dicts or tuples)

Values: numbers, str, names (str), object references (Ref), soft paths (str), bool, structs: Vector/Rotator/Quat/
Vector2D/Vector4 as tuples (LWC doubles or floats by size), Color as (r, g, b, a) bytes, LinearColor floats, Guid hex,
material inputs as {"Expression": Ref, "OutputIndex", "InputName", "Mask", ...}, other structs as tagged dicts.
"""
from .package import (UE4_ADDED_SOFT_OBJECT_PATH, UE5_ADD_SOFTOBJECTPATH_LIST, UE5_FSOFTOBJECTPATH_REMOVE_ASSET_PATH_FNAMES,
                      UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME, UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION,
                      UnrealError, Reader)

HAS_ARRAY_INDEX, HAS_PROPERTY_GUID, HAS_EXTENSIONS, HAS_BINARY_OR_NATIVE, BOOL_TRUE, SKIPPED = 1, 2, 4, 8, 16, 32
EXT_OVERRIDABLE = 2


class Raw:
    """A value this reader leaves undecoded: its type and bytes."""
    __slots__ = ("type", "data", "error")

    def __init__(self, type_, data, error=""):
        self.type, self.data, self.error = type_, data, error

    def __repr__(self):
        return "Raw(%s, %d bytes)" % (self.type, len(self.data))


class Ref:
    """An object reference: a package index and the object's path (resolved when the package knows it)."""
    __slots__ = ("index", "path")

    def __init__(self, index, path):
        self.index, self.path = index, path

    def __repr__(self):
        return "Ref(%s)" % (self.path or self.index)

    def __bool__(self):
        return self.index != 0


class Props(dict):
    """name -> value; a property with an array index > 0 is stored as name[index]."""


class Tag:
    __slots__ = ("name", "type", "size", "index", "flags", "struct", "inner", "value_type", "enum", "bool", "params")


def _type_tree(r):
    """The UE 5.4+ FPropertyTypeName: a flattened tree of (name, parameter count)."""
    nodes, remaining = [], 1
    while remaining > 0:
        n = (r.name(), r.i32())
        if n[1] < 0 or n[1] > 64 or len(nodes) > 256:
            raise UnrealError("bad property type name")
        nodes.append(n)
        remaining += n[1] - 1
    return nodes


def _subtree(nodes, start):
    """The index after the subtree that starts at nodes[start]."""
    remaining, i = 1, start
    while remaining:
        remaining += nodes[i][1] - 1
        i += 1
    return i


def _params(nodes, start=0):
    """The parameter subtrees of nodes[start] as lists of nodes."""
    out, i = [], start + 1
    for _ in range(nodes[start][1]):
        j = _subtree(nodes, i)
        out.append(nodes[i:j])
        i = j
    return out


def read_tag(pkg, r):
    name = r.name()
    if name == "None":
        return None
    t = Tag()
    t.name, t.struct, t.inner, t.value_type, t.enum, t.bool, t.params, t.index = name, None, None, None, None, False, [], 0
    if pkg.ue5 >= UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
        nodes = _type_tree(r)
        t.type = nodes[0][0]
        t.params = _params(nodes)
        t.size = r.i32()
        t.flags = r.u8()
        if t.flags & HAS_ARRAY_INDEX:
            t.index = r.i32()
        if t.flags & HAS_PROPERTY_GUID:
            r.raw(16)
        if t.flags & HAS_EXTENSIONS:
            if r.u8() & EXT_OVERRIDABLE:
                r.u8(), r.u8()
        t.bool = bool(t.flags & BOOL_TRUE)
        p0 = t.params[0][0][0] if t.params else None
        if t.type == "StructProperty":
            t.struct = p0
        elif t.type in ("ByteProperty", "EnumProperty"):
            t.enum = p0
        elif t.type in ("ArrayProperty", "SetProperty", "OptionalProperty"):
            t.inner = p0
        elif t.type == "MapProperty":
            t.inner, t.value_type = p0, (t.params[1][0][0] if len(t.params) > 1 else None)
        return t
    t.type, t.size, t.index, t.flags = r.name(), r.i32(), r.i32(), 0
    if t.type == "StructProperty":
        t.struct = r.name()
        r.raw(16)                                         # struct guid
    elif t.type == "BoolProperty":
        t.bool = r.u8() != 0
    elif t.type in ("ByteProperty", "EnumProperty"):
        t.enum = r.name()
    elif t.type in ("ArrayProperty", "SetProperty", "OptionalProperty"):
        t.inner = r.name()
    elif t.type == "MapProperty":
        t.inner, t.value_type = r.name(), r.name()
    if r.u8():                                            # has property guid
        r.raw(16)
    if pkg.ue5 >= UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION:
        if r.u8() & EXT_OVERRIDABLE:
            r.u8(), r.u8()
    return t


def read_tagged(pkg, r, end, top=False):
    """top: an object's own properties (preceded by the class serialization control byte), not a nested struct's."""
    props = Props()
    if top and pkg.ue5 >= UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION:
        if r.u8() & EXT_OVERRIDABLE:                       # the class serialization control byte
            r.u8()
    while r.pos < end:
        t = read_tag(pkg, r)
        if t is None:
            break
        if t.flags & SKIPPED:
            continue
        start = r.pos
        if t.size < 0 or start + t.size > end:
            raise UnrealError("property %s: size %d runs past the object" % (t.name, t.size))
        try:
            v = value(pkg, r, t.type, t, t.size, start + t.size, top=True)
        except (UnrealError, ValueError, IndexError) as e:
            v = Raw(t.type, pkg.data[start:start + t.size], str(e))
        r.pos = start + t.size
        props[t.name if not t.index else "%s[%d]" % (t.name, t.index)] = v
    return props


def soft_path(pkg, r):
    if pkg.ue5 >= UE5_ADD_SOFTOBJECTPATH_LIST and pkg.soft_paths:
        i = r.i32()
        return pkg.soft_paths[i] if 0 <= i < len(pkg.soft_paths) else ""
    if pkg.ue4 < UE4_ADDED_SOFT_OBJECT_PATH:
        return r.fstring()
    if pkg.ue5 >= UE5_FSOFTOBJECTPATH_REMOVE_ASSET_PATH_FNAMES:
        a, b = r.name(), r.name()
        path = a + ("." + b if b != "None" else "")
    else:
        path = r.name()
    sub = r.fstring()
    return path + (":" + sub if sub else "")


def ref(pkg, r):
    i = r.i32()
    return Ref(i, pkg.object_path(i))


def _vec(r, n, size):
    """n components, doubles (UE5 large world coordinates) or floats, chosen by the serialized size."""
    if size == 8 * n:
        return tuple(r.f64() for _ in range(n))
    return tuple(r.f32() for _ in range(n))


def _material_input(pkg, r, end):
    d = {"Expression": ref(pkg, r), "OutputIndex": r.i32(), "InputName": r.name(), "Mask": r.i32(),
         "MaskR": r.i32(), "MaskG": r.i32(), "MaskB": r.i32(), "MaskA": r.i32()}
    if r.pos + 4 <= end:
        d["UseConstant"] = r.i32() != 0
    return d


VEC_STRUCTS = {"Vector": 3, "Rotator": 3, "Quat": 4, "Vector2D": 2, "Vector4": 4, "Plane": 4, "Vector3f": 3,
               "Vector2f": 2, "Vector4f": 4, "Quat4f": 4, "Rotator3f": 3, "Vector3d": 3, "Vector2d": 2,
               "Vector_NetQuantize": 3, "Vector_NetQuantize10": 3, "Vector_NetQuantize100": 3,
               "Vector_NetQuantizeNormal": 3, "DeprecateSlateVector2D": 2}
MATERIAL_INPUTS = ("ExpressionInput", "ColorMaterialInput", "ScalarMaterialInput", "VectorMaterialInput",
                   "Vector2MaterialInput", "MaterialAttributesInput", "ShadingModelMaterialInput",
                   "SubstrateMaterialInput", "StrataMaterialInput")
# structs with a native binary serialiser (no tags inside); the rest are read as tagged property lists
NATIVE = set(VEC_STRUCTS) | set(MATERIAL_INPUTS) | {
    "Box", "Box2D", "Box2f", "Box3f", "Color", "LinearColor", "Guid", "IntPoint", "IntVector", "IntVector2", "IntVector4",
    "Matrix", "Matrix44f", "SoftObjectPath", "SoftClassPath", "SoftAssetPath", "StringAssetReference",
    "StringClassReference", "DateTime", "Timespan", "FrameNumber", "PerPlatformFloat", "PerPlatformInt",
    "PerPlatformBool", "PerPlatformFrameRate", "PerQualityLevelInt", "PerQualityLevelFloat", "FloatRange",
    "RichCurveKey", "TwoVectors", "Int32Point", "Int64Point", "UintPoint", "TopLevelAssetPath", "FrameRate",
    "NavAgentSelector", "SmartName", "FontData", "FontCharacter", "GameplayTagContainer", "InstancedStruct",
    "MovieSceneFloatChannel", "MovieSceneDoubleChannel"}


def movie_scene_channel(r, end, double):
    """FMovieSceneFloat/DoubleChannel (UE 5.x layout; UAssetAPI documents it): extrapolation bytes, the key times
    (FFrameNumber) and values (value, tangents, interp mode) as sized arrays, the default, the tick resolution.
    -> {"times" (frames), "values", "interp" (0 linear, 1 constant, 2 cubic), "default", "tick" (frames per second)}"""
    import numpy as np
    r.u8(), r.u8()
    tsize, n = r.i32(), r.i32()
    times = np.frombuffer(r.raw(tsize * n), dtype="<i4")[::max(1, tsize // 4)] if n else np.zeros(0, np.int32)
    vsize, m = r.i32(), r.i32()
    raw = r.raw(vsize * m)
    vt = "<f8" if double else "<f4"
    vb = 8 if double else 4
    vals = np.array([np.frombuffer(raw, vt, 1, k * vsize)[0] for k in range(m)]) if m else np.zeros(0)
    interp = np.array([raw[k * vsize + vsize - 4] for k in range(m)]) if m and vsize >= vb + 24 else np.zeros(m, np.uint8)
    default = r.f64() if double else r.f32()
    has_default = r.i32() != 0
    num, den = r.i32(), r.i32()
    if r.pos + 4 <= end:
        r.i32()                                           # bShowCurve
    return {"times": times, "values": vals, "interp": interp, "default": default if has_default else None,
            "tick": num / den if den else 0.0}


def struct_value(pkg, r, name, size, end, native=None):
    if name in VEC_STRUCTS:
        return _vec(r, VEC_STRUCTS[name], size if size else (24 if pkg.ue5 >= 1004 and not name.endswith("f") else 12))
    if name == "Color":
        b, g, rr, a = r.u8(), r.u8(), r.u8(), r.u8()
        return (rr, g, b, a)
    if name == "LinearColor":
        return (r.f32(), r.f32(), r.f32(), r.f32())
    if name == "Guid":
        return r.guid()
    if name in ("IntPoint", "Int32Point", "IntVector2"):
        return (r.i32(), r.i32())
    if name == "IntVector":
        return (r.i32(), r.i32(), r.i32())
    if name in ("Box", "Box3f"):
        n = 3
        dbl = size == 49 or (not size and pkg.ue5 >= 1004 and name == "Box")
        mn = tuple((r.f64() if dbl else r.f32()) for _ in range(n))
        mx = tuple((r.f64() if dbl else r.f32()) for _ in range(n))
        return {"Min": mn, "Max": mx, "IsValid": r.u8() != 0}
    if name in ("SoftObjectPath", "SoftClassPath", "SoftAssetPath", "StringAssetReference", "StringClassReference"):
        return soft_path(pkg, r)
    if name == "TopLevelAssetPath":
        a, b = r.name(), r.name()
        return a + ("." + b if b != "None" else "")
    if name in MATERIAL_INPUTS:
        return _material_input(pkg, r, end)
    if name in ("MovieSceneFloatChannel", "MovieSceneDoubleChannel"):
        return movie_scene_channel(r, end, name == "MovieSceneDoubleChannel")
    if name == "PerPlatformFloat":
        return {"Cooked": r.i32() != 0, "Default": r.f32()}
    if name == "PerPlatformInt":
        return {"Cooked": r.i32() != 0, "Default": r.i32()}
    if name == "PerPlatformBool":
        return {"Cooked": r.i32() != 0, "Default": r.u8() != 0}
    if native is False or (native is None and name not in NATIVE):
        return read_tagged(pkg, r, end)
    return Raw(name, r.raw(max(0, end - r.pos)))


def value(pkg, r, type_, t, size, end, top=False, in_container=False):
    if type_ == "BoolProperty":
        return (r.u8() != 0) if in_container else t.bool
    if type_ in ("IntProperty",):
        return r.i32()
    if type_ == "FloatProperty":
        return r.f32()
    if type_ == "DoubleProperty":
        return r.f64()
    if type_ == "Int64Property":
        return r.i64()
    if type_ == "UInt32Property":
        return r.u32()
    if type_ == "UInt64Property":
        return r.u64()
    if type_ == "Int16Property":
        return r.i16()
    if type_ == "UInt16Property":
        return r.u16()
    if type_ == "Int8Property":
        return r.i8()
    if type_ == "StrProperty":
        return r.fstring()
    if type_ == "NameProperty":
        return r.name()
    if type_ == "EnumProperty":
        return r.name()
    if type_ == "ByteProperty":
        if in_container:
            return r.u8() if (t.enum in (None, "None") or size == 1) else r.name()
        return r.u8() if size == 1 else r.name()
    if type_ in ("ObjectProperty", "ClassProperty", "WeakObjectProperty", "InterfaceProperty"):
        return ref(pkg, r)
    if type_ == "LazyObjectProperty":
        return r.guid()
    if type_ in ("SoftObjectProperty", "SoftClassProperty"):
        return soft_path(pkg, r)
    if type_ == "StructProperty":
        native = None
        if top and pkg.ue5 >= UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
            native = bool(t.flags & HAS_BINARY_OR_NATIVE)
        return struct_value(pkg, r, t.struct, size if top else 0, end, native)
    if type_ in ("ArrayProperty", "SetProperty"):
        if type_ == "SetProperty":
            r.i32()                                       # elements to remove
        n = r.i32()
        if n < 0 or n > max(0, end - r.pos):
            raise UnrealError("array count %d" % n)
        inner = t.inner
        if inner == "StructProperty" and pkg.ue5 < UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME and type_ == "ArrayProperty":
            it = read_tag(pkg, r)                         # the inner struct's tag, once for the array
            sname, esize = it.struct, (it.size // n if n else 0)
        else:
            sname = _inner_struct(t)
            esize = (end - r.pos) // n if n else 0
        out = []
        for _ in range(n):
            if inner == "StructProperty":
                out.append(struct_value(pkg, r, sname, esize if sname in VEC_STRUCTS or sname in ("Box", "Box3f") else 0, end))
            else:
                sub = Tag()
                sub.type, sub.enum, sub.struct, sub.inner, sub.bool, sub.flags = inner, t.enum, None, None, False, 0
                out.append(value(pkg, r, inner, sub, esize, end, in_container=True))
        return out
    if type_ == "MapProperty":
        r.i32()                                           # keys to remove (empty in saved assets)
        n = r.i32()
        if n < 0 or n > max(0, end - r.pos):
            raise UnrealError("map count %d" % n)
        out = []
        kt, vt = Tag(), Tag()
        for x, ty in ((kt, t.inner), (vt, t.value_type)):
            x.type, x.enum, x.struct, x.inner, x.bool, x.flags = ty, None, None, None, False, 0
        ks, vs = _inner_struct(t, 0), _inner_struct(t, 1)
        for _ in range(n):
            k = struct_value(pkg, r, ks, 0, end) if t.inner == "StructProperty" and ks else value(pkg, r, t.inner, kt, 0, end, in_container=True)
            v = struct_value(pkg, r, vs, 0, end) if t.value_type == "StructProperty" and vs else value(pkg, r, t.value_type, vt, 0, end, in_container=True)
            out.append((k, v))
        return out
    if type_ == "TextProperty":
        return text(pkg, r, end)
    return Raw(type_, r.raw(max(0, end - r.pos)))


def _inner_struct(t, which=0):
    """The struct name of an array/set/map element from the UE 5.4+ type tree (None for older tags)."""
    if len(t.params) <= which:
        return None
    sub = t.params[which]
    if sub and sub[0][0] == "StructProperty" and sub[0][1] >= 1:
        return sub[1][0]
    return None


def text(pkg, r, end):
    """FText: the source string of a base (localised) text, else the raw bytes."""
    flags, kind = r.u32(), r.i8()
    if kind == 0:                                         # base: namespace, key, source string
        r.fstring(), r.fstring()
        return r.fstring()
    if kind == -1:                                        # none: maybe a culture-invariant string
        if r.pos + 4 <= end and r.i32():
            return r.fstring()
        return ""
    return Raw("TextProperty", r.raw(max(0, end - r.pos)))
