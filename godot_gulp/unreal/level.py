"""Unreal levels (.umap, and World Partition maps' one-file-per-actor __ExternalActors__) to placements.

    placements(content, map_object_path) -> ([Placement], notes)

A Placement is one static mesh in the level: its actor and component names, the mesh's object path, the override
materials (per slot, None where the mesh's own applies) and its world matrix in glTF's space. Instanced (and
hierarchical instanced) static mesh components give one Placement per instance.

Transforms: a scene component's RelativeLocation, RelativeRotation (FRotator: pitch, yaw, roll in degrees) and
RelativeScale3D, composed up its AttachParent chain. Unreal is left-handed Z-up in centimetres; glTF is right-handed
Y-up in metres. The world matrix M is carried over as S M S^-1 with S swapping Y and Z and scaling by 0.01: the same
map the vertices take, so placements and meshes stay consistent whatever the rotation.
"""
import math, os, struct

import numpy as np

from .package import Package, Reader, UnrealError

S = np.array([[0.01, 0, 0, 0], [0, 0, 0.01, 0], [0, 0.01, 0, 0], [0, 0, 0, 1.0]])
S_INV = np.linalg.inv(S)
MESH_COMPONENTS = {"StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent",
                   "FoliageInstancedStaticMeshComponent", "SplineMeshComponent"}


SKY_MESHES = ("/Engine/EngineSky/", "/Engine/MapTemplates/Sky/")


class Placement:
    __slots__ = ("actor", "component", "mesh", "materials", "matrix", "instance")

    def __init__(self, actor, component, mesh, materials, matrix, instance=-1):
        self.actor, self.component, self.mesh, self.materials, self.matrix, self.instance = actor, component, mesh, materials, matrix, instance


