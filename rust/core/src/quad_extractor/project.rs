use super::QuadExtractor;
use super::geom::{add_scaled_vec3, add_two_scaled_vec3, fma_first, fma_first_sub};
use crate::iso_remesh_kernel::{AxisAlignedBoundingBox, AxisAlignedBoundingBoxTree};
use crate::par::parallel_each;
use crate::vector2::Vector2;
use crate::vector3::Vector3;
use std::collections::{BTreeMap, BTreeSet};

impl<'a> QuadExtractor<'a> {
    pub(crate) fn smooth_and_project(
        &mut self,
        iterations: usize,
        // Membership-only use (`contains`, `is_empty`).
        movable_vertices: Option<&BTreeSet<usize>>,
    ) {
        if 0 == iterations
            || self.remeshed_vertices.is_empty()
            || self.remeshed_polygons.is_empty()
            || self.triangles.is_empty()
        {
            return;
        }

        let mut neighbors: Vec<BTreeSet<usize>> =
            vec![BTreeSet::new(); self.remeshed_vertices.len()];
        let mut edge_use_count: BTreeMap<(usize, usize), usize> = BTreeMap::new();
        for face in &self.remeshed_polygons {
            for i in 0..face.len() {
                let j = (i + 1) % face.len();
                if face[i] >= neighbors.len() || face[j] >= neighbors.len() {
                    continue;
                }
                neighbors[face[i]].insert(face[j]);
                neighbors[face[j]].insert(face[i]);
                *edge_use_count
                    .entry((face[i].min(face[j]), face[i].max(face[j])))
                    .or_default() += 1;
            }
        }
        let mut locked = vec![false; self.remeshed_vertices.len()];
        if let Some(movable) = movable_vertices {
            for (index, lock) in locked.iter_mut().enumerate() {
                *lock = !movable.contains(&index);
            }
        }
        for (edge, use_count) in &edge_use_count {
            if 1 == *use_count {
                locked[edge.0] = true;
                locked[edge.1] = true;
            }
        }

        let mut triangle_boxes = vec![AxisAlignedBoundingBox::default(); self.triangles.len()];
        let mut triangle_indices = vec![0; self.triangles.len()];
        let mut group_box = AxisAlignedBoundingBox::default();
        for (i, triangle) in self.triangles.iter().enumerate() {
            for k in 0..3 {
                triangle_boxes[i].update(&self.vertices[triangle[k]]);
                group_box.update(&self.vertices[triangle[k]]);
            }
            triangle_boxes[i].update_center();
            triangle_indices[i] = i;
        }
        group_box.update_center();
        let tree = AxisAlignedBoundingBoxTree::new(triangle_boxes, triangle_indices, group_box);
        let target_normals = if self.v2_repairs {
            compute_triangle_normals(self.vertices, self.triangles)
        } else {
            Vec::new()
        };

        // Average quad edge length drives the initial search radius
        let mut total_edge_length = 0.0;
        let mut edge_num = 0;
        for edge in edge_use_count.keys() {
            total_edge_length +=
                (self.remeshed_vertices[edge.0] - self.remeshed_vertices[edge.1]).length();
            edge_num += 1;
        }
        if 0 == edge_num {
            return;
        }
        let average_edge_length = total_edge_length / edge_num as f64;
        if average_edge_length <= 0.0 {
            return;
        }

        // Both passes below already read one buffer and write another, and
        // the projection only reads the bounding box tree, so each vertex
        // is independent and the parallel result is the same as the serial
        // one. (Restructure: `parallel_each` for `tbb::parallel_for`.)
        const SMOOTH_FACTOR: f64 = 0.5;
        for _ in 0..iterations {
            let (vertex_normals, local_edge_lengths) = if self.v2_repairs {
                let (normals, edges, _) =
                    compute_polygon_geometry(&self.remeshed_vertices, &self.remeshed_polygons);
                (normals, edges)
            } else {
                (Vec::new(), Vec::new())
            };
            let mut smoothed_vertices = self.remeshed_vertices.clone();
            parallel_each(&mut smoothed_vertices, |i, smoothed| {
                if locked[i] || neighbors[i].is_empty() {
                    return;
                }
                let mut center = Vector3::default();
                for neighbor in &neighbors[i] {
                    center += self.remeshed_vertices[*neighbor];
                }
                center /= neighbors[i].len() as f64;
                // Unfused `v + smoothFactor * (center - v)` (see module FMA note).
                *smoothed = add_scaled_vec3(
                    self.remeshed_vertices[i],
                    center - self.remeshed_vertices[i],
                    SMOOTH_FACTOR,
                );
            });
            parallel_each(&mut smoothed_vertices, |i, smoothed| {
                if locked[i] || neighbors[i].is_empty() {
                    return;
                }
                if self.v2_repairs {
                    let local_len = if local_edge_lengths[i] > 0.0 {
                        local_edge_lengths[i]
                    } else {
                        average_edge_length
                    };
                    let max_radius = (local_len * 3.0).max(average_edge_length * 3.0);
                    if let Some(projected) = Self::project_to_target_mesh_guarded(
                        self.vertices,
                        self.triangles,
                        &target_normals,
                        &tree,
                        average_edge_length.min(max_radius),
                        max_radius,
                        *smoothed,
                        vertex_normals[i],
                        0.0,
                    ) {
                        *smoothed = projected;
                    } else {
                        // Never leave a vertex pulled inward off the surface if no
                        // normal-aligned target triangle matched within range.
                        *smoothed = self.remeshed_vertices[i];
                    }
                } else if let Some(projected) = Self::project_to_target_mesh(
                    self.vertices,
                    self.triangles,
                    &tree,
                    average_edge_length,
                    *smoothed,
                ) {
                    *smoothed = projected;
                }
            });
            self.remeshed_vertices = smoothed_vertices;
        }
    }

