"""Pack-level helpers: texture recolour, texture lookup by name, the per-mesh report and the Godot import check.

The Godot check needs no game project: it runs a stub project (made in a temp dir) whose one script loads each glTF
with GLTFDocument and reports tris, surfaces and how many surfaces got a base colour texture. A check that does not
run marks every converted mesh NOT CHECKED; nothing is ever reported OK unchecked.
"""
import csv, hashlib, json, os, re, shutil, subprocess, tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_EXT = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".webp")
# spelling variants of each map role, for packs that name maps differently
ROLE_ALIASES = {"BC": ("bc", "basecolor", "base_color", "albedo", "diffuse", "color", "col", "diff"),
                "N": ("n", "normal", "nrm", "norm", "normalgl"),
                "metallic": ("metallic", "metal", "metalness", "mtl"),
                "roughness": ("roughness", "rough", "rgh"),
                "occlusion": ("occlusion", "ao", "ambientocclusion")}


def rel(p, root):
    return os.path.relpath(p, root).replace(os.sep, "/")


FORMATS = {"usd": (".usd", ".usda", ".usdc", ".usdz"), "fbx": (".fbx",), "obj": (".obj",), "dae": (".dae",),
           "blend": (".blend",)}
FORMATS["all"] = FORMATS["usd"] + FORMATS["fbx"] + FORMATS["obj"] + FORMATS["dae"] + FORMATS["blend"]


def find_files(src, only=(), exts=FORMATS["all"]):
    import fnmatch
    out = []
    for dp, _dn, fn in os.walk(src):
        for f in fn:
            if f.lower().endswith(exts):
                p = os.path.join(dp, f)
                if not only or any(fnmatch.fnmatch(rel(p, src), g) for g in only):
                    out.append(p)
    return sorted(out)


def gltf_names(rels):
    """Each source's output path: <stem>.gltf, or <stem>.<ext>.gltf when another source in its folder shares the stem
    (a pack shipping axe.fbx and axe.obj side by side)."""
    stems = {}
    for r in rels:
        stems.setdefault(os.path.splitext(r)[0].lower(), []).append(r)
    return {r: os.path.splitext(r)[0] + ".gltf" if len(stems[os.path.splitext(r)[0].lower()]) == 1 else r + ".gltf" for r in rels}


class Textures:
    """Derived textures under <out>/textures, cached by their arguments, written atomically."""

    def __init__(self, out):
        self.dir = os.path.join(out, "textures")
        os.makedirs(self.dir, exist_ok=True)
        self.made = {}

    def recolor(self, src, hue_to, sat_mul=1.0, val_mul=1.0):
        """A hue-moved copy of a base colour map; returns its path relative to <out>."""
        from PIL import Image
        key = "rc|%s|%s|%s|%s" % (src, hue_to, sat_mul, val_mul)
        if key in self.made:
            return self.made[key]
        name = "%s_rc%s.png" % (os.path.splitext(os.path.basename(src))[0], hashlib.sha1(key.encode()).hexdigest()[:6])
        dst = os.path.join(self.dir, name)
        if not os.path.exists(dst):
            im = Image.open(src).convert("RGB")
            hsv = np.asarray(im.convert("HSV")).astype(np.float32)
            h, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            w = sat > 40                                    # the saturated pixels decide the current hue
            cur = float(np.median(h[w])) * 360.0 / 255.0 if w.any() else 0.0
            h2 = (h + (hue_to - cur) * 255.0 / 360.0) % 256.0
            arr = np.stack([h2, np.clip(sat * sat_mul, 0, 255), np.clip(val * val_mul, 0, 255)], axis=-1).astype(np.uint8)
            tmp = "%s.%d.tmp" % (dst, os.getpid())
            Image.fromarray(arr, "HSV").convert("RGB").save(tmp, "PNG", compress_level=3)
            os.replace(tmp, dst)
        self.made[key] = "textures/" + name
        return self.made[key]


def _tokens(name):
    return [t for t in re.split(r"[_\-. ()]+", os.path.splitext(name)[0].lower()) if t]


