# Third-party notices

## Ported code

### migueldeicaza/godot-usd (MIT)

Source: https://github.com/migueldeicaza/godot-usd (src/usd_materials.cpp). godot-gulp's `godot_gulp/materials.py`
ports, in Python, its UsdPreviewSurface shader walk (following connected sources and material interface inputs),
its opacity-into-albedo composite (glTF has no opacity map), and its IOR to F0 rule. godot-gulp extends that logic
(UsdTransform2d rotation and per-texture transforms kept, ORM packing, emissive strength), and those extensions are
Apache-2.0 like the rest of this project.

```
Copyright (c) 2026 Miguel de Icaza (https://github.com/migueldeicaza)

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## Vendored code

### ufbx (MIT or public domain, Unlicense)

Source: https://github.com/ufbx/ufbx, v0.23.1, unmodified, in `godot_gulp/_vendor/ufbx/` (ufbx.c, ufbx.h). It is
compiled into `godot_gulp._ufbxdump` (godot-gulp's own C, Apache-2.0) and reads FBX and OBJ files. godot-gulp uses
it under the MIT alternative:

```
------------------------------------------------------------------------------
This software is available under 2 licenses -- choose whichever you prefer.
------------------------------------------------------------------------------
ALTERNATIVE A - MIT License
Copyright (c) 2020 Samuli Raivio
Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
------------------------------------------------------------------------------
ALTERNATIVE B - Public Domain (www.unlicense.org)
This is free and unencumbered software released into the public domain.
Anyone is free to copy, modify, publish, use, compile, sell, or distribute this
software, either in source code form or as a compiled binary, for any purpose,
commercial or non-commercial, and by any means.
In jurisdictions that recognize copyright laws, the author or authors of this
software dedicate any and all copyright interest in the software to the public
domain. We make this dedication for the benefit of the public at large and to
the detriment of our heirs and successors. We intend this dedication to be an
overt act of relinquishment in perpetuity of all present and future rights to
this software under copyright law.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
----------------------------------------
```

## References (read, not copied)

The Unity reader's formulas (coordinate conversion, metallic/smoothness and HDRP mask repacking, tiling and offset)
were cross-checked against KhronosGroup/UnityGLTF (MIT, Copyright 2017 AltspaceVR) and V-Sekai/unidot_importer (MIT).
No code from them, or from GPL or Unity Companion License tools, is included. See docs/research/unity-importers.md.

The Unreal package reader (`godot_gulp/unreal/`) is written here, following the file layouts as the open readers
document them in code: FabianFG/CUE4Parse (Apache-2.0: the package trailer, FEditorBulkData, FCompressedBuffer, bulk
data headers, custom version numbers), jokelbaf/uasset-parser (MIT, Copyright 2026 JokelBaf: the package summary,
import and export maps, property tags) and atenfyr/UAssetAPI (MIT: native struct layouts such as material inputs).
Nothing from FModel (GPL-3.0) or from Epic's engine source is used. The TSCF_UEDELTA source filter was worked out
from engine content, not from source. See docs/research/unreal-readers.md.

The Blender reader (`godot_gulp/blend.py`) is written here from the .blend format's facts (header, block headers,
SDNA, struct and field names, enum values) as documented in docs/research/blend-readers.md; Blender's source
(GPL-2.0-or-later) and blender-asset-tracer (GPL) were read to learn those facts and no code from them is included.
Test data: two default-scene files from the Rust `blend` crate (MIT, Copyright 2019 Lucas Bittencourt de Souza,
https://github.com/lukebitts/blend), see tests/data/README.md and tests/data/LICENSE.rust-blend.

Oodle (RAD/Epic, proprietary) is not included: a payload compressed with it is read only through the user's own Oodle
library (GODOT_GULP_OODLE), e.g. the one inside their own Unreal Engine install.

## Runtime dependencies (installed by pip, not bundled)

| Package | Licence | Use |
|---|---|---|
| usd-core (OpenUSD, Pixar) | Tomorrow Open Source Technology License 1.0 (a modified Apache 2.0) | reading USD stages |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib and CC0-1.0 parts) | geometry and buffers |
| Pillow | MIT-CMU (HPND) | texture packing and conversion |
| PyYAML | MIT | Unity YAML documents |
| zstandard | BSD-3-Clause | zstd-compressed .blend files (Blender 3.0+) |

Godot (MIT, https://godotengine.org) is used only as an external program for the optional import check.