def rotator_matrix(pitch, yaw, roll):
    """FRotator -> the 3x3 rotation for column vectors (Unreal's FRotationMatrix is written for row vectors)."""
    p, y, r = (math.radians(a) for a in (pitch, yaw, roll))
    sp, sy, sr, cp, cy, cr = math.sin(p), math.sin(y), math.sin(r), math.cos(p), math.cos(y), math.cos(r)
    rows = np.array([[cp * cy, cp * sy, sp],
                     [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
                     [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]])
    return rows.T


def relative(props):
    loc = props.get("RelativeLocation") or (0.0, 0.0, 0.0)
    rot = props.get("RelativeRotation") or (0.0, 0.0, 0.0)
    sc = props.get("RelativeScale3D") or (1.0, 1.0, 1.0)
    M = np.eye(4)
    M[:3, :3] = rotator_matrix(*rot) @ np.diag(sc)
    M[:3, 3] = loc
    return M


def to_gltf(M):
    return S @ M @ S_INV


def instance_matrices(pkg, i, end_props_pos, end):
    """The PerInstanceSMData bulk array after an instanced component's properties: element size 64 (floats, UE4) or
    128 (doubles, UE5), each an FMatrix (row vectors, translation in the last row). Found by its shape, since what
    precedes it varies by engine version."""
    b = pkg.data
    for off in range(end_props_pos, min(end - 8, end_props_pos + 4096)):
        esize, n = struct.unpack_from("<ii", b, off)
        if esize not in (64, 128) or n <= 0 or off + 8 + esize * n > end:
            continue
        fmt = "<16f" if esize == 64 else "<16d"
        first = np.array(struct.unpack_from(fmt, b, off + 8)).reshape(4, 4)
        if abs(first[3, 3] - 1.0) > 1e-4 or np.abs(first[:3, 3]).max() > 1e-4:
            continue
        out = []
        for k in range(n):
            m = np.array(struct.unpack_from(fmt, b, off + 8 + k * esize)).reshape(4, 4)
            out.append(m.T)                                  # row-vector FMatrix -> column vectors
        return out
    return None


def blueprint_template(content, class_path, comp_name, depth=0):
    """A Blueprint component's defaults: <name>_GEN_VARIABLE in the Blueprint class's package (its construction
    script's template, or a child Blueprint's override of an inherited one), merged over the same in its parent
    Blueprints (the class's super). -> props (empty when nothing is found)."""
    if depth > 8 or not class_path.startswith("/") or class_path.startswith("/Script/"):
        return {}
    try:
        bp = content.package(class_path)
    except UnrealError:
        return {}
    cls = next((i for i, e in enumerate(bp.exports) if e.name == class_path.split(".")[-1]), None)
    parent = bp.object_path(bp.exports[cls].super_index) if cls is not None and bp.exports[cls].super_index else ""
    out = dict(blueprint_template(content, parent, comp_name, depth + 1)) if parent else {}
    want = comp_name + "_GEN_VARIABLE"
    for i, e in enumerate(bp.exports):
        if e.name == want:
            try:
                out.update(bp.props(i)[0])
            except UnrealError:
                pass
    return out


class _Level:
    def __init__(self, content, notes):
        self.c, self.notes = content, notes
        self.out = []

    def package(self, pkg):
        props = {}
        for i in range(len(pkg.exports)):
            try:
                props[i] = pkg.props(i)
            except UnrealError as e:
                props[i] = ({}, None, 0)
                if pkg.exports[i].class_name in MESH_COMPONENTS:
                    self.notes.append("%s: %s" % (pkg.exports[i].name, e))
        # a Blueprint actor's components get their mesh, materials and default transform from the Blueprint
        # (construction-script templates): the level stores only what an instance changed
        for i, e in enumerate(pkg.exports):
            if e.class_name not in MESH_COMPONENTS or props[i][0].get("StaticMesh") or e.outer <= 0:
                continue
            actor = pkg.exports[e.outer - 1]
            tp = blueprint_template(self.c, pkg.object_path(actor.class_index), e.name)
            if tp:
                merged = dict(tp)
                merged.update(props[i][0])
                props[i] = (merged,) + tuple(props[i][1:])
        if any(e.class_name in ("LandscapeComponent", "LandscapeStreamingProxy", "Landscape") for e in pkg.exports):
            self.notes.append("landscape in %s: not converted" % os.path.basename(pkg.path))
        world = {}

        def world_of(i, depth=0):
            if i in world:
                return world[i]
            p = props[i][0]
            M = relative(p)
            parent = p.get("AttachParent")
            if parent and parent.index > 0 and depth < 64:
                M = world_of(parent.index - 1, depth + 1) @ M
            world[i] = M
            return M

        for i, e in enumerate(pkg.exports):
            if e.class_name not in MESH_COMPONENTS:
                continue
            p, r, end = props[i]
            mesh = p.get("StaticMesh")
            if not mesh:
                continue
            if mesh.path.startswith(SKY_MESHES):             # the engine's sky dome (scaled to km): sky, not content
                self.notes.append("engine sky sphere %s left out" % mesh.path.split(".")[-1])
                continue
            if p.get("bVisible") is False or p.get("bHiddenInGame") is True:
                self.notes.append("%s: hidden, left out" % e.name)
                continue
            actor = pkg.exports[e.outer - 1] if e.outer > 0 else None
            if actor is not None:
                ap = props[e.outer - 1][0]
                if ap.get("bHidden") is True:
                    self.notes.append("%s: hidden actor, left out" % actor.name)
                    continue
                aname = ap.get("ActorLabel") or actor.name
            else:
                aname = ""
            mats = [(m.path if m else None) for m in (p.get("OverrideMaterials") or [])]
            W = world_of(i)
            if e.class_name == "SplineMeshComponent":
                self.notes.append("%s: spline mesh placed undeformed" % e.name)
            if "Instanced" in e.class_name:
                inst = instance_matrices(pkg, i, r.pos, end) if r is not None else None
                if inst is None:
                    self.notes.append("%s: instance data not found" % e.name)
                    continue
                for k, I in enumerate(inst):
                    self.out.append(Placement(aname, e.name, mesh.path, mats, to_gltf(W @ I), k))
            else:
                self.out.append(Placement(aname, e.name, mesh.path, mats, to_gltf(W)))


def placements(content, map_object_path):
    notes = []
    lv = _Level(content, notes)
    pkg = content.package(map_object_path)
    lv.package(pkg)
    # World Partition: every actor in its own package under /<mount>/__ExternalActors__/<map path>
    mpath = map_object_path.split(".")[0]
    mount = "/" + mpath.split("/")[1]
    root = content.mounts.get(mount, "")
    ext_dir = os.path.join(root, "__ExternalActors__", mpath[len(mount):].lstrip("/"))
    if os.path.isdir(ext_dir):
        n = 0
        for dp, _dn, fn in os.walk(ext_dir):
            for f in sorted(fn):
                if f.endswith(".uasset"):
                    try:
                        lv.package(Package.open(os.path.join(dp, f)))
                        n += 1
                    except UnrealError as e:
                        notes.append("%s: %s" % (f, e))
        notes.append("%d external actor packages" % n)
    return lv.out, notes