    fn project_to_target_mesh(
        vertices: &[Vector3],
        triangles: &[Vec<usize>],
        tree: &AxisAlignedBoundingBoxTree,
        average_edge_length: f64,
        position: Vector3,
    ) -> Option<Vector3> {
        let mut radius = average_edge_length;
        while radius <= average_edge_length * 8.0 {
            let mut query_boxes = vec![AxisAlignedBoundingBox::default()];
            query_boxes[0].update(&Vector3::new(
                position.x() - radius,
                position.y() - radius,
                position.z() - radius,
            ));
            query_boxes[0].update(&Vector3::new(
                position.x() + radius,
                position.y() + radius,
                position.z() + radius,
            ));
            query_boxes[0].update_center();
            let outer = query_boxes[0].clone();
            let query_tree = AxisAlignedBoundingBoxTree::new(query_boxes, vec![0], outer);
            let mut pairs = Vec::new();
            tree.test(tree.root(), &query_tree, query_tree.root(), &mut pairs);
            let mut min_distance2 = f64::MAX;
            let mut projected = None;
            for (key, _) in &pairs {
                let triangle = &triangles[*key];
                let candidate = closest_point_on_triangle(
                    position,
                    vertices[triangle[0]],
                    vertices[triangle[1]],
                    vertices[triangle[2]],
                );
                let distance2 = (candidate - position).length_squared();
                if distance2 < min_distance2 {
                    min_distance2 = distance2;
                    projected = Some(candidate);
                }
            }
            if min_distance2 < f64::MAX {
                return projected;
            }
            radius *= 2.0;
        }
        None
    }

    #[allow(clippy::too_many_arguments)]
    fn project_to_target_mesh_guarded(
        vertices: &[Vector3],
        triangles: &[Vec<usize>],
        target_normals: &[Vector3],
        tree: &AxisAlignedBoundingBoxTree,
        initial_radius: f64,
        max_radius: f64,
        position: Vector3,
        expected_normal: Vector3,
        min_normal_dot: f64,
    ) -> Option<Vector3> {
        project_point_normal_guarded(
            vertices,
            triangles,
            target_normals,
            tree,
            initial_radius,
            max_radius,
            position,
            expected_normal,
            min_normal_dot,
        )
    }

