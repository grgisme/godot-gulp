# Test data

- `scene_42.blend`: our own scene, written by `tests/make_blend_fixture.py` with Blender 4.2 (Apache-2.0 like the
  rest of this repository).
- `rig_42.blend` and `hidden_42.blend`: our own, written by `tests/make_blend_rig_fixture.py` with Blender 4.2 (a quad
  skinned to a 2-bone armature; a plain quad beside a render-hidden rig widget with a Subdivision modifier).
- `rust_blend_2_90.blend` (gzip-wrapped) and `rust_blend_5_2.blend`: Blender's default scene saved by Blender 2.90
  and 5.2, from the examples of the Rust `blend` crate (https://github.com/lukebitts/blend, examples/blend_files),
  MIT licence, Copyright 2019 Lucas Bittencourt de Souza: see `LICENSE.rust-blend`.