def resolve_by_name(usd, mat):
    """Texture files for a material whose authored files do not resolve. Candidates: images under <dir>/textures (and
    its subfolders), <dir>/tex, <dir> and the parent's textures folder. A file matches when one of the material's name
    tokens (MI_<X> -> X, or the stem of an authored texture) is a token of the file name and a role spelling
    (ROLE_ALIASES) is another. A 1K/2K file beats a larger one. -> {role: path}"""
    base = os.path.dirname(usd)
    roots = [os.path.join(base, "textures"), os.path.join(base, "tex"), base, os.path.join(os.path.dirname(base), "textures")]
    cands = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dp, _dn, fn in os.walk(r):
            if dp != base and dp.count(os.sep) - r.count(os.sep) > 2:
                continue
            cands += [os.path.join(dp, f) for f in fn if f.lower().endswith(IMG_EXT)]
    nm = re.sub(r"\.\d+$", "", mat["name"])
    stems = []
    m = re.match(r"^MI_(.+)$", nm)
    if m:
        stems.append(m.group(1).lower())
    stems.append(nm.lower())
    stems += [t for t in _tokens(nm)[::-1] if len(t) > 2 and t not in ("mi", "material", "mat")]
    for n in mat.get("nodes", []):
        if n.get("path"):
            b = os.path.splitext(os.path.basename(n["path"]))[0]
            stems.append(re.sub(r"[_\-. ](bc|basecolor|base_color|albedo|diffuse|color|n|normal|nrm|metallic|metal|roughness|rough|occlusion|ao|orm)$",
                                "", b, flags=re.I).lower())
    found = {}
    pref = lambda p: (0 if re.search(r"(^|[_\-. ])(1k|2k)(?=[_\-. ]|$)", os.path.basename(p), re.I) else 1, len(p))
    for stem in stems:
        stoks = _tokens(stem)
        for role, aliases in ROLE_ALIASES.items():
            if role in found:
                continue
            ok = []
            for c in cands:
                ft = _tokens(os.path.basename(c))
                ft += [x + y for x, y in zip(ft, ft[1:])]          # "Hand Rake_BC" serves a material named handrake
                tail = stem.replace(" ", "_") in os.path.basename(c).lower().replace(" ", "_") or all(t in ft for t in stoks)
                if tail and any(a in ft for a in aliases):
                    ok.append(c)
            if ok:
                found[role] = sorted(ok, key=pref)[0]
    return found


# ---- the Godot import check

STUB_PROJECT = """config_version=5

[application]
config/name="godot-gulp import check"
"""


def godot_check(out, rows, godot="godot", batch=50, log_name="godot_check.log"):
    """Every converted row comes back godot_ok True or False, never unset. Returns "" when the check ran, else why not."""
    items = [os.path.join(out, r["gltf_rel"]) for r in rows if r.get("ok")]
    if not items:
        return ""
    proj = tempfile.mkdtemp(prefix="godot-gulp-check-")
    with open(os.path.join(proj, "project.godot"), "w") as f:
        f.write(STUB_PROJECT)
    shutil.copy(os.path.join(HERE, "godot", "check_import.gd"), os.path.join(proj, "check_import.gd"))
    os.makedirs(os.path.join(out, ".analysis"), exist_ok=True)
    log = os.path.join(out, ".analysis", log_name)
    env = dict(os.environ, XDG_DATA_HOME=os.path.join(proj, ".data"))
    why, got = "", {}
    with open(log, "w") as fh:
        for b in range(0, len(items), batch):
            lst, res = os.path.join(proj, "list.json"), os.path.join(proj, "res.json")
            json.dump(items[b:b + batch], open(lst, "w"))
            if os.path.exists(res):
                os.remove(res)
            try:
                p = subprocess.run([godot, "--headless", "--audio-driver", "Dummy", "--path", proj, "-s", "res://check_import.gd",
                                    "--", lst, res], env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=3600)
                if p.returncode != 0:
                    why = why or "godot exited %d (batch from %d)" % (p.returncode, b)
            except (OSError, subprocess.TimeoutExpired) as e:
                why = why or "godot did not run: %s" % e
            if os.path.isfile(res):
                got.update({os.path.normpath(g["path"]): g for g in json.load(open(res))})
            else:
                why = why or "godot wrote no result (batch from %d)" % b
    shutil.rmtree(proj, ignore_errors=True)
    for r in rows:
        if not r.get("ok"):
            continue
        g = got.get(os.path.normpath(os.path.join(out, r["gltf_rel"])))
        if g is None:
            r["godot_ok"], r["godot_notes"] = False, "NOT CHECKED: %s (log %s)" % (why or "missing from the check's result", log)
            continue
        r["godot_ok"] = g.get("err") == 0 and g.get("surfaces", 0) > 0
        r["godot_tris"], r["godot_surfaces"], r["godot_textured"] = g.get("tris"), g.get("surfaces"), g.get("textured")
        r["godot_notes"] = g.get("notes", "")
        if r.get("expect_albedo", 0) and (g.get("textured") or 0) < r["expect_albedo"]:
            r["godot_ok"] = False
            r["godot_notes"] = ("%s base colour textures expected, %s loaded; " % (r["expect_albedo"], g.get("textured"))) + r["godot_notes"]
    return why if not got else ""


