"""Synthetic Unity assets for the conformance suite, written as a project folder or a .unitypackage. Every file is
generated here (Unity YAML by hand, an OBJ, small PNGs): no third-party asset is used.

    project(dir) -> the Assets folder path ; package(dir) -> a .unitypackage path ; GUIDS: name -> guid
"""
import hashlib, io, os, tarfile

from PIL import Image

HEAD = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"


def guid(name):
    return hashlib.md5(name.encode()).hexdigest()


GUIDS = {n: guid(n) for n in ("albedo.png", "metalgloss.png", "mask.png", "normal.png", "quad.obj", "Std.mat", "Urp.mat",
                              "Hdrp.mat", "Thing.prefab", "Outer.prefab", "Mirror.prefab", "QuadMesh.asset",
                              "Skinned.prefab", "Cube.prefab", "SkinMesh.asset", "Rig.prefab", "Lod.prefab", "Ground.asset", "Grass.terrainlayer",
                              "World.unity", "grass.png", "Rig.controller", "Lift.anim",
                              "Field.asset", "Dirt.terrainlayer", "dirt.png", "Field.unity")}
ROOT_TF, CHILD_TF, ROOT_GO, CHILD_GO = 400000, 400001, 100000, 100001


def _png(path, mode, size, fill):
    im = Image.new(mode, size)
    im.putdata([fill(x, y) for y in range(size[1]) for x in range(size[0])])
    im.save(path)


def _mat(name, texs, floats, colors):
    t = "".join("    - %s:\n        m_Texture: {fileID: 2800000, guid: %s, type: 3}\n        m_Scale: {x: %s, y: %s}\n"
                "        m_Offset: {x: %s, y: %s}\n" % (k, GUIDS[f], s[0], s[1], o[0], o[1]) for k, (f, s, o) in texs.items())
    fl = "".join("    - %s: %s\n" % (k, v) for k, v in floats.items())
    co = "".join("    - %s: {r: %s, g: %s, b: %s, a: %s}\n" % ((k,) + tuple(v)) for k, v in colors.items())
    return (HEAD + "--- !u!21 &2100000\nMaterial:\n  serializedVersion: 8\n  m_Name: %s\n  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}\n"
            "  m_ShaderKeywords: _EMISSION\n  m_SavedProperties:\n    serializedVersion: 3\n    m_TexEnvs:\n%s    m_Floats:\n%s    m_Colors:\n%s"
            % (name, t, fl, co))


def _prefab(rot=(0, 0.70710678, 0, 0.70710678), scale=(1, 1, 1), mat="Std.mat"):
    """Root (rotated) -> child Quad at local (1, 0, 0) with the OBJ and one material."""
    return HEAD + """--- !u!1 &{rg}
GameObject:
  m_Component:
  - component: {{fileID: {rt}}}
  m_Name: Root
--- !u!4 &{rt}
Transform:
  m_GameObject: {{fileID: {rg}}}
  m_LocalRotation: {{x: {q0}, y: {q1}, z: {q2}, w: {q3}}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Children:
  - {{fileID: {ct}}}
  m_Father: {{fileID: 0}}
--- !u!1 &{cg}
GameObject:
  m_Component:
  - component: {{fileID: {ct}}}
  m_Name: Quad
--- !u!4 &{ct}
Transform:
  m_GameObject: {{fileID: {cg}}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 1, y: 0, z: 0}}
  m_LocalScale: {{x: {s0}, y: {s1}, z: {s2}}}
  m_Children: []
  m_Father: {{fileID: {rt}}}
--- !u!33 &500001
MeshFilter:
  m_GameObject: {{fileID: {cg}}}
  m_Mesh: {{fileID: 4300000, guid: {mesh}, type: 3}}
--- !u!23 &600001
MeshRenderer:
  m_GameObject: {{fileID: {cg}}}
  m_Materials:
  - {{fileID: 2100000, guid: {mat}, type: 2}}
""".format(rg=ROOT_GO, rt=ROOT_TF, cg=CHILD_GO, ct=CHILD_TF, q0=rot[0], q1=rot[1], q2=rot[2], q3=rot[3],
           s0=scale[0], s1=scale[1], s2=scale[2], mesh=GUIDS["quad.obj"], mat=GUIDS[mat])


