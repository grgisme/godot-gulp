"""Unity to glTF on synthetic assets (godot_gulp/unity_fixtures.py): materials (Standard, URP, HDRP), the prefab
hierarchy in glTF's space, a nested prefab's override, a mirroring placement, and .unitypackage == project folder."""
import os, tempfile, unittest

import numpy as np

from godot_gulp import materials as mt, unity, unity_fixtures as uf
from test_convert import Gltf, face_normals


def worlds(g):
    """Every glTF node's world matrix (column vectors), from the scene roots down."""
    out = {}
    def walk(i, parent):
        n = g.j["nodes"][i]
        L = np.array(n["matrix"]).reshape(4, 4).T if "matrix" in n else np.eye(4)
        out[i] = parent @ L
        for c in n.get("children", []):
            walk(c, out[i])
    for r in g.j["scenes"][0]["nodes"]:
        walk(r, np.eye(4))
    return out


def mesh_world(g):
    w = worlds(g)
    return next(w[i] for i, n in enumerate(g.j["nodes"]) if "mesh" in n)


def lin(c):
    return unity.srgb_to_linear(c)


class UnityMaterials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.assets = unity.Assets(uf.project(cls.d))

    def mat(self, name):
        return unity.material(self.assets, uf.GUIDS[name], {})

    def write(self, m, name):
        gl = {"materials": []}
        out = os.path.join(self.d, "out_" + name)
        os.makedirs(out, exist_ok=True)
        w = mt.GltfMaterials(gl, out)
        return gl, gl["materials"][w.index(m)], out

    def img(self, gl, out, info):
        from PIL import Image
        return Image.open(os.path.join(out, gl["images"][gl["textures"][info["index"]]["source"]]["uri"]))

    def test_standard_metallic_smoothness_cutout_and_srgb_colour(self):
        gl, m, out = self.write(self.mat("Std.mat"), "std")
        pbr = m["pbrMetallicRoughness"]
        self.assertTrue(np.allclose(pbr["baseColorFactor"][:3], [lin(1), lin(0.5), lin(0.25)], atol=1e-4))
        self.assertEqual((m["alphaMode"], m["alphaCutoff"]), ("MASK", 0.4))
        # metal-gloss R 128 is metallic; A 200 x _GlossMapScale 0.5 = 100 smoothness -> roughness 155
        self.assertEqual(self.img(gl, out, pbr["metallicRoughnessTexture"]).getpixel((1, 1)), (255, 155, 128))
        self.assertEqual(self.img(gl, out, pbr["baseColorTexture"]).getpixel((6, 1))[3], 0)      # the alpha cuts
        self.assertNotIn("scale", m["normalTexture"])                                              # _BumpScale 1

    def test_urp_lit_transparency_and_factors(self):
        gl, m, _ = self.write(self.mat("Urp.mat"), "urp")
        self.assertEqual(m["alphaMode"], "BLEND")
        pbr = m["pbrMetallicRoughness"]
        self.assertAlmostEqual(pbr["metallicFactor"], 0.7, places=5)
        self.assertAlmostEqual(pbr["roughnessFactor"], 0.75, places=5)

    def test_hdrp_mask_map_repacks_to_orm(self):
        gl, m, out = self.write(self.mat("Hdrp.mat"), "hdrp")
        # mask R 50 metallic, G 180 occlusion, A 100 smoothness -> ORM (180, 155, 50)
        self.assertEqual(self.img(gl, out, m["pbrMetallicRoughness"]["metallicRoughnessTexture"]).getpixel((1, 1)), (180, 155, 50))
        self.assertIn("occlusionTexture", m)