def check_line(rows):
    conv = [r for r in rows if r.get("ok")]
    if conv and all(r.get("godot_ok") is None for r in conv):
        return " (Godot check SKIPPED: nothing here is Godot-verified)"
    unchecked = sum(1 for r in conv if str(r.get("godot_notes", "")).startswith("NOT CHECKED"))
    return "; NOT CHECKED: %d (see Failed)" % unchecked if unchecked else ""


def stamp_source(out, rows, source):
    """Where the inputs came from and under which licence (--source-store/--source-licence/--source-id), on every
    row and in every converted glTF's asset.extras.source, so a downstream ledger can flag what needs a check
    (a Unity Asset Store Restricted Asset, an engine-only licence). Never inferred: only what the user states."""
    source = {k: v for k, v in (source or {}).items() if v}
    if not source:
        return
    for r in rows:
        r.update({"source_" + k: v for k, v in source.items()})
        p = os.path.join(out, r.get("gltf_rel", ""))
        if not r.get("ok") or not os.path.isfile(p):
            continue
        with open(p) as fh:
            gl = json.load(fh)
        gl.setdefault("asset", {}).setdefault("extras", {})["source"] = source
        tmp = p + ".%d.tmp" % os.getpid()
        with open(tmp, "w") as fh:
            json.dump(gl, fh, separators=(",", ":"))
        os.replace(tmp, p)


def write_report(out, rows, title="godot-gulp report", speed=""):
    cols = ["id", "ok", "meshes", "unique_meshes", "tris", "verts", "w_m", "d_m", "h_m", "materials", "seconds", "notes", "problems",
            "godot_ok", "godot_tris", "godot_surfaces", "godot_textured", "godot_notes", "error",
            "source_store", "source_licence", "source_id"]
    with open(os.path.join(out, "report.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    ok = sum(1 for r in rows if r.get("ok"))
    gok = sum(1 for r in rows if r.get("godot_ok") is True)
    bad = [r for r in rows if not r.get("ok") or r.get("godot_ok") is False]
    prob = [r for r in rows if r.get("problems")]
    L = ["# " + title, "",
         "- Files: %d; converted: %d; Godot import OK: %d%s" % (len(rows), ok, gok, check_line(rows)),
         "- Tris total: %d" % sum(int(r.get("tris") or 0) for r in rows),
         "- Missing maps: %d files" % len(prob),
         "- Failed conversions or imports: %d" % len(bad)] + (["- Speed: " + speed] if speed else []) + [""]
    for head, lst, key in (("Failed", bad, "error"), ("Missing maps", prob, "problems")):
        if lst:
            L += ["## " + head, ""] + ["- `%s`: %s" % (r["id"], r.get(key) or r.get("godot_notes") or "") for r in lst[:200]] + [""]
    with open(os.path.join(out, "REPORT.md"), "w") as f:
        f.write("\n".join(L) + "\n")
