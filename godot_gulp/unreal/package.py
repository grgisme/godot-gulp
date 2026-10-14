"""An Unreal package (.uasset / .umap, editor-saved or cooked, UE 4.27 and 5.x) read without Unreal: the summary, the
name, import and export maps, the soft object path list, and each export's tagged properties (properties.py).

    pkg = Package.open(path)          # the .uasset, with its .uexp when the package was cooked split
    pkg.exports[i].class_name, pkg.exports[i].name, pkg.props(i), pkg.export_data(i)

The layout follows the engine's FPackageFileSummary / FObjectImport / FObjectExport serialisers as documented by the
open readers (jokelbaf/uasset-parser and UAssetAPI, MIT; CUE4Parse, Apache-2.0): see THIRD_PARTY.md.
"""
import os, struct

TAG = 0x9E2A83C1

# EUnrealEngineObjectUE4Version / UE5 values that change what is read here
UE4_PRELOAD_DEPENDENCIES_IN_COOKED_EXPORTS = 507
UE4_TEMPLATE_INDEX_IN_COOKED_EXPORTS = 508
UE4_BIT64_EXPORTMAP_SERIALSIZES = 511
UE4_ADDED_SOFT_OBJECT_PATH = 514
UE4_ADDED_PACKAGE_SUMMARY_LOCALIZATION_ID = 516
UE4_ADDED_PACKAGE_OWNER = 518
UE4_NON_OUTER_PACKAGE_IMPORT = 520
UE5_NAMES_REFERENCED_FROM_EXPORT_DATA = 1001
UE5_PAYLOAD_TOC = 1002
UE5_OPTIONAL_RESOURCES = 1003
UE5_LARGE_WORLD_COORDINATES = 1004
UE5_REMOVE_OBJECT_EXPORT_PACKAGE_GUID = 1005
UE5_TRACK_OBJECT_EXPORT_IS_INHERITED = 1006
UE5_FSOFTOBJECTPATH_REMOVE_ASSET_PATH_FNAMES = 1007
UE5_ADD_SOFTOBJECTPATH_LIST = 1008
UE5_DATA_RESOURCES = 1009
UE5_SCRIPT_SERIALIZATION_OFFSET = 1010
UE5_PROPERTY_TAG_EXTENSION_AND_OVERRIDABLE_SERIALIZATION = 1011
UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME = 1012
UE5_METADATA_SERIALIZATION_OFFSET = 1014
UE5_VERSE_CELLS = 1015
UE5_PACKAGE_SAVED_HASH = 1016
UE5_IMPORT_TYPE_HIERARCHIES = 1018
UE5_KNOWN = 1018                          # newer packages are read as this and say so in Package.warnings

PKG_FILTER_EDITOR_ONLY = 0x80000000
PKG_UNVERSIONED_PROPERTIES = 0x00002000
RF_CLASS_DEFAULT_OBJECT = 0x10
JSON_FIRST = {"AssetImportData", "InterchangeAssetImportData", "FbxAssetImportData", "FbxStaticMeshImportData",
              "FbxSkeletalMeshImportData", "FbxTextureImportData", "FbxAnimSequenceImportData", "FbxSceneImportData",
              "ReimportFbxSceneFactory"}


class UnrealError(Exception):
    pass


class Reader:
    """Little-endian reads over bytes, with the package's names for FName."""

    def __init__(self, data, pos=0, names=None):
        self.b, self.pos, self.names = data, pos, names or []

    def _u(self, fmt, n):
        if self.pos + n > len(self.b):
            raise UnrealError("read past the end (%d + %d > %d)" % (self.pos, n, len(self.b)))
        v = struct.unpack_from(fmt, self.b, self.pos)[0]
        self.pos += n
        return v

    def u8(self): return self._u("<B", 1)
    def i16(self): return self._u("<h", 2)
    def u16(self): return self._u("<H", 2)
    def i32(self): return self._u("<i", 4)
    def u32(self): return self._u("<I", 4)
    def i64(self): return self._u("<q", 8)
    def u64(self): return self._u("<Q", 8)
    def f32(self): return self._u("<f", 4)
    def f64(self): return self._u("<d", 8)
    def i8(self): return self._u("<b", 1)

    def bool32(self):
        return self.i32() != 0

    def raw(self, n):
        if n < 0 or self.pos + n > len(self.b):
            raise UnrealError("bad length %d at %d" % (n, self.pos))
        v = self.b[self.pos:self.pos + n]
        self.pos += n
        return v

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:                                            # UTF-16, -n code units with the terminator
            if -n > 1 << 20:
                raise UnrealError("string length %d" % n)
            return self.raw(-n * 2)[:-2].decode("utf-16-le", "replace")
        if n > 1 << 20:
            raise UnrealError("string length %d" % n)
        return self.raw(n)[:-1].decode("latin-1")

    def guid(self):
        return self.raw(16).hex()

    def name(self):
        i, num = self.i32(), self.i32()
        if not 0 <= i < len(self.names):
            raise UnrealError("name index %d of %d" % (i, len(self.names)))
        s = self.names[i]
        return s if num == 0 else "%s_%d" % (s, num - 1)

    def array(self, fn, limit=1 << 22):
        n = self.i32()
        if not 0 <= n <= limit:
            raise UnrealError("array count %d at %d" % (n, self.pos - 4))
        return [fn() for _ in range(n)]


