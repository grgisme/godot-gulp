"""Writes two small fixtures with headless Blender (run once, by hand; the files are committed):

    blender -b --factory-startup --python tests/make_blend_rig_fixture.py -- tests/data

Our own content, made from scratch (no default-scene objects kept):
- rig_42.blend: "Body", a quad skinned to the 2-bone armature "Rig" by an enabled Armature modifier (the reader must hand
  it to Blender, which keeps the skin)
- hidden_42.blend: "Box", a plain quad; "WGT-shape", a quad hidden from render with an enabled Subdivision modifier, used
  as a bone's custom shape (a rig widget: never rendered, so it must neither block the native reader nor be exported)
"""
import os
import sys
import bpy

out = sys.argv[sys.argv.index("--") + 1]


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for d in (bpy.data.meshes, bpy.data.armatures):
        for x in list(d):
            d.remove(x)


def mesh(name, z=0.0):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, z), (1, 0, z), (1, 1, z), (0, 1, z)], [], [(0, 1, 2, 3)])
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def armature(name):
    arm = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    a = arm.edit_bones.new("root")
    a.head, a.tail = (0, 0, 0), (0, 0, 1)
    b = arm.edit_bones.new("tip")
    b.head, b.tail = (0, 0, 1), (0, 0, 2)
    b.parent = a
    bpy.ops.object.mode_set(mode="OBJECT")
    return ob


clear()
rig = armature("Rig")
body = mesh("Body")
body.parent = rig
m = body.modifiers.new("Armature", "ARMATURE")
m.object = rig
g = body.vertex_groups.new(name="root")
g.add([0, 1, 2, 3], 1.0, "REPLACE")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "rig_42.blend"), compress=True)

clear()
mesh("Box")
w = mesh("WGT-shape", z=3.0)
w.modifiers.new("Subdivision", "SUBSURF")
w.hide_render = True
rig = armature("Rig")
rig.pose.bones["root"].custom_shape = w
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "hidden_42.blend"), compress=True)
