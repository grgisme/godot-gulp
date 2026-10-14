// godot-gulp's FBX/OBJ reader: ufbx (vendored, MIT or public domain) loads the file into glTF's space and this dumps
// the whole scene as JSON in one call, so Python never holds a pointer into ufbx memory.
//   _ufbxdump.dump(path) -> bytes (JSON): nodes (parent index, local and geometry matrices, mesh index), meshes
//   (triangulated parts per material: positions, normals, uv0, colour, vertex index per corner; skin clusters and
//   per-vertex weights), materials (PBR values and texture file names), animations (baked keys per node).
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "_vendor/ufbx/ufbx.h"

typedef struct { char *p; size_t n, cap; int oom; } buf;

static void put(buf *b, const char *s, size_t len) {
    if (b->oom) return;
    if (b->n + len + 1 > b->cap) {
        size_t cap = b->cap ? b->cap * 2 : 1 << 16;
        while (cap < b->n + len + 1) cap *= 2;
        char *q = (char *)realloc(b->p, cap);
        if (!q) { b->oom = 1; return; }
        b->p = q; b->cap = cap;
    }
    memcpy(b->p + b->n, s, len);
    b->n += len;
}
static void puts_(buf *b, const char *s) { put(b, s, strlen(s)); }
static void num(buf *b, double v) { char t[40]; int k = snprintf(t, sizeof t, "%.9g", v); put(b, t, (size_t)k); }
static void inum(buf *b, long long v) { char t[32]; int k = snprintf(t, sizeof t, "%lld", v); put(b, t, (size_t)k); }
static void str(buf *b, ufbx_string s) {
    put(b, "\"", 1);
    for (size_t i = 0; i < s.length; i++) {
        unsigned char c = (unsigned char)s.data[i];
        if (c == '"' || c == '\\') { char t[2] = {'\\', (char)c}; put(b, t, 2); }
        else if (c < 0x20) { char t[8]; int k = snprintf(t, sizeof t, "\\u%04x", c); put(b, t, (size_t)k); }
        else put(b, (const char *)&c, 1);
    }
    put(b, "\"", 1);
}
static void mat(buf *b, ufbx_matrix m) {        // 4x4 row-major, column vectors (the last row 0 0 0 1)
    double r[16] = {m.m00, m.m01, m.m02, m.m03, m.m10, m.m11, m.m12, m.m13, m.m20, m.m21, m.m22, m.m23, 0, 0, 0, 1};
    put(b, "[", 1);
    for (int i = 0; i < 16; i++) { if (i) put(b, ",", 1); num(b, r[i]); }
    put(b, "]", 1);
}
static void tex(buf *b, const char *key, ufbx_texture *t) {
    puts_(b, ",\""); puts_(b, key); puts_(b, "\":");
    if (!t) { puts_(b, "null"); return; }
    puts_(b, "["); str(b, t->absolute_filename); puts_(b, ","); str(b, t->filename); puts_(b, ","); str(b, t->relative_filename); puts_(b, "]");
}
static void mapv(buf *b, const char *key, ufbx_material_map m) {
    puts_(b, ",\""); puts_(b, key); puts_(b, "\":");
    if (!m.has_value) { puts_(b, "null"); return; }
    puts_(b, "["); num(b, m.value_vec4.x); puts_(b, ","); num(b, m.value_vec4.y); puts_(b, ","); num(b, m.value_vec4.z); puts_(b, ","); num(b, m.value_vec4.w); puts_(b, "]");
}