class Import:
    __slots__ = ("class_package", "class_name", "outer", "name", "package_name", "optional")


class Export:
    __slots__ = ("class_index", "super_index", "template_index", "outer", "name", "flags", "serial_size",
                 "serial_offset", "class_name", "is_inherited")


class Package:
    def __init__(self, data, path="", uexp=b""):
        self.path, self.warnings = path, []
        r = Reader(data)
        self._summary(r)
        self.data = data + uexp                       # a cooked split package's export offsets run on into .uexp
        r = Reader(self.data, self.name_offset)
        names = []
        for _ in range(self.name_count):
            names.append(r.fstring())
            r.pos += 4                                # the case-preserving and non-case hashes
        self.names = names
        self.soft_paths = []
        if self.soft_path_count > 0 and self.soft_path_offset > 0:
            r = Reader(self.data, self.soft_path_offset, names)
            self.soft_paths = [self._soft_path(r) for _ in range(self.soft_path_count)]
        r = Reader(self.data, self.import_offset, names)
        self.imports = [self._import(r) for _ in range(self.import_count)]
        r = Reader(self.data, self.export_offset, names)
        self.exports = [self._export(r) for _ in range(self.export_count)]
        for e in self.exports:
            e.class_name = self.object_name(e.class_index) or "Class"

    @classmethod
    def open(cls, path):
        with open(path, "rb") as f:
            data = f.read()
        uexp = os.path.splitext(path)[0] + ".uexp"
        extra = open(uexp, "rb").read() if os.path.isfile(uexp) else b""
        return cls(data, path, extra)

    # ---- the summary

    def _summary(self, r):
        if r.u32() != TAG:
            raise UnrealError("not an Unreal package (no package tag)")
        legacy = r.i32()
        if legacy >= 0 or legacy < -9:
            raise UnrealError("legacy file version %d" % legacy)
        if legacy != -4:
            r.i32()                                   # the UE3 version
        self.ue4 = r.i32()
        self.ue5 = r.i32() if legacy <= -8 else 0
        self.licensee = r.i32()
        if not (self.ue4 or self.ue5):
            raise UnrealError("unversioned package (cooked without versions): not supported")
        if self.ue4 < UE4_ADDED_PACKAGE_SUMMARY_LOCALIZATION_ID:
            raise UnrealError("package version UE4 %d: older than 4.19 (516) is not supported" % self.ue4)
        if self.ue5 > UE5_KNOWN:
            self.warnings.append("package version UE5 %d is newer than the reader knows (%d)" % (self.ue5, UE5_KNOWN))
        if self.ue5 >= UE5_PACKAGE_SAVED_HASH:
            r.raw(20)                                 # saved hash
            self.total_header_size = r.i32()
        self.custom_versions = {}
        for _ in range(r.i32()):
            g = r.guid()
            self.custom_versions[g] = r.i32()
        if self.ue5 < UE5_PACKAGE_SAVED_HASH:
            self.total_header_size = r.i32()
        self.package_name = r.fstring()
        self.flags = r.u32()
        self.editor = not self.flags & PKG_FILTER_EDITOR_ONLY
        self.unversioned_props = bool(self.flags & PKG_UNVERSIONED_PROPERTIES)
        self.name_count, self.name_offset = r.i32(), r.i32()
        self.soft_path_count = self.soft_path_offset = 0
        if self.ue5 >= UE5_ADD_SOFTOBJECTPATH_LIST:
            self.soft_path_count, self.soft_path_offset = r.i32(), r.i32()
        if self.editor:
            self.localization_id = r.fstring()
        r.i32(), r.i32()                              # gatherable text data
        self.export_count, self.export_offset = r.i32(), r.i32()
        self.import_count, self.import_offset = r.i32(), r.i32()
        if self.ue5 >= UE5_VERSE_CELLS:
            r.raw(16)                                 # cell export and import counts and offsets
        if self.ue5 >= UE5_METADATA_SERIALIZATION_OFFSET:
            r.i32()
        r.i32()                                       # depends offset
        r.i32(), r.i32()                              # soft package references
        r.i32()                                       # searchable names
        r.i32()                                       # thumbnail table
        if self.ue5 >= UE5_IMPORT_TYPE_HIERARCHIES:
            r.i32(), r.i32()
        if self.ue5 < UE5_PACKAGE_SAVED_HASH:
            r.guid()                                  # package guid
        if self.editor and (self.ue5 or self.ue4 >= UE4_ADDED_PACKAGE_OWNER):     # both arrived in UE4 518 (4.22)
            r.guid()                                  # persistent guid
            if self.ue4 < UE4_NON_OUTER_PACKAGE_IMPORT:
                r.guid()                              # owner persistent guid (UE4 518-519 only)
        for _ in range(r.i32()):                      # generations
            r.i32(), r.i32()
        self.saved_by = self._engine_version(r)
        self._engine_version(r)                       # compatible with
        if r.u32() != 0 or r.i32() != 0:
            raise UnrealError("package-level compression is not supported")
        r.u32()                                       # package source
        r.array(r.fstring)                            # additional packages to cook
        r.i32()                                       # asset registry data
        self.bulk_data_start = r.i64()
        r.i32()                                       # world tile info
        r.array(r.i32)                                # chunk ids
        r.i32(), r.i32()                              # preload dependencies
        if self.ue5 >= UE5_NAMES_REFERENCED_FROM_EXPORT_DATA:
            r.i32()
        self.payload_toc = r.i64() if self.ue5 >= UE5_PAYLOAD_TOC else -1
        self.data_resource_offset = r.i32() if self.ue5 >= UE5_DATA_RESOURCES else -1

    @staticmethod
    def _engine_version(r):
        v = (r.u16(), r.u16(), r.u16(), r.u32())
        r.fstring()
        return v

    # ---- maps

    def _soft_path(self, r):
        if self.ue5 >= UE5_FSOFTOBJECTPATH_REMOVE_ASSET_PATH_FNAMES:
            pkg, asset = r.name(), r.name()
            path = pkg + ("." + asset if asset != "None" else "")
        else:
            path = r.name()
        sub = r.fstring()
        return path + (":" + sub if sub else "")

    def _import(self, r):
        im = Import()
        im.class_package, im.class_name, im.outer, im.name = r.name(), r.name(), r.i32(), r.name()
        im.package_name = r.name() if self.ue4 >= UE4_NON_OUTER_PACKAGE_IMPORT and self.editor else ""
        im.optional = r.bool32() if self.ue5 >= UE5_OPTIONAL_RESOURCES else False
        return im

    def _export(self, r):
        e = Export()
        e.class_index, e.super_index = r.i32(), r.i32()
        e.template_index = r.i32() if self.ue4 >= UE4_TEMPLATE_INDEX_IN_COOKED_EXPORTS else 0
        e.outer, e.name, e.flags = r.i32(), r.name(), r.u32()
        if self.ue4 >= UE4_BIT64_EXPORTMAP_SERIALSIZES:
            e.serial_size, e.serial_offset = r.i64(), r.i64()
        else:
            e.serial_size, e.serial_offset = r.i32(), r.i32()
        r.bool32(), r.bool32(), r.bool32()            # forced export, not for client, not for server
        if self.ue5 < UE5_REMOVE_OBJECT_EXPORT_PACKAGE_GUID:
            r.guid()
        e.is_inherited = r.bool32() if self.ue5 >= UE5_TRACK_OBJECT_EXPORT_IS_INHERITED else False
        r.u32()                                       # package flags
        r.bool32(), r.bool32()                        # not always loaded for editor game, is asset
        if self.ue5 >= UE5_OPTIONAL_RESOURCES:
            r.bool32()                                # generate public hash
        if self.ue4 >= UE4_PRELOAD_DEPENDENCIES_IN_COOKED_EXPORTS:
            r.raw(20)                                 # first export dependency and four counts
        if not self.unversioned_props and self.ue5 >= UE5_SCRIPT_SERIALIZATION_OFFSET:
            r.i64(), r.i64()                          # script serialization start and end
        return e

    # ---- objects

    def ref(self, index):
        """A package index -> ("import", Import) / ("export", Export) / None."""
        if index < 0 and -index - 1 < len(self.imports):
            return "import", self.imports[-index - 1]
        if index > 0 and index - 1 < len(self.exports):
            return "export", self.exports[index - 1]
        return None

    def object_name(self, index):
        r = self.ref(index)
        return r[1].name if r else ""

    def object_path(self, index):
        """An import's full path (/Game/Pack/Mesh.Mesh) or an export's path inside this package."""
        parts, seen = [], 0
        while index and seen < 64:
            r = self.ref(index)
            if not r:
                break
            parts.append(r[1].name)
            index = r[1].outer
            seen += 1
        parts.reverse()
        if not parts:
            return ""
        return parts[0] + ("." + ".".join(parts[1:]) if len(parts) > 1 else "")

    def export_data(self, i):
        e = self.exports[i]
        return self.data[e.serial_offset:e.serial_offset + e.serial_size]

    def reader(self, i):
        e = self.exports[i]
        return Reader(self.data, e.serial_offset, self.names), e.serial_offset + e.serial_size

    def props(self, i):
        """The export's tagged properties -> (Props, the reader positioned after them and the object guid, end)."""
        from . import properties
        r, end = self.reader(i)
        if self.editor and self.exports[i].class_name in JSON_FIRST:
            r.fstring()                               # UAssetImportData: its source files as JSON, before the tags
        p = properties.read_tagged(self, r, end, top=True)
        if not self.exports[i].flags & RF_CLASS_DEFAULT_OBJECT and r.pos + 4 <= end and r.bool32():
            r.raw(16)                                 # the object's guid
        return p, r, end

    def custom_version(self, guid):
        return self.custom_versions.get(guid, -1)

    def find(self, class_name):
        return [i for i, e in enumerate(self.exports) if e.class_name == class_name]