OUTER = HEAD + """--- !u!1001 &700000
PrefabInstance:
  m_Modification:
    m_TransformParent: {{fileID: 0}}
    m_Modifications:
    - target: {{fileID: {rt}, guid: {thing}, type: 3}}
      propertyPath: m_LocalPosition.x
      value: 5
      objectReference: {{fileID: 0}}
    - target: {{fileID: {rg}, guid: {thing}, type: 3}}
      propertyPath: m_Name
      value: Moved
      objectReference: {{fileID: 0}}
  m_SourcePrefab: {{fileID: 100100000, guid: {thing}, type: 3}}
"""


def _mesh_asset():
    """A Unity Mesh .asset (text): a quad in Unity space at x 2..3 facing -Z (Unity's front), float32 position,
    normal and uv0 in one stream, 16-bit indices wound as Unity does (clockwise seen from the front)."""
    import struct
    P = [(2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)]
    UV = [(0, 0), (1, 0), (1, 1), (0, 1)]
    vb = b"".join(struct.pack("<3f3f2f", *p, 0, 0, -1, *uv) for p, uv in zip(P, UV))
    ib = struct.pack("<6H", 0, 2, 1, 0, 3, 2)          # clockwise from -Z: Unity's front face
    chans = "".join("    - stream: 0\n      offset: %d\n      format: 0\n      dimension: %d\n" % (o, d) if d else
                    "    - stream: 0\n      offset: 0\n      format: 0\n      dimension: 0\n"
                    for o, d in ((0, 3), (12, 3), (0, 0), (0, 0), (24, 2)) + ((0, 0),) * 9)
    return HEAD + ("--- !u!43 &4300000\nMesh:\n  m_Name: QuadMesh\n  m_SubMeshes:\n  - serializedVersion: 2\n    firstByte: 0\n"
                   "    indexCount: 6\n    topology: 0\n    baseVertex: 0\n    firstVertex: 0\n    vertexCount: 4\n"
                   "  m_IndexFormat: 0\n  m_IndexBuffer: %s\n  m_VertexData:\n    serializedVersion: 3\n    m_VertexCount: 4\n"
                   "    m_Channels:\n%s    m_DataSize: %d\n    _typelessdata: %s\n" % (ib.hex(), chans, len(vb), vb.hex()))


def _skin_asset():
    """The quad again (x 2..3, y 0..1) with a skin: the bottom two vertices on bone 0 (bound at the origin), the top
    two on bone 1 (bound at y 1), weights and indices in vertex channels 12 and 13, and the two bind poses."""
    import struct
    P = [(2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)]
    UV = [(0, 0), (1, 0), (1, 1), (0, 1)]
    bone = [0, 0, 1, 1]
    vb = b"".join(struct.pack("<3f3f2f4f4I", *p, 0, 0, -1, *uv, 1, 0, 0, 0, b, 0, 0, 0) for p, uv, b in zip(P, UV, bone))
    ib = struct.pack("<6H", 0, 2, 1, 0, 3, 2)
    spec = {0: (0, 0, 3), 1: (12, 0, 3), 4: (24, 0, 2), 12: (32, 0, 4), 13: (48, 6, 4)}
    chans = "".join("    - stream: 0\n      offset: %d\n      format: %d\n      dimension: %d\n" % spec.get(i, (0, 0, 0)) for i in range(14))
    def mat(ty):
        m = [[1, 0, 0, 0], [0, 1, 0, -ty], [0, 0, 1, 0], [0, 0, 0, 1]]
        return "  - " + ", ".join("e%d%d: %s" % (r, c, m[r][c]) for r in range(4) for c in range(4)).replace(", ", "\n    ") + "\n"
    return HEAD + ("--- !u!43 &4300000\nMesh:\n  m_Name: SkinMesh\n  m_SubMeshes:\n  - firstByte: 0\n    indexCount: 6\n"
                   "    baseVertex: 0\n  m_BindPose:\n%s%s  m_IndexFormat: 0\n  m_IndexBuffer: %s\n  m_VertexData:\n"
                   "    m_VertexCount: 4\n    m_Channels:\n%s    _typelessdata: %s\n" % (mat(0), mat(1), ib.hex(), chans, vb.hex()))


def _terrain_data():
    """A 3x3 heightmap (2 m between samples, 10 m high at full scale): the centre sample at half height, the rest 0;
    one terrain layer."""
    import struct
    h = [0, 0, 0, 0, 16383, 0, 0, 0, 0]
    return HEAD + ("--- !u!156 &15600000\nTerrainData:\n  m_Name: Ground\n  m_SplatDatabase:\n    m_TerrainLayers:\n"
                   "    - {fileID: 11400000, guid: %s, type: 2}\n  m_Heightmap:\n    m_Heights: %s\n    m_Resolution: 3\n"
                   "    m_Scale: {x: 2, y: 10, z: 2}\n" % (GUIDS["Grass.terrainlayer"], struct.pack("<9h", *h).hex()))