    pub(crate) fn compute_remeshed_vertex_uvs(&mut self) {
        self.remeshed_vertex_uvs.clear();
        if self.remeshed_vertices.is_empty()
            || self.triangles.is_empty()
            || self.triangle_uvs.len() != self.triangles.len()
        {
            return;
        }

        // Bounding-box tree over the source triangles, same construction as
        // smoothAndProject: output vertices lie on the source surface, so
        // an expanding-radius query finds the home triangle in a few
        // probes.
        let mut triangle_boxes = vec![AxisAlignedBoundingBox::default(); self.triangles.len()];
        let mut triangle_indices = vec![0; self.triangles.len()];
        let mut group_box = AxisAlignedBoundingBox::default();
        for (i, triangle) in self.triangles.iter().enumerate() {
            for k in 0..3 {
                triangle_boxes[i].update(&self.vertices[triangle[k]]);
                group_box.update(&self.vertices[triangle[k]]);
            }
            triangle_boxes[i].update_center();
            triangle_indices[i] = i;
        }
        group_box.update_center();
        let tree = AxisAlignedBoundingBoxTree::new(triangle_boxes, triangle_indices, group_box);

        let mut total_edge_length = 0.0;
        let mut edge_num = 0;
        for face in &self.remeshed_polygons {
            for i in 0..face.len() {
                let j = (i + 1) % face.len();
                if face[i] >= self.remeshed_vertices.len()
                    || face[j] >= self.remeshed_vertices.len()
                {
                    continue;
                }
                total_edge_length +=
                    (self.remeshed_vertices[face[i]] - self.remeshed_vertices[face[j]]).length();
                edge_num += 1;
            }
        }
        if 0 == edge_num {
            return;
        }
        let average_edge_length = total_edge_length / edge_num as f64;
        if average_edge_length <= 0.0 {
            return;
        }

        // Each vertex is independent (reads the tree and the source mesh,
        // writes its own slot), so the parallel result matches the serial
        // one exactly. (Restructure: `parallel_each` for `tbb::parallel_for`.)
        let mut uvs = vec![Vector2::default(); self.remeshed_vertices.len()];
        parallel_each(&mut uvs, |i, uv| {
            let mut projected = self.remeshed_vertices[i];
            let home = Self::find_home_triangle(
                self.vertices,
                self.triangles,
                &tree,
                average_edge_length,
                self.remeshed_vertices[i],
                &mut projected,
            );
            let triangle = &self.triangles[home];
            let corner_uvs = &self.triangle_uvs[home];
            if corner_uvs.len() < 3 {
                *uv = if corner_uvs.is_empty() {
                    Vector2::default()
                } else {
                    corner_uvs[0]
                };
                return;
            }
            let area = Vector3::area(
                &self.vertices[triangle[0]],
                &self.vertices[triangle[1]],
                &self.vertices[triangle[2]],
            );
            // `!(area > ...)` like the C++ (NaN takes this arm on both sides).
            #[allow(clippy::neg_cmp_op_on_partial_ord)]
            if !(area > 1e-18) {
                *uv = Vector2::new(
                    (corner_uvs[0].x() + corner_uvs[1].x() + corner_uvs[2].x()) / 3.0,
                    (corner_uvs[0].y() + corner_uvs[1].y() + corner_uvs[2].y()) / 3.0,
                );
                return;
            }
            let bary = Vector3::barycentric_coordinates(
                &self.vertices[triangle[0]],
                &self.vertices[triangle[1]],
                &self.vertices[triangle[2]],
                &projected,
            );
            // FMA: three left-nested products fuse outside-in, `fma(bz,
            // uz, fma(bx, ux, by * uy))` per the probe (same shape as the
            // dot-product chain).
            *uv = Vector2::new(
                bary.z().mul_add(
                    corner_uvs[2].x(),
                    fma_first(bary.x(), corner_uvs[0].x(), bary.y(), corner_uvs[1].x()),
                ),
                bary.z().mul_add(
                    corner_uvs[2].y(),
                    fma_first(bary.x(), corner_uvs[0].y(), bary.y(), corner_uvs[1].y()),
                ),
            );
        });

        // Normalize to 0..1 over this island's UV bounding box. Non-finite
        // interpolants (a degenerate parameterization corner) collapse to
        // the box center so the accessor never emits NaN or infinity.
        let mut min_u = 0.0;
        let mut max_u = 0.0;
        let mut min_v = 0.0;
        let mut max_v = 0.0;
        let mut have_finite = false;
        for uv in &uvs {
            if !uv.x().is_finite() || !uv.y().is_finite() {
                continue;
            }
            if !have_finite {
                min_u = uv.x();
                max_u = uv.x();
                min_v = uv.y();
                max_v = uv.y();
                have_finite = true;
            } else {
                min_u = min_u.min(uv.x());
                max_u = max_u.max(uv.x());
                min_v = min_v.min(uv.y());
                max_v = max_v.max(uv.y());
            }
        }
        if !have_finite {
            uvs = vec![Vector2::new(0.5, 0.5); uvs.len()];
            self.remeshed_vertex_uvs = uvs;
            return;
        }
        let range_u = max_u - min_u;
        let range_v = max_v - min_v;
        let center_u = (min_u + max_u) * 0.5;
        let center_v = (min_v + max_v) * 0.5;
        for uv in &mut uvs {
            let mut u = if uv.x().is_finite() { uv.x() } else { center_u };
            let mut v = if uv.y().is_finite() { uv.y() } else { center_v };
            u = if range_u > 1e-12 {
                (u - min_u) / range_u
            } else {
                0.5
            };
            v = if range_v > 1e-12 {
                (v - min_v) / range_v
            } else {
                0.5
            };
            *uv = Vector2::new(u, v);
        }
        self.remeshed_vertex_uvs = uvs;
    }

