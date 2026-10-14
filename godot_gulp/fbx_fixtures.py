"""Synthetic FBX for the tests and the conformance run, written as ASCII FBX 7.4 (no exporter, no third-party file):

    rigged(dir) -> path      a 1 m quad (Y up, centimetres in the file) under a Root node, its material "Red" with a
                             base-colour texture beside it, skinned to one bone "Bone", and a take "Wave" moving the
                             bone 1 m along +X over one second
"""
import os

from PIL import Image

SECOND = 46186158000          # FBX time units per second

IDENT = "1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1"

RIGGED = """; FBX 7.4.0 project file
FBXHeaderExtension:  {
	FBXHeaderVersion: 1003
	FBXVersion: 7400
}
GlobalSettings:  {
	Version: 1000
	Properties70:  {
		P: "UpAxis", "int", "Integer", "",1
		P: "UpAxisSign", "int", "Integer", "",1
		P: "FrontAxis", "int", "Integer", "",2
		P: "FrontAxisSign", "int", "Integer", "",1
		P: "CoordAxis", "int", "Integer", "",0
		P: "CoordAxisSign", "int", "Integer", "",1
		P: "UnitScaleFactor", "double", "Number", "",1
	}
}
Objects:  {
	Geometry: 1000, "Geometry::Quad", "Mesh" {
		Vertices: *12 {
			a: 0,0,0,100,0,0,100,100,0,0,100,0
		}
		PolygonVertexIndex: *4 {
			a: 0,1,2,-4
		}
		GeometryVersion: 124
		LayerElementUV: 0 {
			Version: 101
			Name: "UVMap"
			MappingInformationType: "ByPolygonVertex"
			ReferenceInformationType: "IndexToDirect"
			UV: *8 {
				a: 0,0,1,0,1,1,0,1
			}
			UVIndex: *4 {
				a: 0,1,2,3
			}
		}
		LayerElementMaterial: 0 {
			Version: 101
			Name: ""
			MappingInformationType: "AllSame"
			ReferenceInformationType: "IndexToDirect"
			Materials: *1 {
				a: 0
			}
		}
		Layer: 0 {
			Version: 100
			LayerElement:  {
				Type: "LayerElementUV"
				TypedIndex: 0
			}
			LayerElement:  {
				Type: "LayerElementMaterial"
				TypedIndex: 0
			}
		}
	}
	Model: 2000, "Model::Root", "Null" {
		Version: 232
	}
	Model: 2100, "Model::Quad", "Mesh" {
		Version: 232
	}
	Model: 2200, "Model::Bone", "LimbNode" {
		Version: 232
	}
	NodeAttribute: 2300, "NodeAttribute::Bone", "LimbNode" {
		TypeFlags: "Skeleton"
	}
	Material: 3000, "Material::Red", "" {
		Version: 102
		ShadingModel: "phong"
		Properties70:  {
			P: "DiffuseColor", "Color", "", "A",1,0,0
		}
	}
	Texture: 3100, "Texture::red", "" {
		Type: "TextureVideoClip"
		Version: 202
		TextureName: "Texture::red"
		FileName: "C:/elsewhere/red.png"
		RelativeFilename: "red.png"
	}
	Video: 3200, "Video::red", "Clip" {
		Type: "Clip"
		FileName: "C:/elsewhere/red.png"
		RelativeFilename: "red.png"
	}
	Deformer: 4000, "Deformer::Skin", "Skin" {
		Version: 101
		Link_DeformAcuracy: 50
	}
	Deformer: 4100, "SubDeformer::Bone", "Cluster" {
		Version: 100
		Indexes: *4 {
			a: 0,1,2,3
		}
		Weights: *4 {
			a: 1,1,1,1
		}
		Transform: *16 {
			a: %(ident)s
		}
		TransformLink: *16 {
			a: %(ident)s
		}
	}
	AnimationStack: 5000, "AnimStack::Wave", "" {
		Properties70:  {
			P: "LocalStart", "KTime", "Time", "",0
			P: "LocalStop", "KTime", "Time", "",%(sec)d
		}
	}
	AnimationLayer: 5100, "AnimLayer::Base", "" {
	}
	AnimationCurveNode: 5200, "AnimCurveNode::T", "" {
		Properties70:  {
			P: "d|X", "Number", "", "A",0
			P: "d|Y", "Number", "", "A",0
			P: "d|Z", "Number", "", "A",0
		}
	}
	AnimationCurve: 5300, "AnimCurve::", "" {
		Default: 0
		KeyVer: 4009
		KeyTime: *2 {
			a: 0,%(sec)d
		}
		KeyValueFloat: *2 {
			a: 0,100
		}
		KeyAttrFlags: *1 {
			a: 264
		}
		KeyAttrDataFloat: *4 {
			a: 0,0,0,0
		}
		KeyAttrRefCount: *1 {
			a: 2
		}
	}
}
Connections:  {
	C: "OO",2000,0
	C: "OO",2100,2000
	C: "OO",2200,2000
	C: "OO",2300,2200
	C: "OO",1000,2100
	C: "OO",3000,2100
	C: "OP",3100,3000, "DiffuseColor"
	C: "OO",3200,3100
	C: "OO",4000,1000
	C: "OO",4100,4000
	C: "OO",2200,4100
	C: "OO",5100,5000
	C: "OO",5200,5100
	C: "OP",5200,2200, "Lcl Translation"
	C: "OP",5300,5200, "d|X"
}
"""


