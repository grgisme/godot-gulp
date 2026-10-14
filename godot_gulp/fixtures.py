"""Small USD stages authored with OpenUSD, one feature each, for the converter's conformance suite.
Each builder writes <dir>/<name>.usda and returns its path. Units are metres unless the fixture is about units."""
import os
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

# a unit cube as 6 quads, outward with right-handed winding (USD's default orientation)
CUBE_PTS = [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
            (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]
CUBE_IDX = [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 2, 3, 7, 6, 1, 2, 6, 5, 0, 4, 7, 3]
CUBE_COUNTS = [4] * 6


def stage(d, name, mpu=1.0, up=UsdGeom.Tokens.y):
    path = os.path.join(d, name + ".usda")
    st = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageMetersPerUnit(st, mpu)
    UsdGeom.SetStageUpAxis(st, up)
    root = UsdGeom.Xform.Define(st, "/Root")
    st.SetDefaultPrim(root.GetPrim())
    return st, path


def cube(st, path="/Root/Cube", pts=CUBE_PTS):
    m = UsdGeom.Mesh.Define(st, path)
    m.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
    m.CreateFaceVertexCountsAttr(CUBE_COUNTS)
    m.CreateFaceVertexIndicesAttr(CUBE_IDX)
    return m


def quad(st, path="/Root/Quad", z=0.0):
    """One quad facing +Z (counter-clockwise seen from +Z)."""
    m = UsdGeom.Mesh.Define(st, path)
    m.CreatePointsAttr([Gf.Vec3f(0, 0, z), Gf.Vec3f(1, 0, z), Gf.Vec3f(1, 1, z), Gf.Vec3f(0, 1, z)])
    m.CreateFaceVertexCountsAttr([4])
    m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    return m


def save(st, path):
    st.GetRootLayer().Save()
    return path


# ---- one feature each

def f_cube(d):
    st, p = stage(d, "cube")
    cube(st)
    return save(st, p)


def f_holes(d):
    st, p = stage(d, "holes")
    cube(st).CreateHoleIndicesAttr([0, 1])          # two faces cut out: 4 quads = 8 tris left
    return save(st, p)


def f_left_handed(d):
    st, p = stage(d, "left_handed")
    m = UsdGeom.Mesh.Define(st, "/Root/Quad")
    m.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 1, 0), Gf.Vec3f(1, 1, 0), Gf.Vec3f(1, 0, 0)])   # clockwise from +Z...
    m.CreateFaceVertexCountsAttr([4])
    m.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    m.CreateOrientationAttr(UsdGeom.Tokens.leftHanded)          # ...which left-handed makes face +Z
    return save(st, p)


def f_mirror(d):
    st, p = stage(d, "mirror")
    x = UsdGeom.Xform.Define(st, "/Root/Flip")
    x.AddScaleOp().Set(Gf.Vec3f(-1, 1, 1))                       # a mirror: the quad must be re-wound to stay front-facing
    quad(st, "/Root/Flip/Quad")
    return save(st, p)


def f_units_zup(d):
    st, p = stage(d, "units_zup", mpu=0.01, up=UsdGeom.Tokens.z)
    m = UsdGeom.Mesh.Define(st, "/Root/Tri")
    m.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(100, 0, 0), Gf.Vec3f(0, 0, 100)])   # 1 m along X, 1 m up (Z) in cm
    m.CreateFaceVertexCountsAttr([3])
    m.CreateFaceVertexIndicesAttr([0, 1, 2])
    return save(st, p)


def f_uv_sets(d):
    st, p = stage(d, "uv_sets")
    m = quad(st)
    api = UsdGeom.PrimvarsAPI(m)
    st0 = api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
    st0.Set(Vt.Vec2fArray([(0, 0), (1, 0), (1, 1)]))                 # indexed: 3 values, 4 corners
    st0.SetIndices(Vt.IntArray([0, 1, 2, 2]))
    st1 = api.CreatePrimvar("lightmapUV", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
    st1.Set(Vt.Vec2fArray([(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)]))
    return save(st, p)


def f_vertex_colour(d):
    st, p = stage(d, "vertex_colour")
    m = quad(st)
    m.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set([Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0), Gf.Vec3f(0, 0, 1), Gf.Vec3f(1, 1, 1)])
    return save(st, p)


def f_subsets(d):
    st, p = stage(d, "subsets")
    m = cube(st)
    UsdGeom.Subset.CreateGeomSubset(m, "top", UsdGeom.Tokens.face, Vt.IntArray([3]), "materialBind")
    UsdGeom.Subset.CreateGeomSubset(m, "sides", UsdGeom.Tokens.face, Vt.IntArray([0, 1, 2, 4, 5]), "materialBind")
    return save(st, p)


