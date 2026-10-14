"""Unreal animation sequences (UE 5.2+ saves: the keys live in the sequence's AnimationSequencerDataModel, a MovieScene
with one control-rig section, one FK control per bone) to glTF animation channels.

    sequence(content, object_path) -> {"name", "skeleton" (object path), "fps", "bones": {bone: (times s, T, R, S)}}

Each control (<bone>_CONTROL) holds nine float channels in the bone's parent space: Translation X/Y/Z (cm),
Rotation X/Y/Z (roll, pitch, yaw in degrees, composed as an FRotator) and Scale X/Y/Z. They are sampled at the union
of their key frames (Unreal keys a baked import every frame, linear), composed in Unreal space and carried into glTF's
space like the bones themselves (mesh.transform_matrix), then split into translation, rotation (x, y, z, w), scale.
Checked against Epic's own glTF export of the same sequences.
"""
import numpy as np

from ..dae import decompose
from . import level, mesh
from .package import UnrealError


def _channel(ctrl, key, i):
    return ctrl.get(key if i == 0 else "%s[%d]" % (key, i))


def _eval(c, frames, fallback):
    """A channel at the given frames: linear between keys, held outside them; its default (or fallback) when unkeyed."""
    if not isinstance(c, dict) or not len(c.get("times", [])):
        d = c.get("default") if isinstance(c, dict) else None
        return np.full(len(frames), fallback if d is None else d, dtype=np.float64)
    t = np.asarray(c["times"], dtype=np.float64)
    v = np.asarray(c["values"], dtype=np.float64)
    if len(v) != len(t):
        raise UnrealError("animation channel: %d times, %d values" % (len(t), len(v)))
    out = np.interp(frames, t, v)
    const = np.asarray(c.get("interp", []))
    if len(const) == len(t) and (const == 1).any():            # constant keys hold their value to the next key
        idx = np.clip(np.searchsorted(t, frames, side="right") - 1, 0, len(t) - 1)
        hold = const[idx] == 1
        out[hold] = v[idx][hold]
    return out


def sequence(content, object_path):
    pkg = content.package(object_path)
    i = content.export_of(pkg, object_path)
    if pkg.exports[i].class_name != "AnimSequence":
        raise UnrealError("%s is a %s" % (object_path, pkg.exports[i].class_name))
    props, _r, _e = pkg.props(i)
    skel = props.get("Skeleton")
    sections = pkg.find("MovieSceneControlRigParameterSection")
    if not sections:
        raise UnrealError("%s: no control-rig section (saved before UE 5.2? the older raw tracks are not read yet)" % object_path)
    sp, _r, _e = pkg.props(sections[0])
    controls = {}
    for x in sp.get("TransformParameterNamesAndCurves") or []:
        if isinstance(x, dict) and x.get("ParameterName"):
            name = x["ParameterName"]
            controls[name[:-len("_CONTROL")] if name.endswith("_CONTROL") else name] = x
    tick = 0.0
    for ctrl in controls.values():
        c = _channel(ctrl, "Translation", 0)
        if isinstance(c, dict) and c.get("tick"):
            tick = c["tick"]
            break
    if not tick:
        sfr = props.get("SamplingFrameRate") or {}
        tick = float(sfr.get("Numerator", 30)) / float(sfr.get("Denominator", 1) or 1)
    bones = {}
    for bone, ctrl in controls.items():
        keyed = [np.asarray(c["times"]) for k in ("Translation", "Rotation", "Scale") for j in range(3)
                 for c in [_channel(ctrl, k, j)] if isinstance(c, dict) and len(c.get("times", []))]
        if not keyed:
            continue                                        # an unkeyed control: the bone keeps its rest pose
        frames = np.unique(np.concatenate(keyed)).astype(np.float64)
        tr = np.stack([_eval(_channel(ctrl, "Translation", j), frames, 0.0) for j in range(3)], 1)
        ro = np.stack([_eval(_channel(ctrl, "Rotation", j), frames, 0.0) for j in range(3)], 1)
        sc = np.stack([_eval(_channel(ctrl, "Scale", j), frames, 1.0) for j in range(3)], 1)
        T, R, S = [], [], []
        for k in range(len(frames)):
            M = np.eye(4)
            M[:3, :3] = level.rotator_matrix(ro[k, 1], ro[k, 2], ro[k, 0]) @ np.diag(sc[k])   # roll X, pitch Y, yaw Z
            M[:3, 3] = tr[k]
            t, q, s = decompose(mesh.S4 @ M @ mesh.S4_INV)
            if R and np.dot(R[-1], q) < 0:
                q = -q
            T.append(t)
            R.append(q)
            S.append(s)
        bones[bone] = ((frames - frames[0]) / tick, np.array(T), np.array(R), np.array(S))
    return {"name": pkg.exports[i].name, "skeleton": skel.path if skel else "", "fps": tick, "bones": bones}


def gltf_animation(seq, key_of_bone):
    """A sequence -> write_gltf's animation dict, channels keyed by the skeleton's node keys."""
    chans = []
    for bone, (times, T, R, S) in seq["bones"].items():
        k = key_of_bone.get(bone)
        if k is None:
            continue
        for path, vals in (("translation", T), ("rotation", R), ("scale", S)):
            if len(vals) > 1 and np.ptp(vals, axis=0).max() > 1e-7 or path != "scale" and len(vals) == 1:
                chans.append({"key": k, "path": path, "times": list(times), "values": vals.tolist()})
    return {"name": seq["name"], "channels": chans}
