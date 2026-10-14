"""Blender files without Blender (godot_gulp/blend.py): three on-disk mesh eras (2.90 structs, 4.2 named layers,
5.2 attribute storage), gzip and zstd, the object tree, material slots, the Principled BSDF and its maps (a packed
image, a channel through Separate Color, a Normal Map), n-gons, flat and smooth faces, and the modifier policy (skinned meshes to Blender, render-hidden objects left out)."""
import os, shutil, tempfile, unittest

import numpy as np

from godot_gulp import blend
from test_convert import Gltf
from test_unity import worlds

DATA = os.path.join(os.path.dirname(__file__), "data")


class Blend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.info = blend.convert(os.path.join(DATA, "scene_42.blend"), os.path.join(cls.d, "s.gltf"), modifiers="ignore")
        cls.g = Gltf(os.path.join(cls.d, "s.gltf"))

    def node(self, name):
        return next(i for i, n in enumerate(self.g.j["nodes"]) if n["name"] == name)

    def mat(self, name):
        return next(m for m in self.g.j["materials"] if m["name"] == name)

    def test_a_shape_changing_modifier_needs_blender(self):
        with self.assertRaises(blend.NeedsBlender) as cm:
            blend.convert(os.path.join(DATA, "scene_42.blend"), os.path.join(self.d, "refused.gltf"))
        self.assertIn("Mirrored: modifier Mirror", str(cm.exception))

    def test_a_skinned_mesh_needs_blender(self):
        # the reader keeps no skin; Blender keeps the skin and the actions (rig_42: a quad skinned to a 2-bone armature)
        with self.assertRaises(blend.NeedsBlender) as cm:
            blend.convert(os.path.join(DATA, "rig_42.blend"), os.path.join(self.d, "rig.gltf"))
        self.assertIn("Body: skinned to an armature", str(cm.exception))

    def test_a_render_hidden_object_neither_blocks_nor_exports(self):
        # hidden_42: a rig widget (hidden from render, a Subdivision modifier, a bone's custom shape) beside a plain quad
        info = blend.convert(os.path.join(DATA, "hidden_42.blend"), os.path.join(self.d, "hidden.gltf"))
        g = Gltf(os.path.join(self.d, "hidden.gltf"))
        self.assertEqual([n["name"] for n in g.j["nodes"]], ["Box"])
        self.assertIn("WGT-shape: hidden from render, left out", info["material_notes"])

    def test_object_tree_in_gltf_space(self):
        w = worlds(self.g)
        np.testing.assert_allclose(w[self.node("Slab")][:3, 3], [0, 1, 0], atol=1e-6)      # Blender (0, 0, 1)
        np.testing.assert_allclose(w[self.node("Child")][:3, 3], [0, 1, -2], atol=1e-6)    # 2 m along Slab's X, Slab yawed 90
        self.assertIn(self.node("Child"), self.g.j["nodes"][self.node("Slab")].get("children", []))
        self.assertEqual(self.info["tris"], 2 + 4 + 1 + 2)                                  # quad, 6-corner n-gon, child, mirrored base

    def test_ngon_and_shading(self):
        mesh = self.g.j["meshes"][self.g.j["nodes"][self.node("Slab")]["mesh"]]
        by = {self.g.j["materials"][p["material"]]["name"]: p for p in mesh["primitives"]}
        t = self.g.tris(by["Plain"])
        area = np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2
        self.assertAlmostEqual(area, 3.0, places=5)                                         # the concave L: 2 + 1 m2
        n = self.g.acc(by["Painted"]["attributes"]["NORMAL"])
        np.testing.assert_allclose(n, np.tile([0, 1, 0], (len(n), 1)), atol=1e-6)          # flat quad faces up
        fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        self.assertTrue((fn[:, 1] > 0).all())                                               # counter-clockwise, up

    def test_principled_and_maps(self):
        p = self.mat("Painted")["pbrMetallicRoughness"]
        self.assertIn("baseColorTexture", p)
        self.assertAlmostEqual(p["metallicFactor"], 0.25, places=5)
        self.assertIn("metallicRoughnessTexture", p)                                        # roughness from the green channel
        self.assertIn("normalTexture", self.mat("Painted"))
        q = self.mat("Plain")["pbrMetallicRoughness"]
        np.testing.assert_allclose(q["baseColorFactor"], [0, 0, 1, 1], atol=1e-6)
        self.assertAlmostEqual(q["roughnessFactor"], 0.7, places=5)

    def test_other_eras(self):
        for f, ver, tris in (("rust_blend_2_90.blend", "2.90", 1100), ("rust_blend_5_2.blend", "5.2", 12)):
            info = blend.convert(os.path.join(DATA, f), os.path.join(self.d, f + ".gltf"), modifiers="ignore")
            self.assertEqual((info["blender_version"], info["tris"]), (ver, tris), f)

    def test_the_container(self):
        bf = blend.BlendFile(os.path.join(DATA, "rust_blend_5_2.blend"))                    # zstd, the 17-byte header
        self.assertEqual((bf.version, bf.bhead), (502, "large"))
        self.assertEqual([m.id_name() for m in bf.ids("ME")], ["Cube"])
        bf = blend.BlendFile(os.path.join(DATA, "rust_blend_2_90.blend"))                   # gzip, the 12-byte header
        self.assertEqual((bf.version, bf.bhead), (290, "small"))

    @unittest.skipUnless(os.access(os.environ.get("GODOT_GULP_BLENDER", "/nonexistent"), os.X_OK), "no GODOT_GULP_BLENDER")
    def test_the_blender_fallback(self):
        info = blend.convert_or_blender(os.path.join(DATA, "scene_42.blend"), os.path.join(self.d, "fb", "s.gltf"))
        self.assertTrue(info["material_notes"][0].startswith("converted by Blender (headless): Mirrored"))
        self.assertGreater(info["tris"], 9)                                                 # the mirror applied

    def test_no_blender_names_the_cause(self):
        old = os.environ.pop("GODOT_GULP_BLENDER", None)
        try:
            with self.assertRaises(blend.NeedsBlender) as cm:
                blend.convert_or_blender(os.path.join(DATA, "scene_42.blend"), os.path.join(self.d, "nb.gltf"))
            self.assertIn("GODOT_GULP_BLENDER", str(cm.exception))
        finally:
            if old is not None:
                os.environ["GODOT_GULP_BLENDER"] = old
