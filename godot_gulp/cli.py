"""godot-gulp: convert 3D asset packs to glTF for Godot, ahead of time.

    godot-gulp pack <src dir> <out dir> [--rules rules.json] [--jobs N] [--variant set=value] [--only GLOB]
                                        [--force] [--godot PATH | --no-godot] [--title TEXT]
    godot-gulp file <in.usd|.fbx|.obj|.dae|.blend> <out.gltf> [--variant set=value] [--json]
    godot-gulp unity <pack.unitypackage | project folder> <out dir> [--only GLOB] [--godot PATH | --no-godot]
    godot-gulp unreal <Content folder> <out dir> [--mount /Engine=PATH] [--only GLOB] [--godot PATH | --no-godot]
    godot-gulp conformance [--godot PATH]

pack: every USD, FBX, OBJ, DAE and .blend (or one --format) under <src dir> becomes <out dir>/<same path>.gltf (+ .bin), textures shared in <out dir>/textures,
a report (REPORT.md, report.csv) and a per-file analysis in <out dir>/.analysis. Unchanged files (by content: every
layer and texture they use, the rules, the variants) are skipped. Then Godot imports every result (a stub project,
no game project needed); a check that cannot run exits 3 and marks every file NOT CHECKED.
Environment: GODOT_GULP_GODOT (the Godot binary, default "godot"), GODOT_GULP_JOBS (default: CPU count, max 8).
"""
import argparse, json, os, sys, time
from concurrent.futures import ProcessPoolExecutor

from . import __version__


def _jobs():
    try:
        return int(os.environ.get("GODOT_GULP_JOBS", ""))
    except ValueError:
        return min(8, os.cpu_count() or 2)


def _source(a):
    return {"store": a.source_store, "licence": a.source_licence, "id": a.source_id}


