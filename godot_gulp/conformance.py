"""The conformance suite end to end: every synthetic fixture (fixtures.py) authored with OpenUSD, converted, then
imported by Godot in one pass (the same stub-project check as `godot-gulp pack`). Godot must load every base colour
texture each glTF names. The unit asserts live in tests/; this proves Godot takes every result.

    godot-gulp conformance [--godot PATH] [--out DIR]       exit 1 when any fixture fails
"""
import argparse, json, os, sys, tempfile, time

from . import convert, fixtures as fx, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="")
    ap.add_argument("--godot", default=os.environ.get("GODOT_GULP_GODOT", "godot"))
    a = ap.parse_args(argv)
    out = a.out or tempfile.mkdtemp(prefix="godot-gulp-conformance-")
    src = os.path.join(out, "src")
    os.makedirs(src, exist_ok=True)
    rows = []
    for f in fx.ALL:
        path = f(src)
        name = os.path.splitext(os.path.basename(path))[0]
        t0 = time.time()
        try:
            info, _ = convert.convert(path, os.path.join(out, name + ".gltf"))
            with open(os.path.join(out, name + ".gltf")) as fh:
                gl = json.load(fh)
            albedo = sum(1 for m in gl["meshes"] for p in m["primitives"]
                         if "baseColorTexture" in gl["materials"][p["material"]].get("pbrMetallicRoughness", {}))
            rows.append({"id": name, "gltf_rel": name + ".gltf", "ok": True, "tris": info["tris"], "s": time.time() - t0,
                         "expect_albedo": albedo})
        except Exception as e:
            rows.append({"id": name, "gltf_rel": name + ".gltf", "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
    # the Unity fixtures, as a .unitypackage, through the Unity reader
    from . import unity, unity_fixtures
    for r in unity.convert_source(unity_fixtures.package(os.path.join(out, "unity-src")), os.path.join(out, "unity")):
        r["id"], r["gltf_rel"] = "unity:" + r["id"], "unity/" + r["gltf_rel"]
        rows.append(r)
    # the FBX fixture (mesh, texture, skin, take) through the vendored ufbx
    from . import fbx, fbx_fixtures
    t0 = time.time()
    try:
        info = fbx.convert(fbx_fixtures.rigged(os.path.join(out, "fbx-src")), os.path.join(out, "fbx", "rigged.gltf"))
        rows.append({"id": "fbx:rigged", "gltf_rel": "fbx/rigged.gltf", "ok": True, "tris": info["tris"], "s": time.time() - t0,
                     "expect_albedo": 1})
    except Exception as e:
        rows.append({"id": "fbx:rigged", "gltf_rel": "fbx/rigged.gltf", "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
    # the COLLADA fixture (transform stack, texture, skin, animation)
    from . import dae, dae_fixtures
    t0 = time.time()
    try:
        info = dae.convert(dae_fixtures.scene(os.path.join(out, "dae-src")), os.path.join(out, "dae", "scene.gltf"))
        rows.append({"id": "dae:scene", "gltf_rel": "dae/scene.gltf", "ok": True, "tris": info["tris"], "s": time.time() - t0,
                     "expect_albedo": 1})
    except Exception as e:
        rows.append({"id": "dae:scene", "gltf_rel": "dae/scene.gltf", "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
    # the Unreal sample (materials, a mesh, a level) through the Unreal reader
    from .unreal import convert as ue, fixtures as ufx
    for r in ue.convert_source(ufx.sample(os.path.join(out, "unreal-src")), os.path.join(out, "unreal")):
        r["id"], r["gltf_rel"] = "unreal:" + r["id"], "unreal/" + r["gltf_rel"]
        rows.append(r)
    why = report.godot_check(out, rows, a.godot)
    bad = 0
    for r in rows:
        good = r["ok"] and r.get("godot_ok") is True
        bad += not good
        print("%-4s %-22s tris %-5s %5.2fs godot surfaces %s textured %s/%s %s" % (
            "ok" if good else "FAIL", r["id"], r.get("tris", "-"), r.get("s", 0), r.get("godot_surfaces", "-"),
            r.get("godot_textured", "-"), r.get("expect_albedo", "-"), r.get("error") or r.get("godot_notes") or ""))
    print("conformance: %d fixtures, %d failed%s (out %s)" % (len(rows), bad, "; godot: " + why if why else "", out))
    return 1 if bad or why else 0


if __name__ == "__main__":
    sys.exit(main())
