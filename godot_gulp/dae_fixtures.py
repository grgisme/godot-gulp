"""Synthetic COLLADA for the tests and the conformance run, written here (no exporter, no third-party file).

    scene(dir) -> path    Z up, centimetres: a 100 cm quad (polylist) with a textured material, under a node whose
                          transform is a stack (translate 0 0 200, rotate Z 90); a two-joint skinned triangle (bind
                          shape matrix, inverse bind matrices, weights); the second joint's matrix animated over 1 s
"""
import os

from PIL import Image

DAE = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit name="centimeter" meter="0.01"/><up_axis>Z_UP</up_axis></asset>
  <library_images><image id="grass-img"><init_from>C:/elsewhere/grass.png</init_from></image></library_images>
  <library_effects>
    <effect id="Grass-fx"><profile_COMMON>
      <newparam sid="grass-surface"><surface type="2D"><init_from>grass-img</init_from></surface></newparam>
      <newparam sid="grass-sampler"><sampler2D><source>grass-surface</source></sampler2D></newparam>
      <technique sid="common"><phong>
        <diffuse><texture texture="grass-sampler" texcoord="UVMap"/></diffuse>
        <shininess><float>50</float></shininess>
      </phong></technique>
    </profile_COMMON></effect>
    <effect id="Skin-fx"><profile_COMMON><technique sid="common"><lambert>
      <diffuse><color>1 0 0 1</color></diffuse></lambert></technique></profile_COMMON></effect>
  </library_effects>
  <library_materials>
    <material id="Grass-mat" name="Grass"><instance_effect url="#Grass-fx"/></material>
    <material id="Skin-mat" name="Skin"><instance_effect url="#Skin-fx"/></material>
  </library_materials>
  <library_geometries>
    <geometry id="quad" name="Quad"><mesh>
      <source id="quad-pos"><float_array id="quad-pos-a" count="12">0 0 0 100 0 0 100 100 0 0 100 0</float_array>
        <technique_common><accessor source="#quad-pos-a" count="4" stride="3"/></technique_common></source>
      <source id="quad-nrm"><float_array id="quad-nrm-a" count="3">0 0 1</float_array>
        <technique_common><accessor source="#quad-nrm-a" count="1" stride="3"/></technique_common></source>
      <source id="quad-uv"><float_array id="quad-uv-a" count="8">0 0 1 0 1 1 0 1</float_array>
        <technique_common><accessor source="#quad-uv-a" count="4" stride="2"/></technique_common></source>
      <vertices id="quad-v"><input semantic="POSITION" source="#quad-pos"/></vertices>
      <polylist material="grass-sym" count="1">
        <input semantic="VERTEX" source="#quad-v" offset="0"/>
        <input semantic="NORMAL" source="#quad-nrm" offset="1"/>
        <input semantic="TEXCOORD" source="#quad-uv" offset="2" set="0"/>
        <vcount>4</vcount><p>0 0 0 1 0 1 2 0 2 3 0 3</p>
      </polylist>
    </mesh></geometry>
    <geometry id="tri" name="Tri"><mesh>
      <source id="tri-pos"><float_array id="tri-pos-a" count="9">0 0 0 100 0 0 0 0 100</float_array>
        <technique_common><accessor source="#tri-pos-a" count="3" stride="3"/></technique_common></source>
      <vertices id="tri-v"><input semantic="POSITION" source="#tri-pos"/></vertices>
      <triangles material="skin-sym" count="1"><input semantic="VERTEX" source="#tri-v" offset="0"/><p>0 1 2</p></triangles>
    </mesh></geometry>
  </library_geometries>
  <library_controllers>
    <controller id="tri-skin"><skin source="#tri">
      <bind_shape_matrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</bind_shape_matrix>
      <source id="j"><Name_array id="j-a" count="2">Root Tip</Name_array>
        <technique_common><accessor source="#j-a" count="2" stride="1"/></technique_common></source>
      <source id="ib"><float_array id="ib-a" count="32">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 1 0 0 0 0 1 0 0 0 0 1 -100 0 0 0 1</float_array>
        <technique_common><accessor source="#ib-a" count="2" stride="16"/></technique_common></source>
      <source id="w"><float_array id="w-a" count="3">1 0.25 0.75</float_array>
        <technique_common><accessor source="#w-a" count="3" stride="1"/></technique_common></source>
      <joints><input semantic="JOINT" source="#j"/><input semantic="INV_BIND_MATRIX" source="#ib"/></joints>
      <vertex_weights count="3"><input semantic="JOINT" source="#j" offset="0"/><input semantic="WEIGHT" source="#w" offset="1"/>
        <vcount>1 2 1</vcount><v>0 0 0 1 1 2 1 0</v></vertex_weights>
    </skin></controller>
  </library_controllers>
  <library_animations>
    <animation id="tip-anim">
      <source id="t"><float_array id="t-a" count="2">0 1</float_array>
        <technique_common><accessor source="#t-a" count="2" stride="1"/></technique_common></source>
      <source id="m"><float_array id="m-a" count="32">1 0 0 0 0 1 0 0 0 0 1 100 0 0 0 1 1 0 0 50 0 1 0 0 0 0 1 100 0 0 0 1</float_array>
        <technique_common><accessor source="#m-a" count="2" stride="16"/></technique_common></source>
      <sampler id="s"><input semantic="INPUT" source="#t"/><input semantic="OUTPUT" source="#m"/></sampler>
      <channel source="#s" target="TipNode/transform"/>
    </animation>
  </library_animations>
  <library_visual_scenes><visual_scene id="Scene">
    <node id="QuadNode" name="QuadNode"><translate sid="location">0 0 200</translate><rotate sid="rotationZ">0 0 1 90</rotate>
      <instance_geometry url="#quad"><bind_material><technique_common>
        <instance_material symbol="grass-sym" target="#Grass-mat"/></technique_common></bind_material></instance_geometry>
    </node>
    <node id="RootNode" name="Root" sid="Root" type="JOINT"><matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>
      <node id="TipNode" name="Tip" sid="Tip" type="JOINT"><matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 100 0 0 0 1</matrix></node>
    </node>
    <node id="SkinNode" name="Skinned"><instance_controller url="#tri-skin"><skeleton>#RootNode</skeleton>
      <bind_material><technique_common><instance_material symbol="skin-sym" target="#Skin-mat"/></technique_common></bind_material>
    </instance_controller></node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#Scene"/></scene>
</COLLADA>
"""


def scene(d):
    os.makedirs(d, exist_ok=True)
    Image.new("RGB", (4, 4), (60, 140, 40)).save(os.path.join(d, "grass.png"))
    p = os.path.join(d, "scene.dae")
    with open(p, "w") as f:
        f.write(DAE)
    return p