def cmd_pack(a):
    from . import engine, report
    src, out = os.path.abspath(a.src), os.path.abspath(a.out)
    adir = os.path.join(out, ".analysis")
    os.makedirs(adir, exist_ok=True)
    tex_dir = os.path.join(out, "textures")
    rules = os.path.abspath(a.rules) if a.rules else None
    foliage = () if a.foliage_cutout == ["none"] else tuple(a.foliage_cutout or engine.FOLIAGE_DEFAULT)

    def mine(rel):                    # the globs go into a file's cache key only when they match it
        return foliage if engine.is_foliage(rel, foliage) else ()
    memo_path = os.path.join(adir, "hashes.json")
    try:
        memo = json.load(open(memo_path))
    except (OSError, ValueError):
        memo = {}
    files = report.find_files(src, a.only or (), report.FORMATS[a.format])
    names = report.gltf_names([report.rel(u, src) for u in files])
    todo, cached = [], 0
    for u in files:
        r = report.rel(u, src)
        gl = os.path.join(out, names[r])
        an = os.path.join(adir, r.replace("/", "__") + ".json")
        if not a.force and os.path.isfile(gl) and os.path.isfile(an):
            try:
                prev = json.load(open(an))
            except ValueError:
                prev = {}
            if prev.get("engine") == engine.ENGINE and prev.get("ok") and prev.get("deps") and \
                    prev.get("cache_key") == engine.cache_key(prev["deps"], a.variant, rules, memo, mine(r)):
                cached += 1
                continue
        todo.append({"id": r, "usd": u, "gltf": gl, "analysis": an, "tex_dir": tex_dir, "variants": a.variant, "rules": rules,
                     "foliage": foliage})
    print("files %d, cached %d, to convert %d (%d workers)" % (len(files), cached, len(todo), a.jobs))
    t0 = time.time()
    by_id = {t["id"]: t for t in todo}
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for n, info in enumerate(ex.map(engine.job, todo, chunksize=4), 1):
            if info.get("ok"):
                info["cache_key"] = engine.cache_key(info["deps"], a.variant, rules, memo, mine(info["id"]))
            json.dump(info, open(by_id[info["id"]]["analysis"], "w"))
            if n % 50 == 0:
                print("  %d/%d converted (%.1f s)" % (n, len(todo), time.time() - t0))
    wall = time.time() - t0
    json.dump(memo, open(memo_path, "w"))
    rows = []
    for u in files:
        r = report.rel(u, src)
        an = os.path.join(adir, r.replace("/", "__") + ".json")
        if not os.path.isfile(an):
            continue
        info = json.load(open(an))
        row = {"id": r, "gltf_rel": names[r], "ok": info.get("ok"), "error": info.get("error", ""),
               "seconds": info.get("seconds"), "expect_albedo": info.get("expect_albedo", 0),
               "notes": "; ".join(info.get("texture_notes", [])), "problems": "; ".join(info.get("problems", []))}
        if info.get("size"):
            row.update(meshes=info["meshes"], unique_meshes=info.get("unique_meshes"), tris=info["tris"], verts=info["verts"],
                       w_m=info["size"][0], d_m=info["size"][1], h_m=info["size"][2], materials=len(info.get("materials", [])))
        rows.append(row)
    why = "" if a.no_godot else report.godot_check(out, rows, a.godot)
    secs = sorted(r["seconds"] for r in rows if r.get("seconds") is not None)
    speed = "%d converted in %.1f s on %d workers; per file median %.2f s; %d unchanged and skipped" % (
        len(todo), wall, a.jobs, secs[len(secs) // 2] if secs else 0, cached)
    report.stamp_source(out, rows, _source(a))
    report.write_report(out, rows, a.title, speed)
    print(speed)
    print("report:", os.path.join(out, "REPORT.md"))
    if why:
        print("godot-gulp: the Godot import check did not run (%s); every converted file is marked NOT CHECKED" % why, file=sys.stderr)
        return 3
    return 0


def cmd_file(a):
    from . import convert, fbx
    if a.src.lower().endswith(".blend"):
        from . import blend
        info = blend.convert_or_blender(a.src, a.dst)
    elif a.src.lower().endswith(".dae"):
        from . import dae
        info = dae.convert(a.src, a.dst)
    elif a.src.lower().endswith(fbx.MODEL_EXTS):
        info = fbx.convert(a.src, a.dst)
    else:
        info, _stage = convert.convert(a.src, a.dst, a.variant)
    print(json.dumps(info) if a.json else "%s: %d meshes, %d tris, %d verts" % (a.dst, info["meshes"], info["tris"], info["verts"]))
    return 0


def cmd_unity(a):
    from . import report, unity
    if getattr(a, "fileid_map", ""):
        os.environ["GODOT_GULP_UNITY_IDS"] = os.path.abspath(a.fileid_map)    # read by the worker processes too
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    rows = unity.convert_source(os.path.abspath(a.src), out, a.only or ())
    why = "" if a.no_godot else report.godot_check(out, rows, a.godot)
    report.stamp_source(out, rows, _source(a))
    report.write_report(out, rows, a.title, "%d assets in %.1f s" % (len(rows), time.time() - t0))
    print("%d assets; report: %s" % (len(rows), os.path.join(out, "REPORT.md")))
    if why:
        print("godot-gulp: the Godot import check did not run (%s)" % why, file=sys.stderr)
        return 3
    return 0 if all(r.get("ok") for r in rows) else 1


def cmd_unreal(a):
    from . import report
    from .unreal import convert as ue
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    mounts = {}
    for m in a.mount or []:
        k, _, v = m.partition("=")
        mounts[k] = os.path.abspath(v)
    t0 = time.time()
    rows = ue.convert_source(os.path.abspath(a.src), out, a.as_mount, mounts, a.only or ())
    why = "" if a.no_godot else report.godot_check(out, rows, a.godot)
    report.stamp_source(out, rows, _source(a))
    report.write_report(out, rows, a.title, "%d assets in %.1f s" % (len(rows), time.time() - t0))
    print("%d assets; report: %s" % (len(rows), os.path.join(out, "REPORT.md")))
    if why:
        print("godot-gulp: the Godot import check did not run (%s)" % why, file=sys.stderr)
        return 3
    return 0 if all(r.get("ok") for r in rows) else 1


def cmd_conformance(a):
    from . import conformance
    return conformance.main(["--godot", a.godot] + (["--out", a.out] if a.out else []))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="godot-gulp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="godot-gulp " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    godot = os.environ.get("GODOT_GULP_GODOT", "godot")
    p = sub.add_parser("pack", help="convert every USD under a folder")
    p.add_argument("src")
    p.add_argument("out")
    p.add_argument("--rules", help="material rules table (JSON); see examples/rules.example.json")
    p.add_argument("--foliage-cutout", action="append", metavar="GLOB",
                   help="paths whose alpha-blended materials become cutouts (MASK, 0.5); repeatable; default *Foliage/*; "
                        "'none' turns it off")
    p.add_argument("--jobs", type=int, default=_jobs())
    p.add_argument("--variant", action="append", default=[], help="set=value (repeatable)")
    p.add_argument("--only", action="append", help="glob on the path under src (repeatable)")
    p.add_argument("--format", default="all", choices=("all", "usd", "fbx", "obj", "dae", "blend"),
                   help="which sources to convert when a pack ships the same models in several formats (default all)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--godot", default=godot)
    p.add_argument("--source-store", default="", help="where the inputs came from (e.g. unity-asset-store, fab, cgtrader)")
    p.add_argument("--source-licence", default="", help="the licence they are held under, as you know it (e.g. standard-eula, restricted)")
    p.add_argument("--source-id", default="", help="the listing or order id")
    p.add_argument("--no-godot", action="store_true")
    p.add_argument("--title", default="godot-gulp report")
    f = sub.add_parser("file", help="convert one USD file")
    f.add_argument("src")
    f.add_argument("dst")
    f.add_argument("--variant", action="append", default=[])
    f.add_argument("--json", action="store_true")
    u = sub.add_parser("unity", help="convert a .unitypackage or a Unity project folder (prefabs, scenes, models)")
    u.add_argument("src")
    u.add_argument("out")
    u.add_argument("--only", action="append", help="glob on the asset path (repeatable)")
    u.add_argument("--godot", default=godot)
    u.add_argument("--source-store", default="", help="where the inputs came from (e.g. unity-asset-store, fab, cgtrader)")
    u.add_argument("--source-licence", default="", help="the licence they are held under, as you know it (e.g. standard-eula, restricted)")
    u.add_argument("--source-id", default="", help="the listing or order id")
    u.add_argument("--no-godot", action="store_true")
    u.add_argument("--title", default="godot-gulp unity report")
    u.add_argument("--fileid-map", default="", help="a .tsv of each model's sub-assets as Unity named them (model, class, "
                   "name, fileID), e.g. written through the Unity CLI: exact meshes where Unity 2019.3+ hashed the ids")
    e = sub.add_parser("unreal", help="convert an Unreal Content folder (static meshes and levels, editor-saved)")
    e.add_argument("src", help="the Content folder (mounted as /Game, or --as-mount)")
    e.add_argument("out")
    e.add_argument("--as-mount", default="/Game")
    e.add_argument("--mount", action="append", help="/Engine=<UE>/Engine/Content, /<Plugin>=<plugin>/Content (repeatable)")
    e.add_argument("--only", action="append", help="glob on the asset path (repeatable)")
    e.add_argument("--godot", default=godot)
    e.add_argument("--source-store", default="", help="where the inputs came from (e.g. unity-asset-store, fab, cgtrader)")
    e.add_argument("--source-licence", default="", help="the licence they are held under, as you know it (e.g. standard-eula, restricted)")
    e.add_argument("--source-id", default="", help="the listing or order id")
    e.add_argument("--no-godot", action="store_true")
    e.add_argument("--title", default="godot-gulp unreal report")
    c = sub.add_parser("conformance", help="author the synthetic fixtures, convert them and import them in Godot")
    c.add_argument("--godot", default=godot)
    c.add_argument("--out", default="")
    a = ap.parse_args(argv)
    return {"pack": cmd_pack, "file": cmd_file, "unity": cmd_unity, "unreal": cmd_unreal,
            "conformance": cmd_conformance}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