    #[allow(clippy::too_many_arguments)]
    fn find_home_triangle(
        vertices: &[Vector3],
        triangles: &[Vec<usize>],
        tree: &AxisAlignedBoundingBoxTree,
        average_edge_length: f64,
        position: Vector3,
        projected: &mut Vector3,
    ) -> usize {
        let mut home = 0;
        let mut radius = average_edge_length;
        while radius <= average_edge_length * 8.0 {
            let mut query_boxes = vec![AxisAlignedBoundingBox::default()];
            query_boxes[0].update(&Vector3::new(
                position.x() - radius,
                position.y() - radius,
                position.z() - radius,
            ));
            query_boxes[0].update(&Vector3::new(
                position.x() + radius,
                position.y() + radius,
                position.z() + radius,
            ));
            query_boxes[0].update_center();
            let outer = query_boxes[0].clone();
            let query_tree = AxisAlignedBoundingBoxTree::new(query_boxes, vec![0], outer);
            let mut pairs = Vec::new();
            tree.test(tree.root(), &query_tree, query_tree.root(), &mut pairs);
            let mut min_distance2 = f64::MAX;
            for (key, _) in &pairs {
                let triangle = &triangles[*key];
                let candidate = closest_point_on_triangle(
                    position,
                    vertices[triangle[0]],
                    vertices[triangle[1]],
                    vertices[triangle[2]],
                );
                let distance2 = (candidate - position).length_squared();
                if distance2 < min_distance2 {
                    min_distance2 = distance2;
                    *projected = candidate;
                    home = *key;
                }
            }
            if min_distance2 < f64::MAX {
                return home;
            }
            radius *= 2.0;
        }
        // Straggler (smoothing pushed it past the search radius): fall back
        // to a full scan so every vertex still gets a home triangle.
        let mut min_distance2 = f64::MAX;
        for (key, triangle) in triangles.iter().enumerate() {
            let candidate = closest_point_on_triangle(
                position,
                vertices[triangle[0]],
                vertices[triangle[1]],
                vertices[triangle[2]],
            );
            let distance2 = (candidate - position).length_squared();
            if distance2 < min_distance2 {
                min_distance2 = distance2;
                *projected = candidate;
                home = key;
            }
        }
        home
    }
}