static void dump_mesh(buf *b, ufbx_scene *scene, ufbx_mesh *mesh) {
    uint32_t *tri = (uint32_t *)malloc(sizeof(uint32_t) * (mesh->max_face_triangles * 3 + 3));
    size_t nmat = mesh->materials.count ? mesh->materials.count : 1;
    puts_(b, "{\"parts\":[");
    for (size_t part = 0; part < nmat; part++) {
        if (part) puts_(b, ",");
        puts_(b, "{\"material\":");
        if (mesh->materials.count) inum(b, (long long)mesh->materials.data[part]->typed_id); else puts_(b, "-1");
        // corners of this part's triangles
        buf idx = {0};
        size_t ncorners = 0;
        for (size_t f = 0; f < mesh->faces.count; f++) {
            uint32_t fm = mesh->face_material.count ? mesh->face_material.data[f] : 0;
            if (fm != part) continue;
            uint32_t nt = ufbx_triangulate_face(tri, mesh->max_face_triangles * 3, mesh, mesh->faces.data[f]);
            put(&idx, (const char *)tri, nt * 3 * sizeof(uint32_t));
            ncorners += nt * 3;
        }
        uint32_t *cs = (uint32_t *)idx.p;
        puts_(b, ",\"pos\":[");
        for (size_t k = 0; k < ncorners; k++) { ufbx_vec3 v = ufbx_get_vertex_vec3(&mesh->vertex_position, cs[k]); if (k) put(b, ",", 1); num(b, v.x); put(b, ",", 1); num(b, v.y); put(b, ",", 1); num(b, v.z); }
        puts_(b, "],\"nrm\":[");
        if (mesh->vertex_normal.exists) for (size_t k = 0; k < ncorners; k++) { ufbx_vec3 v = ufbx_get_vertex_vec3(&mesh->vertex_normal, cs[k]); if (k) put(b, ",", 1); num(b, v.x); put(b, ",", 1); num(b, v.y); put(b, ",", 1); num(b, v.z); }
        puts_(b, "],\"uv\":[");
        if (mesh->vertex_uv.exists) for (size_t k = 0; k < ncorners; k++) { ufbx_vec2 v = ufbx_get_vertex_vec2(&mesh->vertex_uv, cs[k]); if (k) put(b, ",", 1); num(b, v.x); put(b, ",", 1); num(b, v.y); }
        puts_(b, "],\"col\":[");
        if (mesh->vertex_color.exists) for (size_t k = 0; k < ncorners; k++) { ufbx_vec4 v = ufbx_get_vertex_vec4(&mesh->vertex_color, cs[k]); if (k) put(b, ",", 1); num(b, v.x); put(b, ",", 1); num(b, v.y); put(b, ",", 1); num(b, v.z); put(b, ",", 1); num(b, v.w); }
        puts_(b, "],\"vert\":[");
        for (size_t k = 0; k < ncorners; k++) { if (k) put(b, ",", 1); inum(b, (long long)mesh->vertex_indices.data[cs[k]]); }
        puts_(b, "]}");
        free(idx.p);
    }
    puts_(b, "],\"skin\":");
    if (mesh->skin_deformers.count) {
        ufbx_skin_deformer *sk = mesh->skin_deformers.data[0];
        puts_(b, "{\"clusters\":[");
        for (size_t c = 0; c < sk->clusters.count; c++) {
            ufbx_skin_cluster *cl = sk->clusters.data[c];
            if (c) puts_(b, ",");
            puts_(b, "{\"bone\":"); inum(b, cl->bone_node ? (long long)cl->bone_node->typed_id : -1);
            puts_(b, ",\"geometry_to_bone\":"); mat(b, cl->geometry_to_bone); puts_(b, "}");
        }
        puts_(b, "],\"weights\":[");
        for (size_t v = 0; v < sk->vertices.count; v++) {
            ufbx_skin_vertex sv = sk->vertices.data[v];
            if (v) puts_(b, ",");
            puts_(b, "[");
            for (uint32_t k = 0; k < sv.num_weights; k++) {
                ufbx_skin_weight w = sk->weights.data[sv.weight_begin + k];
                if (k) puts_(b, ",");
                inum(b, w.cluster_index); puts_(b, ","); num(b, w.weight);
            }
            puts_(b, "]");
        }
        puts_(b, "]}");
    } else puts_(b, "null");
    puts_(b, "}");
    free(tri);
    (void)scene;
}

