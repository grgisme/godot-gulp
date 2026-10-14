# Reading Blender .blend files without Blender: survey for godot-gulp

Date: 2026-10-11. Research only, no reader built. Clones were shallow, read and grepped only.
Paths below are relative to each project's repository root. Blender facts come from reading GPL source (blender main
`c31386f6`, 2026-10-11, which is 5.4 alpha: `BKE_blender_version.h:23` `BLENDER_VERSION 504`) plus raw files
fetched from release tags into `blender-tags/`. Nothing from Blender was copied into anything we ship.

A throwaway read-only probe (not committed) parsed real files
from 2.80 to 5.2 to check the claims below. Its findings are marked **[probe]**.

## 1. Verdict

**Feasible.** A pure-Python, Apache-2.0 reader for static meshes, UVs, material slots, Principled BSDF
constants and image references is a medium-sized job (estimate 1.5k to 2.5k lines). The container format is
simple and self-describing (SDNA). The hard part is the mesh layout: it changed four times between 2.8 and 5.2.
Most of the real risk is semantic, not format: modifiers, auto smooth, custom normals, node graphs. Mitigation:
only the base mesh, a refusal list, and a fall-back to "needs Blender" for anything outside it.

Nobody we can port from already does this for current Blender:
- assimp's importer is BSD (portable) but stuck at pre-4.0 meshes and pre-2.8 materials.
- BAT is GPL and decodes no geometry.
- The Rust `blend` crate (MIT) is a generic DNA walker with no mesh logic for 3.5 and later.

We write our own, using the format facts below.

## 2. Comparison table