def _terrain_layer(name="Grass", tex="grass.png"):
    return HEAD + ("--- !u!1953259897 &8574412962073106934\nTerrainLayer:\n  m_Name: %s\n"
                   "  m_DiffuseTexture: {fileID: 2800000, guid: %s, type: 3}\n  m_TileSize: {x: 2, y: 2}\n"
                   "  m_TileOffset: {x: 0, y: 0}\n" % (name, GUIDS[tex]))


def _field_data():
    """A flat 3x3 terrain (4 m square) with two layers, Grass and Dirt, and a 2x2 RGBA32 alphamap (a sub-asset of the
    same file): the x = 0 column all Grass (R), the x = 1 column all Dirt (G)."""
    import struct
    alpha = bytes([255, 0, 0, 0, 0, 255, 0, 0] * 2)
    return HEAD + ("--- !u!156 &15600000\nTerrainData:\n  m_Name: Field\n  m_SplatDatabase:\n    m_TerrainLayers:\n"
                   "    - {fileID: 11400000, guid: %s, type: 2}\n    - {fileID: 11400000, guid: %s, type: 2}\n"
                   "    m_AlphaTextures:\n    - {fileID: 2800000}\n    m_AlphamapResolution: 2\n"
                   "  m_Heightmap:\n    m_Heights: %s\n    m_Resolution: 3\n    m_Scale: {x: 2, y: 10, z: 2}\n"
                   "--- !u!28 &2800000\nTexture2D:\n  m_Name: SplatAlpha 0\n  m_Width: 2\n  m_Height: 2\n"
                   "  m_TextureFormat: 4\n  m_MipCount: 1\n  image data: 16\n  _typelessdata: %s\n"
                   % (GUIDS["Grass.terrainlayer"], GUIDS["Dirt.terrainlayer"], struct.pack("<9h", *[0] * 9).hex(), alpha.hex()))


def _world(data="Ground.asset"):
    """A scene with a Terrain at Unity x 10."""
    return HEAD + """--- !u!1 &100000
GameObject:
  m_Name: Terrain
--- !u!4 &400000
Transform:
  m_GameObject: {fileID: 100000}
  m_LocalPosition: {x: 10, y: 0, z: 0}
  m_Father: {fileID: 0}
--- !u!218 &700000
Terrain:
  m_GameObject: {fileID: 100000}
  m_TerrainData: {fileID: 15600000, guid: %s, type: 2}
""" % GUIDS[data]


def _lod():
    """A root with a LODGroup over two children: LOD0 the built-in Cube, LOD1 the Quad."""
    return HEAD + """--- !u!1 &100000
GameObject:
  m_Name: Lod
--- !u!4 &400000
Transform:
  m_GameObject: {fileID: 100000}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_Father: {fileID: 0}
--- !u!205 &900000
LODGroup:
  m_GameObject: {fileID: 100000}
  m_LODs:
  - screenRelativeHeight: 0.5
    renderers:
    - renderer: {fileID: 600001}
  - screenRelativeHeight: 0.1
    renderers:
    - renderer: {fileID: 600002}
--- !u!1 &100001
GameObject:
  m_Name: High
--- !u!4 &400001
Transform:
  m_GameObject: {fileID: 100001}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_Father: {fileID: 400000}
--- !u!33 &500001
MeshFilter:
  m_GameObject: {fileID: 100001}
  m_Mesh: {fileID: 10202, guid: 0000000000000000e000000000000000, type: 0}
--- !u!23 &600001
MeshRenderer:
  m_GameObject: {fileID: 100001}
  m_Materials: []
--- !u!1 &100002
GameObject:
  m_Name: Low
--- !u!4 &400002
Transform:
  m_GameObject: {fileID: 100002}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_Father: {fileID: 400000}
--- !u!33 &500002
MeshFilter:
  m_GameObject: {fileID: 100002}
  m_Mesh: {fileID: 10210, guid: 0000000000000000e000000000000000, type: 0}
--- !u!23 &600002
MeshRenderer:
  m_GameObject: {fileID: 100002}
  m_Materials: []
"""


CONTROLLER = HEAD + """--- !u!91 &9100000
AnimatorController:
  m_Name: Rig
--- !u!1102 &110200000
AnimatorState:
  m_Name: Lift
  m_Motion: {{fileID: 7400000, guid: {clip}, type: 2}}
"""

