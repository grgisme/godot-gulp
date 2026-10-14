#!/usr/bin/env python3
"""The converter's conformance suite, geometry: each fixture in conformance/fixtures.py is authored
with OpenUSD, converted by usd2gltf.py, and its glTF read back and checked."""
import os

import json, tempfile, unittest
import numpy as np
from godot_gulp import fixtures as fx
from godot_gulp import convert as usd2gltf

COMP = {5126: np.float32, 5125: np.uint32, 5123: np.uint16}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Gltf:
    def __init__(self, path):
        with open(path) as f:
            self.j = json.load(f)
        with open(os.path.join(os.path.dirname(path), self.j["buffers"][0]["uri"]), "rb") as f:
            self.bin = f.read()

    def acc(self, i):
        a = self.j["accessors"][i]
        v = self.j["bufferViews"][a["bufferView"]]
        arr = np.frombuffer(self.bin, dtype=COMP[a["componentType"]], count=a["count"] * WIDTH[a["type"]], offset=v["byteOffset"])
        return arr.reshape(a["count"], WIDTH[a["type"]]) if WIDTH[a["type"]] > 1 else arr

    def prims(self):
        return [p for m in self.j["meshes"] for p in m["primitives"]]

    def tris(self, p):
        """(n, 3, 3) world positions of a primitive's triangles."""
        return self.acc(p["attributes"]["POSITION"])[self.acc(p["indices"]).reshape(-1, 3)]


def face_normals(t):
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    return n / np.linalg.norm(n, axis=1, keepdims=True)


class Geometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()

    def conv(self, fixture, *variants):
        src = fixture(self.d)
        dst = os.path.join(self.d, "out", os.path.basename(src).replace(".usda", ".gltf"))
        info, _ = usd2gltf.convert(src, dst, variants)
        return info, Gltf(dst)

    def test_a_cube_is_12_outward_triangles_with_normals(self):
        info, g = self.conv(fx.f_cube)
        self.assertEqual((info["meshes"], info["tris"]), (1, 12))
        p = g.prims()[0]
        t = g.tris(p)
        centre = t.mean(axis=1)
        self.assertTrue((np.einsum("ij,ij->i", face_normals(t), centre) > 0).all(), "every face winds outward")
        self.assertIn("NORMAL", p["attributes"])
        self.assertEqual(info["size"], [1.0, 1.0, 1.0])

    def test_hole_faces_are_dropped(self):
        self.assertEqual(self.conv(fx.f_holes)[0]["tris"], 8)

    def test_a_left_handed_quad_still_faces_its_front(self):
        _, g = self.conv(fx.f_left_handed)
        self.assertTrue((face_normals(g.tris(g.prims()[0]))[:, 2] > 0.99).all())

    def test_a_mirrored_quad_is_rewound(self):
        _, g = self.conv(fx.f_mirror)
        self.assertTrue((face_normals(g.tris(g.prims()[0]))[:, 2] > 0.99).all())

    def test_centimetres_and_z_up_become_metres_and_y_up(self):
        info, g = self.conv(fx.f_units_zup)
        pos = g.acc(g.prims()[0]["attributes"]["POSITION"])
        self.assertTrue(np.allclose(pos.max(axis=0), [1.0, 1.0, 0.0], atol=1e-5), pos)   # 100 cm up the Z axis is 1 m up Y
        self.assertAlmostEqual(info["size"][2], 1.0, places=5)

    def test_every_uv_set_with_st_first_and_v_flipped(self):
        info, g = self.conv(fx.f_uv_sets)
        p = g.prims()[0]["attributes"]
        self.assertEqual(info["uv_sets"], ["lightmapUV", "st"])
        uv0, uv1 = g.acc(p["TEXCOORD_0"]), g.acc(p["TEXCOORD_1"])
        pos = g.acc(p["POSITION"])
        corner = {tuple(np.round(q, 3)): i for i, q in enumerate(pos)}
        top_right = corner[(1.0, 1.0, 0.0)]
        self.assertTrue(np.allclose(uv0[top_right], [1.0, 0.0]))     # st (1,1) -> glTF (1, 0): indexed primvar, V flipped
        self.assertTrue(np.allclose(uv1[top_right], [0.5, 0.5]))     # lightmapUV (0.5, 0.5) -> (0.5, 0.5)

    def test_vertex_colour_becomes_color_0(self):
        _, g = self.conv(fx.f_vertex_colour)
        p = g.prims()[0]["attributes"]
        col, pos = g.acc(p["COLOR_0"]), g.acc(p["POSITION"])
        origin = [i for i, q in enumerate(pos) if np.allclose(q, 0)][0]
        self.assertTrue(np.allclose(col[origin], [1, 0, 0, 1]))

    def test_material_subsets_become_primitives(self):
        info, g = self.conv(fx.f_subsets)
        self.assertEqual(info["subsets"], 2)
        self.assertEqual(sorted(len(g.acc(p["indices"])) // 3 for p in g.prims()), [2, 10])

    def test_authored_normals_are_kept(self):
        _, g = self.conv(fx.f_normals)
        n = g.acc(g.prims()[0]["attributes"]["NORMAL"])
        self.assertTrue(np.allclose(n, [0, 0, 1]))

    def test_instanceable_prims_are_realised(self):
        info, g = self.conv(fx.f_instanceable)
        self.assertEqual((info["meshes"], info["tris"]), (2, 24))
        self.assertAlmostEqual(info["size"][0], 4.0, places=5)          # -0.5 .. 3.5

    def test_variants_default_and_selected(self):
        info, _ = self.conv(fx.f_variants)
        self.assertEqual(info["tris"], 12)                              # the authored selection: box
        info, _ = self.conv(fx.f_variants, "shape=flat")
        self.assertEqual(info["tris"], 2)
        with self.assertRaises(RuntimeError):
            self.conv(fx.f_variants, "colour=red")

    def test_invisible_and_guide_prims_are_left_out(self):
        self.assertEqual(self.conv(fx.f_invisible_and_guide)[0]["meshes"], 1)

    def test_the_gltf_is_valid_enough_for_its_readers(self):
        _, g = self.conv(fx.f_subsets)
        for a in g.j["accessors"]:
            v = g.j["bufferViews"][a["bufferView"]]
            self.assertEqual(v["byteOffset"] % 4, 0)
            self.assertLessEqual(v["byteOffset"] + v["byteLength"], g.j["buffers"][0]["byteLength"])
        for p in g.prims():
            self.assertIn("min", g.j["accessors"][p["attributes"]["POSITION"]])



class Materials(unittest.TestCase):
    """Step b: UsdPreviewSurface to glTF PBR, textures, ORM, opacity, normal maps and the UV transforms."""
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()

    def conv(self, fixture):
        src = fixture(self.d)
        out = os.path.join(self.d, "out", os.path.basename(src).replace(".usda", ""))
        dst = os.path.join(out, "m.gltf")
        info, _ = usd2gltf.convert(src, dst)
        return info, Gltf(dst), out

    def img(self, g, out, texinfo):
        from PIL import Image
        uri = g.j["images"][g.j["textures"][texinfo["index"]]["source"]]["uri"]
        return Image.open(os.path.join(out, uri))

    def uv_at(self, g, corner, k="TEXCOORD_0"):
        p = g.prims()[0]["attributes"]
        pos, uv = g.acc(p["POSITION"]), g.acc(p[k])
        return uv[[i for i, q in enumerate(pos) if np.allclose(q, corner)][0]]

    def test_constants_become_factors_emissive_strength_and_ior(self):
        _, g, _ = self.conv(fx.f_mat_constants)
        m = g.j["materials"][0]
        pbr = m["pbrMetallicRoughness"]
        self.assertTrue(np.allclose(pbr["baseColorFactor"], [0.8, 0.2, 0.1, 1]))
        self.assertAlmostEqual(pbr["metallicFactor"], 0.9, places=5)
        self.assertAlmostEqual(pbr["roughnessFactor"], 0.25, places=5)
        self.assertTrue(np.allclose(m["emissiveFactor"], [1.0, 0.5, 0.0]))
        self.assertAlmostEqual(m["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"], 4.0, places=5)
        self.assertAlmostEqual(m["extensions"]["KHR_materials_ior"]["ior"], 1.33, places=5)

    def test_an_emissive_texture_with_a_zero_factor_is_not_written(self):
        # glTF reads it as no glow, but Godot's importer adds the texture anyway (an Unreal mask behind Multiply 0
        # turned 283 store materials orange)
        from PIL import Image
        from godot_gulp import materials as mt
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mask.png")
            Image.new("RGB", (1, 1), (255, 150, 0)).save(p)
            gl = {}
            w = mt.GltfMaterials(gl, d)
            for f, want in (((0.0, 0.0, 0.0, 1.0), False), ((1.0, 1.0, 1.0, 1.0), True)):
                m = mt.Mat("m%d" % want)
                m.tex["emissive"] = mt.Tex(p, "rgb", 0, None, 10497, 10497, f, (0.0, 0.0, 0.0, 0.0), "auto", None)
                g = gl["materials"][w.index(m)]
                self.assertEqual("emissiveTexture" in g, want)
                self.assertEqual("emissiveFactor" in g, want)

    def test_a_diffuse_texture_its_scale_and_its_wrap(self):
        _, g, out = self.conv(fx.f_mat_diffuse_texture)
        pbr = g.j["materials"][0]["pbrMetallicRoughness"]
        self.assertEqual(self.img(g, out, pbr["baseColorTexture"]).convert("RGB").getpixel((0, 0)), (255, 0, 0))
        self.assertTrue(np.allclose(pbr["baseColorFactor"], [0.5, 0.5, 0.5, 1]))
        self.assertEqual(g.j["samplers"][g.j["textures"][0]["sampler"]]["wrapS"], 33648)        # mirror

    def test_opacity_constant_blends_and_a_mask_cuts_from_the_base_colour_alpha(self):
        _, g, out = self.conv(fx.f_mat_opacity)
        m = {x["name"]: x for x in g.j["materials"]}
        self.assertEqual(m["Half"]["alphaMode"], "BLEND")
        self.assertAlmostEqual(m["Half"]["pbrMetallicRoughness"]["baseColorFactor"][3], 0.5)
        self.assertEqual((m["Cut"]["alphaMode"], m["Cut"]["alphaCutoff"]), ("MASK", 0.5))
        im = self.img(g, out, m["Cut"]["pbrMetallicRoughness"]["baseColorTexture"])
        self.assertEqual((im.getpixel((1, 1)), im.getpixel((6, 1))), ((0, 255, 0, 255), (0, 255, 0, 0)))

    def test_roughness_metallic_occlusion_pack_into_one_orm_map(self):
        _, g, out = self.conv(fx.f_mat_orm)
        m = g.j["materials"][0]
        orm = m["pbrMetallicRoughness"]["metallicRoughnessTexture"]
        self.assertEqual(orm["index"] if False else self.img(g, out, orm).getpixel((2, 2)), (128, 200, 60))
        self.assertEqual(self.img(g, out, m["occlusionTexture"]).getpixel((2, 2)), (128, 200, 60))   # the same map

    def test_a_directx_normal_map_has_its_green_flipped(self):
        _, g, out = self.conv(fx.f_mat_normal)
        m = g.j["materials"][0]
        self.assertEqual(self.img(g, out, m["normalTexture"]).getpixel((0, 0)), (128, 191, 255))
        self.assertNotIn("scale", m["normalTexture"])                                         # scale 2, bias -1: unit
        self.assertTrue(any("green flipped" in n for n in m["extras"]["usd_notes"]))

    def test_a_uv_transform_is_baked_into_the_uvs(self):
        _, g, _ = self.conv(fx.f_mat_uv_transform)
        # USD (1, 1) scaled (2, 3) and moved (0.25, 0.5) is (2.25, 3.5); glTF's V is 1 - 3.5
        self.assertTrue(np.allclose(self.uv_at(g, (1, 1, 0)), [2.25, -2.5]), self.uv_at(g, (1, 1, 0)))
        self.assertNotIn("extensionsUsed", g.j)                                               # nothing left to a reader

    def test_a_rotation_is_baked_not_dropped(self):
        _, g, _ = self.conv(fx.f_mat_uv_rotation)
        # USD (1, 1) turned 90 degrees counter-clockwise is (-1, 1); glTF (-1, 0). Miguel's importer drops this
        self.assertTrue(np.allclose(self.uv_at(g, (1, 1, 0)), [-1.0, 0.0], atol=1e-6), self.uv_at(g, (1, 1, 0)))

    def test_two_transforms_bake_one_and_keep_the_other_as_khr_texture_transform(self):
        _, g, _ = self.conv(fx.f_mat_two_transforms)
        m = g.j["materials"][0]
        self.assertTrue(np.allclose(self.uv_at(g, (1, 1, 0)), [4.0, -3.0]))                   # the base colour's x4 baked
        kt = m["pbrMetallicRoughness"]["metallicRoughnessTexture"]["extensions"]["KHR_texture_transform"]
        self.assertTrue(np.allclose(kt["scale"], [0.25, 0.25]) and np.allclose(kt["offset"], [0.0, 0.75]), kt)
        self.assertIn("KHR_texture_transform", g.j["extensionsUsed"])
        self.assertTrue(any("KHR_texture_transform" in n for n in m["extras"]["usd_notes"]))

    def test_a_texture_reads_the_uv_set_its_reader_names(self):
        info, g, _ = self.conv(fx.f_mat_second_uv_set)
        self.assertEqual(info["uv_sets"], ["detailUV", "st"])
        self.assertEqual(g.j["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["texCoord"], 1)

    def test_a_constant_display_colour_tints_the_default_material(self):
        _, g, _ = self.conv(fx.f_mat_display_colour)
        self.assertTrue(np.allclose(g.j["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"], [0.1, 0.6, 0.3, 1]))

    def test_an_unwired_sibling_ao_map_becomes_the_occlusion(self):
        _, g, out = self.conv(fx.f_mat_sibling_ao)
        m = g.j["materials"][0]
        self.assertEqual(self.img(g, out, m["occlusionTexture"]).getpixel((1, 1))[0], 90)      # ORM R is the AO
        self.assertTrue(any("sibling map" in n for n in m["extras"]["usd_notes"]))

    def test_each_subset_gets_its_bound_material(self):
        _, g, _ = self.conv(fx.f_mat_subsets)
        by_tris = {len(g.acc(p["indices"])) // 3: g.j["materials"][p["material"]]["name"] for p in g.prims()}
        self.assertEqual(by_tris, {2: "Red", 10: "Blue"})



class Instancing(unittest.TestCase):
    """Step c: instancers and instanceable prims as shared glTF meshes, mirrors baked, variants reported."""
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()

    def conv(self, fixture, *variants):
        src = fixture(self.d)
        dst = os.path.join(self.d, "out", os.path.basename(src).replace(".usda", ".gltf"))
        info, _ = usd2gltf.convert(src, dst, variants)
        return info, Gltf(dst)

    def world_points(self, g, node):
        n = g.j["nodes"][node]
        pos = np.concatenate([g.acc(p["attributes"]["POSITION"]) for p in g.j["meshes"][n["mesh"]]["primitives"]]).astype(np.float64)
        if "matrix" in n:
            pos = (np.c_[pos, np.ones(len(pos))] @ np.array(n["matrix"]).reshape(4, 4))[:, :3]
        return pos

    def test_a_point_instancer_places_shared_meshes_and_honours_the_mask(self):
        info, g = self.conv(fx.f_point_instancer)
        self.assertEqual(info["meshes"], 4)                              # 5 instances, 1 masked off
        self.assertEqual(len(g.j["meshes"]), 3)                          # box and card shared; the mirrored card baked
        centres = sorted(round(float(self.world_points(g, i)[:, 0].mean()), 3) for i in range(len(g.j["nodes"])))
        self.assertEqual(centres, [0.0, 2.0, 4.5, 7.5])                  # the card's quad spans x .. x+1; x 6 is masked
        tall = [i for i in range(len(g.j["nodes"])) if np.ptp(self.world_points(g, i)[:, 1]) > 1.5]
        self.assertEqual(len(tall), 1)                                   # the box scaled 2 in Y

    def test_a_mirrored_instance_is_baked_and_still_faces_front(self):
        info, g = self.conv(fx.f_instanceable_mirror)
        self.assertEqual((info["meshes"], info["unique_meshes"]), (2, 2))
        for i, n in enumerate(g.j["nodes"]):
            if "matrix" not in n:                                        # the baked mirror
                p = g.j["meshes"][n["mesh"]]["primitives"][0]
                t = g.tris(p)
                self.assertTrue((face_normals(t)[:, 2] > 0.99).all())

    def test_shared_meshes_are_written_once(self):
        info, g = self.conv(fx.f_instanceable)
        self.assertEqual((info["meshes"], info["unique_meshes"], len(g.j["meshes"])), (2, 1, 1))
        self.assertTrue(all("matrix" in n for n in g.j["nodes"]))

    def test_the_variant_sets_are_reported(self):
        info, _ = self.conv(fx.f_variants)
        self.assertEqual(info["variants"], {"/Root:shape": {"names": ["box", "flat"], "selected": "box"}})


if __name__ == "__main__":
    unittest.main()
