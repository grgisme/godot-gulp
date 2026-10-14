"""The pack engine: one process-pool worker per USD file
(usd2gltf.convert), then our repair rules on the written glTF, and a cache key so an unchanged file is never redone.

    job(dict) -> info      the worker: {"id", "usd", "gltf", "tex_dir", "variants", "rules"} in, the analysis out
    cache_key(info, ...)   sha1 over the engine version, the variant choices, the rules table and every dependency
                           (each used layer and texture, by content; a memo keyed on path, size and mtime)

Repair rules (the materials table, first match by material name): uv_world_m (metres one texture repeat
covers: baked into the UVs, not left to KHR_texture_transform), recolor (hue/sat/val), tint, glass, sign; a
window/glass-named material with no base colour map becomes glass; a texture the USD does not resolve is found by
name (report.resolve_by_name).
"""
import fnmatch, hashlib, json, os, re, sys, time
import numpy as np

ENGINE = "pxr-1"                  # bump to redo every cached file after a change to what the engine writes
# foliage: an alpha-blended material under these paths is written as a cutout (alphaMode MASK, alphaCutoff 0.5):
# leaves and grass sort and cast shadows like the solid plants they are (pack --foliage-cutout; "none" turns it off)
FOLIAGE_DEFAULT = ("*Foliage/*",)
_RULES, _RULES_PATH = [], None


def _rules(path):
    global _RULES, _RULES_PATH
    if path != _RULES_PATH:
        _RULES = json.load(open(path))["rules"] if path else []
        _RULES_PATH = path
    return _RULES


def rule_for(name, rules):
    for r in rules:
        if re.search(r["match"], re.sub(r"\.\d+$", "", name)):
            return r
    return None


# ---- the cache

_MEMO = {}


def dep_hash(path, memo=None):
    memo = _MEMO if memo is None else memo
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    k = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    if k not in memo:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        memo[k] = h.hexdigest()
    return memo[k]


def cache_key(deps, variants, rules_path, memo=None, foliage=()):
    h = hashlib.sha1()
    h.update(ENGINE.encode())
    if foliage:                       # only a file the foliage globs match: every other file keeps its old key (cached)
        h.update(json.dumps(sorted(foliage)).encode())
    h.update(json.dumps(sorted(variants or [])).encode())
    h.update((dep_hash(rules_path, memo) if rules_path else "-").encode())
    for d in sorted(deps):
        h.update(d.encode())
        h.update(dep_hash(d, memo).encode())
    return h.hexdigest()


# ---- glTF buffer access (our own files: one buffer, tightly packed)