class UnityScenes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.assets = unity.Assets(uf.project(cls.d))

    def conv(self, name):
        dst = os.path.join(self.d, "out", name.replace(".prefab", ".gltf"))
        info = unity.convert_asset(self.assets, uf.GUIDS[name], dst)
        return info, Gltf(dst)

    def test_the_hierarchy_lands_in_gltf_space(self):
        info, g = self.conv("Thing.prefab")
        self.assertEqual(info["meshes"], 1)
        M = mesh_world(g)
        # Unity: Root turned 90 degrees about Y, Quad at local (1, 0, 0) -> Unity world (0, 0, -1) -> glTF (0, 0, -1)
        self.assertTrue(np.allclose(M[:3, 3], [0, 0, -1], atol=1e-6), M[:3, 3])
        self.assertEqual([n["name"] for n in g.j["nodes"]], ["Root", "Quad"])           # the hierarchy is kept

    def test_the_tiling_and_offset_are_baked_into_the_uvs(self):
        _, g = self.conv("Thing.prefab")
        p = g.prims()[0]["attributes"]
        pos, uv = g.acc(p["POSITION"]), g.acc(p["TEXCOORD_0"])
        top_right = [i for i, q in enumerate(pos) if np.allclose(q, [1, 1, 0])][0]
        self.assertTrue(np.allclose(uv[top_right], [2.25, -2.5]), uv[top_right])   # (1,1) x (2,3) + (.25,.5), V flipped

    def test_a_nested_prefab_override_moves_its_instance(self):
        _, g = self.conv("Outer.prefab")
        M = mesh_world(g)
        self.assertTrue(np.allclose(M[:3, 3], [-5, 0, -1], atol=1e-6), M[:3, 3])   # Unity x 5 -> glTF -5

    def test_a_mirroring_placement_is_baked_and_faces_front(self):
        info, g = self.conv("Mirror.prefab")
        n = g.j["nodes"][0]
        self.assertNotIn("matrix", n)
        self.assertTrue((face_normals(g.tris(g.prims()[0]))[:, 2] > 0.99).all())

    def test_a_mesh_asset_on_a_skinned_renderer_is_mirrored_and_faces_its_front(self):
        info, g = self.conv("Skinned.prefab")
        self.assertEqual((info["meshes"], info["tris"]), (1, 2))
        pos = g.acc(g.prims()[0]["attributes"]["POSITION"])
        self.assertTrue(np.allclose(sorted(set(np.round(pos[:, 0], 3))), [-3, -2]))        # Unity x 2..3 -> glTF -3..-2
        self.assertTrue((face_normals(g.tris(g.prims()[0]))[:, 2] < -0.99).all())        # Unity's front (-Z) kept

    def test_the_built_in_cube_is_12_outward_triangles(self):
        info, g = self.conv("Cube.prefab")
        self.assertEqual(info["tris"], 12)
        t = g.tris(g.prims()[0])
        self.assertTrue((np.einsum("ij,ij->i", face_normals(t), t.mean(axis=1)) > 0).all())

    def test_a_skinned_mesh_becomes_a_gltf_skin_and_poses_like_unity(self):
        info, g = self.conv("Rig.prefab")
        sk = g.j["skins"][0]
        self.assertEqual(len(sk["joints"]), 2)
        p = g.prims()[0]["attributes"]
        pos, J, Wt = g.acc(p["POSITION"]).astype(np.float64), g.acc(p["JOINTS_0"]), g.acc(p["WEIGHTS_0"])
        self.assertEqual(g.j["accessors"][p["JOINTS_0"]]["componentType"], 5123)
        ibm = g.acc(sk["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)     # column-major MAT4
        w = worlds(g)
        joints = [w[j] for j in sk["joints"]]
        posed = np.array([sum(Wt[i, k] * (joints[int(J[i, k])] @ ibm[int(J[i, k])] @ np.r_[pos[i], 1.0])[:3]
                              for k in range(4)) for i in range(len(pos))])
        # bone 1 was bound at y 1 and now sits at y 2: the top of the quad goes from y 1 to y 2, the bottom stays at 0
        self.assertEqual(sorted(set(np.round(posed[:, 1], 4))), [0.0, 2.0])
        self.assertTrue(np.allclose(sorted(set(np.round(posed[:, 0], 4))), [-3, -2]))

    def test_an_animator_clip_becomes_a_gltf_animation(self):
        info, g = self.conv("Rig.prefab")
        a = g.j["animations"][0]
        self.assertEqual(a["name"], "Lift")
        by = {(g.j["nodes"][c["target"]["node"]]["name"], c["target"]["path"]): a["samplers"][c["sampler"]] for c in a["channels"]}
        tr = by[("Bone1", "translation")]
        self.assertEqual(tr["interpolation"], "CUBICSPLINE")
        out = g.acc(tr["output"]).reshape(-1, 3, 3)                       # (in-tangent, value, out-tangent) per key
        self.assertTrue(np.allclose(out[-1, 1], [0, 3, 0]), out[-1, 1])
        rot = by[("Bone0", "rotation")]
        q = g.acc(rot["output"])[-1]                                       # Unity +90 deg about Y, mirrored into glTF
        self.assertTrue(np.allclose(q, [0, -0.70710678, 0, 0.70710678], atol=1e-4), q)
        self.assertTrue(np.allclose(g.acc(rot["input"])[[0, -1]], [0, 1]))

    def test_a_lod_group_keeps_lod0_and_names_the_rest(self):
        info, _ = self.conv("Lod.prefab")
        self.assertEqual((info["meshes"], info["tris"]), (1, 12))                 # the cube (LOD0), not the quad
        self.assertTrue(any("LOD1 left out" in n for n in info["material_notes"]))

    def test_a_terrain_becomes_a_heightmap_mesh_facing_up_with_its_layer_tiled(self):
        dst = os.path.join(self.d, "out", "World.gltf")
        info = unity.convert_asset(self.assets, uf.GUIDS["World.unity"], dst)
        g = Gltf(dst)
        self.assertEqual(info["tris"], 8)                                           # 2 x (3-1)^2
        p = g.prims()[0]
        t = g.tris(p)
        self.assertTrue((face_normals(t)[:, 1] > 0).all())                          # every face up
        pos = g.acc(p["attributes"]["POSITION"])
        M = np.array(g.j["nodes"][0]["matrix"]).reshape(4, 4)
        self.assertTrue(np.allclose(M[3, :3], [-10, 0, 0]))                          # Unity x 10 -> glTF -10
        centre = [q for q in pos if np.allclose([q[0], q[2]], [-2, 2])][0]
        self.assertAlmostEqual(float(centre[1]), 16383 / 32766 * 10, places=4)       # half height of 10 m
        uv = g.acc(p["attributes"]["TEXCOORD_0"])
        self.assertAlmostEqual(float(np.ptp(uv[:, 0])), 2.0, places=5)              # 4 m wide, a tile every 2 m
        self.assertEqual(g.j["materials"][p["material"]]["name"], "Grass")

    def test_two_terrain_layers_are_baked_by_the_alphamap(self):
        from PIL import Image
        dst = os.path.join(self.d, "out", "Field.gltf")
        info = unity.convert_asset(self.assets, uf.GUIDS["Field.unity"], dst)
        g = Gltf(dst)
        p = g.prims()[0]
        m = g.j["materials"][p["material"]]
        self.assertEqual(m["name"], "Field")
        self.assertTrue(any("2 layers blended" in n for n in info["material_notes"]), info["material_notes"])
        img = g.j["images"][g.j["textures"][m["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
        px = np.asarray(Image.open(os.path.join(os.path.dirname(dst), img["uri"])).convert("RGB")).astype(int)
        w = px.shape[1]
        self.assertTrue(np.allclose(px[:, : w // 5], (60, 140, 40), atol=2))     # u near 0: the x = 0 column, Grass
        self.assertTrue(np.allclose(px[:, -w // 5:], (120, 80, 40), atol=2))     # u near 1: Dirt
        mid = px[:, w // 2].mean(0)
        self.assertTrue(60 < mid[0] < 120 and 80 < mid[1] < 140, mid)           # blended in between
        uv = g.acc(p["attributes"]["TEXCOORD_0"])
        self.assertAlmostEqual(float(np.ptp(uv[:, 0])), 1.0, places=5)          # one texture over the whole terrain

    def test_the_alphamap_rows_run_from_unity_z_0(self):
        from PIL import Image
        d = tempfile.mkdtemp()
        uf.project(d)
        f = os.path.join(d, "Assets", "Terrain", "Field.asset")
        rows = bytes([255, 0, 0, 0] * 2 + [0, 255, 0, 0] * 2).hex()             # row 0 (z = 0) Grass, row 1 Dirt
        text = open(f).read().replace(bytes([255, 0, 0, 0, 0, 255, 0, 0] * 2).hex(), rows)
        open(f, "w").write(text)
        dst = os.path.join(d, "out", "Field.gltf")
        unity.convert_asset(unity.Assets(os.path.join(d, "Assets")), uf.GUIDS["Field.unity"], dst)
        g = Gltf(dst)
        p = g.prims()[0]
        pos, uv = g.acc(p["attributes"]["POSITION"]), g.acc(p["attributes"]["TEXCOORD_0"])
        m = g.j["materials"][p["material"]]
        img = g.j["images"][g.j["textures"][m["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
        px = np.asarray(Image.open(os.path.join(os.path.dirname(dst), img["uri"])).convert("RGB")).astype(int)
        h, w = px.shape[:2]
        for z, want in ((0.0, (60, 140, 40)), (4.0, (120, 80, 40))):
            k = next(i for i, q in enumerate(pos) if abs(q[2] - z) < 1e-4 and abs(q[0] + 2) < 1e-4)
            u, v = uv[k]
            self.assertTrue(np.allclose(px[min(h - 1, int(v * h)), min(w - 1, int(u * w))], want, atol=2), (z, u, v))

    def test_a_unitypackage_converts_like_the_folder(self):
        pkg = uf.package(tempfile.mkdtemp())
        rows = unity.convert_source(pkg, os.path.join(self.d, "pkgout"))
        by = {r["id"]: r for r in rows}
        self.assertTrue(all(r["ok"] for r in rows), [r.get("error") for r in rows])
        self.assertEqual(by["Assets/Prefabs/Outer.prefab"]["tris"], 2)
        self.assertEqual(sorted(by), sorted(["Assets/Models/quad.obj", "Assets/Prefabs/Mirror.prefab",
                                             "Assets/Prefabs/Outer.prefab", "Assets/Prefabs/Thing.prefab",
                                             "Assets/Prefabs/Skinned.prefab", "Assets/Prefabs/Cube.prefab",
                                             "Assets/Prefabs/Rig.prefab", "Assets/Prefabs/Lod.prefab",
                                             "Assets/Scenes/World.unity", "Assets/Scenes/Field.unity"]))


if __name__ == "__main__":
    unittest.main()


class UnityModelSubMeshes(unittest.TestCase):
    """A prefab's MeshFilter naming a mesh inside a model whose .meta has no name table (Unity 2019.3+ hashes the ids):
    the mesh is the model's node of the GameObject's name, in that node's own space (the GameObject places it), and a
    fileID map Unity wrote overrides the name. Found against Unity's own glTF export through the Unity CLI."""

    def project(self, go_name):
        from godot_gulp import fbx_fixtures as ff
        d = tempfile.mkdtemp()
        a = os.path.join(d, "Assets")
        os.makedirs(a)
        ff.two_nodes(a)
        open(os.path.join(a, "Two.fbx.meta"), "w").write(
            "fileFormatVersion: 2\nguid: %s\nModelImporter:\n  meshes:\n    globalScale: 1\n  internalIDToNameTable: []\n"
            % uf.guid("Two.fbx"))
        ref = "{fileID: 4242, guid: %s, type: 3}" % uf.guid("Two.fbx")
        open(os.path.join(a, "P.prefab"), "w").write(uf._single(go_name, "mesh", ref))
        open(os.path.join(a, "P.prefab.meta"), "w").write("fileFormatVersion: 2\nguid: %s\n" % uf.guid("P.prefab"))
        return d, a

    def bounds(self, d, a):
        dst = os.path.join(d, "out", "P.gltf")
        info = unity.convert_asset(unity.Assets(a), uf.guid("P.prefab"), dst)
        g = Gltf(dst)
        i = next(k for k, n in enumerate(g.j["nodes"]) if "mesh" in n)
        p = g.acc(g.j["meshes"][g.j["nodes"][i]["mesh"]]["primitives"][0]["attributes"]["POSITION"])
        W = worlds(g)[i]
        w = (np.c_[p, np.ones(len(p))] @ W.T)[:, :3]
        return np.ptp(w, 0), np.abs(w).max(0), info

    def test_the_gameobject_name_picks_the_node_in_its_own_space(self):
        d, a = self.project("B")
        size, reach, _info = self.bounds(d, a)
        np.testing.assert_allclose(size[:2], [0.5, 0.5], atol=1e-5)     # B's 0.5 m quad, not A's 1 m one
        self.assertLess(reach[0], 0.51)                                  # B's node offset (3 m) not baked in again

    def test_a_pivoted_node_is_moved_to_its_pivot_like_unity(self):
        # Unity imports a node at its pivot, its mesh around it; the world place of the quad is unchanged
        from godot_gulp import fbx_fixtures as ff
        p = ff.two_nodes(tempfile.mkdtemp())
        loc = dict(unity._fbx(p, 1.0, "local"))["C|default"]["POSITION"]
        wor = dict(unity._fbx(p, 1.0, "world"))["C|default"]["POSITION"]
        np.testing.assert_allclose([loc[:, 0].min(), loc[:, 0].max()], [0.0, 0.5], atol=1e-5)
        np.testing.assert_allclose([wor[:, 0].min(), wor[:, 0].max()], [1.0, 1.5], atol=1e-5)

    def test_a_unity_fileid_map_overrides_the_name(self):
        d, a = self.project("Renamed")
        m = os.path.join(d, "ids.tsv")
        open(m, "w").write("model\tclass\tname\tfileID\nAssets/Two.fbx\tMesh\tA\t4242\n")
        os.environ["GODOT_GULP_UNITY_IDS"] = m
        try:
            size, _reach, _info = self.bounds(d, a)
        finally:
            del os.environ["GODOT_GULP_UNITY_IDS"]
        np.testing.assert_allclose(size[:2], [1.0, 1.0], atol=1e-5)     # A, by the map

    def test_a_mesh_the_unity_map_does_not_list_is_left_out_like_unity(self):
        # a vendor prefab can keep a stale mesh reference; Unity draws nothing there, never the model's first mesh
        d, a = self.project("Renamed")
        m = os.path.join(d, "ids.tsv")
        open(m, "w").write("model\tclass\tname\tfileID\nAssets/Two.fbx\tMesh\tA\t1\nAssets/Two.fbx\tMesh\tB\t2\n")
        os.environ["GODOT_GULP_UNITY_IDS"] = m
        try:
            dst = os.path.join(d, "out", "P.gltf")
            info = unity.convert_asset(unity.Assets(a), uf.guid("P.prefab"), dst)
        finally:
            del os.environ["GODOT_GULP_UNITY_IDS"]
        self.assertEqual(info["meshes"], 0)
        self.assertTrue(any("not in the model" in n for n in info["material_notes"]))

    def test_an_override_of_one_rotation_component_is_normalized_like_unity(self):
        # a nested prefab override sets m_LocalRotation.x only; the source's w stays 1: Unity normalizes, so do we
        M = unity._trs({"m_LocalRotation": {"x": 0.94, "y": 0, "z": 0, "w": 1}})
        R = M[:3, :3]
        np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-9)
