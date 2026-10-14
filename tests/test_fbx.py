"""FBX to glTF through the vendored ufbx on a synthetic ASCII FBX (godot_gulp/fbx_fixtures.py): centimetres to metres,
the node tree, a texture whose stored path is another machine's, a skin cluster as a joint, a take as an animation;
and Unity's FBX model path on the same file."""
import os, tempfile, unittest

import numpy as np

from godot_gulp import fbx, fbx_fixtures as ff, unity
from test_convert import Gltf


class Fbx(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.src = ff.rigged(os.path.join(cls.d, "src"))
        cls.info = fbx.convert(cls.src, os.path.join(cls.d, "out", "rigged.gltf"))
        cls.g = Gltf(os.path.join(cls.d, "out", "rigged.gltf"))

    def node(self, name):
        return next(i for i, n in enumerate(self.g.j["nodes"]) if n["name"] == name)

    def test_tree_and_metres(self):
        root = self.g.j["nodes"][self.node("Root")]
        self.assertEqual(sorted(self.g.j["nodes"][c]["name"] for c in root["children"]), ["Bone", "Quad"])
        (p,) = self.g.prims()
        P = self.g.acc(p["attributes"]["POSITION"])
        np.testing.assert_allclose(P.max(0) - P.min(0), [1, 1, 0], atol=1e-5)      # 100 cm square
        self.assertEqual(self.info["tris"], 2)

    def test_material_texture_found_beside_the_file(self):
        (m,) = self.g.j["materials"]
        self.assertEqual(m["name"], "Red")
        self.assertIn("baseColorTexture", m["pbrMetallicRoughness"])
        self.assertEqual(self.info["textures"], 1)
        self.assertEqual(self.info["material_notes"], [])

    def test_skin(self):
        q = self.g.j["nodes"][self.node("Quad")]
        skin = self.g.j["skins"][q["skin"]]
        self.assertEqual(skin["joints"], [self.node("Bone")])
        np.testing.assert_allclose(self.g.acc(skin["inverseBindMatrices"]).reshape(4, 4), np.eye(4), atol=1e-6)
        (p,) = self.g.prims()
        np.testing.assert_allclose(self.g.acc(p["attributes"]["WEIGHTS_0"])[:, 0], 1.0)

    def test_take(self):
        (a,) = self.g.j["animations"]
        self.assertEqual(a["name"], "Wave")
        (c,) = a["channels"]                              # constant rotation and scale dropped
        self.assertEqual((c["target"]["node"], c["target"]["path"]), (self.node("Bone"), "translation"))
        s = a["samplers"][c["sampler"]]
        t, v = self.g.acc(s["input"]), self.g.acc(s["output"])
        self.assertAlmostEqual(float(t[0]), 0.0)
        self.assertAlmostEqual(float(t[-1]), 1.0, places=5)
        np.testing.assert_allclose(v[0], [0, 0, 0], atol=1e-6)
        np.testing.assert_allclose(v[-1], [1, 0, 0], atol=1e-5)

    def test_unity_model_path(self):
        ((name, a),) = unity._fbx(self.src, 2.0)
        self.assertEqual(name, "Quad|Red")
        P = a["POSITION"]
        np.testing.assert_allclose(P.max(0) - P.min(0), [2, 2, 0], atol=1e-5)
        self.assertEqual(len(P), 6)

    def test_bad_file(self):
        bad = os.path.join(self.d, "bad.fbx")
        with open(bad, "w") as f:
            f.write("not an fbx")
        with self.assertRaises(RuntimeError):
            fbx.scene(bad)


class Names(unittest.TestCase):
    def test_shared_stems_keep_their_extension(self):
        from godot_gulp import report
        self.assertEqual(report.gltf_names(["a/axe.fbx", "a/axe.obj", "a/rake.usd", "b/axe.fbx"]),
                         {"a/axe.fbx": "a/axe.fbx.gltf", "a/axe.obj": "a/axe.obj.gltf", "a/rake.usd": "a/rake.gltf",
                          "b/axe.fbx": "b/axe.gltf"})

    def test_loose_texture_by_joined_name(self):
        from godot_gulp import report
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "textures", "Hand Rake_2K"))
        for f in ("Hand Rake_BaseColor_2K.jpg", "Hand Rake_Normal_2K.jpg", "Shovel_BaseColor_2K.jpg"):
            open(os.path.join(d, "textures", "Hand Rake_2K", f), "w").close()
        found = report.resolve_by_name(os.path.join(d, "handrake.fbx"), {"name": "garden_handrake"})
        self.assertEqual({k: os.path.basename(v) for k, v in found.items()},
                         {"BC": "Hand Rake_BaseColor_2K.jpg", "N": "Hand Rake_Normal_2K.jpg"})


class Source(unittest.TestCase):
    def test_the_source_and_licence_reach_rows_and_gltf(self):
        import json
        from godot_gulp import report
        d = tempfile.mkdtemp()
        fbx.convert(ff.rigged(os.path.join(d, "src")), os.path.join(d, "a.gltf"))
        rows = [{"id": "a", "gltf_rel": "a.gltf", "ok": True}, {"id": "b", "gltf_rel": "b.gltf", "ok": False}]
        report.stamp_source(d, rows, {"store": "unity-asset-store", "licence": "restricted", "id": ""})
        self.assertEqual(rows[1]["source_licence"], "restricted")
        self.assertNotIn("source_id", rows[0])                                   # empty values are not written
        self.assertEqual(json.load(open(os.path.join(d, "a.gltf")))["asset"]["extras"]["source"],
                         {"store": "unity-asset-store", "licence": "restricted"})