def f_normals(d):
    st, p = stage(d, "normals")
    m = quad(st)
    m.CreateNormalsAttr([Gf.Vec3f(0, 0, 1)] * 4)
    m.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    return save(st, p)


def f_instanceable(d):
    """A prototype referenced twice as instanceable: both copies must come out."""
    proto = os.path.join(d, "proto_cube.usda")
    ps = Usd.Stage.CreateNew(proto)
    UsdGeom.SetStageMetersPerUnit(ps, 1.0)
    UsdGeom.SetStageUpAxis(ps, UsdGeom.Tokens.y)
    root = UsdGeom.Xform.Define(ps, "/Proto")
    ps.SetDefaultPrim(root.GetPrim())
    cube(ps, "/Proto/Cube")
    ps.GetRootLayer().Save()
    st, p = stage(d, "instanceable")
    for i, x in enumerate((0.0, 3.0)):
        inst = UsdGeom.Xform.Define(st, "/Root/I%d" % i)
        inst.AddTranslateOp().Set(Gf.Vec3d(x, 0, 0))
        inst.GetPrim().GetReferences().AddReference(proto)
        inst.GetPrim().SetInstanceable(True)
    return save(st, p)


def f_variants(d):
    st, p = stage(d, "variants")
    root = st.GetPrimAtPath("/Root")
    vs = root.GetVariantSets().AddVariantSet("shape")
    for name in ("box", "flat"):
        vs.AddVariant(name)
        vs.SetVariantSelection(name)
        with vs.GetVariantEditContext():
            if name == "box":
                cube(st, "/Root/Shape")
            else:
                quad(st, "/Root/Shape")
    vs.SetVariantSelection("box")
    return save(st, p)


def f_invisible_and_guide(d):
    st, p = stage(d, "invisible_and_guide")
    cube(st, "/Root/Seen")
    cube(st, "/Root/Hidden").CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    cube(st, "/Root/Guide").CreatePurposeAttr(UsdGeom.Tokens.guide)
    return save(st, p)


ALL = [f_cube, f_holes, f_left_handed, f_mirror, f_units_zup, f_uv_sets, f_vertex_colour, f_subsets, f_normals,
       f_instanceable, f_variants, f_invisible_and_guide]


# ---- materials (step b): UsdPreviewSurface networks over small generated textures

from pxr import UsdShade


def png(d, name, size=(8, 8), colour=(255, 255, 255), mode="RGB", fill=None):
    """A small texture; fill(x, y) -> pixel overrides the flat colour."""
    from PIL import Image
    im = Image.new(mode, size, colour)
    if fill:
        for y in range(size[1]):
            for x in range(size[0]):
                im.putpixel((x, y), fill(x, y))
    p = os.path.join(d, name)
    im.save(p)
    return p


def material(st, path="/Root/Mat"):
    m = UsdShade.Material.Define(st, path)
    ps = UsdShade.Shader.Define(st, path + "/PS")
    ps.CreateIdAttr("UsdPreviewSurface")
    m.CreateSurfaceOutput().ConnectToSource(ps.ConnectableAPI(), "surface")
    return m, ps


def texture(st, mat_path, name, file, out="rgb", varname="st", xf=None, wrap="repeat", scale=None):
    """A UsdUVTexture reading <varname> through an optional UsdTransform2d (scale, rotation, translation)."""
    rd = UsdShade.Shader.Define(st, "%s/Reader_%s" % (mat_path, name))
    rd.CreateIdAttr("UsdPrimvarReader_float2")
    rd.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(varname)
    src = rd.CreateOutput("result", Sdf.ValueTypeNames.Float2)
    if xf:
        t2 = UsdShade.Shader.Define(st, "%s/Xf_%s" % (mat_path, name))
        t2.CreateIdAttr("UsdTransform2d")
        t2.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(src)
        t2.CreateInput("scale", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*xf[0]))
        t2.CreateInput("rotation", Sdf.ValueTypeNames.Float).Set(float(xf[1]))
        t2.CreateInput("translation", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*xf[2]))
        src = t2.CreateOutput("result", Sdf.ValueTypeNames.Float2)
    tx = UsdShade.Shader.Define(st, "%s/Tex_%s" % (mat_path, name))
    tx.CreateIdAttr("UsdUVTexture")
    tx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(os.path.basename(file))
    tx.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(src)
    tx.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set(wrap)
    tx.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set(wrap)
    if scale:
        tx.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*scale))
    return tx.CreateOutput(out, Sdf.ValueTypeNames.Float3 if out == "rgb" else Sdf.ValueTypeNames.Float)