class Doc:
    def __init__(self, path):
        self.path = path
        with open(path) as f:
            self.j = json.load(f)
        self.bin_path = os.path.join(os.path.dirname(path), self.j["buffers"][0]["uri"])
        with open(self.bin_path, "rb") as f:
            self.bin = bytearray(f.read())

    def acc(self, i):
        a = self.j["accessors"][i]
        v = self.j["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        dt = np.float32 if a["componentType"] == 5126 else np.uint32
        return np.frombuffer(bytes(self.bin), dtype=dt, count=a["count"] * n, offset=v["byteOffset"]).reshape(a["count"], n)

    def put(self, i, arr):
        v = self.j["bufferViews"][self.j["accessors"][i]["bufferView"]]
        data = np.ascontiguousarray(arr, dtype=np.float32).tobytes()
        assert len(data) == v["byteLength"]
        self.bin[v["byteOffset"]:v["byteOffset"] + len(data)] = data

    def save(self):
        with open(self.bin_path, "wb") as f:
            f.write(self.bin)
        with open(self.path, "w") as f:
            json.dump(self.j, f, separators=(",", ":"))

    def uses(self):
        """(primitive, node scale) for every placement: a shared mesh counts once per node."""
        out = []
        for n in self.j["nodes"]:
            if "mesh" not in n:
                continue
            sc = 1.0
            if "matrix" in n:
                m = np.array(n["matrix"]).reshape(4, 4)[:3, :3]
                sc = float(np.mean(np.linalg.norm(m, axis=1)))
            for p in self.j["meshes"][n["mesh"]]["primitives"]:
                out.append((p, sc))
        return out


def metres_per_uv(doc, mat_index, texcoord):
    """(m per U, m per V): the median world length one UV unit covers on the material's triangles."""
    mu, mv = [], []
    for p, sc in doc.uses():
        if p.get("material") != mat_index or ("TEXCOORD_%d" % texcoord) not in p["attributes"]:
            continue
        P = doc.acc(p["attributes"]["POSITION"]).astype(np.float64) * sc
        T = doc.acc(p["attributes"]["TEXCOORD_%d" % texcoord]).astype(np.float64)
        idx = doc.acc(p["indices"]).reshape(-1, 3).astype(np.int64)
        e1, e2 = P[idx[:, 1]] - P[idx[:, 0]], P[idx[:, 2]] - P[idx[:, 0]]
        d1, d2 = T[idx[:, 1]] - T[idx[:, 0]], T[idx[:, 2]] - T[idx[:, 0]]
        det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        ok = np.abs(det) > 1e-9
        if not ok.any():
            continue
        inv = 1.0 / det[ok]
        dpu = (e1[ok] * d2[ok, 1:2] - e2[ok] * d1[ok, 1:2]) * inv[:, None]
        dpv = (e2[ok] * d1[ok, 0:1] - e1[ok] * d2[ok, 0:1]) * inv[:, None]
        mu.append(float(np.median(np.linalg.norm(dpu, axis=1))))
        mv.append(float(np.median(np.linalg.norm(dpv, axis=1))))
    return (float(np.mean(mu)), float(np.mean(mv))) if mu else None


def repair(gltf, rules, tex_dir, notes, problems, info):
    """The materials table on our glTF. Returns the base-colour map count Godot must load."""
    from .report import Textures
    doc = Doc(gltf)
    gdir = os.path.dirname(gltf)
    tex = Textures(os.path.dirname(tex_dir)) if tex_dir else None
    changed = False
    for mi, gm in enumerate(doc.j.get("materials", [])):
        name = gm.get("name", "")
        pbr = gm.setdefault("pbrMetallicRoughness", {})
        r = rule_for(name, rules)
        base = pbr.get("baseColorTexture")
        if r and r.get("uv_world_m") and base is not None:
            mpu = metres_per_uv(doc, mi, base.get("texCoord", 0))
            if mpu:
                info.setdefault("uv_tile_m", {})[name] = [round(mpu[0], 3), round(mpu[1], 3)]
                want = float(r["uv_world_m"])
                su, sv = mpu[0] / want, mpu[1] / want
                if abs(su - 1) > 0.05 or abs(sv - 1) > 0.05:
                    tc = "TEXCOORD_%d" % base.get("texCoord", 0)
                    done = set()
                    for p, _sc in doc.uses():
                        if p.get("material") == mi and tc in p["attributes"] and p["attributes"][tc] not in done:
                            a = p["attributes"][tc]
                            doc.put(a, doc.acc(a) * np.array([su, sv], dtype=np.float32))     # baked: every reader
                            done.add(a)
                    notes.append("%s: UV tiling restored: one texture repeat was %.2f x %.2f m, now %.2f m (x%.2f, x%.2f)" % (
                        name, mpu[0], mpu[1], want, su, sv))
                    changed = True
        if r and r.get("recolor") and base is not None and tex:
            img = doc.j["images"][doc.j["textures"][base["index"]]["source"]]
            src = os.path.normpath(os.path.join(gdir, img["uri"]))
            rc = r["recolor"]
            made = tex.recolor(src, rc["hue_to"], rc.get("sat_mul", 1.0), rc.get("val_mul", 1.0))
            doc.j["images"].append({"uri": os.path.relpath(os.path.join(os.path.dirname(tex_dir), made), gdir).replace(os.sep, "/")})
            doc.j["textures"].append(dict(doc.j["textures"][base["index"]], source=len(doc.j["images"]) - 1))
            base["index"] = len(doc.j["textures"]) - 1
            notes.append("%s: colour recovered (recolor): %s" % (name, r.get("why", "")))
            changed = True
        if r and r.get("tint") and not r.get("sign"):
            f = pbr.get("baseColorFactor", [1, 1, 1, 1])
            pbr["baseColorFactor"] = [f[0] * r["tint"][0], f[1] * r["tint"][1], f[2] * r["tint"][2], f[3]]
            notes.append("%s: colour recovered (tint): %s" % (name, r.get("why", "")))
            changed = True
        if r and r.get("sign"):
            notes.append("%s: SIGN face blank in the USD (in-house sign art job)" % name)
        if r and r.get("glass"):
            g = r["glass"]
            for k in ("baseColorTexture", "metallicRoughnessTexture"):
                pbr.pop(k, None)
            gm.pop("normalTexture", None)
            gm.pop("occlusionTexture", None)
            pbr["baseColorFactor"] = g["color"]
            pbr["metallicFactor"], pbr["roughnessFactor"] = g.get("metallic", 0.0), g.get("roughness", 0.08)
            gm["alphaMode"], gm["doubleSided"] = "BLEND", True
            notes.append("%s: glass from the table" % name)
            changed = True
        elif re.search(r"window|glass|windshield", name, re.I) and "baseColorTexture" not in pbr:
            pbr["baseColorFactor"] = [0.55, 0.66, 0.72, 0.22]
            pbr["metallicFactor"], pbr["roughnessFactor"] = 0.0, 0.05
            gm["alphaMode"], gm["doubleSided"] = "BLEND", True
            notes.append("%s: glass rebuilt from the name (USD exports it opaque)" % name)
            changed = True
        elif "baseColorTexture" not in pbr and any(n.startswith(name + ": diffuse") and "does not resolve" in n for n in notes):
            problems.append("%s: albedo map missing" % name)
    if changed:
        doc.save()
    return sum(1 for p, _ in doc.uses() if "baseColorTexture" in doc.j["materials"][p["material"]].get("pbrMetallicRoughness", {}))


# ---- the worker

def is_foliage(rel, globs):
    r = rel.replace("\\", "/").lower()
    return any(fnmatch.fnmatch(r, g.lower()) for g in globs or ())


def foliage_cutout(gltf_path, notes, cutoff=0.5):
    """Alpha-blended materials of a foliage file become cutouts (MASK at `cutoff`); -> how many changed."""
    with open(gltf_path) as f:
        g = json.load(f)
    n = 0
    for m in g.get("materials", []):
        if m.get("alphaMode") == "BLEND":
            m["alphaMode"], m["alphaCutoff"] = "MASK", cutoff
            n += 1
            notes.append("%s: foliage, alpha blend written as a cutout (alphaCutoff %.2f)" % (m.get("name", "material"), cutoff))
    if n:
        with open(gltf_path, "w") as f:
            json.dump(g, f)
    return n


def job(j):
    """One file: convert, repair, analyse. Never raises: a failure is an analysis with ok False and the error."""
    t0 = time.time()
    out = {"id": j["id"], "usd": j["usd"], "gltf": j["gltf"], "ok": False}
    try:
        from . import materials as usdmat
        from . import convert as usd2gltf, fbx
        from .report import resolve_by_name
        role = {"diffuse": "BC", "normal": "N", "metallic": "metallic", "roughness": "roughness", "occlusion": "occlusion"}
        def resolver(mat_name, slot, authored):
            found = resolve_by_name(j["usd"], {"name": mat_name, "nodes": [{"path": authored}] if authored else []})
            return found.get(role.get(slot, ""))
        usdmat.RESOLVER = resolver
        if j["usd"].lower().endswith(".blend"):               # Blender files, read here (Blender itself as the fallback)
            from . import blend
            info = blend.convert_or_blender(j["usd"], j["gltf"], j.get("tex_dir"))
        elif j["usd"].lower().endswith(".dae"):               # COLLADA, read here
            from . import dae
            info = dae.convert(j["usd"], j["gltf"], j.get("tex_dir"))
        elif j["usd"].lower().endswith(fbx.MODEL_EXTS):      # FBX/OBJ through the vendored ufbx
            info = fbx.convert(j["usd"], j["gltf"], j.get("tex_dir"))
        else:
            info, _stage = usd2gltf.convert(j["usd"], j["gltf"], j.get("variants") or (), j.get("tex_dir"))
        notes = list(info.pop("material_notes"))
        problems = []
        out["expect_albedo"] = repair(j["gltf"], _rules(j.get("rules")), j.get("tex_dir"), notes, problems, out)
        if is_foliage(j["id"], j.get("foliage")):
            foliage_cutout(j["gltf"], notes)
        out.update(ok=True, meshes=info["meshes"], unique_meshes=info["unique_meshes"], tris=info["tris"], verts=info["verts"],
                   size=info["size"], materials=info["material_names"], texture_notes=notes, problems=problems,
                   variants=info["variants"], uv_sets=info["uv_sets"],
                   deps=sorted(set(info["layers"]) | set(info["texture_sources"])))
    except Exception as e:                                   # the report shows it; the run goes on
        import traceback
        out["error"] = "%s: %s" % (type(e).__name__, e)
        out["trace"] = traceback.format_exc()[-1500:]
        out["deps"] = [j["usd"]]
    out["seconds"] = round(time.time() - t0, 3)
    out["engine"] = ENGINE
    return out
