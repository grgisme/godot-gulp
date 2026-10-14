# godot-gulp

**Status: archived.** Built for the reclaim game project and released as open source in 2026-10, when that project
moved to Unreal Engine. It is no longer maintained by the author: issues and pull requests may go unanswered. Fork it
freely under the licence below. The development history stays private; this repository starts from a single
release commit.

Convert 3D asset packs to glTF for Godot, ahead of time, from the command line: USD, FBX, OBJ and Unity today, more
source formats planned.

godot-gulp reads a USD stage with OpenUSD (no Blender, no editor plugin) and writes `.gltf` + `.bin` files that any
glTF reader takes, with what the source's materials say kept, not approximated:

- **Geometry:** every mesh, holes dropped, left-handed and mirrored meshes re-wound, authored or computed normals,
  every UV set, vertex colour, one primitive per material subset, `metersPerUnit` and `upAxis` to metres and Y up.
- **Materials:** UsdPreviewSurface to glTF PBR.
  - Base colour, opacity (BLEND, or MASK from `opacityThreshold`), emissive (with strength above 1) and IOR.
  - Metallic, roughness and occlusion packed into one ORM map, from any channel.
  - Normal maps, with DirectX green flipped to glTF's convention.
  - Wrap modes, and the UV set each texture's reader names.
- **UV transforms:** `UsdTransform2d` is baked into the UVs, rotation included, so it survives in every reader,
  Godot's included. A second, different transform on the same material is kept as `KHR_texture_transform`.
- **Instancing:** instanceable prototypes and PointInstancers become shared meshes placed by nodes.
- **Variants:** selectable with `--variant set=value`, and listed in the report.
- **Packs:** a process pool, and a cache keyed on the content of every layer and texture a file uses. A rerun of an
  unchanged pack converts nothing.
  - A rules table (`examples/rules.example.json`) repairs what the export lost: tiling, tint, recolour, glass.
  - An unwired sibling AO map is picked up.
  - A per-file report.
- **Godot import check:** every result is loaded by Godot (a stub project, no game project needed). A check that
  cannot run fails loudly; nothing is reported OK unchecked.

## FBX and OBJ

