"""Unreal content folders to glTF: every StaticMesh asset and every level (.umap), with their materials.

    convert_source(content_dir, out, mount="/Game", extra_mounts=None, only=()) -> rows (for report.godot_check)

A mesh becomes <out>/<asset path>.gltf; a level becomes <out>/<map path>.gltf with one node per placed mesh (each
mesh written once and shared). Textures are decoded from their source art into <out>/.unreal-textures and copied
into <out>/textures by the material writer.
"""
import fnmatch, json, os

import numpy as np

from .. import convert as gltf_out, materials as mt
from . import anim, level, material, mesh
from .package import Package, UnrealError


class Converter:
    def __init__(self, mounts, out):
        self.content = material.Content(mounts, os.path.join(out, ".unreal-textures"))
        self.out = out
        self.tex_dir = os.path.join(out, "textures")
        self.mats, self.meshes = {}, {}
        self.anims = {}                        # skeleton object path -> [animation sequence object paths]

    def mat(self, path, slot):
        key = path or ("slot:" + (slot or ""))
        if key not in self.mats:
            if not path:
                m = mt.Mat(slot or "material")
                m.notes.append("no material assigned")
            else:
                try:
                    m = self.content.material(path)
                except (UnrealError, OSError) as e:
                    m = mt.Mat(path.split(".")[-1])
                    m.notes.append("material not read: %s" % e)
            self.mats[key] = m
        return self.mats[key]

    def mesh(self, path, overrides=None):
        """A mesh dict (shared per mesh and material overrides) or None."""
        key = (path, tuple(overrides or ()))
        if key not in self.meshes:
            try:
                base = self.meshes.get((path, ()))
                if base is None:
                    base, notes = mesh.static_mesh(self.content, path, self.mat)
                    base["notes"] = notes
                    self.meshes[(path, ())] = base
                self.meshes[key] = base
                if overrides and any(overrides):
                    m = dict(base)
                    m["prims"] = [dict(p, mat=self.mat(overrides[p["slot"]], p["path"]) if p["slot"] < len(overrides)
                                       and overrides[p["slot"]] else p["mat"]) for p in base["prims"]]
                    self.meshes[key] = m
            except (UnrealError, OSError, KeyError, ValueError) as e:
                self.meshes[key] = None
                self.meshes.setdefault(("error", path), str(e))
        return self.meshes[key]

    def write_mesh(self, path, dst):
        m = self.mesh(path)
        if m is None:
            raise UnrealError(self.meshes.get(("error", path), "mesh not read"))
        info = gltf_out.write_gltf(dst, [], self.tex_dir, [{"key": 0, "name": m["name"], "local": np.eye(4), "mesh": m,
                                                           "children": []}])
        info["material_notes"] = list(m.get("notes", [])) + info["material_notes"]
        return info

    def skeletal(self, path):
        """-> (tree roots for write_gltf, notes, animations): the bones as nodes, the skinned mesh on a node of its own,
        and every animation sequence of its skeleton."""
        m, bl, notes = mesh.skeletal_mesh(self.content, path, self.mat)
        nodes = [{"key": "bone:%d" % k, "name": n, "local": L, "children": []} for k, (n, _p, L) in enumerate(bl)]
        roots = []
        for k, (_n, parent, _L) in enumerate(bl):
            (nodes[parent]["children"] if parent >= 0 else roots).append(nodes[k])
        m["skin_keys"] = ["bone:%d" % k for k in range(len(bl))]
        anims = []
        pkg = self.content.package(path)
        props = pkg.props(self.content.export_of(pkg, path))[0]
        skel = props.get("Skeleton")
        if skel:
            key_of = {n.lower(): "bone:%d" % k for k, (n, _p, _L) in enumerate(bl)}
            for ap in self.anims.get(skel.path, []):
                try:
                    seq = anim.sequence(self.content, ap)
                except UnrealError as e:
                    notes.append("animation %s not read: %s" % (ap.split(".")[-1], e))
                    continue
                a = anim.gltf_animation(dict(seq, bones={b.lower(): v for b, v in seq["bones"].items()}), key_of)
                if a["channels"]:
                    anims.append(a)
        return [{"key": "mesh", "name": m["name"], "local": np.eye(4), "mesh": m, "children": []}] + roots, notes, anims

    def write_skeletal(self, path, dst):
        tree, notes, anims = self.skeletal(path)
        info = gltf_out.write_gltf(dst, [], self.tex_dir, tree, anims)
        info["material_notes"] = notes + info["material_notes"]
        return info

    def write_level(self, map_path, dst):
        pl, notes = level.placements(self.content, map_path)
        nodes, missing = [], {}
        for k, p in enumerate(pl):
            m = self.mesh(p.mesh, p.materials)
            if m is None:
                missing[p.mesh] = self.meshes.get(("error", p.mesh), "")
                continue
            name = p.actor + ("/" + p.component if p.component else "") + ("#%d" % p.instance if p.instance >= 0 else "")
            nodes.append({"key": k, "name": name, "local": p.matrix, "mesh": m, "children": []})
        info = gltf_out.write_gltf(dst, [], self.tex_dir, nodes)
        info["material_notes"] = notes + ["mesh %s not read: %s" % (k, v) for k, v in sorted(missing.items())] + info["material_notes"]
        return info