def rigged(d):
    os.makedirs(d, exist_ok=True)
    Image.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(d, "red.png"))
    p = os.path.join(d, "rigged.fbx")
    with open(p, "w") as f:
        f.write(RIGGED % {"ident": IDENT, "sec": SECOND})
    return p


TWO_NODES = """; FBX 7.4.0 project file
FBXHeaderExtension:  {
	FBXHeaderVersion: 1003
	FBXVersion: 7400
}
GlobalSettings:  {
	Version: 1000
	Properties70:  {
		P: "UpAxis", "int", "Integer", "",1
		P: "UpAxisSign", "int", "Integer", "",1
		P: "FrontAxis", "int", "Integer", "",2
		P: "FrontAxisSign", "int", "Integer", "",1
		P: "CoordAxis", "int", "Integer", "",0
		P: "CoordAxisSign", "int", "Integer", "",1
		P: "UnitScaleFactor", "double", "Number", "",1
	}
}
Objects:  {
	Geometry: 1000, "Geometry::QuadA", "Mesh" {
		Vertices: *12 {
			a: 0,0,0,100,0,0,100,100,0,0,100,0
		}
		PolygonVertexIndex: *4 {
			a: 0,1,2,-4
		}
		GeometryVersion: 124
	}
	Geometry: 1100, "Geometry::QuadB", "Mesh" {
		Vertices: *12 {
			a: 0,0,0,50,0,0,50,50,0,0,50,0
		}
		PolygonVertexIndex: *4 {
			a: 0,1,2,-4
		}
		GeometryVersion: 124
	}
	Geometry: 1200, "Geometry::QuadC", "Mesh" {
		Vertices: *12 {
			a: 100,0,0,150,0,0,150,50,0,100,50,0
		}
		PolygonVertexIndex: *4 {
			a: 0,1,2,-4
		}
		GeometryVersion: 124
	}
	Model: 2000, "Model::A", "Mesh" {
		Version: 232
	}
	Model: 2200, "Model::C", "Mesh" {
		Version: 232
		Properties70:  {
			P: "RotationPivot", "Vector3D", "Vector", "",100,0,0
			P: "ScalingPivot", "Vector3D", "Vector", "",100,0,0
		}
	}
	Model: 2100, "Model::B", "Mesh" {
		Version: 232
		Properties70:  {
			P: "Lcl Translation", "Lcl Translation", "", "A",300,0,0
		}
	}
}
Connections:  {
	C: "OO",2000,0
	C: "OO",2100,0
	C: "OO",1000,2000
	C: "OO",1100,2100
	C: "OO",2200,0
	C: "OO",1200,2200
}
"""


def two_nodes(d):
    """-> path of an FBX with mesh nodes "A", a 1 m quad at the origin; "B", a 0.5 m quad on a node moved 3 m along +X;
    and "C", a 0.5 m quad from x 1 m whose node keeps its pivot there (3ds Max style), centimetres in the file."""
    p = os.path.join(d, "Two.fbx")
    with open(p, "w") as fh:
        fh.write(TWO_NODES)
    return p