CLIP = HEAD + """--- !u!74 &7400000
AnimationClip:
  m_Name: Lift
  m_PositionCurves:
  - curve:
      m_Curve:
      - time: 0
        value: {x: 0, y: 2, z: 0}
        inSlope: {x: 0, y: 0, z: 0}
        outSlope: {x: 0, y: 0, z: 0}
      - time: 1
        value: {x: 0, y: 3, z: 0}
        inSlope: {x: 0, y: 0, z: 0}
        outSlope: {x: 0, y: 0, z: 0}
    path: Bone0/Bone1
  m_EulerCurves:
  - curve:
      m_Curve:
      - time: 0
        value: {x: 0, y: 0, z: 0}
        inSlope: {x: 0, y: 90, z: 0}
        outSlope: {x: 0, y: 90, z: 0}
      - time: 1
        value: {x: 0, y: 90, z: 0}
        inSlope: {x: 0, y: 90, z: 0}
        outSlope: {x: 0, y: 90, z: 0}
    path: Bone0
"""

RIG = HEAD + """--- !u!1 &100000
GameObject:
  m_Name: Rig
--- !u!4 &400000
Transform:
  m_GameObject: {{fileID: 100000}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Father: {{fileID: 0}}
--- !u!1 &100001
GameObject:
  m_Name: Bone0
--- !u!4 &400001
Transform:
  m_GameObject: {{fileID: 100001}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Father: {{fileID: 400000}}
--- !u!1 &100002
GameObject:
  m_Name: Bone1
--- !u!4 &400002
Transform:
  m_GameObject: {{fileID: 100002}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 2, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Father: {{fileID: 400001}}
--- !u!95 &950000
Animator:
  m_GameObject: {{fileID: 100000}}
  m_Controller: {{fileID: 9100000, guid: {ctl}, type: 2}}
--- !u!137 &500000
SkinnedMeshRenderer:
  m_GameObject: {{fileID: 100000}}
  m_Mesh: {{fileID: 4300000, guid: {mesh}, type: 2}}
  m_Bones:
  - {{fileID: 400001}}
  - {{fileID: 400002}}
  m_Materials:
  - {{fileID: 2100000, guid: {mat}, type: 2}}
"""


def _single(name, kind, mesh_ref, mat="Std.mat"):
    """One GameObject at the origin with a renderer of `kind` on mesh_ref ({fileID, guid})."""
    return HEAD + """--- !u!1 &100000
GameObject:
  m_Component:
  - component: {{fileID: 400000}}
  m_Name: {name}
--- !u!4 &400000
Transform:
  m_GameObject: {{fileID: 100000}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Children: []
  m_Father: {{fileID: 0}}
""".format(name=name) + ("""--- !u!137 &500000
SkinnedMeshRenderer:
  m_GameObject: {{fileID: 100000}}
  m_Mesh: {mesh}
  m_Materials:
  - {{fileID: 2100000, guid: {mat}, type: 2}}
""" if kind == "skinned" else """--- !u!33 &500000
MeshFilter:
  m_GameObject: {{fileID: 100000}}
  m_Mesh: {mesh}
--- !u!23 &600000
MeshRenderer:
  m_GameObject: {{fileID: 100000}}
  m_Materials:
  - {{fileID: 2100000, guid: {mat}, type: 2}}
""").format(mesh=mesh_ref, mat=GUIDS[mat])


def _write(files, d):
    """files: rel path -> bytes|str ; each gets a .meta with its guid (models a ModelImporter)."""
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if callable(data):
            data(p)
        else:
            open(p, "wb").write(data.encode() if isinstance(data, str) else data)
        name = os.path.basename(rel)
        meta = "fileFormatVersion: 2\nguid: %s\n" % GUIDS[name]
        if name.endswith(".obj"):
            meta += "ModelImporter:\n  meshes:\n    globalScale: 1\n  internalIDToNameTable: []\n"
        open(p + ".meta", "w").write(meta)


