"""Writes tests/data/scene_42.blend with headless Blender (run once, by hand; the file is committed):

    blender -b --factory-startup --python tests/make_blend_fixture.py -- tests/data/scene_42.blend

Our own content, made from scratch (no default-scene objects kept):
- "Slab": a 2 x 1 m quad and a concave L-shaped n-gon (6 corners), 1 m up, rotated 90 degrees about Z, two material
  slots: "Painted" (a Principled BSDF: base colour from a packed 4x4 red PNG, roughness from its green channel through
  Separate Color, a normal map through a Normal Map node, metallic 0.25) and "Plain" (base colour 0 0 1, roughness 0.7);
  the quad flat-shaded, the n-gon smooth; a UV map
- "Child": a triangle parented to Slab, 2 m along Slab's local X
- "Mirrored": a quad with an enabled Mirror modifier (the reader must hand it to Blender)
"""
import sys
import bpy

out = sys.argv[sys.argv.index("--") + 1]
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)


def mesh(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


slab = mesh("Slab", [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0),
                     (3, 0, 0), (5, 0, 0), (5, 1, 0), (4, 1, 0), (4, 2, 0), (3, 2, 0)],
            [(0, 1, 2, 3), (4, 5, 6, 7, 8, 9)])
slab.location = (0, 0, 1)
slab.rotation_euler = (0, 0, 1.5707963267948966)
uv = slab.data.uv_layers.new(name="UVMap")
for loop in slab.data.loops:
    co = slab.data.vertices[loop.vertex_index].co
    uv.data[loop.index].uv = (co.x / 5.0, co.y / 2.0)
slab.data.polygons[0].use_smooth = False
slab.data.polygons[1].use_smooth = True

img = bpy.data.images.new("redpix", 4, 4)
img.pixels = [1.0, 0.5, 0.0, 1.0] * 16
img.filepath_raw = "//redpix.png"
img.file_format = "PNG"
img.pack()

painted = bpy.data.materials.new("Painted")
painted.use_nodes = True
nt = painted.node_tree
bsdf = nt.nodes["Principled BSDF"]
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = img
sep = nt.nodes.new("ShaderNodeSeparateColor")
nmap = nt.nodes.new("ShaderNodeNormalMap")
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(tex.outputs["Color"], sep.inputs["Color"])
nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
bsdf.inputs["Metallic"].default_value = 0.25

plain = bpy.data.materials.new("Plain")
plain.use_nodes = True
pb = plain.node_tree.nodes["Principled BSDF"]
pb.inputs["Base Color"].default_value = (0, 0, 1, 1)
pb.inputs["Roughness"].default_value = 0.7
slab.data.materials.append(painted)
slab.data.materials.append(plain)
slab.data.polygons[1].material_index = 1

child = mesh("Child", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
child.parent = slab
child.location = (2, 0, 0)

mirrored = mesh("Mirrored", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [(0, 1, 2, 3)])
mirrored.location = (10, 0, 0)
mirrored.modifiers.new("Mirror", "MIRROR")

bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
