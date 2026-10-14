#!/usr/bin/env python3
"""The pxr engine's own logic: the repair rules on our glTF, the cache key, a bad file never stops a run."""
import os

import json, tempfile, time, unittest
import numpy as np
from pxr import Gf, Sdf, UsdGeom, Vt
from godot_gulp import fixtures as fx
from godot_gulp import engine as pxr_engine


def big_quad(d, name="tiled"):
    """A 2 m x 2 m quad whose st runs 0..1 (one repeat over 2 m), with a material named MI_Brick."""
    st, p = fx.stage(d, name)
    q = UsdGeom.Mesh.Define(st, "/Root/Quad")
    q.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(2, 0, 0), Gf.Vec3f(2, 2, 0), Gf.Vec3f(0, 2, 0)])
    q.CreateFaceVertexCountsAttr([4])
    q.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    UsdGeom.PrimvarsAPI(q).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex).Set(
        Vt.Vec2fArray([(0, 0), (1, 0), (1, 1), (0, 1)]))
    m, ps = fx.material(st, "/Root/MI_Brick")
    f = fx.png(d, "brick.png", colour=(120, 60, 40))
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(fx.texture(st, "/Root/MI_Brick", "d", f))
    fx.bind(q.GetPrim(), m)
    return fx.save(st, p), f


class Engine(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "rules.json")
        json.dump({"rules": [{"match": "^MI_Brick$", "uv_world_m": 0.5, "tint": [0.5, 1.0, 1.0], "why": "test"}]},
                  open(self.rules, "w"))

    def run_job(self, usd):
        gl = os.path.join(self.d, "out", os.path.basename(usd).replace(".usda", ".gltf"))
        return pxr_engine.job({"id": "x", "usd": usd, "gltf": gl, "tex_dir": os.path.join(self.d, "out", "textures"),
                               "rules": self.rules}), gl

    def test_uv_world_m_is_baked_into_the_uvs_and_tint_multiplies(self):
        usd, _ = big_quad(self.d)
        info, gl = self.run_job(usd)
        self.assertTrue(info["ok"], info.get("error"))
        doc = pxr_engine.Doc(gl)
        p = doc.j["meshes"][0]["primitives"][0]
        uv = doc.acc(p["attributes"]["TEXCOORD_0"])
        self.assertAlmostEqual(float(np.ptp(uv[:, 0])), 4.0, places=4)      # 2 m at 0.5 m a repeat: 4 repeats
        self.assertNotIn("extensionsUsed", doc.j)                            # baked, not left to the reader
        self.assertTrue(np.allclose(doc.j["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][:3], [0.5, 1, 1]))
        self.assertEqual(info["expect_albedo"], 1)
        self.assertTrue(any("UV tiling restored" in n for n in info["texture_notes"]))

    def test_the_cache_key_follows_content_not_time(self):
        usd, tex = big_quad(self.d)
        info, _ = self.run_job(usd)
        k1 = pxr_engine.cache_key(info["deps"], [], self.rules, {})
        self.assertIn(os.path.realpath(tex), [os.path.realpath(x) for x in info["deps"]])
        os.utime(tex, (time.time() + 5, time.time() + 5))                    # touched, same bytes
        self.assertEqual(pxr_engine.cache_key(info["deps"], [], self.rules, {}), k1)
        fx.png(self.d, "brick.png", colour=(1, 2, 3))                        # new bytes
        self.assertNotEqual(pxr_engine.cache_key(info["deps"], [], self.rules, {}), k1)
        self.assertNotEqual(pxr_engine.cache_key(info["deps"], ["shape=flat"], self.rules, {}), k1)

    def test_a_broken_file_is_an_analysis_not_a_crash(self):
        bad = os.path.join(self.d, "broken.usda")
        open(bad, "w").write("#usda 1.0\nthis is not usd\n")
        info, _ = self.run_job(bad)
        self.assertFalse(info["ok"])
        self.assertTrue(info["error"])


if __name__ == "__main__":
    unittest.main()


class FoliageCutout(unittest.TestCase):
    def test_a_foliage_path_turns_blend_into_a_cutout(self):
        import json, tempfile
        from godot_gulp import engine
        self.assertTrue(engine.is_foliage("Meshes/Foliage/Trees/SM_Beech.usd", engine.FOLIAGE_DEFAULT))
        self.assertTrue(engine.is_foliage("Meshes\\foliage\\Bush.usd", engine.FOLIAGE_DEFAULT))       # any case, any slash
        self.assertFalse(engine.is_foliage("Meshes/Architecture/Window.usd", engine.FOLIAGE_DEFAULT))
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.gltf")
        json.dump({"materials": [{"name": "Leaves", "alphaMode": "BLEND"}, {"name": "Bark"},
                                 {"name": "Glass", "alphaMode": "MASK", "alphaCutoff": 0.3}]}, open(p, "w"))
        notes = []
        self.assertEqual(engine.foliage_cutout(p, notes), 1)
        g = json.load(open(p))
        self.assertEqual((g["materials"][0]["alphaMode"], g["materials"][0]["alphaCutoff"]), ("MASK", 0.5))
        self.assertNotIn("alphaMode", g["materials"][1])
        self.assertEqual(g["materials"][2]["alphaCutoff"], 0.3)                                     # an authored cutout kept
        self.assertIn("Leaves: foliage", notes[0])

    def test_the_foliage_globs_change_only_a_matching_files_cache_key(self):
        from godot_gulp import engine
        k_old = engine.cache_key([], ["v=1"], None)                            # before the option existed
        self.assertEqual(engine.cache_key([], ["v=1"], None, foliage=()), k_old)   # a non-foliage file: still cached
        self.assertNotEqual(engine.cache_key([], ["v=1"], None, foliage=("*Foliage/*",)), k_old)
