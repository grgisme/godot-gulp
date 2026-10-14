# Unreal asset readers: survey for a headless Python CLI (glTF out)

Method: shallow clones of each project, code read only, nothing executed.
Date 2026-10-11. Paths below are relative to each project's repository root.

## 1. Comparison table

| Tool | Lang | SPDX (from LICENSE) | Port into Apache-2.0 Python? | Cooked .uasset | Uncooked (editor) .uasset | .pak | IoStore | Mesh/tex/mat/map out |
|---|---|---|---|---|---|---|---|---|
| CUE4Parse (FabianFG) | C# | Apache-2.0 (NOTICE lists many MIT/MS-PL/zlib deps) | Yes, with NOTICE credit (keep copyright, mark changes) | Excellent, UE1..5.6+ with per-game hacks | Tagged properties only; mesh/texture bodies skipped (see s3) | v1..v15 | Yes (.utoc/.ucas, UE4.25+/5) | glTF (SharpGLTF), PSK/ActorX, USD, UEFormat, PNG, map->USD/JSON |
| FModel (4sval) | C# GUI | GPL-3.0-only (LICENSE is GPLv3 text; check headers before relying on "or later") | NO (GPL contaminates). Read for behaviour only | uses CUE4Parse | same | same | same | GUI over CUE4Parse |
| UEViewer / umodel (gildor2) | C++ | MIT (LICENSE.txt, README l.227); libs/ has separate third-party licences | Yes with credit (MIT) | Very good, UE1..4.x, partial UE5 | Only legacy FRawMesh source meshes and PNG source art; MeshDescription is an explicit TODO | v1..v11 (older), partial | Partial/limited | PSK/PSKX, glTF, MD5, PNG/TGA/DDS, materials .mat text |
| UAssetAPI (atenfyr) | C# | MIT | Yes with credit | Yes, read+write, 4.13..5.7, usmap for unversioned | Best at this: round-trips editor assets, tagged properties, binary equality | via repak bindings | no | Structure only; no mesh/texture decode (exports without class handler become RawExport/NormalExport with raw tail) |
| UAssetGUI (atenfyr) | C# GUI | MIT | Yes (but nothing to port; it wraps UAssetAPI) | same | same | - | - | JSON view |
| repak (trumank) | Rust | MIT OR Apache-2.0 (Cargo.toml, both LICENSE files) | Yes (pick Apache-2.0 side) | n/a | n/a | v1..v11 (README "compatibility"), AES, zlib/gzip/zstd/oodle | no | extract only |
| unrealmodding/unreal_asset + unreal_pak (AstroTechies) | Rust | MIT (LICENSE; Cargo.toml `license = "MIT"`) | Yes with credit | Yes, 4.x (modding focus) | Yes for properties; exports: Normal, Level, DataTable, StringTable, Enum, Struct, Function, Raw | pak v1..v11 | no | structure only |
| jokelbaf/uasset-parser | pure Python 3.12 | MIT | Yes with credit; closest to our target | Yes, UE4/5 cooked Texture2D | Reads summary/tagged props (summary.py branches on is_filter_editor_only) but textures only from cooked platform data | none (feed bytes) | none | Texture2D -> RGBA numpy; ~1.8k lines |
| Befzz blender3d_import_psk_psa | Python/Blender | GPL-2.0-or-later (header block in addons/*.py) | NO; and only reads PSK/PSA which UEViewer/CUE4Parse write | n/a | n/a | n/a | n/a | PSK/PSA -> Blender |
| Epic Interchange / Datasmith | C++ | UE EULA (source only visible to licensed Epic accounts); not cloneable | NO | - | Interchange/Datasmith import FBX/CAD, not .uasset reading; CUE4Parse has Exports/Interchange only to parse the asset classes | - | - | n/a |

Other Python hits from `gh search`: ay27/uasset-parser-py, kudiaotaibai/UE4-UAssetParser, ahalpha/simple-uasset-parsing. ay27 clone returned nothing usable, others are small hobby scripts; not read. Treat as unvetted.

## 2. Container formats

- Loose package = .uasset (header, name/import/export maps, thumbnails) + .uexp (export bodies) + .ubulk/.uptnl (streamed bulk). In UE5 uncooked (editor-saved) assets are a single .uasset with the exports inline, plus an optional package TRAILER (FPackageTrailer) holding virtualized/large payloads. Cooked files are split .uasset/.uexp.
- Cooked vs uncooked is flagged by package flag PKG_FilterEditorOnly (cooked sets it). Seen in: CUE4Parse `FAssetArchive.IsFilterEditorOnly` (UE4/Assets/Readers/FAssetArchive.cs:40), jokelbaf `summary.py:69/106`.
- .pak: CUE4Parse `UE4/Pak/Objects/FPakInfo.cs` enumerates versions 1..15 (v8 FName compression methods, v9 frozen index, v10 path-hash index, v11 fnv64 fix, v12+ utf8 dir, v13 sorted flat dir, v14 pakchunk index, v15 AES method in trailer). repak covers v1..v11 (+AES). Compression methods in FPakInfo: None, Zlib, Gzip, Oodle, LZ4, Zstd. AES-256 on index and data when encrypted.
- IoStore (.utoc/.ucas + global.utoc): CUE4Parse `UE4/IO/IoStoreReader.cs`, `IoChunkToc.cs`, `IoPackage.cs` (UE5 zen package format, different header from classic .uasset). Only CUE4Parse implements it among the open tools. Commonly Oodle-compressed.
- Oodle: proprietary RAD/Epic. CUE4Parse `Compression/Compression.cs:8,25` uses OodleDotNet and downloads/loads native oo2core; repak `oodle_loader/src/lib.rs:101,188` fetches a prebuilt DLL/SO from the WorkingRobot/OodleUE GitHub repo at runtime with a hash check. No open-source decoder is legitimately redistributable; shipping the binary or its decoder needs an Epic/RAD licence. For a Python tool: shell out to a user-supplied lib via ctypes, never bundle.
- What Fab/Marketplace packs ship: a Content folder of EDITOR (uncooked) .uasset files saved by the editor (and sometimes .umap), no .pak, no .utoc, no Oodle at package level. This is the common case for bought packs. It is also the case where every existing reader is weakest (see s3).

## 3. Package structure and class decoding

### Header and maps (all tools)
Summary (tag 0x9E2A83C1, legacy file version negative, UE4 object version + UE5 object version + licensee version, custom version container, name/import/export map offsets, flags, thumbnail/bulk offsets, trailer offset) -> name map -> import map (class package, class name, outer, object name) -> export map (class index, super, outer, name, serial size/offset, flags). Versioned reading: jokelbaf `summary.py` (read_versions, 260 lines) and `versions.py` (ObjectVersionUE4/UE5 enums, `PackageFileVersion.from_release(4,25)` for packages with zeroed versions). UAssetAPI `UnrealTypes/ObjectVersion.cs`, `EngineVersion.cs` (VER_UE4_13..VER_UE5_7), `CustomVersions/CustomVersions.cs`. CUE4Parse `UE4/Versions/` has ~60 custom version files (FRenderingObjectVersion, FEditorObjectVersion, FUE5MainStreamObjectVersion ...), each changes serialization of some class.

### Properties
- Tagged properties (all editor/uncooked assets and most UE4 cooked): name, type name, size, tag data per type, array/map/struct recursion. UE5.4+ changed type names to a tree (UAssetAPI `FPropertyTypeName.cs`). Easy and well specified.
- Unversioned properties (UE5/UE4.25+ cooked with the unversioned flag): bitmask fragments + needs a schema (.usmap mappings) to know property order/types. UAssetAPI `Unversioned/FUnversionedHeader.cs, Usmap.cs`; CUE4Parse MappingsProvider. NOT needed for editor assets (tagged).

### StaticMesh
- Cooked: `UStaticMesh.Deserialize` (CUE4Parse UE4/Assets/Exports/StaticMesh/UStaticMesh.cs) reads FStripDataFlags, bCooked, BodySetup, NavCollision, LightingGuid, Sockets, then ONLY IF cooked `FStaticMeshRenderData` (FStaticMeshLODResources: Sections {MaterialIndex, FirstIndex, NumTriangles, Min/MaxVertexIndex}, PositionVertexBuffer, StaticMeshVertexBuffer (packed tangents + UVs, half or float), ColorVertexBuffer, IndexBuffer 16/32, optional Nanite resources `FNaniteResources`). Material slots = `FStaticMaterial` (MaterialInterface, SlotName, ImportedName).
- CRITICAL: line ~178 of UStaticMesh.cs: `if (!Ar.IsFilterEditorOnly || Ar.Game < GAME_UE4_0) { Ar.Position = validPos; return; }`. So CUE4Parse returns with NO geometry for any uncooked package. Render data does not exist in editor assets: the editor stores SOURCE data and rebuilds render data into the Derived Data Cache (DDC) on load; the DDC is not shipped with packs.
- Uncooked storage: `StaticMesh.SourceModels[]` each holding `FMeshDescriptionBulkData` (UE4.24+/5.x) or legacy `FRawMeshBulkData` (<4.24 era; FEditorObjectVersion::StaticMeshDeprecatedRawMesh). UEViewer `Unreal/UnrealMesh/UnMesh4.cpp:2678-2720` shows the serialization and `ConvertSourceModels()` (l.3081-3200) decodes only the legacy FRawMesh path (WedgeIndices, VertexPositions, WedgeTexCoords[8], WedgeNormal/Tangent/Binormal, FaceMaterialIndices, FaceSmoothingMasks; one vertex per wedge, indices 0..n). For the modern MeshDescription path UEViewer leaves a TODO (l.2706-2712): "serialization of BulkData: FMeshDescription::Serialize ... TBitArray AllocatedIndices then items where bit is 1". No open tool decodes FMeshDescription. This is the main gap for our CLI.
- FMeshDescription (from the TODO and Epic source knowledge): a bulk-data blob (zlib/Oodle FCompressedBuffer in UE5 via FEditorBulkData, in-package or in the package trailer) containing TMeshElementContainers: Vertices, VertexInstances, Edges, Triangles, Polygons, PolygonGroups (= material slots), each as bit-array of allocated ids + per-element data, plus attribute sets (VertexPosition, VertexInstanceNormal/Tangent/BinormalSign/Color/UV with channel count, PolygonGroupImportedMaterialSlotName). Needs custom-version-driven reading (FEditorObjectVersion, FReleaseObjectVersion, FUE5MainStreamObjectVersion, FEnterpriseObjectVersion). Plan on reverse engineering against Epic's MeshDescription.cpp (EULA-gated source; must work from behaviour and test files, not copy).
- Editor bulk data container: CUE4Parse `UE4/Assets/Objects/FEditorBulkData.cs` (flags incl. StoredInPackageTrailer 1<<9, GUID, content SHA, PayloadSize, offset), `FPackageTrailer.cs` (header, payload lookup table keyed by FSHAHash, Local/Referenced access modes), `Objects/Core/Compression/FCompressedBuffer(.Header).cs` (magic 0xb7756362, method None/Oodle/LZ4, block layout). CUE4Parse reads the payload bytes but does not decompress them (no consumer). Compression of these payloads in practice: None, LZ4, or Oodle (Epic default is Oodle Mermaid/Kraken for virtualization; plain package payloads often uncompressed or Oodle).

### Texture2D
- Cooked: `UTexture2D` + `FTexturePlatformData` (CUE4Parse Texture/FTexturePlatformData.cs: SizeX, SizeY, PackedData (NumSlices/CubeMap flag), PixelFormat string, FirstMipToSerialize, `FTexture2DMipMap` list with FByteBulkData inline / .ubulk, optional VT data `FVirtualTextureBuiltData`). UE5.2+ throws NotImplemented for "bUsingDerivedData". Formats DXT1/5, BC4/5/6H/7, ASTC, ETC, with decoders in CUE4Parse-Conversion/Textures (BC/, DXT/, ASTC/, ETC/, Crunch/, plus PlatformDeswizzlers.cs); jokelbaf `textures/decoders/`.
- Uncooked: `UTexture.Deserialize` (CUE4Parse UTexture.cs:81-101) reads, when editor data is not stripped, either legacy `FByteBulkData SourceArt` (before FUE5MainStreamObjectVersion.TextureSourceVirtualization) or `FEditorBulkData EditorData`. Pixel layout/size/format come from the `Source` struct property (FTextureSource: SizeX, SizeY, NumSlices, NumMips, Format ETextureSourceFormat {G8, BGRA8, BGRE8, RGBA16, RGBA16F}, CompressionFormat TSCF_None/PNG/JPEG) - see UEViewer UnMaterial3.h:15-45 `bPNGCompressed`, `Format`. UEViewer UnTexture3.cpp:933-945, 1043-1054 only uses SourceArt when PNG compressed (decode with libpng). CUE4Parse never turns EditorData into an image. There are no mips and no platform data in uncooked textures; you get the original art (PNG/raw BGRA8, 16-bit, HDR), which is actually BETTER than cooked for our purposes (lossless, no BC artefacts). Then apply SRGB, CompressionSettings (TC_Normalmap, TC_Masks, TC_Grayscale...) from properties (UTexture.cs:68-74).

### MaterialInstanceConstant / Material
- `UMaterialInstanceConstant` (CUE4Parse UE4/Assets/Exports/Material/UMaterialInstanceConstant.cs): Parent (FPackageIndex), ScalarParameterValues [ParameterInfo{Name, Association, Index}, ParameterValue, ExpressionGUID], VectorParameterValues (LinearColor), TextureParameterValues (texture ref), all plain tagged properties. Also `BasePropertyOverrides`, `StaticParameters` (switch parameters; hand-serialized before FRenderingObjectVersion.MaterialAttributeLayerParameters, UMaterialInstance.cs:47). Resolution walks Parent chain (`GetParams` in the same file) and fills CMaterialParams (CMaterialParams.cs: Diffuse, Normal, Specular, Emissive, Opacity, Cube...) by GUESSING from parameter names ("BaseColor", "Diffuse", "Normal", "Rough" ...).
- `UMaterial` (parent master material): in a cooked file expressions are stripped; CUE4Parse/UEViewer pick textures by walking the expression list or by name heuristics. In an uncooked UMaterial the full expression graph exists (UMaterialExpression* exports: TextureSample, ScalarParameter, VectorParameter, Multiply, Lerp, ...; connection via FExpressionInput properties on the UMaterial: BaseColor, Metallic, Specular, Roughness, Normal, EmissiveColor, Opacity). That is real data, no heuristics needed, but evaluating the graph to a glTF PBR material is a mini-compiler. Realistic subset: trace each input back through TextureSample / Parameter / Multiply / Constant / TextureCoordinate / ComponentMask / OneMinus nodes; export unsupported nodes as a warning and a flat factor.
- Packed textures: Unreal ORM/ORD/MRA/ARM masks (e.g. R=AO, G=Roughness, B=Metallic) must be repacked to glTF's G=roughness, B=metallic, R=occlusion; channel mapping is only known from the master material graph, which is why the graph walk matters.

### Version dependence
Object version UE4 (VER_UE4_*, up to 522 at 4.27) and UE5 (VER_UE5_*, INITIAL_VERSION 1000 ..., NAMES_REFERENCED_FROM_EXPORT_DATA, PAYLOAD_TOC, OPTIONAL_RESOURCES, LARGE_WORLD_COORDINATES (5.0: float->double in FVector/FTransform tagged/binary), REMOVE_OBJECT_EXPORT_PACKAGE_GUID, TRACK_OBJECT_EXPORT_IS_INHERITED, ADD_SOFTOBJECTPATH_LIST, DATA_RESOURCES (5.3), SCRIPT_SERIALIZATION_OFFSET (5.4), PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION (5.4), PROPERTY_TAG_COMPLETE_TYPE_NAME (5.5)...). LWC is the most visible: in UE5 FVector is 3 doubles inside structs, floats in some native binary blobs. Custom versions decide the rest.

## 4. Maps (.umap)

- A .umap is a package whose exports include UWorld, ULevel, and every Actor with its components as sub-exports. ULevel holds an Actors array of FPackageIndex. Actor -> `RootComponent` property -> USceneComponent properties `RelativeLocation` (FVector), `RelativeRotation` (FRotator, degrees pitch/yaw/roll), `RelativeScale3D`, `AttachParent`, `AttachSocketName`, `bAbsoluteLocation/Rotation/Scale`. StaticMeshActor has `StaticMeshComponent` whose `StaticMesh` property points (import) to the mesh asset and `OverrideMaterials` array overrides slots. Instances: `UInstancedStaticMeshComponent`/HISM with PerInstanceSMData (binary FInstancedStaticMeshInstanceData: FMatrix) - ComponentDto.cs:213-233, 273.
- CUE4Parse reconstruction: `CUE4Parse-Conversion/Dto/WorldDto.cs` (roots = actors with no AttachParent, l.30), `ActorDto.cs` (RootComponent), `ComponentDto.cs` (SceneComponentDto with `component.GetRelativeTransform()`, children list, AttachedActors; TODO: bAbsolute* flags and attach sockets are NOT handled, ComponentDto.cs:62-80), `Exporters/WorldExporter.cs`, `Formats/World/UsdWorldFormat.cs` (positions/orientations mirrored, l.220-221). World Partition / streaming levels: `StreamingLevelFilterArgs.cs`, `ActorStreamingDto.cs`; external actors (__ExternalActors__ one-file-per-actor) and data layers needed for UE5 WP maps.
- Composition: world = parent.world * local, local = T(RelativeLocation) * R(RelativeRotation.Quaternion()) * S(RelativeScale3D), applied root to leaf through AttachParent.
- Uncooked .umap works with plain tagged-property reading; no binary bodies needed for this path (only the mesh assets themselves need the heavy decode). So "levels as glTF nodes referencing mesh files" is cheap and low risk.

## 5. Coordinate conversion

Unreal: left-handed, Z up, X forward, Y right, unit = cm. FRotator = degrees, order applied yaw(Z), pitch(Y), roll(X) (see formula). glTF: right-handed, +Y up, -Z forward (by convention), metres.

Both big exporters use the same position/direction map, a Y/Z swap (a reflection that also flips handedness):
- UEViewer `Exporters/ExportGLTF.cpp:55-75`: `TransformPosition: swap(y,z); *0.01`; `TransformDirection: swap(y,z)`; `TransformRotation: swap(q.y,q.z)` with the W negate commented out ("works correctly without").
- CUE4Parse `CUE4Parse-Conversion/Writers/Gltf/Gltf.cs:25,244-250`: `UnitScale = 0.01f`, `SwapYZ(v) = (x, z, y)`, `SwapYZ(q) = (qx, qz, qy, -qw)`, normals/tangents swap + normalize, tangent.w kept.
- Triangle indices are copied in the original order (Gltf.cs:164-166, UEViewer l.285-290). The reflection flips the geometric orientation and the handedness flips the winding convention, which cancel: glTF front faces (CCW) come out right with NO index swap.
- UVs: copied as-is, no V flip (Unreal UV origin top-left == glTF). Only add 1-v if targeting Blender/OpenGL-origin tools.

So for our CLI (verified by the two independent implementations plus the math: a reflection M with det -1 maps a rotation quaternion to (pseudovector rule) (-x',-y',-z',w) = (x',y',z',-w) after the swap):
```
p_gltf = (px, pz, py) * 0.01
n_gltf = normalize(nx, nz, ny)
tangent_gltf = (tx, tz, ty, w)     # check sign of w against a normal-mapped test asset
q_gltf = (qx, qz, qy, -qw)         # CUE4Parse; UEViewer's no-negate version is a known disagreement: test with a rotated actor
indices unchanged, uv unchanged
scale: (sx, sz, sy)
```
Quaternion from FRotator (CUE4Parse Objects/Core/Math/FRotator.cs:88-110, matches Epic):
```
sp,cp = sin/cos(Pitch/2); sy,cy = sin/cos(Yaw/2); sr,cr = sin/cos(Roll/2)   # degrees->radians
X =  cr*sp*sy - sr*cp*cy
Y = -cr*sp*cy - sr*cp*sy
Z =  cr*cp*sy - sr*sp*cy
W =  cr*cp*cy + sr*sp*sy
```
Note CUE4Parse USD output uses a different map (x,-y,z) i.e. mirror Y and keep Z up (UsdMeshFormat.cs:230, `MIRROR_MESH`; quat (-x,y,-z,w)); glTF with Y-up uses the swap above. Alternative for Z-up targets (Blender/Godot Z-up import): mirror Y only. Pick one and document.

Skinned mesh/bone transforms use the same swap (Gltf.cs:129-131).

## 6. Where each breaks

- CUE4Parse: needs correct game/version selection (EGame enum, Defines.md); per-game hacks everywhere (GAME_* branches in UStaticMesh.cs) are noise for us. Skips uncooked meshes (UStaticMesh.cs ~178). UE5.2+ texture derived-data refs throw NotImplemented (FTexturePlatformData.cs:68). Editor bulk data payload not decompressed. Nanite meshes: `FNaniteResources` is parsed (clusters in `Nanite/`, exported as `MeshLodDto.NaniteClusters.cs`) but cooked static meshes with Nanite typically still carry a fallback low LOD render data. Virtual textures (RVT/SVT): `FVirtualTextureBuiltData`, tile-based, partial. Encrypted paks need the AES key; IoStore also may need a key per container.
- UEViewer: effectively frozen for UE5 (UE4 era; maintainer notes), cannot do IoStore properly, MeshDescription TODO, material resolution by heuristics, Linux build needs libs.
- UAssetAPI: no geometry/texture decode by design. Excellent tagged properties and import/export map. Unknown binary tails stay raw. "Failed to maintain binary equality" asserts are reported as a goal. Contributing note: repo rejects AI-generated PRs (README). We are not contributing, only reading, but do not send them AI-written patches.
- unreal_asset (Rust): modding oriented (Astroneer era), UE4.x, lags UE5.
- jokelbaf: Texture2D cooked only, UE5.2+ derived-data textures, no mesh.
- Anything: Oodle-compressed payloads unreadable without the proprietary lib (pak data, IoStore chunks, FCompressedBuffer).
- Nanite in UNCOOKED assets: the source mesh (MeshDescription) is still a normal triangle mesh. Nanite is a build-time flag (`NaniteSettings.bEnabled`), so uncooked is easier than cooked. High-poly sources can be millions of triangles; plan for LOD0 only plus an optional decimate.

## 7. Reuse, credit, and what ours needs

### Reuse / credit (NOTICE entries)
- CUE4Parse (Apache-2.0): port-able structure for summary, name/import/export maps, FEditorBulkData/FPackageTrailer/FCompressedBufferHeader, custom-version tables, FRotator.Quaternion, Gltf.cs conversion constants. Credit in NOTICE with copyright line and note of modification. Its own third-party list is large; we depend on none of it.
- UAssetAPI (MIT): custom version tables, property type coverage, `FPropertyTypeName` (UE5.5 type names), usmap if we ever want cooked unversioned. Credit in NOTICE.
- UEViewer (MIT): the FRawMesh layout and ConvertSourceModels (l.3081-3200), FTextureSource handling, glTF exporter semantic. Credit in NOTICE.
- jokelbaf/uasset-parser (MIT): the only Python template (reader/summary/versions/bulk_data/texture decoders); fork-and-extend is the fastest start. Credit.
- repak (MIT OR Apache-2.0): reference for pak v1..v11 index/entry layout if we add .pak; or just shell out.
- Do NOT port: FModel (GPL-3.0), Befzz Blender add-on (GPL-2.0+). Read only. Do not paste Epic engine source (EULA) - FMeshDescription has to be reverse engineered from test assets and the TODO notes.

### Scope for "Fab editor .uasset -> glTF"
UE versions: realistic target UE4.27, UE5.0 through 5.6 (Fab content in 2025-26 is mostly 5.1-5.6; many packs also list 4.27). Version-handling plan: read summary object versions + custom versions, branch only where a class we care about changed; fail loudly (with version numbers) elsewhere.

Minimal class set:
- Core: package summary, names, imports, exports, custom versions, tagged properties incl. FVector/FRotator/FTransform/FLinearColor/soft+hard object refs, struct/array/map/enum/name/text/bytes, UE5 LWC doubles, UE5.5 type names.
- UStaticMesh (editor): properties (StaticMaterials, LightMapResolution, NaniteSettings), SourceModels bulk (FMeshDescription via FEditorBulkData, legacy FRawMesh via FByteBulkData), trailer payload resolution, FCompressedBuffer decode (None, LZ4 easy, Oodle via optional user lib, zlib).
- UTexture2D (editor): `Source` struct + EditorData/SourceArt payload -> image (G8, BGRA8, RGBA16, RGBA16F; PNG/JPEG compressed variants), plus CompressionSettings and SRGB. Also still handle cooked platform data as a fallback (BC1-7, simple).
- UMaterialInstanceConstant: Parent chain + Scalar/Vector/Texture params + BasePropertyOverrides (BlendMode, TwoSided). UMaterial: expression graph for BaseColor/Metallic/Roughness/Normal/Emissive/Opacity/AO, with TextureSample, parameters, Multiply, Lerp, Constant, TextureCoordinate, ComponentMask, OneMinus, Append. Optional later: MaterialFunctionCall inlining (Quixel/Megascans and Fab packs use many functions; this is where fidelity drops).
- ULevel/UWorld/AActor/USceneComponent/UStaticMeshComponent/UInstancedStaticMeshComponent for .umap; skip landscape, foliage (FoliageInstancedStaticMeshComponent has its own binary), splines first pass.
- Skeletal meshes: out of scope for v1.

### Feasibility verdict (honest)
- Headless Python reading of UNCOOKED Fab .uasset is realistic for textures and materials/maps (hard but bounded), and is the right target because it avoids Oodle, IoStore, pak, unversioned props, platform swizzles. Estimated difficulty: package + tagged properties + MIC + level transforms is a few days of focused work using jokelbaf as a base and UAssetAPI/CUE4Parse as references. Source-art textures: easy (days) once FEditorBulkData + FCompressedBuffer are done, except Oodle payloads.
- Static meshes are the risk. No open tool decodes FMeshDescription; we would reverse-engineer a TArray/TBitArray-based container whose layout depends on 3-5 custom versions across UE4.24..5.6, with attribute sets, and validate against real packs. Realistic but needs test assets from each version and a ground-truth oracle (export the same mesh from the editor to OBJ/FBX via someone with UE, or compare triangle counts/bounds with the asset's `Bounds` and `NumTriangles` in the uasset's metadata). Without ground truth, risk of silently wrong normals/UVs is high.
- Bulk data payloads may be Oodle-compressed (UE5 virtualization default). Plain editor-saved packages usually keep payloads inline and uncompressed or zlib, but we cannot guarantee; we must carry an optional ctypes loader for a user-supplied liboo2core and a clear error otherwise. Do not redistribute Oodle.
- Fall-backs that reduce risk: (1) many Fab packs include FBX/other source or cooked previews; check the pack first. (2) If a pack ships DDC or cooked uasset/uexp, use the cooked render data path (well documented in CUE4Parse FStaticMeshLODResources / UEViewer). (3) Use a one-off mesh oracle from the Unreal editor on one machine to fix the reader, then run headless forever.
- It is NOT realistic to match Unreal's material look generally; target "base colour + packed ORM + normal + emissive + opacity mask + two-sided" and flag the rest.

## 8. Build plan for ours (ordered)
1. Fork structure of jokelbaf/uasset-parser (MIT, credit): extend summary/versions for UE5.x, add package trailer + FEditorBulkData + FCompressedBuffer (CUE4Parse refs), custom-version container.
2. Tagged property reader complete (struct types, LWC, UE5.5 type names); verify against UAssetAPI JSON output on a few Fab files (that tool is the oracle for structure; do not run in CI).
3. Textures from Source/EditorData -> PNG/EXR. Then MIC/UMaterial graph -> glTF PBR.
4. Maps: actor/component transform tree -> glTF nodes; ISM instances -> multiple nodes or EXT_mesh_gpu_instancing.
5. Static mesh: legacy FRawMesh first (UEViewer algorithm, easy win for 4.x packs), then FMeshDescription with test corpus; LOD0 only; one primitive per material slot (PolygonGroup); weld wedges by (position, normal, uv) tuple.
6. Conversion constants in section 5, unit tests on a rotated/mirrored asymmetrical cube (include an L-shaped mesh to detect mirror/winding errors).
7. Optional: cooked fallback (pak via repak subprocess, uasset cooked mesh/texture), Oodle via user-supplied library only.

## 9. What was built, and how it was checked (2026-10-12)

The reader in `godot_gulp/unreal/` covers steps 1 to 6 of section 8 for UE 5.x editor packages. FMeshDescription,
the gap in section 3, turned out to be a regular container format; its layout (in `mesh.py`) was worked out from
engine content, not from Epic source.

Checked against a UE 5.8 install's own engine content (local only, nothing from it is committed):

- package and properties: 55,120 of 55,376 exports (the misses are Blueprint pin types)
- textures: 4,254 of 4,254 source images, including Oodle payloads (through the install's own Oodle library) and the
  UE 5.6+ TSCF_UEDELTA filter (blocks of whole rows up to 128 KiB, each row a byte delta from the one above)
- materials: 491 of 491 materials and instances evaluated
- static meshes: 183 of 184 (518k triangles); the miss is an experimental Dataflow mesh
- oracle: UnrealEditor-Cmd, headless (-nullrhi, no window), exported 10 engine meshes to OBJ with Epic's exporter.
  All 10 match the decoded meshes triangle for triangle (largest error 5e-7 cm, 58,540-triangle meshes included),
  corners in the same cyclic order. Epic's OBJ exporter writes (x, z, y), the same map godot-gulp uses for glTF, so
  positions, triangulation and winding agree with Epic's own export.
- skeletal (UE 5.1+): the mesh description adds SkinWeights on Vertices (an array attribute: chunks of up to 256
  elements, each chunk its packed values (weight in the low 16 bits, bone in the high 16), the elements' offsets (not
  in element order), counts and capacities) and a Bones element (Name, ParentIndex, Pose as FTransform doubles).
  UE 5.8 resaves of the engine's skeletal meshes, exported by Epic's glTF exporter, match: 68 of 68 joints (bind pose
  within 1e-6), every triangle, and the weights of all 3,189 vertex positions.
- animation (UE 5.2+): an AnimSequence's keys are a MovieScene control-rig section, one FK control per bone, nine
  FMovieSceneFloatChannels (translation cm, rotation as roll/pitch/yaw degrees, scale) in the parent bone's space.
  The engine's idle and walk, resaved in 5.8, match Epic's glTF export within 9e-5 m and 4e-4 (quaternion).
- UE 5.8 writes an FName attribute channel's count twice; older saves once (tried in that order; a wrong guess cannot
  parse to the end).