def bind(prim, m):
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(m)


def uv_quad(st, path="/Root/Quad"):
    q = quad(st, path)
    pv = UsdGeom.PrimvarsAPI(q).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
    pv.Set(Vt.Vec2fArray([(0, 0), (1, 0), (1, 1), (0, 1)]))
    return q


def f_mat_constants(d):
    st, p = stage(d, "mat_constants")
    q = uv_quad(st)
    m, ps = material(st)
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.8, 0.2, 0.1))
    ps.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.9)
    ps.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.25)
    ps.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(4.0, 2.0, 0.0))      # past 1: a strength
    ps.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.33)
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_diffuse_texture(d):
    st, p = stage(d, "mat_diffuse_texture")
    q = uv_quad(st)
    m, ps = material(st)
    f = png(d, "red.png", colour=(255, 0, 0))
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture(st, "/Root/Mat", "d", f, wrap="mirror", scale=(0.5, 0.5, 0.5, 1)))
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_opacity(d):
    st, p = stage(d, "mat_opacity")
    a = uv_quad(st, "/Root/A")
    b = uv_quad(st, "/Root/B")
    ma, pa = material(st, "/Root/Half")
    pa.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.5)                    # constant: BLEND, alpha 0.5
    bind(a.GetPrim(), ma)
    mb, pb = material(st, "/Root/Cut")
    f = png(d, "diffuse.png", colour=(0, 255, 0))
    o = png(d, "mask.png", mode="L", colour=0, fill=lambda x, y: 255 if x < 4 else 0)
    pb.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture(st, "/Root/Cut", "d", f))
    pb.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(texture(st, "/Root/Cut", "o", o, out="r"))
    pb.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(0.5)          # MASK at 0.5, alpha from the mask
    bind(b.GetPrim(), mb)
    return save(st, p)


def f_mat_orm(d):
    st, p = stage(d, "mat_orm")
    q = uv_quad(st)
    m, ps = material(st)
    r = png(d, "rough.png", mode="L", colour=200)
    mt = png(d, "metal.png", colour=(0, 0, 0), fill=lambda x, y: (0, 0, 60))          # metallic read from blue
    ao = png(d, "ao.png", mode="L", colour=128)
    ps.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(texture(st, "/Root/Mat", "r", r, out="r"))
    ps.CreateInput("metallic", Sdf.ValueTypeNames.Float).ConnectToSource(texture(st, "/Root/Mat", "m", mt, out="b"))
    ps.CreateInput("occlusion", Sdf.ValueTypeNames.Float).ConnectToSource(texture(st, "/Root/Mat", "o", ao, out="r"))
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_normal(d):
    st, p = stage(d, "mat_normal")
    q = uv_quad(st)
    m, ps = material(st)
    n = png(d, "normal.png", colour=(128, 64, 255))
    tx = texture(st, "/Root/Mat", "n", n, scale=(2, -2, 2, 1))                         # a DirectX map: green down
    ps.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(tx)
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_uv_transform(d, name="mat_uv_transform", xf=((2.0, 3.0), 0.0, (0.25, 0.5))):
    st, p = stage(d, name)
    q = uv_quad(st)
    m, ps = material(st)
    f = png(d, "tile.png", colour=(10, 20, 30))
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture(st, "/Root/Mat", "d", f, xf=xf))
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_uv_rotation(d):
    return f_mat_uv_transform(d, "mat_uv_rotation", ((1.0, 1.0), 90.0, (0.0, 0.0)))


def f_mat_two_transforms(d):
    st, p = stage(d, "mat_two_transforms")
    q = uv_quad(st)
    m, ps = material(st)
    f = png(d, "albedo2.png", colour=(90, 90, 90))
    r = png(d, "rough2.png", mode="L", colour=100)
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture(st, "/Root/Mat", "d", f, xf=((4, 4), 0, (0, 0))))
    ps.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(texture(st, "/Root/Mat", "r", r, out="r", xf=((1, 1), 0, (0, 0))))
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_second_uv_set(d):
    st, p = stage(d, "mat_second_uv_set")
    q = uv_quad(st)
    pv = UsdGeom.PrimvarsAPI(q).CreatePrimvar("detailUV", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
    pv.Set(Vt.Vec2fArray([(0, 0), (2, 0), (2, 2), (0, 2)]))
    m, ps = material(st)
    f = png(d, "detail.png", colour=(1, 2, 3))
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture(st, "/Root/Mat", "d", f, varname="detailUV"))
    bind(q.GetPrim(), m)
    return save(st, p)