`godot-gulp pack` and `godot-gulp file` read `.fbx` (binary and ASCII) and `.obj` (with its `.mtl`) through
[ufbx](https://github.com/ufbx/ufbx), vendored and compiled into the package (no Blender, no FBX SDK):

- the node tree with each node's transform, converted to glTF's space (right-handed, Y up, metres) in the geometry
- one primitive per material, normals (generated when missing), the first UV set, vertex colour
- materials from ufbx's PBR view of every shading model: base colour, roughness, metalness, normal, emission,
  opacity, occlusion; a texture whose stored path is another machine's is found beside the file or in `textures/`,
  and maps a pack ships loose (nothing wired) are found by the material's name, like the USD path
- skins: each cluster's bone a joint, its bind matrix the inverse bind matrix, the four strongest weights per vertex
- animation: every take baked by ufbx into translation, rotation and scale keys
- two sources sharing a stem in one folder (`axe.fbx`, `axe.obj`) keep their extension in the output name

## Blender (.blend)

`.blend` files (Blender 2.80 to 5.x, gzip or zstd) are read here, without Blender, by `pack` and `file`: the object
tree and transforms, meshes in all three on-disk eras (2.8-3.6 structs, 4.0-5.0 named attribute layers, 5.1+
attribute storage), n-gons triangulated (ear clipping for concave faces), flat and smooth faces, the UV map, material
slots (mesh or object), the Principled BSDF (base colour, metallic, roughness, alpha, emission, normal) with its
Image Texture maps (external, or packed and written out) including channels through Separate Color and normals
through a Normal Map node.

A .blend stores only the base mesh, not what its modifiers make. A file with an enabled shape-changing modifier
(mirror, subdivision, geometry nodes, booleans...) or a curve, text or metaball object raises `NeedsBlender`; with
`GODOT_GULP_BLENDER` set to a Blender binary it is then converted by headless Blender with the modifiers applied, and
the report says why. A mesh skinned to an armature goes to Blender too (the reader keeps no skin; Blender keeps the
skin and the actions). Harmless modifiers (triangulate, weighted normal, smooth by angle, simulations at rest) are noted
and left out, and objects hidden from render (rig widgets, helpers) are neither judged nor exported.

## COLLADA

`.dae` (1.4 and 1.5) is read here too, by `pack` and `file`: the node tree and each node's transform stack, up_axis
and units to glTF's space, triangles/polylists/polygons, profile_COMMON materials with their textures (found beside
the file when the stored path is another machine's), skins (bind shape, inverse bind matrices, weights) and
animations (matrix or per-element channels, re-sampled to translation/rotation/scale).

## Unreal

`godot-gulp unreal <Content folder> <out> [--mount /Engine=<UE>/Engine/Content]` converts editor-saved Unreal
packages (UE 5.x, what Fab packs ship) without Unreal: every StaticMesh to its own glTF and every level to a glTF of
placed meshes.


- the package summary, name, import and export maps, the soft object path list, and every export's tagged properties
  (both the UE 5.4+ type-name tags and the older flat tags; a value it cannot decode stays raw, bounded by its size)
- Texture2D source art (lossless, not cooked mips) from the package trailer or legacy bulk data: raw formats
  (8 and 16 bit, half and float HDR clamped to 8-bit), PNG, JPEG and the UEDELTA row filter
- Oodle-compressed payloads (UE5's default) need your own Oodle library: `GODOT_GULP_OODLE=<UE install>/Engine/Source/Runtime/OodleDataCompression/Sdks/<ver>/lib/Linux/liboo2corelinux64.so.9`;
  LZ4 payloads need `pip install lz4`

- materials from the editor graph, not name guesses: a material instance's parent chain and its scalar, vector,
  texture and static switch overrides; the base material's inputs evaluated through parameters, constants, texture
  samples (with tiling), multiply/add/subtract/divide, lerp by a constant, masks (packed ORM/MRA channels kept),
  switches, reroutes and inlined material functions; DirectX normal maps flipped; blend mode, mask clip, two-sided.
  Procedural nodes (world position, panners, custom HLSL) are named in the notes and left at their defaults

- static meshes: LOD0's source mesh (FMeshDescription, decoded here: no open tool reads it) with its normals, two
  UV sets, vertex colour and one primitive per material slot; Unreal's left-handed Z-up centimetres to glTF's space
  (positions (x, z, y) * 0.01, Unreal's clockwise front faces kept), checked on every mesh against its own normals
- levels: actors' scene components composed up their attach chain, static mesh components with their override
  materials, instanced and hierarchical instanced components (one node per instance), World Partition external actor
  files; landscapes, foliage types and spline deformation are named in the report, not converted
- skeletal meshes (UE 5.1+ saves): LOD0's mesh description with its skin weights (the four strongest per vertex)
  and its bones (names, parents, reference pose) as a glTF skin with inverse bind matrices
- animation sequences (UE 5.2+ saves: the control-rig keys of each bone) on every skeletal mesh of their skeleton:
  one glTF per character with all its takes
- UE 4.24-4.27 static meshes (the older FMeshDescription layout, a zlib FByteBulkData in the mesh's native data)
- not yet: FRawMesh (saved before 4.24), UE 5.0 and UE4 skeletal meshes, sequences saved before 5.2, cooked
  (.pak / IoStore) content

The research behind it: docs/research/unreal-readers.md.

## Re-ingesting a pack (godot-gulp upgrades)

- Texture file names carry a content hash (`<hash>_<name>.png`, `<hash>_orm.png`), so a re-ingest with a newer godot-gulp
  can rename every texture of a pack. A game project that imported the old files keeps materials pointing at textures
  it never imported and shows them white: after a re-ingest, re-link the pack and reimport (for a Godot project:
  refresh its copy or links of the pack, then a full import) before judging any render.
- Foliage: under `--foliage-cutout GLOB` paths (default `*Foliage/*`, case-insensitive; `none` turns it off), an
  alpha-blended material is written as a cutout (`alphaMode` MASK, `alphaCutoff` 0.5): leaves and grass then sort and
  cast shadows as solid plants do. An authored cutout keeps its own threshold.

## Unity

`godot-gulp unity <pack.unitypackage | project folder> <out>` converts every prefab, scene and model without Unity:

- the package's GUID tar or a project's `.meta` files resolve every reference; Unity YAML is read per document
- the GameObject/Transform hierarchy kept as glTF nodes, nested prefabs with their overrides (position, rotation, scale, name, materials),
  stripped objects followed to their source
- Standard, URP Lit and HDRP Lit materials: base colour (sRGB to linear), metallic/smoothness (map R and A, or the
  albedo alpha) and the HDRP mask map repacked into ORM, normal maps and their scale, occlusion, emission, cutout and
  transparency; tiling and offset baked into the UVs
- meshes from OBJ (read here), FBX (ufbx, as above), Unity's own text Mesh `.asset` files (vertex channels and streams,
  16/32-bit indices, submeshes) and the built-in Cube, Plane and Quad
- a prefab's mesh inside a model: by the .meta's name table; where Unity 2019.3+ left it out (it hashes those ids), by a
  fileID map Unity wrote (`--fileid-map`, e.g. through the Unity CLI), else by the renderer's GameObject name; each such
  mesh in its own node's space, placed by the prefab's own transform
- LOD groups: LOD0 kept, the coarser levels named in the report (Godot makes its own LODs on import)
- animation clips: an Animator's controller states to glTF animations on its node tree (position, rotation and
  scale curves with their slopes as CUBICSPLINE; Euler curves sampled at 30 fps)
- terrains: the heightmap as a grid mesh; one layer is tiled over it, two or more are blended by the alphamaps
  (RGBA32 text sub-assets) into one base colour texture, each layer tiled as Unity's terrain shader does and mixed in
  linear space (up to 256 px per tile, at most `GODOT_GULP_SPLAT_MAX` px a side, default 4096; the report names the
  resulting px per metre). Layer normal and mask maps are not blended yet
- skinned meshes as glTF skins: the SkinnedMeshRenderer's bones as joints, the mesh's bind poses as inverse bind
  matrices, bone weights and indices (vertex channels or the older m_Skin) as WEIGHTS_0/JOINTS_0
- Unity's left-handed space to glTF's: positions (-x, y, z), rotations (x, -y, -z, w); a mirroring placement is
  baked with its winding fixed

How it compares with the existing tools: `docs/research/unity-importers.md`.

## Licensing of inputs

You must hold the rights to every file you convert; conversion does not change an asset's licence. Pass
`--source-store`, `--source-licence` and `--source-id` to record where a pack came from and under which licence:
they land in the report and in each glTF's `asset.extras.source`. See docs/LICENSING-OF-INPUTS.md (Unity Restricted
Assets, engine-only licences, never automating store downloads).

## Install

```sh
pip install "godot-gulp @ git+https://github.com/grgisme/gulp"
```

Python 3.10+ and a C compiler (the FBX reader builds on install). Dependencies (`usd-core`, `numpy`, `Pillow`,
`PyYAML`) install with it. Godot 4.x on `PATH` (or
`GODOT_GULP_GODOT`) is needed only for the import check.

## Use

```sh
godot-gulp pack ./pack-src ./pack-out --rules rules.json --jobs 8
godot-gulp file model.usd model.gltf --variant shape=flat --json
godot-gulp conformance          # author the synthetic fixtures, convert them, import them in Godot
```

`pack` writes `<out>/<same path>.gltf`, shared `<out>/textures/`, `REPORT.md`, `report.csv`, and a per-file analysis
in `<out>/.analysis/`. It exits 3 when the Godot check could not run.

## Tests

```sh
pip install -e ".[test]"
pytest                          # the conformance asserts on the written glTF
godot-gulp conformance          # plus Godot importing every fixture
```

Fixtures are synthetic (see Test data below). No commercial or store asset ships in this repository.

## Test data

The tests need no download. USD, Unity, Unreal, FBX and COLLADA fixtures are synthetic, written at test time; the few
small `.blend` files in `tests/data/` (100-150 KB each) are our own or MIT-licensed Blender default scenes, listed with
their sources in `tests/data/README.md`. The repository uses no Git LFS.

## Licence

Apache-2.0 (`LICENSE`). Code ported from godot-usd by Miguel de Icaza is used under MIT (`NOTICE`, `THIRD_PARTY.md`).