/// Closest point on a triangle (mirrors the anonymous-namespace
/// `closestPointOnTriangle`; FMA per the module note).
fn closest_point_on_triangle(p: Vector3, a: Vector3, b: Vector3, c: Vector3) -> Vector3 {
    let ab = b - a;
    let ac = c - a;
    let ap = p - a;
    let d1 = Vector3::dot_product(&ab, &ap);
    let d2 = Vector3::dot_product(&ac, &ap);
    if d1 <= 0.0 && d2 <= 0.0 {
        return a;
    }

    let bp = p - b;
    let d3 = Vector3::dot_product(&ab, &bp);
    let d4 = Vector3::dot_product(&ac, &bp);
    if d3 >= 0.0 && d4 <= d3 {
        return b;
    }

    let vc = fma_first_sub(d1, d4, d3, d2);
    if vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0 {
        let denom = d1 - d3;
        let v = if 0.0 != denom { d1 / denom } else { 0.0 };
        return add_scaled_vec3(a, ab, v);
    }

    let cp = p - c;
    let d5 = Vector3::dot_product(&ab, &cp);
    let d6 = Vector3::dot_product(&ac, &cp);
    if d6 >= 0.0 && d5 <= d6 {
        return c;
    }

    let vb = fma_first_sub(d5, d2, d1, d6);
    if vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0 {
        let denom = d2 - d6;
        let w = if 0.0 != denom { d2 / denom } else { 0.0 };
        return add_scaled_vec3(a, ac, w);
    }

    let va = fma_first_sub(d3, d6, d5, d4);
    if va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0 {
        let denom = (d4 - d3) + (d5 - d6);
        let w = if 0.0 != denom { (d4 - d3) / denom } else { 0.0 };
        return add_scaled_vec3(b, c - b, w);
    }

    let denom = va + vb + vc;
    if 0.0 == denom {
        return a;
    }
    let v = vb / denom;
    let w = vc / denom;
    add_two_scaled_vec3(a, ab, v, ac, w)
}

fn vec3_is_finite(v: &Vector3) -> bool {
    v.x().is_finite() && v.y().is_finite() && v.z().is_finite()
}

/// Unit triangle normals for `triangles` indexed into `vertices`. Degenerate
/// or out-of-range triangles receive a zero vector so callers treat them safely.
fn compute_triangle_normals(vertices: &[Vector3], triangles: &[Vec<usize>]) -> Vec<Vector3> {
    let mut normals = vec![Vector3::default(); triangles.len()];
    for (i, tri) in triangles.iter().enumerate() {
        if tri.len() < 3 {
            continue;
        }
        let (a, b, c) = (tri[0], tri[1], tri[2]);
        if a >= vertices.len() || b >= vertices.len() || c >= vertices.len() {
            continue;
        }
        let va = vertices[a];
        let vb = vertices[b];
        let vc = vertices[c];
        if !vec3_is_finite(&va) || !vec3_is_finite(&vb) || !vec3_is_finite(&vc) {
            continue;
        }
        let n = Vector3::cross_product(&(vb - va), &(vc - va));
        if !n.is_zero() && vec3_is_finite(&n) {
            normals[i] = n.normalized();
        }
    }
    normals
}

/// Computes unit area-weighted vertex normals, per-vertex mean incident edge
/// lengths, and the global mean edge length across `polygons`.
fn compute_polygon_geometry(
    vertices: &[Vector3],
    polygons: &[Vec<usize>],
) -> (Vec<Vector3>, Vec<f64>, f64) {
    let n_verts = vertices.len();
    let mut normals = vec![Vector3::default(); n_verts];
    let mut edge_sum = vec![0.0; n_verts];
    let mut edge_count = vec![0usize; n_verts];
    let mut global_edge_sum = 0.0;
    let mut global_edge_count = 0usize;

    for face in polygons {
        if face.len() < 3 {
            continue;
        }
        let mut poly_area_normal = Vector3::default();
        let v0 = face[0];
        if v0 < n_verts && vec3_is_finite(&vertices[v0]) {
            for k in 1..face.len() - 1 {
                let v1 = face[k];
                let v2 = face[k + 1];
                if v1 >= n_verts || v2 >= n_verts {
                    continue;
                }
                if !vec3_is_finite(&vertices[v1]) || !vec3_is_finite(&vertices[v2]) {
                    continue;
                }
                poly_area_normal += Vector3::cross_product(
                    &(vertices[v1] - vertices[v0]),
                    &(vertices[v2] - vertices[v0]),
                );
            }
        }
        if !poly_area_normal.is_zero() && vec3_is_finite(&poly_area_normal) {
            for &vi in face {
                if vi < n_verts {
                    normals[vi] += poly_area_normal;
                }
            }
        }

        for i in 0..face.len() {
            let a = face[i];
            let b = face[(i + 1) % face.len()];
            if a >= n_verts || b >= n_verts {
                continue;
            }
            if !vec3_is_finite(&vertices[a]) || !vec3_is_finite(&vertices[b]) {
                continue;
            }
            let len = (vertices[a] - vertices[b]).length();
            if len.is_finite() && len > 0.0 {
                edge_sum[a] += len;
                edge_count[a] += 1;
                edge_sum[b] += len;
                edge_count[b] += 1;
                global_edge_sum += len;
                global_edge_count += 1;
            }
        }
    }

    for n in &mut normals {
        if !n.is_zero() && vec3_is_finite(n) {
            *n = n.normalized();
        } else {
            *n = Vector3::default();
        }
    }

    let global_avg = if global_edge_count > 0 {
        global_edge_sum / global_edge_count as f64
    } else {
        0.0
    };
    let mut local_edges = vec![global_avg; n_verts];
    for i in 0..n_verts {
        if edge_count[i] > 0 {
            local_edges[i] = edge_sum[i] / edge_count[i] as f64;
        }
    }
    (normals, local_edges, global_avg)
}

