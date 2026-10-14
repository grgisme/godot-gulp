from setuptools import Extension, setup

# the FBX/OBJ reader: ufbx (vendored, MIT or public domain) behind a one-call C dump (godot_gulp/_ufbxdump.c)
setup(ext_modules=[Extension("godot_gulp._ufbxdump", sources=["godot_gulp/_ufbxdump.c", "godot_gulp/_vendor/ufbx/ufbx.c"],
                             include_dirs=["godot_gulp"], extra_compile_args=["-O2"])])