| Tool | Lang | Licence (SPDX) | Port into Apache-2.0? | Blender versions | Decodes | Last commit (HEAD) | Breaks on |
|---|---|---|---|---|---|---|---|
| blender-asset-tracer (BAT) `bat/` | Python 3.13 | GPL-2.0-or-later (LICENSE.txt, file headers); pyproject says GPL-3.0-or-later | **No**, read only | v1 reader: 2.4x to 5.x header (handles 17-byte header, `header.py:109-132`). v2.0+ (2026-04-17) runs *inside* Blender 5.1+ | Header, BHeads, SDNA, generic field get, pointer deref, gzip/zstd. No mesh or material decoding: it lists datablocks and file paths | 2026-10-08 (v2.2.1) | Needs `zstandard` for 3.0+ compressed files (`magic_compression.py:30-38`). v2 needs Blender to run |
| assimp Blender importer `assimp/code/AssetLib/Blender/` | C++ | BSD-3-Clause (header of `BlenderLoader.cpp`) | Yes, with attribution. Not worth it | 2.4x to 2.79 (test models 2.41 to 2.79). Meshes up to 3.x by luck | MVert/MEdge/MPoly/MLoop/MLoopUV/MFace, legacy Blender Internal materials (MTex), cameras, lamps, object tree, some modifiers (mirror, subsurf in `BlenderModifier.cpp`) | 2026-09-29 (bounds hardening, #6866). **Deprecated, not removed** | 4.0+ files: `*mvert` is `ErrorPolicy_Fail` (`BlenderScene.cpp:504`), and 4.0+ no longer writes MVert. 5.0+ 17-byte header (only reads 12 bytes: `BlenderLoader.cpp:142-149`). zstd (gzip only: `BlenderLoader.cpp:46-69`). Node materials: `nodetree` commented out (`BlenderScene.h:863`) |
| Rust `blend` crate `rust-blend/` | Rust | MIT (`LICENSE`, `Cargo.toml:6`) | Yes (idea level; it is Rust) | 2.80 to 5.2 header and blocks (0.9: "Added Blender 5.0/5.1 file-format support", CHANGELOG) | Generic DNA reflection (`get_f32_vec`, `get_iter`). Mesh example only for 2.80-era `mpoly/mloop/mvert` (`examples/read_mesh_280.rs`) | 2026-07-29 | No compression at all (`src/lib.rs:119`). No knowledge of generic attributes or AttributeStorage |
| fbtBlend-Header-Only `fbtBlend-Header-Only/` | C++ | Zlib (`LICENSE`, header of `fbtBlend.h`) | Legally yes. See caution | Per-version folders 280 to 502_LTS, each with a `Blender.h` "Generated using BLENDER v.502" | Full struct access through compiled per-version headers. Demos stop at 3.0 ("Blender 3.1 ... removed normals from struct MVert", README) | 2026-08-14 | Needs a recompile per Blender version. Its `Blender.h` is generated from Blender's GPL DNA headers, and its 5.x header comments quote Blender's own comment text (`502_LTS/fbtBlend.h:2826-2835`). Use as a reference only |
| Kaitai `blender_blend.ksy` `kaitai/media/` | KSY spec | CC0-1.0 (`meta.license`) | Yes | Legacy 12-byte header only (`header` seq reads magic, ptr char, endian char, 3-digit version) | Header, blocks, SDNA tables | ksy last touched 2023-03-17 | 5.0+ files (17-byte header, LargeBHead8) |
| blender_sdna (randy408) `blender_sdna/` | C | BSD-2-Clause | Yes | 2019-era | Dumps SDNA as C structs only | 2019-04-15 | Not maintained; metadata only |
| BlendFileParser (LapisSea) `BlendFileParser/` | Java | **No licence file found** (all rights reserved by default) | **No** | 2019-era | Header, DNA1, blocks | 2019-11-26 | Unmaintained |
| Godot `.blend` import `godot/modules/gltf/editor/` | C++ | MIT | n/a | Whatever Blender is installed | **Shells out to Blender**: `editor_import_blend_runner.cpp:161-180` runs `filesystem/import/blender/blender_path` with `--background` and calls `bpy.ops.export_scene.gltf` (lines 77, 93), optionally over XML-RPC (lines 43-65, 190-264) | 2026-10-09 | Needs Blender. Confirmed |
| three.js / Babylon.js | JS | MIT / Apache-2.0 | n/a | n/a | No .blend loader found (unverified by code search; neither ships one to my knowledge) | n/a | n/a |

Searched but not found or not cloned: "py-blend", "pyblend", "bpy-free" readers. `gh search repos` returned
only the C, C++ and Java parsers above. Older Python readers I know of (Campbell Barton's `blendfile.py` in
`io_blend_utils`) are BAT's ancestor and are GPL as well (unverified: not cloned).

## 3. File format summary (container)

### 3.1 Compression (whole-file wrapper)
- Uncompressed files start with `BLENDER`. Compressed ones are a single gzip stream (`1f 8b`, before 3.0) or a
  zstd stream (`28 b5 2f fd`, 3.0 and later). Decompress first, then parse. Source: `bat/.../magic_compression.py:30-50,116-132`.
  BAT also checks the zstd skippable-frame magic `50 2a 4d 18` with mask `f0 ff ff ff` (lines 49-51, 129).
- **[probe]** `rust-blend/examples/blend_files/5_2.blend1` starts `28 b5 2f fd` and decodes with the `zstandard`
  package to a valid 5.2 file. BAT fixtures: 9 of 41 are zstd.
- Python: gzip is stdlib. zstd needs the `zstandard` package on 3.12 (it is installed here), or stdlib
  `compression.zstd` on Python 3.14+ (PEP 784; from memory, unverified here).

### 3.2 File header (two formats)
From Blender `source/blender/blenloader_core/BLO_core_blend_header.hh:21-54`, and cross-checked in
`bat/.../header.py:81-132` and **[probe]**:

- **Format 0 (legacy, every version up to 4.5): 12 bytes.** `BLENDER`, then one pointer-size char
  (`_`=4, `-`=8), then one endian char (`v`=little, `V`=big), then 3 ASCII digits (`280`, `305`, `402`).
  Example: `BLENDER-v402`.
- **Format 1 (Blender 5.0 and later): 17 bytes.** `BLENDER`, then the header size as 2 ASCII digits (`17`),
  then `-`, then the format version as 2 digits (`01`), then `v`, then 4 version digits.
  Example `BLENDER17-01v0502`. Always 64-bit pointers and little-endian. The writer chooses it in
  `blenloader/intern/writefile.cc:1660-1677`. The file comment says "Current Blend-files (Blender 5.0 and later)"
  (`writefile.cc:17-21`). **[probe]** the 5.0, 5.1 and 5.2 fixtures all use format 1. 4.2 and older use format 0.
- Big-endian files can no longer be opened by Blender itself, since 5.0 (`readfile.cc:1235-1243`). We can
  refuse them.
- `BLENDER_FILE_MIN_VERSION 405` (`BKE_blender_version.h:42`): files from current main claim to be readable
  by 4.5.

### 3.3 Block headers (BHead)
`BLO_core_bhead.hh:16-58`. Every block is a header followed by `len` bytes:
- `BHead4` (32-bit files): `code[4], int32 len, uint32 old, int32 SDNAnr, int32 nr`. 20 bytes.
- `SmallBHead8` (64-bit, format 0): `code[4], int32 len, uint64 old, int32 SDNAnr, int32 nr`. 24 bytes.
- `LargeBHead8` (format 1): `code[4], int32 SDNAnr, uint64 old, int64 len, int64 nr`. 32 bytes. **The field
  order changes**, not only the widths. BAT gets this right in `header.py:152-170`.

Codes (`BLO_core_bhead.hh:74-108`): `DATA`, `GLOB` (FileGlobal; subversion is the short at offset 4, per
`bat/.../__init__.py:360-377`), `DNA1`, `TEST` (thumbnail), `REND`, `USER`, `ENDB` (end).

ID blocks use a 2-char ID code plus padding: `ME` mesh, `OB` object, `MA` material, `IM` image, `SC` scene,
`NT` node group, `LI` library, and so on. An ID block is followed by its `DATA` blocks (writefile.cc:39-58).

`old` is the pointer key: any pointer field in any struct is resolved by looking up the block whose `old`
equals it.
- Since stable address ids (4.x era; exact version unverified), `old` is no longer a real memory address. It is
  a generated id seeded from an XXH3 hash of the ID name (`writefile.cc:625-665, 839-874`).
- On disk the pointer map is not cleared between IDs. It is cleared only for undo memfiles (`writefile.cc:720-738`).
  So a global `old -> block` map works, and that is what BAT does (`__init__.py:396-416`).

### 3.4 SDNA (DNA1 block)
Layout per Blender `makesdna/intern/dna_genfile.cc:37-68` and the writer `makesdna/intern/makesdna.cc:602-646`.
Parsed identically in `bat/.../__init__.py:275-358` and assimp `BlenderDNA.cpp:77-160`:

```
"SDNA" "NAME" int32 n, n NUL-terminated field names ("*next", "co[3]", "(*func)()")
pad to 4, "TYPE" int32 m, m NUL-terminated type names
pad to 4, "TLEN" m x int16 type sizes
pad to 4, "STRC" int32 k, k x { int16 type_index, int16 nfields, nfields x { int16 type, int16 name } }
```

How to compute a field's size and offset:
- **Size:** if the name starts with `*` or `(*` it is a pointer (4 or 8 bytes). Otherwise it is
  `TLEN[type]`. Either way, multiply by every `[N]` in the name.
- **Offset:** the running sum of the sizes before it. Blender forbids implicit padding in DNA structs
  (`dna_genfile.cc:95-104`), so no alignment logic is needed.

Two gotchas:
- **Names in the file are the old (aliased) names, not the current C++ names.** `makesdna/intern/dna_rename_defs.h:145-159`:
  Mesh `verts_num` is stored as `totvert`, `faces_num` as `totpoly`, `corners_num` as `totloop`, `vert_data` as
  `vdata`, `corner_data` as `ldata`, `face_offset_indices` as `poly_offset_indices`. **[probe]** confirmed in
  4.2 and 5.2 files. Struct renames too, e.g. `Lamp` -> `Light` (line 46). The reader must key on file names.
- **Raw arrays carry no type.** Plain data blocks (float arrays, int arrays, bool arrays) are written with
  `SDNAnr = 0` (`writefile.cc:1078-1079`, `SDNA_RAW_DATA_STRUCT_INDEX`). Struct 0 is an arbitrary struct (`Link`
  in 2.8, `DrawDataList` in 4.2 **[probe]**). The element type must come from context (layer type,
  AttrType), and the count from `len` divided by the element size, not from `nr`.

### 3.5 Finding datablocks
- Walk all blocks. ID blocks have a 2-letter code (`ME`, `OB`, ...).
- The struct type is `structs[SDNAnr]`. Every ID starts with `ID id`, whose `name[66]` (2-char prefix plus name)
  gives the datablock name.
- Objects: `Object.data` (`ID *`, `DNA_object_types.h:496`) points to the Mesh block. Other useful fields:
  - `Object.type` (line 477)
  - `loc/rot/quat/scale/rotmode` (lines 534-585)
  - `parent` and `parentinv` (lines 483, 551)
  - `modifiers` ListBase (line 513)
  - `mat/matbits/totcol` (lines 525-529): per-object material override
- The world matrix is runtime only in 5.x (`object_to_world()` is a method, line 675). Rebuild it from
  loc/rot/scale/parent.
- Scene membership goes through Collections (`SC` -> master collection -> objects). Treat "all OB blocks" as a
  first approximation.
- Linked libraries: `LI` blocks plus IDs with `lib` set. Skip them, or report them as external.

## 4. Mesh attribute layout by Blender version

The reader must branch on what is *present*, not on the version number. Blender's own versioning does the same:
every legacy converter early-returns if the new layer already exists (`mesh_legacy_convert.cc:1831-1890, 1960-2004`).

| Saved by | Positions | Edges | Faces | Corners (loops) | UVs | Material per face | Smooth flags | Where layers live |
|---|---|---|---|---|---|---|---|---|
| 2.8x, 2.9x, 3.0 | `MVert.co[3]` (plus `short no[3]` normals through 3.0) | `MEdge{v1,v2,crease,bweight,flag}` | `MPoly{loopstart,totloop,mat_nr,flag}` | `MLoop{v,e}` | `MLoopUV{uv[2],flag}` in ldata | `MPoly.mat_nr` | `MPoly.flag & ME_SMOOTH`, `MEdge.flag & ME_SHARP`, mesh auto smooth | `CustomData vdata/edata/pdata/ldata`, layer types 0/3/25/26/16 |
| 3.1 to 3.6 | Same structs on disk (`MVert` lost `no[3]` in 3.1). 3.4 to 3.6 build these legacy structs *at write time* for forward compatibility, while runtime is attribute-based (`blender-tags/mesh-v3.6.0.cc:255-319`) | `MEdge` | `MPoly` | `MLoop` | `MLoopUV` (3.6 converts back to struct on write, line 319) | `mat_nr` | flags | Same. **[probe]** the 3.5 fixture has MVert/MEdge/MPoly/MLoop/MLoopUV layers |
| 4.0 to 5.0 | `CD_PROP_FLOAT3` layer named `position` | `CD_PROP_INT32_2D` `.edge_verts` | `poly_offset_indices` int array, `faces_num+1` long (`mesh.cc:430-436`) | `CD_PROP_INT32` `.corner_vert`, `.corner_edge` | `CD_PROP_FLOAT2` corner layer, named by the user (`UVMap`) | `material_index` int32 face attribute (absent means 0) | `sharp_face` bool face, `sharp_edge` bool edge. Auto smooth removed in 4.1 (becomes a "Smooth by Angle" geometry nodes modifier: `BKE_main_mesh_legacy_convert_auto_smooth`, `versioning_common.cc:868`) | Generic CustomData layers (type 48/46/11/49/50 ...) with names. **[probe]** 4.2 fixture: `position[vec3f x8]`, `.edge_verts`, `.corner_vert`, `.corner_edge`, `UVMap[vec2f]`, `sharp_face` |
| 5.1 and later | `AttributeStorage` entry `position` (AttrType Float3=7, domain Point=0) | `.edge_verts` Int32_2D=4, Edge=1 | `poly_offset_indices` (unchanged) | `.corner_vert`, `.corner_edge` Int32=3, Corner=3 | Float2=6, Corner | `material_index` Int32 Face=2 | `sharp_face`, `sharp_edge` Bool=0 | `Mesh.attribute_storage.dna_attributes` (array of `Attribute{name*, int16 data_type, int8 domain, int8 storage_type, data*}`) -> `AttributeArray{data*, sharing_info*, int64 size, int8 is_single}` (`DNA_attribute_types.h`). Converted at file version 501.19 (`versioning_501.cc:850-853`). CustomData layers for the mesh are empty. **[probe]** 5.2 fixture: 12 attributes, all `storage_type=0` (Array) |

Notes:
- Facts in this table come from:
  - legacy struct layouts: `blender-tags/DNA_meshdata_types-v2.80.h`, `-v3.0.0.h`, `-v3.1.0.h`
  - the 4.0 conversion list: `versioning_400.cc:413-430`, gated at 400.1 (lines 1195-1199)
  - the CustomData type ids: `DNA_customdata_types.h:29-115`
  - AttrType and AttrDomain values: `blender-tags/main-BKE_attribute_enums.hh:16-55`
- Other geometry types (point cloud, curves, grease pencil) moved to AttributeStorage in 5.0 (`versioning_500.cc:3317-3542`).
  Meshes followed in 5.1.
- `AttrStorageType::Single = 1` (one value for the whole domain) exists. 5.1+ can write it, but sets
  `AttributeArray.is_single` and keeps array storage for 5.0 compatibility in some paths
  (`DNA_attribute_types.h` AttributeArray comment; `attribute_storage.cc:624-639`). A reader must handle both,
  expanding a single value to the domain size.
- Normals are not stored. Compute flat or smooth normals from `sharp_face`/`sharp_edge`.
  - Custom split normals: `CD_CUSTOMLOOPNORMAL` before 5.0. Since 5.0 they are an `Int16_2D` corner attribute
    named `custom_normal` (`mesh_custom_normals_to_generic`, `mesh_legacy_convert.cc:2656-2685`, run for files
    before 500.1).
  - Both are encoded relative to Blender's per-corner "lnor space", so decoding them means reimplementing that
    math. **Skip in v1** and use computed normals.
- Vertex groups (`MDeformVert`, CD type 2) still live in CustomData, and names in `vertex_group_names`.
  Skin (`CD_MVERT_SKIN`) is written in the legacy format on purpose (`mesh.cc:383`, `versioning_503.cc:705-713`).
- Shape keys: `Mesh.key` -> `Key` ID with `KeyBlock`s. Skip in v1.
- Implicit sharing: arrays shared between meshes are written once per ID (`writefile.cc:2384-2416`,
  `per_id_written_shared_addresses` is cleared per ID). With the global `old` map, nothing special is needed.
- `Mesh.mat` / `totcol`: material slots on the mesh. `Object.matbits[i]` says whether slot i comes from the
  object instead.
- Pre-2.63 files with only `MFace` tessfaces exist (`versioning_280.cc:2774` converts them). Out of scope.

## 5. Modifiers: only the base mesh is in the file

The file stores the *original* mesh data plus the modifier stack as settings (`Object.modifiers`, a ListBase of
`ModifierData{type, name, flag ...}`, `DNA_modifier_types.h:172-182`). The evaluated mesh (after mirror,
subdivision, array, solidify, bevel, boolean, geometry nodes, and since 4.1 also "Smooth by Angle") is runtime only.
It lives in `Object.runtime`, which is never written.

What that means for us:
- A model whose look depends on modifiers will import **wrong**. Examples: half a car with a Mirror modifier, a
  low-poly cage with Subdivision, a geometry-nodes scatter.
- Policy:
  - v1 lists the modifier stack per object.
  - It imports the base mesh when the stack is empty, or contains only modifiers we can ignore (triangulate,
    weighted normal, smooth by angle).
  - It refuses (routes to "needs Blender") otherwise, and says which modifier forced the refusal.
- Mirror, and maybe Array, are simple enough to apply ourselves later.
- Subdivision and geometry nodes are not realistic without Blender.
- assimp applied mirror and subsurf itself (`assimp/code/AssetLib/Blender/BlenderModifier.cpp`), which shows the
  scale of that work.

## 6. Materials

- Since 2.8 a material's look is its embedded node tree: `Material.nodetree` (`DNA_material_types.h:416`).
  - It is written as an embedded ID right after the material (`material.cc:232-238`).
  - **[probe]** the pointer resolves to a `DATA` block whose struct is `bNodeTree`, in 2.80, 3.5, 4.2 and 5.2.
  - `use_nodes` is deprecated and forced true on write (`material.cc:225-226`).
  - `Material.r/g/b/a`, `roughness` and `metallic` (lines 392-400) are only viewport values. Use them as a
    fallback.
- Graph walk:
  - `bNodeTree.nodes` is a ListBase of `bNode`. Key fields: `idname[64]`, `name[64]`, `inputs/outputs`
    ListBase of `bNodeSocket`, `ID *id`, `void *storage` (`DNA_node_types.h:1645-1712`).
  - `bNodeTree.links` is a ListBase of `bNodeLink{fromnode, tonode, fromsock, tosock}` (lines 1844-1845).
  - Start at the active `ShaderNodeOutputMaterial`, follow the link into its `Surface` input, and expect a
    `ShaderNodeBsdfPrincipled`.
- Principled inputs are matched by `bNodeSocket.identifier` (line 1454).
  - An unlinked input's value is in `default_value`. Its block type is set by the socket type
    (`node.cc:1161-1200`): `bNodeSocketValueFloat`, `...RGBA`, `...Vector` and so on.
  - Read the value through DNA. **[probe]** reading offset 0 of `bNodeSocketValueFloat` returns the `subtype`
    int, not the value.
  - Identifiers differ by version. 4.0 rewrote Principled:
    - 3.6 (`blender-tags/v360-principled.cc`): `Base Color`, `Metallic`, `Roughness`, `Specular`,
      `Emission`, `Alpha`, `Normal`.
    - Main (`blender-tags/main-principled.cc:40-338`): `Base Color`, `Metallic`, `Roughness`, `IOR`,
      `Alpha`, `Normal`, `Coat Weight`, `Emission Color`, `Emission Strength`.
  - Map both sets.
- A linked input:
  - From `ShaderNodeTexImage`: take `node.id` -> `Image` ID.
  - From `ShaderNodeNormalMap`: go one more hop to its image.
  - Anything else (mix nodes, math, procedural textures, color ramps): bake a constant, or flag it. That is the
    long tail.
- Images (`DNA_image_types.h:195-246`):
  - `filepath[1024]`: a leading `//` means relative to the .blend directory. `<UDIM>` tile tokens are possible.
  - `source`, `colorspace_settings`, `alpha_mode`.
- Packed images: `Image.packedfiles` is a ListBase of `ImagePackedFile{packedfile*, view, tile ...}`
  (lines 154-160). `PackedFile{int size, int seek, data*}` (`DNA_packedFile_types.h:15-25`). `data` points to a
  raw DATA block holding the original file bytes (PNG/JPG). We can write those out as is.
  - The old single `Image.packedfile` is deprecated (line 225) but appears in old files.
  - BAT fixture `packed_assets.blend` is a ready test.

## 7. Licensing

| Source | SPDX | What we may do in an Apache-2.0 project |
|---|---|---|
| Blender source (all files read) | GPL-2.0-or-later (e.g. `BLO_core_blend_header.hh:1-3`) | Read to learn facts (layouts, enum values, field names). Do not copy code or comment text. Field names and enum numbers are interface facts we must match anyway |
| BAT | GPL-2.0-or-later (LICENSE.txt and headers); pyproject says GPL-3.0-or-later | Read only. Do not port `header.py`, `dna.py` or `magic_compression.py` |
| assimp | BSD-3-Clause | May port with notice. Little worth porting (pre-4.0 only) |
| Rust `blend` | MIT | May port ideas or code with notice. Its sample files are a test asset (see 8.3) |
| fbtBlend | Zlib | Licence allows it, but the generated `Blender.h` and copied comments derive from Blender's GPL headers. Treat as read only to keep provenance clean |
| Kaitai ksy | CC0-1.0 | Free to use. Covers only the legacy header |
| blender_sdna | BSD-2-Clause | Free, trivial |
| BlendFileParser (Java) | none | Do not use |
| Godot | MIT | n/a (it calls Blender) |

Our reader should be written clean from the format facts in sections 3 to 6, which come from documentation-style
comments, our probe, and permissively licensed code.

## 8. assimp: deprecated, not removed (correction to the brief)

The brief assumed assimp dropped its .blend importer. **It has not.** As of master 2026-09-29 the code is
still in `code/AssetLib/Blender/` and still built (`code/CMakeLists.txt:557-574`). Recent commits are security
hardening (#6866, #6868). What happened instead:

- Issue #5348 "[Discussion] Some ideas for possible next steps" (kimkulling, 2023-11-26):
  https://github.com/assimp/assimp/issues/5348
  > "I have just recognized that the blender import does not work anymore. Blender can export most of the formats people are asking for like: FBX, USD, Alembic, glTF. So my idea is to mark the Blender importer as deprecated. This will free more resources to work on the things you can export from Blender."

  In the thread:
  - stephengold: ".blend was never intended as an interchange format. It changes dramatically from release to release."
  - aavenel proposed deleting it a release later.
- PR #5350 "Fileformats.md: clarify that import of .blend files is deprecated", merged 2024-02-04:
  https://github.com/assimp/assimp/pull/5350
  Now in `assimp/doc/Fileformats.md:70-71`:
  "Note: support for BLEND is deprecated. It is too time-consuming to maintain an undocumented format which
  contains so much more than we need."
- The docs PR assimp/assimp-docs#27 was merged 2024-02-19.

Why it breaks (from reading the code, section 2 row): it requires `MVert`, reads only the 12-byte header,
handles gzip only, and handles legacy materials only.

## 9. Build plan for a pure-Python reader (ordered)

1. **Container (small).**
   - Decompress (gzip stdlib, zstd via `zstandard` or 3.14 `compression.zstd`).
   - Both headers. All three BHead layouts. SDNA parse.
   - Global `old -> block` map. Struct views by file field names.
   - Refuse big-endian and 32-bit files (Blender itself dropped big-endian in 5.0). Refuse unknown header formats.
   - Test: every BAT fixture and every rust-blend fixture parses to `ENDB`.
2. **Object and mesh enumeration.**
   - OB -> data -> ME, names, transforms (loc/rot by rotmode/quat/scale, parent chain with `parentinv`).
   - Material slots with `matbits`.
   - Modifier stack listing, with a refusal policy (section 5).
3. **Mesh decode, newest first (most packs are 3.x to 4.x; check our ledger).**
   - (a) 4.0 to 5.0 CustomData named layers.
   - (b) 5.1+ AttributeStorage, Array and Single.
   - (c) 2.8 to 3.6 legacy MVert/MEdge/MPoly/MLoop/MLoopUV.
   - Then triangulate faces (fan for convex. Ngons and concave faces need ear clipping: a known risk).
   - Split by `material_index`. Normals from sharp flags (smooth: area or angle weighted).
   - UVs: active or render UV map (`active_uv_map_attribute`/`default_uv_map_attribute` strings in 4.x+,
     `active_rnd` on layers before). Flip V for glTF.
   - Emit glTF through the existing gulp writer.
4. **Materials.**
   - Principled constants (both socket name sets) and Image Texture links for base color, metallic/roughness,
     normal (via Normal Map node), emission and alpha.
   - External paths with `//` resolution. Packed images extracted to files.
   - Anything else gets the viewport colour fallback plus a warning in the ledger.
5. **Later, only if packs need them:**
   - mirror/array modifier application
   - vertex colours (`CD_PROP_BYTE_COLOR`/`CD_PROP_COLOR` or `ColorByte`/`ColorFloat`)
   - vertex groups and armatures
   - shape keys
   - custom normals (lnor space decoding)
   - instancing collections
6. **Skip:**
   - subdivision, booleans and geometry nodes (route to Blender)
   - curves, text and metaballs (no mesh in file)
   - grease pencil, particles, linked libraries, UDIM, animation
   - pre-2.80 files

### 9.1 Risks
- Format churn every release: 3.5 runtime change, 4.0 on-disk change, 4.1 auto smooth, 5.0 header, 5.1
  AttributeStorage. A new Blender minor can break us. Reading by presence plus DNA reflection limits that, and a
  CI test on each new fixture catches it.
- Silent wrongness from modifiers (mitigated by refusing).
- Material graphs beyond simple Principled wiring.
- `old` pointer uniqueness: true on disk per `writefile.cc` (map not cleared outside undo), but worth an assert
  (duplicate `old` means refuse).
- Very large files: format 1 allows blocks over 2 GiB (`int64 len`). Use mmap.

### 9.2 Ground truth without running Blender ourselves
- **rust-blend fixtures** (MIT repo, `rust-blend/examples/blend_files/`): 2.80, 2.90, 3.0, 3.5, 4.2 and 5.2
  (one zstd). Default-scene cube, plane and sphere with known counts. **[probe]** Cube 8/6/24, Plane 81/64/256,
  Sphere 482/512/1984.
- **BAT fixtures** (`bat/tests/blendfiles/`, 41 files): versions 2.79 to 5.2 **[probe]**: 279 x8, 280, 281,
  302, 306 x7, 402 x3, 500 x4, 501, 502 x2. Includes packed images and linked libraries. GPL repo: the test
  *files* are data; use them locally for testing, do not vendor them (licence of the fixtures unverified).
- **assimp test models** (`test/models/BLEND`, 2.48 to 2.79): only for the legacy path, and as a BSD-licensed
  source of expected outputs.
- **Our own packs**: many store vendors ship .blend next to FBX/OBJ/glTF of the same model. Compare our decode
  against the gulp FBX/OBJ path (vertex count, bounds, UV count, material names). This is the best
  real-world oracle.
- If an exact oracle is needed later: one-off glTF exports made by someone who has Blender (another machine,
  a lane that already has it), checked in as expected outputs. We still never run Blender in the pipeline.

## 10. Could not verify
- The exact Blender release that introduced stable address ids, and whether 4.5 LTS reads 5.1+ AttributeStorage meshes (the `BLENDER_FILE_MIN_VERSION 405` claim implies it; not checked in the 4.5 branch).
- Python 3.14 `compression.zstd` (PEP 784): from memory, not tested here.
- Licence of the BAT and rust-blend fixture .blend files themselves (repo licence assumed).
- three.js / Babylon.js: no .blend loader, from general knowledge; I did not search their trees.
- Blender's official developer-docs page for the file format: the URLs I tried returned 404. All format facts here come from source comments and the probe.
- My probe crashed on 18 of the 41 BAT fixtures in its throwaway material and mesh code (headers and blocks parsed in all of them). It shows nothing about the format, but the files with packed and linked data were not fully walked.
