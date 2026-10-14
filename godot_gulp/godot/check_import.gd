extends SceneTree
## godot-gulp's import check: loads each .gltf with GLTFDocument and reports tris, surfaces and how many surfaces got
## a base colour texture. godot --headless --path <stub project> -s res://check_import.gd -- <list.json> <out.json>


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var paths: Array = JSON.parse_string(FileAccess.get_file_as_string(args[0]))
	var out: Array = []
	for p in paths:
		var r := {"path": p, "err": -1, "surfaces": 0, "tris": 0, "textured": 0, "notes": ""}
		var st := GLTFState.new()
		var doc := GLTFDocument.new()
		r["err"] = doc.append_from_file(p, st)
		if r["err"] == OK:
			var n := doc.generate_scene(st)
			if n == null:
				r["notes"] = "generate_scene returned null"
			else:
				_walk(n, r)
				n.free()
		out.append(r)
	var f := FileAccess.open(args[1], FileAccess.WRITE)
	f.store_string(JSON.stringify(out))
	f.close()
	quit()


func _walk(n: Node, r: Dictionary) -> void:
	if n is MeshInstance3D and (n as MeshInstance3D).mesh:
		var m := (n as MeshInstance3D).mesh
		r["tris"] += m.get_faces().size() / 3
		for i in m.get_surface_count():
			r["surfaces"] += 1
			var mat := m.surface_get_material(i)
			if mat is BaseMaterial3D and (mat as BaseMaterial3D).albedo_texture != null:
				r["textured"] += 1
	for c in n.get_children():
		_walk(c, r)