#[allow(clippy::too_many_arguments)]
fn project_point_normal_guarded(
    vertices: &[Vector3],
    triangles: &[Vec<usize>],
    target_normals: &[Vector3],
    tree: &AxisAlignedBoundingBoxTree,
    initial_radius: f64,
    max_radius: f64,
    position: Vector3,
    expected_normal: Vector3,
    min_normal_dot: f64,
) -> Option<Vector3> {
    if !initial_radius.is_finite()
        || initial_radius <= 0.0
        || !max_radius.is_finite()
        || max_radius <= 0.0
        || !vec3_is_finite(&position)
    {
        return None;
    }
    let check_normal = !expected_normal.is_zero() && vec3_is_finite(&expected_normal);
    let max_dist2 = max_radius * max_radius;
    let mut radius = initial_radius.min(max_radius);
    loop {
        let mut query_boxes = vec![AxisAlignedBoundingBox::default()];
        query_boxes[0].update(&Vector3::new(
            position.x() - radius,
            position.y() - radius,
            position.z() - radius,
        ));
        query_boxes[0].update(&Vector3::new(
            position.x() + radius,
            position.y() + radius,
            position.z() + radius,
        ));
        query_boxes[0].update_center();
        let outer = query_boxes[0].clone();
        let query_tree = AxisAlignedBoundingBoxTree::new(query_boxes, vec![0], outer);
        let mut pairs = Vec::new();
        tree.test(tree.root(), &query_tree, query_tree.root(), &mut pairs);

        let mut min_distance2 = f64::MAX;
        let mut projected = None;
        for (key, _) in &pairs {
            let triangle = &triangles[*key];
            if triangle.len() < 3 {
                continue;
            }
            let (a, b, c) = (triangle[0], triangle[1], triangle[2]);
            if a >= vertices.len() || b >= vertices.len() || c >= vertices.len() {
                continue;
            }
            let tri_normal = target_normals[*key];
            if tri_normal.is_zero() {
                continue;
            }
            if check_normal && Vector3::dot_product(&expected_normal, &tri_normal) < min_normal_dot
            {
                continue;
            }
            let candidate =
                closest_point_on_triangle(position, vertices[a], vertices[b], vertices[c]);
            if !vec3_is_finite(&candidate) {
                continue;
            }
            let distance2 = (candidate - position).length_squared();
            if distance2 <= max_dist2 && distance2 < min_distance2 {
                min_distance2 = distance2;
                projected = Some(candidate);
            }
        }
        if projected.is_some() {
            return projected;
        }
        if radius >= max_radius {
            break;
        }
        radius = (radius * 2.0).min(max_radius);
    }
    None
}