def f_mat_display_colour(d):
    st, p = stage(d, "mat_display_colour")
    quad(st).CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set([Gf.Vec3f(0.1, 0.6, 0.3)])
    return save(st, p)


def f_mat_subsets(d):
    st, p = stage(d, "mat_subsets")
    c = cube(st)
    top = UsdGeom.Subset.CreateGeomSubset(c, "top", UsdGeom.Tokens.face, Vt.IntArray([3]), "materialBind")
    ma, pa = material(st, "/Root/Red")
    pa.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 0))
    mb, pb = material(st, "/Root/Blue")
    pb.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0, 0, 1))
    bind(top.GetPrim(), ma)
    bind(c.GetPrim(), mb)
    return save(st, p)


MATERIALS = [f_mat_constants, f_mat_diffuse_texture, f_mat_opacity, f_mat_orm, f_mat_normal, f_mat_uv_transform,
             f_mat_uv_rotation, f_mat_two_transforms, f_mat_second_uv_set, f_mat_display_colour, f_mat_subsets]
ALL = ALL + MATERIALS


# ---- instancing (step c)

def f_point_instancer(d):
    """2 prototypes (a cube, a quad), 5 instances placed, scaled and turned; one masked off; one mirrored."""
    st, p = stage(d, "point_instancer")
    pi = UsdGeom.PointInstancer.Define(st, "/Root/Scatter")
    protos = UsdGeom.Scope.Define(st, "/Root/Scatter/Protos")
    cube(st, "/Root/Scatter/Protos/Box")
    quad(st, "/Root/Scatter/Protos/Card")
    pi.CreatePrototypesRel().SetTargets(["/Root/Scatter/Protos/Box", "/Root/Scatter/Protos/Card"])
    pi.CreateProtoIndicesAttr([0, 0, 1, 0, 1])
    pi.CreatePositionsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(2, 0, 0), Gf.Vec3f(4, 0, 0), Gf.Vec3f(6, 0, 0), Gf.Vec3f(8, 0, 0)])
    pi.CreateScalesAttr([Gf.Vec3f(1, 1, 1), Gf.Vec3f(1, 2, 1), Gf.Vec3f(1, 1, 1), Gf.Vec3f(1, 1, 1), Gf.Vec3f(-1, 1, 1)])
    pi.CreateOrientationsAttr([Gf.Quath(1, 0, 0, 0)] * 5)
    pi.CreateInvisibleIdsAttr([3])                                  # the 4th (a box at x 6) is masked off
    return save(st, p)


def f_instanceable_mirror(d):
    """Two instanceable copies of one prototype, the second mirrored: shared once, the mirror baked on its own."""
    proto = os.path.join(d, "proto_quad.usda")
    ps = Usd.Stage.CreateNew(proto)
    UsdGeom.SetStageMetersPerUnit(ps, 1.0)
    UsdGeom.SetStageUpAxis(ps, UsdGeom.Tokens.y)
    r = UsdGeom.Xform.Define(ps, "/P")
    ps.SetDefaultPrim(r.GetPrim())
    quad(ps, "/P/Q")
    ps.GetRootLayer().Save()
    st, p = stage(d, "instanceable_mirror")
    for i, sx in enumerate((1.0, -1.0)):
        inst = UsdGeom.Xform.Define(st, "/Root/I%d" % i)
        inst.AddTranslateOp().Set(Gf.Vec3d(i * 3.0, 0, 0))
        inst.AddScaleOp().Set(Gf.Vec3f(sx, 1, 1))
        inst.GetPrim().GetReferences().AddReference(proto)
        inst.GetPrim().SetInstanceable(True)
    return save(st, p)


def f_mat_sibling_ao(d):
    """A base colour wall_BC.png with an unwired wall_occlusion.png beside it (the exporter dropped the AO input)."""
    st, p = stage(d, "mat_sibling_ao")
    q = uv_quad(st)
    m, ps = material(st)
    f = png(d, "wall_BC.png", colour=(200, 180, 160))
    png(d, "wall_occlusion.png", mode="L", colour=90)
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture(st, "/Root/Mat", "d", f))
    bind(q.GetPrim(), m)
    return save(st, p)


INSTANCING = [f_point_instancer, f_instanceable_mirror, f_mat_sibling_ao]
ALL = ALL + INSTANCING