def convert_source(content_dir, out, mount="/Game", extra_mounts=None, only=()):
    content_dir = os.path.abspath(content_dir)
    mounts = {mount: content_dir}
    mounts.update(extra_mounts or {})
    cv = Converter(mounts, out)
    rows = []
    # first, every animation sequence by its skeleton: a skeletal mesh's glTF carries its skeleton's animations
    for dp, dn, fn in os.walk(content_dir):
        dn[:] = [d for d in dn if d not in ("__ExternalActors__", "__ExternalObjects__", "Developers", "Collections")]
        for f in fn:
            if not f.endswith(".uasset"):
                continue
            try:
                pkg = Package.open(os.path.join(dp, f))
            except UnrealError:
                continue
            for i, e in enumerate(pkg.exports):
                if not e.outer and e.class_name == "AnimSequence":
                    skel = pkg.props(i)[0].get("Skeleton")
                    rel = os.path.relpath(os.path.join(dp, f), content_dir).replace(os.sep, "/")
                    if skel:
                        cv.anims.setdefault(skel.path, []).append(mount + "/" + os.path.splitext(rel)[0] + "." + e.name)
    for dp, dn, fn in os.walk(content_dir):
        dn[:] = sorted(d for d in dn if d not in ("__ExternalActors__", "__ExternalObjects__", "Developers", "Collections"))
        for f in sorted(fn):
            if not f.endswith((".uasset", ".umap")):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, content_dir).replace(os.sep, "/")
            if only and not any(fnmatch.fnmatch(rel, g) for g in only):
                continue
            try:
                pkg = Package.open(p)
            except UnrealError:
                continue
            obj = mount + "/" + os.path.splitext(rel)[0]
            kind = None
            for e in pkg.exports:
                if not e.outer and e.class_name == "StaticMesh":
                    kind, obj = "mesh", obj + "." + e.name
                    break
                if not e.outer and e.class_name == "SkeletalMesh":
                    kind, obj = "skeletal", obj + "." + e.name
                    break
                if not e.outer and e.class_name == "World":
                    kind, obj = "level", obj + "." + e.name
                    break
            if kind is None:
                continue
            gl_rel = os.path.splitext(rel)[0] + ".gltf"
            row = {"id": rel, "gltf_rel": gl_rel, "ok": False}
            try:
                dst = os.path.join(out, gl_rel)
                info = {"mesh": cv.write_mesh, "skeletal": cv.write_skeletal, "level": cv.write_level}[kind](obj, dst)
                row.update(ok=info["meshes"] > 0, meshes=info["meshes"], tris=info["tris"], verts=info["verts"],
                           w_m=info["size"][0], d_m=info["size"][1], h_m=info["size"][2], materials=info["materials"],
                           notes="; ".join(info["material_notes"]))
                if not info["meshes"]:
                    row["error"] = "no meshes"
                with open(dst) as fh:
                    gl = json.load(fh)
                row["expect_albedo"] = sum(1 for m in gl.get("meshes", []) for pr in m["primitives"]
                                           if "baseColorTexture" in gl["materials"][pr["material"]].get("pbrMetallicRoughness", {}))
            except Exception as e:
                row["error"] = "%s: %s" % (type(e).__name__, e)
            rows.append(row)
    return rows