/// Checks that moving `vertex` from its current position to `candidate` keeps
/// every incident polygon corner oriented consistently and non-degenerate.
fn anti_flip_move_ok(
    vertices: &[Vector3],
    polygons: &[Vec<usize>],
    incident_faces: &[usize],
    vertex: usize,
    candidate: Vector3,
) -> bool {
    for &fi in incident_faces {
        let face = &polygons[fi];
        let n = face.len();
        if n < 3 {
            continue;
        }
        let Some(pos_in_face) = face.iter().position(|&v| v == vertex) else {
            continue;
        };
        let prev = face[(pos_in_face + n - 1) % n];
        let next = face[(pos_in_face + 1) % n];
        if prev >= vertices.len() || next >= vertices.len() {
            continue;
        }
        let p_cur = vertices[vertex];
        let p_prev = vertices[prev];
        let p_next = vertices[next];
        let old_corner = Vector3::cross_product(&(p_prev - p_cur), &(p_next - p_cur));
        let old_len2 = old_corner.length_squared();
        if !old_len2.is_finite() || old_len2 <= 1e-24 {
            continue;
        }
        let new_corner = Vector3::cross_product(&(p_prev - candidate), &(p_next - candidate));
        let new_len2 = new_corner.length_squared();
        if !vec3_is_finite(&new_corner) || !new_len2.is_finite() || new_len2 <= 0.02 * old_len2 {
            return false;
        }
        let dot = Vector3::dot_product(&old_corner.normalized(), &new_corner.normalized());
        if dot < 0.05 {
            return false;
        }

        // For quads, also preserve positive alignment between the two triangles
        // if the quad was previously untwisted.
        if n == 4 {
            let opp = face[(pos_in_face + 2) % 4];
            if opp < vertices.len() {
                let p_opp = vertices[opp];
                let old_diag = Vector3::cross_product(&(p_next - p_opp), &(p_prev - p_opp));
                let new_diag = old_diag;
                if !old_diag.is_zero()
                    && Vector3::dot_product(&old_corner.normalized(), &old_diag.normalized()) > 0.05
                    && Vector3::dot_product(&new_corner.normalized(), &new_diag.normalized()) <= 0.0
                {
                    return false;
                }
            }
        }
    }
    true
}

