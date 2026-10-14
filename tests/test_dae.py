"""COLLADA to glTF on a synthetic file (godot_gulp/dae_fixtures.py): Z up in centimetres to glTF's space, a transform
stack, a polylist with its texture found beside the file, a skin, and a matrix animation."""
import os, tempfile, unittest

import numpy as np

from godot_gulp import dae, dae_fixtures as df
from test_convert import Gltf
from test_unity import worlds


class Dae(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.info = dae.convert(df.scene(os.path.join(cls.d, "src")), os.path.join(cls.d, "out", "scene.gltf"))
        cls.g = Gltf(os.path.join(cls.d, "out", "scene.gltf"))

    def node(self, name):
        return next(i for i, n in enumerate(self.g.j["nodes"]) if n["name"] == name)

    def test_quad_in_metres_y_up(self):
        w = worlds(self.g)
        q = self.node("Quad")
        np.testing.assert_allclose(w[q][:3, 3], [0, 2, 0], atol=1e-9)                # translate 0 0 200 cm, Z up
        mesh = self.g.j["meshes"][self.g.j["nodes"][q]["mesh"]]
        p = mesh["primitives"][0]
        P = self.g.acc(p["attributes"]["POSITION"])
        W = (np.c_[P, np.ones(len(P))] @ w[q].T)[:, :3]
        np.testing.assert_allclose(W.max(0) - W.min(0), [1, 0, 1], atol=1e-6)        # a 1 m square, lying flat
        t = W[self.g.acc(p["indices"]).reshape(-1, 3)]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        self.assertTrue((n[:, 1] > 0).all())                                          # faces up, counter-clockwise
        self.assertEqual(self.info["tris"], 3)                                         # 2 (quad) + 1 (skinned)
        m = self.g.j["materials"][p["material"]]
        self.assertEqual(m["name"], "Grass")
        self.assertIn("baseColorTexture", m["pbrMetallicRoughness"])

    def test_skin(self):
        (skin,) = self.g.j["skins"]
        self.assertEqual([self.g.j["nodes"][j]["name"] for j in skin["joints"]], ["Root", "Tip"])
        ibm = self.g.acc(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
        np.testing.assert_allclose(ibm[1][:3, 3], [0, -1, 0], atol=1e-6)               # Tip bound 100 cm up
        w = worlds(self.g)
        np.testing.assert_allclose(w[self.node("Tip")] @ ibm[1], np.eye(4), atol=1e-6)
        sk = next(m for m in self.g.j["meshes"] if m["name"] == "Tri")
        p = sk["primitives"][0]
        J, W = self.g.acc(p["attributes"]["JOINTS_0"]), self.g.acc(p["attributes"]["WEIGHTS_0"])
        P = self.g.acc(p["attributes"]["POSITION"])
        k = int(np.argmin(np.abs(P - [1, 0, 0]).sum(1)))                                 # vertex 1: Root 0.25, Tip 0.75
        self.assertEqual({int(j): round(float(x), 3) for j, x in zip(J[k], W[k]) if x > 0}, {1: 0.75, 0: 0.25})

    def test_matrix_animation(self):
        (a,) = self.g.j["animations"]
        (c,) = a["channels"]                                                             # only the translation moves
        self.assertEqual((c["target"]["node"], c["target"]["path"]), (self.node("Tip"), "translation"))
        v = self.g.acc(a["samplers"][c["sampler"]]["output"])
        np.testing.assert_allclose(v[[0, -1]], [[0, 1, 0], [0.5, 1, 0]], atol=1e-6)
