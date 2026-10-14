"""Unreal packages on synthetic files written by godot_gulp/unreal/fixtures.py (no engine content): the summary and
maps, both property tag formats (UE 5.4+ type name trees and the older flat tags), and Texture2D source art from the
package trailer (raw, PNG, and the UEDELTA row filter)."""
import os, tempfile, unittest

import numpy as np

from godot_gulp.unreal import fixtures as uf, package as P, texture as T


def pixels(w=40, h=7000):
    y, x = np.mgrid[0:h, 0:w]
    return np.stack([x * 6 % 256, y % 256, (x + y) % 256, np.full_like(x, 200)], -1).astype(np.uint8)


class UnrealTexture(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def roundtrip(self, ue5, comp, px):
        p = uf.texture(os.path.join(self.d, "t%d%s.uasset" % (ue5, comp)), px, ue5=ue5, compression=comp)
        pkg = P.Package.open(p)
        (i,) = pkg.find("Texture2D")
        self.assertEqual(pkg.exports[i].name, "T_Test")
        props, _r, _end = pkg.props(i)
        self.assertEqual(props["Source"]["SizeX"], px.shape[1])
        self.assertTrue(props["SRGB"])
        im, info = T.source_image(pkg, i)
        self.assertEqual(info["compression"], comp)
        np.testing.assert_array_equal(np.asarray(im.convert("RGBA")), px)

    def test_type_name_tags_with_uedelta_over_several_blocks(self):
        self.roundtrip(1012, "TSCF_UEDELTA", pixels())          # 160-byte rows: 819 rows a block, 9 blocks

    def test_flat_tags_with_png(self):
        self.roundtrip(1009, "TSCF_PNG", pixels(16, 16))

    def test_raw_source(self):
        self.roundtrip(1012, "TSCF_None", pixels(8, 8))

    def test_undelta_inverts_the_filter(self):
        raw = pixels(53, 900).tobytes()                          # rows that do not divide the 128 KiB block
        self.assertEqual(T.undelta(uf._delta(raw, 53 * 4, 900), 53 * 4, 900), raw)

    def test_not_a_package(self):
        p = os.path.join(self.d, "x.uasset")
        open(p, "wb").write(b"\0" * 64)
        with self.assertRaises(P.UnrealError):
            P.Package.open(p)


if __name__ == "__main__":
    unittest.main()


from godot_gulp.unreal import fixtures as ufx, material as UM




class UnrealMaterial(unittest.TestCase):
    def check(self, ue5):
        d = tempfile.mkdtemp()
        ufx.material_pack(d, ue5)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        m = c.material("/Game/M/M_Brick.M_Brick")
        self.assertEqual(m.notes, [])
        t = m.tex["diffuse"]
        self.assertEqual(np.asarray(__import__("PIL.Image").Image.open(t.path))[0, 0, :3].tolist(), [200, 50, 40])
        self.assertAlmostEqual(t.scale[0], 0.5)                     # the tint multiply as the texture's factor
        self.assertEqual(m.tex["roughness"].channel, "g")            # the packed map keeps its channel
        self.assertAlmostEqual(m.tex["roughness"].scale[0], 0.9, places=5)
        self.assertEqual(m.tex["normal"].scale[1], -1.0)              # DirectX green flipped
        self.assertAlmostEqual(m.metallic, 0.4, places=5)             # 0.8 through the function halving it
        self.assertTrue(m.double_sided)
        self.assertAlmostEqual(m.cutoff, 0.4, places=5)
        mi = c.material("/Game/M/MI_Brick.MI_Brick")
        self.assertEqual(mi.name, "MI_Brick")
        self.assertAlmostEqual(mi.tex["roughness"].scale[0], 0.5, places=5)   # the instance's roughness
        self.assertEqual(np.asarray(__import__("PIL.Image").Image.open(mi.tex["diffuse"].path))[0, 0, :3].tolist(), [40, 160, 40])

    def test_type_name_tags(self):
        self.check(1012)

    def test_flat_tags(self):
        self.check(1009)


from godot_gulp.unreal import level as UL




class UnrealLevel(unittest.TestCase):
    def check(self, ue5):
        d = tempfile.mkdtemp()
        ufx.level(d, ue5)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        pl, notes = UL.placements(c, "/Game/Maps/L_Test.L_Test")
        by = {(p.component, p.instance): p for p in pl}
        self.assertEqual(sorted(by), [("Child", -1), ("Rocks", 0), ("Rocks", 1), ("Root", -1)])
        self.assertTrue(any("hidden actor" in n for n in notes), notes)
        root, child = by[("Root", -1)].matrix, by[("Child", -1)].matrix
        np.testing.assert_allclose(root[:3, 3], [1, 3, 2], atol=1e-6)            # (100, 200, 300) cm, Y and Z swapped
        np.testing.assert_allclose(child[:3, 3], [1, 3, 3], atol=1e-6)           # yaw 90 turns its +X into +Y
        np.testing.assert_allclose(np.linalg.det(root[:3, :3]), 1.0, atol=1e-6)  # still a rotation, not a mirror
        np.testing.assert_allclose(by[("Rocks", 0)].matrix[:3, 3], [0.5, 0, 0], atol=1e-6)
        np.testing.assert_allclose(by[("Rocks", 1)].matrix[:3, 3], [0, 0, 0.5], atol=1e-6)
        self.assertEqual(by[("Root", -1)].actor, "Box")
        self.assertEqual(by[("Root", -1)].mesh, "/Game/Meshes/SM_Box.SM_Box")

    def test_type_name_tags(self):
        self.check(1012)

    def test_flat_tags(self):
        self.check(1009)


from godot_gulp.unreal import mesh as UMESH




class UnrealMesh(unittest.TestCase):
    def check(self, ue5):
        d = tempfile.mkdtemp()
        ufx.material_pack(d, ue5)
        ufx.mesh_asset(d, ue5)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        m, notes = UMESH.static_mesh(c, "/Game/Meshes/SM_Box.SM_Box", lambda p, s: (p, s))
        self.assertEqual(notes, [])
        self.assertEqual([p["mat"] for p in m["prims"]], [("/Game/M/M_Brick.M_Brick", "Stone"), (None, "Bare")])
        floor, wall = m["prims"]
        P = floor["attrs"]["POSITION"]
        np.testing.assert_allclose(P, [[0, 0, 0], [0, 0, 1], [1, 0, 0]], atol=1e-6)      # (x, z, y) * 0.01
        n = np.cross(P[1] - P[0], P[2] - P[0])
        self.assertGreater(n[1], 0)                                                      # counter-clockwise faces up (+Y)
        np.testing.assert_allclose(floor["attrs"]["NORMAL"], [[0, 1, 0]] * 3, atol=1e-6)
        np.testing.assert_allclose(floor["attrs"]["TEXCOORD_0"], [[0, 0], [0, 1], [1, 0]], atol=1e-6)
        W = wall["attrs"]["POSITION"]
        self.assertGreater(np.dot(np.cross(W[1] - W[0], W[2] - W[0]), wall["attrs"]["NORMAL"][0]), 0)

    def test_type_name_tags(self):
        self.check(1012)

    def test_flat_tags(self):
        self.check(1009)

    def test_a_level_and_a_mesh_to_gltf(self):
        from godot_gulp.unreal import convert as UC
        d = tempfile.mkdtemp()
        ufx.material_pack(d)
        ufx.mesh_asset(d)
        ufx.level(d)
        out = os.path.join(d, "out")
        rows = UC.convert_source(d, out)
        by = {r["id"]: r for r in rows}
        self.assertEqual(sorted(by), ["Maps/L_Test.umap", "Meshes/SM_Box.uasset"])
        self.assertTrue(all(r["ok"] for r in rows), [r.get("error") for r in rows])
        self.assertEqual(by["Maps/L_Test.umap"]["meshes"], 4)                          # root, child, two instances
        from test_convert import Gltf
        g = Gltf(os.path.join(out, "Maps", "L_Test.gltf"))
        self.assertEqual(len(g.j["meshes"]), 1)                                           # one mesh, placed four times
        self.assertEqual(by["Meshes/SM_Box.uasset"]["expect_albedo"], 1)


class UnrealSkeletal(unittest.TestCase):
    def test_bones_skin_and_materials(self):
        d = tempfile.mkdtemp()
        ufx.material_pack(d)
        ufx.skeletal_asset(d)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        m, bones, notes = UMESH.skeletal_mesh(c, "/Game/Meshes/SK_Arm.SK_Arm", lambda p, s: (p, s))
        self.assertEqual(notes, [])
        self.assertEqual([b[0] for b in bones], ["Root", "Arm"])
        self.assertEqual(m["prims"][0]["mat"], ("/Game/M/M_Brick.M_Brick", "Skin"))
        arm = bones[1][2]
        np.testing.assert_allclose(arm[:3, 3], [0, 0.5, 0], atol=1e-9)          # 50 cm up: glTF +Y
        np.testing.assert_allclose(np.linalg.det(arm[:3, :3]), 1.0, atol=1e-9)
        np.testing.assert_allclose(arm @ m["ibm"][1], np.eye(4), atol=1e-9)       # the bind pose
        a = m["prims"][0]["attrs"]
        P, J, W = a["POSITION"], a["JOINTS_0"], a["WEIGHTS_0"]
        def weights(k):
            return {int(j): round(float(w), 3) for j, w in zip(J[k], W[k]) if w > 0}
        self.assertEqual(weights(0), {0: 1.0})                                    # corner 0: vertex 0
        self.assertEqual(weights(2), {0: 0.5, 1: 0.5})                            # corner 2: vertex 1
        self.assertEqual(weights(4), {1: 0.75, 0: 0.25})                          # corner 4: vertex 3
        self.assertEqual(weights(3), {1: 1.0})                                    # corner 3: vertex 299, chunk 2
        np.testing.assert_allclose(P[3], [0, 0.5, 0.5], atol=1e-6)

    def test_to_gltf(self):
        from godot_gulp.unreal import convert as UC
        from test_convert import Gltf
        d = tempfile.mkdtemp()
        ufx.sample(d)
        rows = UC.convert_source(d, os.path.join(d, "out"))
        by = {r["id"]: r for r in rows}
        self.assertTrue(by["Meshes/SK_Arm.uasset"]["ok"], by["Meshes/SK_Arm.uasset"].get("error"))
        g = Gltf(os.path.join(d, "out", "Meshes", "SK_Arm.gltf"))
        (skin,) = g.j["skins"]
        self.assertEqual([g.j["nodes"][j]["name"] for j in skin["joints"]], ["Root", "Arm"])


class UnrealAnim(unittest.TestCase):
    def test_a_take_on_its_skeletal_mesh(self):
        from godot_gulp.unreal import anim as UA, convert as UC
        from test_convert import Gltf
        d = tempfile.mkdtemp()
        ufx.sample(d)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        s = UA.sequence(c, "/Game/Meshes/A_Wave.A_Wave")
        t, T, R, S = s["bones"]["arm"]
        np.testing.assert_allclose(t[[0, -1]], [0, 1])                               # 30 frames at 30 fps
        np.testing.assert_allclose(T[[0, -1]], [[0, 0.5, 0], [0, 1.5, 0]], atol=1e-9)  # 50 -> 150 cm up: glTF +Y
        # the rest pose's rotation at frame 0 (yaw 90 = the bone's own pose), a further 90 degrees at the end
        np.testing.assert_allclose(np.abs(R[0]), np.abs([0, np.sqrt(.5), 0, np.sqrt(.5)]), atol=1e-6)
        np.testing.assert_allclose(np.abs(R[-1]), [0, 1, 0, 0], atol=1e-6)
        rows = UC.convert_source(d, os.path.join(d, "out"))
        self.assertTrue(all(r["ok"] for r in rows), [r.get("error") for r in rows])
        g = Gltf(os.path.join(d, "out", "Meshes", "SK_Arm.gltf"))
        (a,) = g.j["animations"]
        self.assertEqual(a["name"], "A_Wave")
        self.assertEqual({g.j["nodes"][ch["target"]["node"]]["name"] for ch in a["channels"]}, {"Arm"})


class UnrealMesh424(unittest.TestCase):
    def test_a_ue424_static_mesh(self):
        d = tempfile.mkdtemp()
        pos = [(0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 50)]
        md = ufx.mesh_description_4(pos, [(0, 2, 1), (0, 3, 2)], [(0, 0, 1)] * 3 + [(1, 0, 0)] * 3,
                                    [(0, 0), (0, 1), (1, 0), (0, 0), (0, 1), (1, 1)], [1, 0], ["Wall", "Floor"])
        os.makedirs(os.path.join(d, "Meshes"))
        ufx.static_mesh_4(os.path.join(d, "Meshes", "SM_Old.uasset"), md, [("Floor", None), ("Wall", None)],
                          "SM_Old", "/Game/Meshes/SM_Old")
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        self.assertEqual(c.package("/Game/Meshes/SM_Old.SM_Old").ue5, 0)
        m, notes = UMESH.static_mesh(c, "/Game/Meshes/SM_Old.SM_Old", lambda p, s: s)
        self.assertEqual(notes, [])
        by = {p["mat"]: p["attrs"] for p in m["prims"]}
        self.assertEqual(sorted(by), ["Floor", "Wall"])                           # each triangle's polygon group
        # triangle 0 (group 1: "Floor") lies flat; triangle 1 (group 0: "Wall") stands up
        np.testing.assert_allclose(by["Floor"]["POSITION"], [[0, 0, 0], [0, 0, 1], [1, 0, 0]], atol=1e-6)
        for slot in ("Floor", "Wall"):
            t = by[slot]["POSITION"]
            self.assertGreater(np.dot(np.cross(t[1] - t[0], t[2] - t[0]), by[slot]["NORMAL"][0]), 0)


class UnrealMeshRaw(unittest.TestCase):
    def test_a_ue419_raw_mesh(self):
        # a UE 4.19 save (UE4 516: no persistent or owner guid in the summary), its source an FRawMesh
        d = tempfile.mkdtemp()
        pos = [(0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 50)]
        raw = ufx.raw_mesh(pos, [0, 2, 1, 0, 3, 2], [1, 0], [(0, 0, 1)] * 3 + [(1, 0, 0)] * 3,
                           [(0, 0), (0, 1), (1, 0), (0, 0), (0, 1), (1, 1)])
        os.makedirs(os.path.join(d, "Meshes"))
        ufx.static_mesh_4(os.path.join(d, "Meshes", "SM_Raw.uasset"), raw, [("Wall", None), ("Floor", None)],
                          "SM_Raw", "/Game/Meshes/SM_Raw", ue4=516, raw=True)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        self.assertEqual(c.package("/Game/Meshes/SM_Raw.SM_Raw").ue4, 516)
        m, notes = UMESH.static_mesh(c, "/Game/Meshes/SM_Raw.SM_Raw", lambda p, s: s)
        self.assertIn("source: a UE 4.19-4.23 raw mesh", notes)
        by = {p["slot"]: p["attrs"] for p in m["prims"]}
        self.assertEqual(sorted(by), [0, 1])                                     # each face's material index
        np.testing.assert_allclose(by[1]["POSITION"], [[0, 0, 0], [0, 0, 1], [1, 0, 0]], atol=1e-6)
        np.testing.assert_allclose(by[1]["TEXCOORD_0"], [[0, 0], [0, 1], [1, 0]], atol=1e-6)
        for slot in (0, 1):
            t = by[slot]["POSITION"]
            self.assertGreater(np.dot(np.cross(t[1] - t[0], t[2] - t[0]), by[slot]["NORMAL"][0]), 0)


class UnrealBlueprintActors(unittest.TestCase):
    def test_a_blueprint_actor_takes_its_mesh_and_transform_from_the_blueprints(self):
        d = tempfile.mkdtemp()
        ufx.material_pack(d)
        ufx.mesh_asset(d)
        ufx.bp_level(d)
        c = UM.Content({"/Game": d}, os.path.join(d, "cache"))
        pl, notes = UL.placements(c, "/Game/Maps/L_BP.L_BP")
        (p,) = pl
        self.assertEqual(p.mesh, "/Game/Meshes/SM_Box.SM_Box")                 # from the parent Blueprint
        np.testing.assert_allclose(p.matrix[:3, 3], [2, 3, 0], atol=1e-6)     # the child's 200 cm X wins over the parent's 100 cm Z; actor 300 up