/// Projects extracted vertices onto a higher-resolution target triangle surface
/// (`target_vertices`, `target_triangles`), guarded by normal dot alignment,
/// local edge-length snap radius, and deterministic incident-face anti-flip.
pub(crate) fn project_mesh_onto_surface(
    remeshed_vertices: &mut [Vector3],
    remeshed_polygons: &[Vec<usize>],
    target_vertices: &[Vector3],
    target_triangles: &[Vec<usize>],
    max_snap_factor: f64,
    min_normal_dot: f64,
) {
    if remeshed_vertices.is_empty()
        || remeshed_polygons.is_empty()
        || target_vertices.is_empty()
        || target_triangles.is_empty()
        || !max_snap_factor.is_finite()
        || max_snap_factor <= 0.0
    {
        return;
    }

    let mut triangle_boxes = Vec::with_capacity(target_triangles.len());
    let mut triangle_indices = Vec::with_capacity(target_triangles.len());
    let mut group_box = AxisAlignedBoundingBox::default();
    for (i, triangle) in target_triangles.iter().enumerate() {
        if triangle.len() < 3 {
            continue;
        }
        let (a, b, c) = (triangle[0], triangle[1], triangle[2]);
        if a >= target_vertices.len() || b >= target_vertices.len() || c >= target_vertices.len() {
            continue;
        }
        if !vec3_is_finite(&target_vertices[a])
            || !vec3_is_finite(&target_vertices[b])
            || !vec3_is_finite(&target_vertices[c])
        {
            continue;
        }
        let mut bbox = AxisAlignedBoundingBox::default();
        bbox.update(&target_vertices[a]);
        bbox.update(&target_vertices[b]);
        bbox.update(&target_vertices[c]);
        bbox.update_center();
        group_box.update(&target_vertices[a]);
        group_box.update(&target_vertices[b]);
        group_box.update(&target_vertices[c]);
        triangle_boxes.push(bbox);
        triangle_indices.push(i);
    }
    if triangle_indices.is_empty() {
        return;
    }
    group_box.update_center();
    let tree = AxisAlignedBoundingBoxTree::new(triangle_boxes, triangle_indices, group_box);
    let target_normals = compute_triangle_normals(target_vertices, target_triangles);

    let (vertex_normals, local_edges, global_avg) =
        compute_polygon_geometry(remeshed_vertices, remeshed_polygons);
    if !global_avg.is_finite() || global_avg <= 0.0 {
        return;
    }

    let mut vertex_faces = vec![Vec::new(); remeshed_vertices.len()];
    for (fi, face) in remeshed_polygons.iter().enumerate() {
        for &vi in face {
            if vi < vertex_faces.len() {
                vertex_faces[vi].push(fi);
            }
        }
    }

    let mut candidates = vec![None; remeshed_vertices.len()];
    parallel_each(&mut candidates, |i, slot| {
        if vertex_faces[i].is_empty() {
            return;
        }
        let edge_len = if local_edges[i] > 0.0 {
            local_edges[i]
        } else {
            global_avg
        };
        let max_radius = edge_len * max_snap_factor;
        let initial_radius = (edge_len * 0.5).min(max_radius);
        *slot = project_point_normal_guarded(
            target_vertices,
            target_triangles,
            &target_normals,
            &tree,
            initial_radius,
            max_radius,
            remeshed_vertices[i],
            vertex_normals[i],
            min_normal_dot,
        );
    });

    for (i, cand_opt) in candidates.into_iter().enumerate() {
        let Some(cand) = cand_opt else {
            continue;
        };
        let old = remeshed_vertices[i];
        if anti_flip_move_ok(
            remeshed_vertices,
            remeshed_polygons,
            &vertex_faces[i],
            i,
            cand,
        ) {
            remeshed_vertices[i] = cand;
        } else {
            let half = (old + cand) * 0.5;
            if anti_flip_move_ok(
                remeshed_vertices,
                remeshed_polygons,
                &vertex_faces[i],
                i,
                half,
            ) {
                remeshed_vertices[i] = half;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_mesh_onto_surface_blocks_backside_snap_on_thin_plate() {
        // Two closely spaced parallel horizontal plates: top (+Z normal) at z=0.05,
        // bottom (-Z normal) at z=-0.05. A quad on the top side sits closer to
        // the bottom plate (z = -0.02, normal +Z) and must snap up to z = 0.05,
        // never down to the opposite -Z wall.
        let target_vertices = vec![
            // Top sheet (+Z)
            Vector3::new(-2.0, -2.0, 0.05),
            Vector3::new(2.0, -2.0, 0.05),
            Vector3::new(2.0, 2.0, 0.05),
            Vector3::new(-2.0, 2.0, 0.05),
            // Bottom sheet (-Z)
            Vector3::new(-2.0, -2.0, -0.05),
            Vector3::new(2.0, -2.0, -0.05),
            Vector3::new(2.0, 2.0, -0.05),
            Vector3::new(-2.0, 2.0, -0.05),
        ];
        let target_triangles = vec![vec![0, 1, 2], vec![0, 2, 3], vec![4, 6, 5], vec![4, 7, 6]];
        let mut verts = vec![
            Vector3::new(0.0, 0.0, -0.02),
            Vector3::new(1.0, 0.0, -0.02),
            Vector3::new(1.0, 1.0, -0.02),
            Vector3::new(0.0, 1.0, -0.02),
        ];
        let quads = vec![vec![0, 1, 2, 3]];
        project_mesh_onto_surface(
            &mut verts,
            &quads,
            &target_vertices,
            &target_triangles,
            1.5,
            0.1,
        );
        for v in &verts {
            assert!(
                (v.z() - 0.05).abs() < 1e-12,
                "expected top sheet z=0.05, got {}",
                v.z()
            );
        }
    }

    #[test]
    fn project_mesh_onto_surface_respects_max_snap_radius() {
        let target_vertices = vec![
            Vector3::new(-5.0, -5.0, 10.0),
            Vector3::new(5.0, -5.0, 10.0),
            Vector3::new(0.0, 5.0, 10.0),
        ];
        let target_triangles = vec![vec![0, 1, 2]];
        let mut verts = vec![
            Vector3::new(0.0, 0.0, 0.0),
            Vector3::new(1.0, 0.0, 0.0),
            Vector3::new(1.0, 1.0, 0.0),
            Vector3::new(0.0, 1.0, 0.0),
        ];
        let quads = vec![vec![0, 1, 2, 3]];
        project_mesh_onto_surface(
            &mut verts,
            &quads,
            &target_vertices,
            &target_triangles,
            1.5,
            0.1,
        );
        for v in &verts {
            assert_eq!(v.z(), 0.0, "distant surface beyond 1.5*edge must not snap");
        }
    }
}