def files():
    obj = ("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\n"
           "usemtl Std\nf 1/1/1 2/2/1 3/3/1 4/4/1\n")
    return {
        "Assets/Tex/albedo.png": lambda p: _png(p, "RGBA", (8, 8), lambda x, y: (200, 100, 50, 255 if x < 4 else 0)),
        "Assets/Tex/metalgloss.png": lambda p: _png(p, "RGBA", (8, 8), lambda x, y: (128, 0, 0, 200)),
        "Assets/Tex/mask.png": lambda p: _png(p, "RGBA", (8, 8), lambda x, y: (50, 180, 0, 100)),
        "Assets/Tex/normal.png": lambda p: _png(p, "RGB", (8, 8), lambda x, y: (128, 128, 255)),
        "Assets/Models/quad.obj": obj,
        "Assets/Materials/Std.mat": _mat("Std", {"_MainTex": ("albedo.png", (2, 3), (0.25, 0.5)),
                                                 "_MetallicGlossMap": ("metalgloss.png", (2, 3), (0.25, 0.5)),
                                                 "_BumpMap": ("normal.png", (1, 1), (0, 0))},
                                         {"_GlossMapScale": 0.5, "_Mode": 1, "_Cutoff": 0.4, "_BumpScale": 1},
                                         {"_Color": (1, 0.5, 0.25, 1), "_EmissionColor": (0, 0, 0, 1)}),
        "Assets/Materials/Urp.mat": _mat("Urp", {"_BaseMap": ("albedo.png", (1, 1), (0, 0))},
                                         {"_Metallic": 0.7, "_Smoothness": 0.25, "_Surface": 1},
                                         {"_BaseColor": (1, 1, 1, 0.5)}),
        "Assets/Materials/Hdrp.mat": _mat("Hdrp", {"_BaseColorMap": ("albedo.png", (1, 1), (0, 0)),
                                                   "_MaskMap": ("mask.png", (1, 1), (0, 0)),
                                                   "_NormalMap": ("normal.png", (1, 1), (0, 0))},
                                          {"_Smoothness": 0.5}, {"_BaseColor": (1, 1, 1, 1)}),
        "Assets/Prefabs/Thing.prefab": _prefab(),
        "Assets/Prefabs/Outer.prefab": OUTER.format(rt=ROOT_TF, rg=ROOT_GO, thing=GUIDS["Thing.prefab"]),
        "Assets/Prefabs/Mirror.prefab": _prefab(rot=(0, 0, 0, 1), scale=(-1, 1, 1), mat="Urp.mat"),
        "Assets/Models/QuadMesh.asset": _mesh_asset(),
        "Assets/Prefabs/Skinned.prefab": _single("Skinned", "skinned", "{fileID: 4300000, guid: %s, type: 2}" % GUIDS["QuadMesh.asset"]),
        "Assets/Prefabs/Cube.prefab": _single("Cube", "filter", "{fileID: 10202, guid: 0000000000000000e000000000000000, type: 0}"),
        "Assets/Models/SkinMesh.asset": _skin_asset(),
        "Assets/Prefabs/Rig.prefab": RIG.format(mesh=GUIDS["SkinMesh.asset"], mat=GUIDS["Std.mat"], ctl=GUIDS["Rig.controller"]),
        "Assets/Animation/Rig.controller": CONTROLLER.format(clip=GUIDS["Lift.anim"]),
        "Assets/Animation/Lift.anim": CLIP,
        "Assets/Prefabs/Lod.prefab": _lod(),
        "Assets/Tex/grass.png": lambda p: _png(p, "RGB", (8, 8), lambda x, y: (60, 140, 40)),
        "Assets/Terrain/Ground.asset": _terrain_data(),
        "Assets/Terrain/Grass.terrainlayer": _terrain_layer(),
        "Assets/Scenes/World.unity": _world(),
        "Assets/Tex/dirt.png": lambda p: _png(p, "RGB", (8, 8), lambda x, y: (120, 80, 40)),
        "Assets/Terrain/Field.asset": _field_data(),
        "Assets/Terrain/Dirt.terrainlayer": _terrain_layer("Dirt", "dirt.png"),
        "Assets/Scenes/Field.unity": _world("Field.asset"),
    }


def project(d):
    _write(files(), d)
    return os.path.join(d, "Assets")


def package(d):
    """The same assets as a .unitypackage: a gzip tar of <guid>/asset, <guid>/asset.meta, <guid>/pathname."""
    tmp = os.path.join(d, "_pkgsrc")
    _write(files(), tmp)
    out = os.path.join(d, "fixtures.unitypackage")
    with tarfile.open(out, "w:gz") as tf:
        for rel in files():
            g = GUIDS[os.path.basename(rel)]
            for member, data in (("asset", open(os.path.join(tmp, rel), "rb").read()),
                                 ("asset.meta", open(os.path.join(tmp, rel + ".meta"), "rb").read()),
                                 ("pathname", (rel + "\n00").encode())):           # Unity's stray "\n00" (the survey)
                ti = tarfile.TarInfo("%s/%s" % (g, member))
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return out