static PyObject *dump(PyObject *self, PyObject *args) {
    const char *path;
    if (!PyArg_ParseTuple(args, "s", &path)) return NULL;
    ufbx_load_opts opts = {0};
    opts.target_axes = ufbx_axes_right_handed_y_up;
    opts.target_unit_meters = 1.0f;
    opts.space_conversion = UFBX_SPACE_CONVERSION_MODIFY_GEOMETRY;
    opts.generate_missing_normals = true;
    opts.pivot_handling = UFBX_PIVOT_HANDLING_ADJUST_TO_PIVOT;   /* nodes at their pivot, as Unity imports them: world positions unchanged */
    ufbx_error err;
    ufbx_scene *scene;
    Py_BEGIN_ALLOW_THREADS
    scene = ufbx_load_file(path, &opts, &err);
    Py_END_ALLOW_THREADS
    if (!scene) {
        char msg[512];
        ufbx_format_error(msg, sizeof msg, &err);
        PyErr_SetString(PyExc_RuntimeError, msg);
        return NULL;
    }
    buf b = {0};
    puts_(&b, "{\"nodes\":[");
    for (size_t i = 0; i < scene->nodes.count; i++) {
        ufbx_node *n = scene->nodes.data[i];
        if (i) puts_(&b, ",");
        puts_(&b, "{\"name\":"); str(&b, n->name);
        puts_(&b, ",\"parent\":"); inum(&b, n->parent ? (long long)n->parent->typed_id : -1);
        puts_(&b, ",\"root\":"); puts_(&b, n->is_root ? "true" : "false");
        puts_(&b, ",\"local\":"); mat(&b, n->node_to_parent);
        puts_(&b, ",\"geometry\":"); mat(&b, n->geometry_to_node);
        puts_(&b, ",\"mesh\":"); inum(&b, n->mesh ? (long long)n->mesh->typed_id : -1);
        puts_(&b, "}");
    }
    puts_(&b, "],\"meshes\":[");
    for (size_t i = 0; i < scene->meshes.count; i++) { if (i) puts_(&b, ","); dump_mesh(&b, scene, scene->meshes.data[i]); }
    puts_(&b, "],\"materials\":[");
    for (size_t i = 0; i < scene->materials.count; i++) {
        ufbx_material *m = scene->materials.data[i];
        if (i) puts_(&b, ",");
        puts_(&b, "{\"name\":"); str(&b, m->name);
        mapv(&b, "base", m->pbr.base_color); mapv(&b, "base_factor", m->pbr.base_factor);
        mapv(&b, "roughness", m->pbr.roughness); mapv(&b, "metalness", m->pbr.metalness);
        mapv(&b, "emission", m->pbr.emission_color); mapv(&b, "emission_factor", m->pbr.emission_factor);
        mapv(&b, "opacity", m->pbr.opacity);
        tex(&b, "base_tex", m->pbr.base_color.texture); tex(&b, "roughness_tex", m->pbr.roughness.texture);
        tex(&b, "metalness_tex", m->pbr.metalness.texture); tex(&b, "normal_tex", m->pbr.normal_map.texture);
        tex(&b, "emission_tex", m->pbr.emission_color.texture); tex(&b, "opacity_tex", m->pbr.opacity.texture);
        tex(&b, "ao_tex", m->pbr.ambient_occlusion.texture);
        puts_(&b, "}");
    }
    puts_(&b, "],\"anims\":[");
    int first = 1;
    for (size_t i = 0; i < scene->anim_stacks.count; i++) {
        ufbx_anim_stack *st = scene->anim_stacks.data[i];
        ufbx_bake_opts bo = {0};
        ufbx_error berr;
        ufbx_baked_anim *ba = ufbx_bake_anim(scene, st->anim, &bo, &berr);
        if (!ba) continue;
        if (!first) puts_(&b, ",");
        first = 0;
        puts_(&b, "{\"name\":"); str(&b, st->name);
        puts_(&b, ",\"begin\":"); num(&b, ba->playback_time_begin);
        puts_(&b, ",\"nodes\":[");
        for (size_t k = 0; k < ba->nodes.count; k++) {
            ufbx_baked_node *bn = &ba->nodes.data[k];
            if (k) puts_(&b, ",");
            puts_(&b, "{\"node\":"); inum(&b, bn->typed_id);
            puts_(&b, ",\"t\":[");
            for (size_t j = 0; j < bn->translation_keys.count; j++) { ufbx_baked_vec3 kk = bn->translation_keys.data[j]; if (j) puts_(&b, ","); puts_(&b, "["); num(&b, kk.time); puts_(&b, ","); num(&b, kk.value.x); puts_(&b, ","); num(&b, kk.value.y); puts_(&b, ","); num(&b, kk.value.z); puts_(&b, "]"); }
            puts_(&b, "],\"r\":[");
            for (size_t j = 0; j < bn->rotation_keys.count; j++) { ufbx_baked_quat kk = bn->rotation_keys.data[j]; if (j) puts_(&b, ","); puts_(&b, "["); num(&b, kk.time); puts_(&b, ","); num(&b, kk.value.x); puts_(&b, ","); num(&b, kk.value.y); puts_(&b, ","); num(&b, kk.value.z); puts_(&b, ","); num(&b, kk.value.w); puts_(&b, "]"); }
            puts_(&b, "],\"s\":[");
            for (size_t j = 0; j < bn->scale_keys.count; j++) { ufbx_baked_vec3 kk = bn->scale_keys.data[j]; if (j) puts_(&b, ","); puts_(&b, "["); num(&b, kk.time); puts_(&b, ","); num(&b, kk.value.x); puts_(&b, ","); num(&b, kk.value.y); puts_(&b, ","); num(&b, kk.value.z); puts_(&b, "]"); }
            puts_(&b, "]}");
        }
        puts_(&b, "]}");
        ufbx_free_baked_anim(ba);
    }
    puts_(&b, "]}");
    ufbx_free_scene(scene);
    if (b.oom) { free(b.p); return PyErr_NoMemory(); }
    PyObject *out = PyBytes_FromStringAndSize(b.p, (Py_ssize_t)b.n);
    free(b.p);
    return out;
}

static PyMethodDef methods[] = {{"dump", dump, METH_VARARGS, "dump(path) -> JSON bytes of the scene (see the source)"}, {NULL, NULL, 0, NULL}};
static struct PyModuleDef mod = {PyModuleDef_HEAD_INIT, "_ufbxdump", "ufbx scene dump for godot-gulp", -1, methods};
PyMODINIT_FUNC PyInit__ufbxdump(void) { return PyModule_Create(&mod); }
