use super::QuadExtractor;
use crate::mesh_separator::MeshSeparator;
use crate::vector2::Vector2;
use crate::vector3::Vector3;
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

impl<'a> QuadExtractor<'a> {
    fn test_point_in_triangle(
        points: &[Vector3],
        triangle: &[usize],
        test_points: &[usize],
    ) -> bool {
        if triangle.len() < 3 || test_points.is_empty() {
            return false;
        }
        let p0 = points[triangle[0]];
        let p1 = points[triangle[1]];
        let p2 = points[triangle[2]];
        let triangle_normal = Vector3::normal(&p0, &p1, &p2);
        if triangle_normal.is_zero()
            || !triangle_normal.x().is_finite()
            || !triangle_normal.y().is_finite()
            || !triangle_normal.z().is_finite()
        {
            return false;
        }
        let max_edge = (p1 - p0)
            .length()
            .max((p2 - p1).length())
            .max((p0 - p2).length());
        let max_plane_dist = 0.5 * max_edge;

        let origin = (p0 + p1 + p2) / 3.0;
        let axis = (p0 - origin).normalized();
        if axis.is_zero() {
            return false;
        }
        let mut points_in_3d = Vec::with_capacity(3 + test_points.len());
        points_in_3d.push(p0);
        points_in_3d.push(p1);
        points_in_3d.push(p2);
        for it in test_points {
            let pt = points[*it];
            // Reject false-positive 2D shadows from far-away curved surfaces
            // (e.g. opposite sides of horns/limbs/ears).
            if Vector3::dot_product(&(pt - origin), &triangle_normal).abs() <= max_plane_dist {
                points_in_3d.push(pt);
            }
        }
        if points_in_3d.len() <= 3 {
            return false;
        }
        let mut points_in_2d = Vec::new();
        Vector3::project_to_2d(
            &points_in_3d,
            &mut points_in_2d,
            &triangle_normal,
            &axis,
            &origin,
        );
        let a = points_in_2d[0];
        let b = points_in_2d[1];
        let c = points_in_2d[2];
        for point in points_in_2d.iter().skip(3) {
            if Vector2::is_in_triangle(&a, &b, &c, point) {
                return true;
            }
        }
        false
    }

    pub(crate) fn rebuild_half_edges(&mut self) {
        self.half_edges.clear();
        for face in &self.remeshed_polygons {
            for i in 0..face.len() {
                let j = (i + 1) % face.len();
                self.half_edges.insert((face[i], face[j]));
            }
        }
    }

    /// Returns true if the input working triangle mesh `(self.vertices, self.triangles)`
    /// has at least one unpaired (boundary) directed edge.
    pub(crate) fn working_mesh_has_boundary(&self) -> bool {
        if self.triangles.is_empty() {
            return false;
        }
        let mut dir_edges: BTreeSet<(usize, usize)> = BTreeSet::new();
        for tri in self.triangles {
            if tri.len() < 3 {
                continue;
            }
            for i in 0..3 {
                dir_edges.insert((tri[i], tri[(i + 1) % 3]));
            }
        }
        dir_edges.iter().any(|&(u, v)| !dir_edges.contains(&(v, u)))
    }

    /// Returns true if `self.half_edges` contains at least one open boundary edge.
    pub(crate) fn has_open_half_edges(&self) -> bool {
        self.half_edges
            .iter()
            .any(|&(u, v)| !self.half_edges.contains(&(v, u)))
    }

    /// Collects positions of vertices on open boundary edges of the working
    /// triangle mesh, used to distinguish true input rims from internal holes.
    fn working_boundary_positions(&self) -> Vec<Vector3> {
        let mut dir_edges: BTreeSet<(usize, usize)> = BTreeSet::new();
        for tri in self.triangles {
            if tri.len() < 3 {
                continue;
            }
            for i in 0..3 {
                dir_edges.insert((tri[i], tri[(i + 1) % 3]));
            }
        }
        let mut verts = BTreeSet::new();
        for &(u, v) in &dir_edges {
            if !dir_edges.contains(&(v, u)) {
                verts.insert(u);
                verts.insert(v);
            }
        }
        verts
            .into_iter()
            .filter_map(|vi| self.vertices.get(vi).copied())
            .collect()
    }

    fn average_quad_edge_length(&self) -> f64 {
        let mut sum = 0.0;
        let mut count = 0usize;
        for face in &self.remeshed_polygons {
            for i in 0..face.len() {
                let a = face[i];
                let b = face[(i + 1) % face.len()];
                if a < self.remeshed_vertices.len() && b < self.remeshed_vertices.len() {
                    let l = (self.remeshed_vertices[a] - self.remeshed_vertices[b]).length();
                    if l.is_finite() && l > 0.0 {
                        sum += l;
                        count += 1;
                    }
                }
            }
        }
        if count > 0 { sum / count as f64 } else { 0.0 }
    }

    /// Returns true if the majority of vertices on `hole` sit right on the
    /// working mesh's open input boundary (so `hole` is a genuine open input rim
    /// rather than an extracted tear).
    fn is_input_boundary_loop(
        &self,
        hole: &[usize],
        working_boundary: &[Vector3],
        avg_edge: f64,
    ) -> bool {
        if working_boundary.is_empty() || hole.is_empty() || avg_edge <= 0.0 {
            return false;
        }
        let tol2 = (avg_edge * 2.0) * (avg_edge * 2.0);
        let mut near = 0usize;
        for &vi in hole {
            let Some(&p) = self.remeshed_vertices.get(vi) else {
                continue;
            };
            if working_boundary
                .iter()
                .any(|&bp| (bp - p).length_squared() <= tol2)
            {
                near += 1;
            }
        }
        near * 10 >= hole.len() * 7
    }

    pub(crate) fn fix_holes(&mut self) {
        let mut loops = Vec::new();
        // Restructure: cloned (the C++ passes the member by const ref while
        // `fix_hole_with_quads` mutates it through `this`).
        self.search_boundaries(&self.half_edges.clone(), &mut loops);
        let working_boundary = self.working_boundary_positions();
        let closed_working = working_boundary.is_empty();
        let avg_edge = self.average_quad_edge_length();
        // Deterministic ordering: smallest holes first so small pinches close
        // cleanly before large loops, tie-broken by original loop order.
        loops.sort_by_key(|l| l.len());
        let mut vertex_normals = None;
        for loop_ in &mut loops {
            let is_input_rim =
                !closed_working && self.is_input_boundary_loop(loop_, &working_boundary, avg_edge);
            if is_input_rim && loop_.len() > 65 {
                self.diagnose(|| format!("Ignore open input rim at length:{}\n", loop_.len()));
                continue;
            }
            // Cap extremely huge loops only if they exceed sanity limit; allow
            // closing genuine large extracted holes (e.g. > 65 up to 1024 edges).
            if loop_.len() > 1024 {
                self.diagnose(|| format!("Ignore huge hole at length:{}\n", loop_.len()));
                continue;
            }
            self.diagnose(|| format!("Fixing hole at length:{}...\n", loop_.len()));
            if !is_input_rim || loop_.len() <= 65 {
                self.fix_hole_with_quads(loop_, true);
                // fixHoleWithQuads returns with 3 or 4 hole verts left only
                // after pushing that final cap, so a second pass there re-emits
                // the identical quad (doubled face, use-count-3 edges).
                // Continue only on a genuine uncapped remainder.
                if loop_.len() > 4 && loop_.len() <= 128 {
                    self.fix_hole_with_quads(loop_, false);
                }
            }
            if loop_.len() >= 3
                && (closed_working || !is_input_rim)
                && self.loop_has_open_edges(loop_)
            {
                let normals =
                    vertex_normals.get_or_insert_with(|| self.compute_remeshed_vertex_normals());
                let normals_snapshot = normals.clone();
                self.close_remaining_hole(loop_, &normals_snapshot);
                // Refresh vertex normals after capping large holes if needed.
                if loop_.len() > 16 {
                    vertex_normals = Some(self.compute_remeshed_vertex_normals());
                }
            }
        }
    }

    fn loop_has_open_edges(&self, hole: &[usize]) -> bool {
        if hole.len() < 3 {
            return false;
        }
        for i in 0..hole.len() {
            let u = hole[i];
            let v = hole[(i + 1) % hole.len()];
            if self.half_edges.contains(&(u, v)) && !self.half_edges.contains(&(v, u)) {
                return true;
            }
        }
        false
    }

    fn compute_remeshed_vertex_normals(&self) -> Vec<Vector3> {
        let mut normals = vec![Vector3::default(); self.remeshed_vertices.len()];
        for face in &self.remeshed_polygons {
            if face.len() < 3 {
                continue;
            }
            let mut fn_acc = Vector3::default();
            let v0 = face[0];
            if v0 < normals.len() {
                for k in 1..face.len() - 1 {
                    let v1 = face[k];
                    let v2 = face[k + 1];
                    if v1 < normals.len() && v2 < normals.len() {
                        fn_acc += Vector3::cross_product(
                            &(self.remeshed_vertices[v1] - self.remeshed_vertices[v0]),
                            &(self.remeshed_vertices[v2] - self.remeshed_vertices[v0]),
                        );
                    }
                }
            }
            if !fn_acc.is_zero()
                && fn_acc.x().is_finite()
                && fn_acc.y().is_finite()
                && fn_acc.z().is_finite()
            {
                for &vi in face {
                    if vi < normals.len() {
                        normals[vi] += fn_acc;
                    }
                }
            }
        }
        for n in &mut normals {
            if !n.is_zero() {
                *n = n.normalized();
            }
        }
        normals
    }

    fn can_emit_polygon(&self, poly: &[usize]) -> bool {
        if poly.len() < 3 {
            return false;
        }
        // Reject duplicate vertices inside the same face.
        for i in 0..poly.len() {
            for j in (i + 1)..poly.len() {
                if poly[i] == poly[j] {
                    return false;
                }
            }
        }
        for i in 0..poly.len() {
            let a = poly[i];
            let b = poly[(i + 1) % poly.len()];
            if self.half_edges.contains(&(a, b)) {
                return false;
            }
        }
        true
    }

    /// Fallback closer for any hole left uncapped by `fix_hole_with_quads` (such
    /// as convex/polygonal loops where dot <= 0, 3D loops blocked by 2D shadow
    /// checks, or holes longer than 65 edges). Emits conflict-free quads when
    /// well-shaped ear quads are available and finishes with normal-consistent
    /// ear-clipped triangles.
    fn close_remaining_hole(&mut self, hole: &mut Vec<usize>, vertex_normals: &[Vector3]) {
        let mut guard = hole.len().saturating_mul(2) + 8;
        while hole.len() >= 3 && guard > 0 {
            guard -= 1;

            // Remove zero-area adjacent repeats or back-and-forth fold spurs
            // (`a -> b -> a`) that can arise after splitting a figure-8 loop.
            let n = hole.len();
            let mut simplified = false;
            for i in 0..n {
                let next = (i + 1) % n;
                if hole[i] == hole[next] {
                    hole.remove(next);
                    simplified = true;
                    break;
                }
                if n > 3 {
                    let next2 = (i + 2) % n;
                    if hole[i] == hole[next2] {
                        // Drop the spike tip at `next` and one duplicate.
                        let a = next.max(next2);
                        let b = next.min(next2);
                        hole.remove(a);
                        hole.remove(b);
                        simplified = true;
                        break;
                    }
                }
            }
            if simplified {
                continue;
            }

            if hole.len() == 3 {
                let tri = vec![hole[2], hole[1], hole[0]];
                if self.can_emit_polygon(&tri) {
                    self.remeshed_polygons.push(tri);
                    self.record_half_edges_of_last_polygon();
                    hole.clear();
                }
                break;
            }

            if hole.len() == 4 {
                let quad = vec![hole[3], hole[2], hole[1], hole[0]];
                if self.can_emit_polygon(&quad) {
                    self.remeshed_polygons.push(quad);
                    self.record_half_edges_of_last_polygon();
                    hole.clear();
                    break;
                }
                // Try splitting the 4-gon across either diagonal if one chord
                // is already free in half_edges.
                let t0a = vec![hole[2], hole[1], hole[0]];
                let t0b = vec![hole[3], hole[2], hole[0]];
                let t1a = vec![hole[3], hole[1], hole[0]];
                let t1b = vec![hole[3], hole[2], hole[1]];
                if self.can_emit_polygon(&t0a) && !self.half_edges.contains(&(hole[0], hole[2])) {
                    self.remeshed_polygons.push(t0a);
                    self.record_half_edges_of_last_polygon();
                    if self.can_emit_polygon(&t0b) {
                        self.remeshed_polygons.push(t0b);
                        self.record_half_edges_of_last_polygon();
                    }
                    hole.clear();
                    break;
                }
                if self.can_emit_polygon(&t1a) && !self.half_edges.contains(&(hole[1], hole[3])) {
                    self.remeshed_polygons.push(t1a);
                    self.record_half_edges_of_last_polygon();
                    if self.can_emit_polygon(&t1b) {
                        self.remeshed_polygons.push(t1b);
                        self.record_half_edges_of_last_polygon();
                    }
                    hole.clear();
                    break;
                }
                break;
            }

            let len = hole.len();
            // First look for a well-shaped, conflict-free ear quad `[k, j, i, h]`.
            let mut best_quad: Option<(f64, usize, usize, Vec<usize>)> = None;
            for i in 0..len {
                let h = (i + len - 1) % len;
                let j = (i + 1) % len;
                let k = (i + 2) % len;
                let quad = vec![hole[k], hole[j], hole[i], hole[h]];
                if !self.can_emit_polygon(&quad) {
                    continue;
                }
                let ph = self.remeshed_vertices[hole[h]];
                let pi = self.remeshed_vertices[hole[i]];
                let pj = self.remeshed_vertices[hole[j]];
                let pk = self.remeshed_vertices[hole[k]];
                let chord = (pk - ph).length();
                let base = (pj - pi).length();
                if chord <= 1e-12 || base <= 1e-12 {
                    continue;
                }
                let n1 = Vector3::cross_product(&(pj - pk), &(pi - pk));
                let n2 = Vector3::cross_product(&(pi - pk), &(ph - pk));
                if n1.is_zero() || n2.is_zero() {
                    continue;
                }
                let un1 = n1.normalized();
                let un2 = n2.normalized();
                let twist_dot = Vector3::dot_product(&un1, &un2);
                if twist_dot < 0.25 {
                    continue;
                }
                let mut ref_n = Vector3::default();
                for &v in &quad {
                    if let Some(vn) = vertex_normals.get(v) {
                        ref_n += *vn;
                    }
                }
                let norm_dot = if !ref_n.is_zero() {
                    Vector3::dot_product(&((un1 + un2).normalized()), &ref_n.normalized())
                } else {
                    1.0
                };
                if norm_dot < -0.1 {
                    continue;
                }
                let left = (ph - pi).normalized();
                let right = (pk - pj).normalized();
                let dir_dot = Vector3::dot_product(&left, &right);
                if dir_dot < 0.1 {
                    continue;
                }
                let ratio_pen = (chord / base - 1.0).abs();
                let score = dir_dot + 0.5 * norm_dot + 0.3 * twist_dot - 0.3 * ratio_pen;
                if best_quad.as_ref().is_none_or(|b| score > b.0) {
                    best_quad = Some((score, i, j, quad));
                }
            }
            if let Some((_, i, j, quad)) = best_quad {
                self.remeshed_polygons.push(quad);
                self.record_half_edges_of_last_polygon();
                let hi_idx = i.max(j);
                let lo_idx = i.min(j);
                hole.remove(hi_idx);
                hole.remove(lo_idx);
                continue;
            }

            // Otherwise clip the best-scored conflict-free triangle ear `[j, i, h]`.
            let mut best_tri: Option<(f64, usize, Vec<usize>)> = None;
            for i in 0..len {
                let h = (i + len - 1) % len;
                let j = (i + 1) % len;
                let tri = vec![hole[j], hole[i], hole[h]];
                if !self.can_emit_polygon(&tri) {
                    continue;
                }
                let ph = self.remeshed_vertices[hole[h]];
                let pi = self.remeshed_vertices[hole[i]];
                let pj = self.remeshed_vertices[hole[j]];
                let e1 = pi - pj;
                let e2 = ph - pj;
                let nvec = Vector3::cross_product(&e1, &e2);
                let area2 = nvec.length();
                let l1 = e1.length();
                let l2 = (ph - pi).length();
                let chord = (ph - pj).length();
                let ref_edge = (l1 + l2) * 0.5;
                let mut ref_n = Vector3::default();
                for &v in &tri {
                    if let Some(vn) = vertex_normals.get(v) {
                        ref_n += *vn;
                    }
                }
                let norm_dot = if !nvec.is_zero() && !ref_n.is_zero() {
                    Vector3::dot_product(&nvec.normalized(), &ref_n.normalized())
                } else {
                    0.0
                };
                let sin_quality = if l1 > 1e-12 && l2 > 1e-12 {
                    area2 / (l1 * l2)
                } else {
                    0.0
                };
                let chord_ratio = if ref_edge > 1e-12 {
                    chord / ref_edge
                } else {
                    chord
                };
                // Favor normal-aligned, compact chords with well-formed triangles.
                let score = 2.0 * norm_dot + 0.5 * sin_quality - chord_ratio;
                if best_tri.as_ref().is_none_or(|b| score > b.0) {
                    best_tri = Some((score, i, tri));
                }
            }
            if let Some((_, i, tri)) = best_tri {
                self.remeshed_polygons.push(tri);
                self.record_half_edges_of_last_polygon();
                hole.remove(i);
            } else {
                break;
            }
        }
    }

    fn record_half_edges_of_last_polygon(&mut self) {
        let last = self.remeshed_polygons.len() - 1;
        for i in 0..self.remeshed_polygons[last].len() {
            let face_len = self.remeshed_polygons[last].len();
            let j = (i + 1) % face_len;
            let edge = (
                self.remeshed_polygons[last][i],
                self.remeshed_polygons[last][j],
            );
            self.half_edges.insert(edge);
        }
    }

    fn fix_hole_with_quads(&mut self, hole: &mut Vec<usize>, check_score: bool) {
        loop {
            if hole.len() <= 2 {
                self.diagnose(|| {
                    format!("fixHoleWithQuads cancel on edge length:{}\n", hole.len())
                });
                return;
            }

            if 3 == hole.len() {
                self.remeshed_polygons.push(vec![hole[2], hole[1], hole[0]]);
                self.record_half_edges_of_last_polygon();
                return;
            }

            if 4 == hole.len() {
                self.remeshed_polygons
                    .push(vec![hole[3], hole[2], hole[1], hole[0]]);
                self.record_half_edges_of_last_polygon();
                return;
            }

            let mut edge_scores = Vec::with_capacity(hole.len());
            for i in 0..hole.len() {
                let h = (i + hole.len() - 1) % hole.len();
                let j = (i + 1) % hole.len();
                let k = (j + 1) % hole.len();
                let left = (self.remeshed_vertices[hole[h]] - self.remeshed_vertices[hole[i]])
                    .normalized();
                let right = (self.remeshed_vertices[hole[k]] - self.remeshed_vertices[hole[j]])
                    .normalized();
                edge_scores.push((i, Vector3::dot_product(&left, &right)));
            }
            // Restructure: `std::sort` (libc++ introsort) becomes
            // `sort_unstable_by` (pdqsort). Both deterministic, but they
            // order score ties differently; the oracle measures the fallout
            // on symmetric holes (see the report).
            edge_scores.sort_unstable_by(|first, second| {
                first.1.partial_cmp(&second.1).unwrap_or(Ordering::Equal)
            });
            let mut hole_changed = false;
            for edge_index in (0..edge_scores.len()).rev() {
                let score = edge_scores[edge_index];
                if check_score && score.1 <= 0.0 {
                    self.diagnose(|| {
                        format!("fixHoleWithQuads failed, highest score(dot):{}\n", score.1)
                    });
                    return;
                }
                let i = score.0;
                let h = (i + hole.len() - 1) % hole.len();
                let j = (i + 1) % hole.len();
                let k = (j + 1) % hole.len();
                let candidate = vec![hole[k], hole[j], hole[i], hole[h]];
                if self.half_edges.contains(&(candidate[0], candidate[1]))
                    || self.half_edges.contains(&(candidate[1], candidate[2]))
                    || self.half_edges.contains(&(candidate[2], candidate[3]))
                    || self.half_edges.contains(&(candidate[3], candidate[0]))
                {
                    self.diagnose(|| {
                        format!(
                            "fixHoleWithQuads ignore score:{} because conflicts with existed quads\n",
                            score.1
                        )
                    });
                    continue;
                }
                let mut remain_points = Vec::new();
                for (w, point) in hole.iter().enumerate() {
                    if w == i || w == j || w == h || w == k {
                        continue;
                    }
                    remain_points.push(*point);
                }
                if Self::test_point_in_triangle(
                    &self.remeshed_vertices,
                    &[candidate[0], candidate[1], candidate[2]],
                    &remain_points,
                ) || Self::test_point_in_triangle(
                    &self.remeshed_vertices,
                    &[candidate[2], candidate[3], candidate[0]],
                    &remain_points,
                ) {
                    self.diagnose(|| {
                        format!(
                            "fixHoleWithQuads ignore score:{} because other point in the same loop fall into quad plane\n",
                            score.1
                        )
                    });
                    continue;
                }
                self.remeshed_polygons.push(candidate);
                self.record_half_edges_of_last_polygon();

                let mut new_hole = Vec::new();
                for (w, point) in hole.iter().enumerate() {
                    if w == i || w == j {
                        continue;
                    }
                    new_hole.push(*point);
                }
                *hole = new_hole;
                hole_changed = true;
                break;
            }
            if !hole_changed {
                break;
            }
        }
    }

    fn search_boundaries(
        &self,
        half_edges: &BTreeSet<(usize, usize)>,
        loops: &mut Vec<Vec<usize>>,
    ) {
        self.diagnose(|| "Searching boundaries...\n".to_string());

        let mut next_map: BTreeMap<usize, BTreeSet<usize>> = BTreeMap::new();
        for (from, to) in half_edges {
            if half_edges.contains(&(*to, *from)) {
                continue;
            }
            next_map.entry(*from).or_default().insert(*to);
        }

        // Map directed polygon edge `(u, v)` to the following vertex `w` in the
        // same face so pinched boundary vertices (`nexts.len() > 1`) can walk
        // the corner face fan instead of discarding loops.
        let mut poly_next: BTreeMap<(usize, usize), usize> = BTreeMap::new();
        for face in &self.remeshed_polygons {
            let n = face.len();
            if n < 3 {
                continue;
            }
            for i in 0..n {
                let u = face[i];
                let v = face[(i + 1) % n];
                let w = face[(i + 2) % n];
                poly_next.entry((u, v)).or_insert(w);
            }
        }

        let remove_edge = |map: &mut BTreeMap<usize, BTreeSet<usize>>, from: usize, to: usize| {
            let empty = if let Some(set) = map.get_mut(&from) {
                set.remove(&to);
                set.is_empty()
            } else {
                false
            };
            if empty {
                map.remove(&from);
            }
        };

        while !next_map.is_empty() {
            let Some((&start_vertex, nexts0)) = next_map.first_key_value() else {
                break;
            };
            let Some(&first_to) = nexts0.iter().next() else {
                next_map.remove(&start_vertex);
                continue;
            };
            self.diagnose(|| format!("Searching loop from:{start_vertex}\n"));

            let mut path_verts = vec![start_vertex];
            let mut path_edges = Vec::new();
            let mut path_edge_set = BTreeSet::new();
            let mut pos_in_path: BTreeMap<usize, usize> = BTreeMap::new();
            pos_in_path.insert(start_vertex, 0);

            let mut prev_v = start_vertex;
            let mut cur_v = first_to;
            path_edges.push((start_vertex, first_to));
            path_edge_set.insert((start_vertex, first_to));

            struct ExtractedCycle {
                verts: Vec<usize>,
                edges: Vec<(usize, usize)>,
            }
            let mut extracted_cycle: Option<ExtractedCycle> = None;

            loop {
                if cur_v == start_vertex && path_verts.len() >= 3 {
                    self.diagnose(|| format!("Found valid loop, size:{}\n", path_verts.len()));
                    extracted_cycle = Some(ExtractedCycle {
                        verts: path_verts.clone(),
                        edges: path_edges.clone(),
                    });
                    break;
                }
                if let Some(&cycle_idx) = pos_in_path.get(&cur_v) {
                    let sub_verts = path_verts[cycle_idx..].to_vec();
                    let sub_edges = path_edges[cycle_idx..].to_vec();
                    if sub_verts.len() >= 3 {
                        self.diagnose(|| format!("Found pinched loop, size:{}\n", sub_verts.len()));
                        extracted_cycle = Some(ExtractedCycle {
                            verts: sub_verts,
                            edges: sub_edges,
                        });
                    }
                    break;
                }

                pos_in_path.insert(cur_v, path_verts.len());
                path_verts.push(cur_v);

                let Some(cands) = next_map.get(&cur_v) else {
                    break;
                };

                let chosen_next = if cands.len() == 1 {
                    cands.iter().next().copied()
                } else {
                    // Fan walk around `cur_v` from the polygon owning `(prev_v, cur_v)`
                    // to find the geometrically adjacent outgoing boundary edge.
                    let mut fan_choice = None;
                    if let Some(&(mut w)) = poly_next.get(&(prev_v, cur_v)) {
                        for _ in 0..64 {
                            if !half_edges.contains(&(w, cur_v)) {
                                if cands.contains(&w) && !path_edge_set.contains(&(cur_v, w)) {
                                    fan_choice = Some(w);
                                }
                                break;
                            }
                            let Some(&next_w) = poly_next.get(&(w, cur_v)) else {
                                break;
                            };
                            if next_w == w {
                                break;
                            }
                            w = next_w;
                        }
                    }
                    fan_choice.or_else(|| {
                        cands
                            .iter()
                            .copied()
                            .find(|&cand| !path_edge_set.contains(&(cur_v, cand)))
                    })
                };

                let Some(next_v) = chosen_next else {
                    break;
                };
                if path_edge_set.contains(&(cur_v, next_v)) {
                    break;
                }
                prev_v = cur_v;
                cur_v = next_v;
                path_edges.push((prev_v, cur_v));
                path_edge_set.insert((prev_v, cur_v));
                if path_edges.len() > half_edges.len() + 1 {
                    break;
                }
            }

            if let Some(cycle) = extracted_cycle {
                for (u, v) in cycle.edges {
                    remove_edge(&mut next_map, u, v);
                }
                loops.push(cycle.verts);
            } else {
                for (u, v) in path_edges {
                    remove_edge(&mut next_map, u, v);
                }
            }
        }

        self.diagnose(|| "Searching boundaries done\n".to_string());
    }

    fn component_area_and_diag(&self, faces: &[Vec<usize>]) -> (f64, f64) {
        let mut area = 0.0;
        let mut min_p = [f64::INFINITY; 3];
        let mut max_p = [f64::NEG_INFINITY; 3];
        for f in faces {
            if f.len() < 3 {
                continue;
            }
            for &vi in f {
                if let Some(p) = self.remeshed_vertices.get(vi) {
                    min_p[0] = min_p[0].min(p.x());
                    min_p[1] = min_p[1].min(p.y());
                    min_p[2] = min_p[2].min(p.z());
                    max_p[0] = max_p[0].max(p.x());
                    max_p[1] = max_p[1].max(p.y());
                    max_p[2] = max_p[2].max(p.z());
                }
            }
            let a = f[0];
            if a >= self.remeshed_vertices.len() {
                continue;
            }
            for k in 1..f.len() - 1 {
                let (b, c) = (f[k], f[k + 1]);
                if b < self.remeshed_vertices.len() && c < self.remeshed_vertices.len() {
                    let cross = Vector3::cross_product(
                        &(self.remeshed_vertices[b] - self.remeshed_vertices[a]),
                        &(self.remeshed_vertices[c] - self.remeshed_vertices[a]),
                    );
                    let len = cross.length();
                    if len.is_finite() {
                        area += 0.5 * len;
                    }
                }
            }
        }
        let diag = if min_p[0].is_finite() && max_p[0].is_finite() {
            let dx = max_p[0] - min_p[0];
            let dy = max_p[1] - min_p[1];
            let dz = max_p[2] - min_p[2];
            (dx * dx + dy * dy + dz * dz).sqrt()
        } else {
            0.0
        };
        (area, diag)
    }

    /// Prunes tiny or collapsed detached sliver components while preserving the
    /// largest component plus any non-trivial valid component (`>= 6` faces
    /// with nontrivial area and spatial extent relative to the largest piece)
    /// so detached horns/ears/limbs are kept without keeping folded micro-shards.
    pub(crate) fn remove_isolated_faces(&mut self) -> bool {
        let mut quads_islands = Vec::new();
        MeshSeparator::split_to_islands(&self.remeshed_polygons, &mut quads_islands);
        if quads_islands.len() <= 1 {
            return false;
        }
        // `std::max_element` returns the FIRST maximum; preserve strict-`<`.
        let mut biggest = 0;
        for (index, island) in quads_islands.iter().enumerate() {
            if quads_islands[biggest].len() < island.len() {
                biggest = index;
            }
        }
        let (big_area, big_diag) = self.component_area_and_diag(&quads_islands[biggest]);
        let min_area = big_area * 0.002;
        let min_diag = big_diag * 0.03;
        let mut kept = Vec::new();
        let mut kept_islands = 0usize;
        for (index, island) in quads_islands.iter().enumerate() {
            if index == biggest {
                kept.extend(island.iter().cloned());
                kept_islands += 1;
                continue;
            }
            if island.len() >= 6 {
                let (area, diag) = self.component_area_and_diag(island);
                if area > 0.0 && area >= min_area && diag >= min_diag {
                    kept.extend(island.iter().cloned());
                    kept_islands += 1;
                }
            }
        }
        if kept_islands == quads_islands.len() {
            return false;
        }
        self.remeshed_polygons = kept;
        true
    }

    /// Removes excess faces incident to true non-manifold edges (duplicate
    /// directed half-edges or undirected edges shared by more than 2 faces).
    pub(crate) fn remove_duplicate_edge_faces(&mut self) -> bool {
        let mut dir_seen: BTreeSet<(usize, usize)> = BTreeSet::new();
        let mut undir_counts: BTreeMap<(usize, usize), usize> = BTreeMap::new();
        let mut kept = Vec::with_capacity(self.remeshed_polygons.len());
        let mut removed = false;
        for face in &self.remeshed_polygons {
            if face.len() < 3 {
                removed = true;
                continue;
            }
            let mut conflict = false;
            for i in 0..face.len() {
                let a = face[i];
                let b = face[(i + 1) % face.len()];
                if a == b || dir_seen.contains(&(a, b)) {
                    conflict = true;
                    break;
                }
                let key = (a.min(b), a.max(b));
                if *undir_counts.get(&key).unwrap_or(&0) >= 2 {
                    conflict = true;
                    break;
                }
            }
            if conflict {
                removed = true;
                continue;
            }
            for i in 0..face.len() {
                let a = face[i];
                let b = face[(i + 1) % face.len()];
                dir_seen.insert((a, b));
                let key = (a.min(b), a.max(b));
                *undir_counts.entry(key).or_default() += 1;
            }
            kept.push(face.clone());
        }
        if removed {
            self.remeshed_polygons = kept;
        }
        removed
    }

    pub(crate) fn remove_non_manifold_faces(&mut self) -> bool {
        let mut changed = self.remove_duplicate_edge_faces();
        let mut edge_to_face_map = BTreeMap::new();
        MeshSeparator::build_edge_to_face_map(&self.remeshed_polygons, &mut edge_to_face_map);
        let mut vertex_open_boundary_count_map: BTreeMap<usize, usize> = BTreeMap::new();
        for edge in edge_to_face_map.keys() {
            if edge_to_face_map.contains_key(&(edge.1, edge.0)) {
                continue;
            }
            *vertex_open_boundary_count_map.entry(edge.0).or_default() += 1;
            *vertex_open_boundary_count_map.entry(edge.1).or_default() += 1;
        }
        let mut manifold_faces = Vec::new();
        for face in &self.remeshed_polygons {
            let mut is_non_manifold = false;
            for vertex in face {
                let Some(find_count) = vertex_open_boundary_count_map.get(vertex) else {
                    continue;
                };
                if *find_count > 2 {
                    is_non_manifold = true;
                    break;
                }
            }
            if is_non_manifold {
                changed = true;
                continue;
            }
            manifold_faces.push(face.clone());
        }
        self.remeshed_polygons = manifold_faces;
        changed
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::vector2::Vector2;

    fn closed_cube_working_mesh() -> (Vec<Vector3>, Vec<Vec<usize>>, Vec<Vec<Vector2>>) {
        let verts = vec![
            Vector3::new(-1.0, -1.0, -1.0),
            Vector3::new(1.0, -1.0, -1.0),
            Vector3::new(1.0, 1.0, -1.0),
            Vector3::new(-1.0, 1.0, -1.0),
            Vector3::new(-1.0, -1.0, 1.0),
            Vector3::new(1.0, -1.0, 1.0),
            Vector3::new(1.0, 1.0, 1.0),
            Vector3::new(-1.0, 1.0, 1.0),
        ];
        let tris = vec![
            vec![0, 2, 1],
            vec![0, 3, 2],
            vec![4, 5, 6],
            vec![4, 6, 7],
            vec![0, 1, 5],
            vec![0, 5, 4],
            vec![1, 2, 6],
            vec![1, 6, 5],
            vec![2, 3, 7],
            vec![2, 7, 6],
            vec![3, 0, 4],
            vec![3, 4, 7],
        ];
        let uvs = vec![vec![Vector2::default(); 3]; tris.len()];
        (verts, tris, uvs)
    }

    #[test]
    fn search_boundaries_handles_pinch_vertex_without_dropping_loops() {
        let (wverts, wtris, wuvs) = closed_cube_working_mesh();
        let mut ext = QuadExtractor::new(&wverts, &wtris, &wuvs);
        // Two 4-edge hole loops touching at pinch vertex 0 (`0->1->2->3->0` and `0->4->5->6->0`):
        let mut edges = BTreeSet::new();
        for (a, b) in [
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 0),
            (0, 4),
            (4, 5),
            (5, 6),
            (6, 0),
        ] {
            edges.insert((a, b));
        }
        ext.remeshed_vertices = vec![Vector3::default(); 7];
        // Also test interior pinch at vertex 3 (`0->3->1->0` and `2->3->4->2`)
        // where `start_vertex = 0` arrives at vertex 3 (`cands.len() == 2`)
        // and uses `poly_next` fan-walk to keep the two sectors separate.
        let mut pinched_edges = BTreeSet::new();
        for (a, b) in [(0, 3), (3, 1), (1, 0), (2, 3), (3, 4), (4, 2)] {
            pinched_edges.insert((a, b));
        }
        ext.remeshed_polygons = vec![vec![0, 3, 1], vec![2, 3, 4]];
        let mut pinched_loops = Vec::new();
        ext.search_boundaries(&pinched_edges, &mut pinched_loops);
        assert_eq!(
            pinched_loops.len(),
            2,
            "must extract both loops through pinch vertex 3: {pinched_loops:?}"
        );
        assert!(
            pinched_loops
                .iter()
                .any(|l| l.contains(&0) && l.contains(&1) && !l.contains(&2))
        );
        assert!(
            pinched_loops
                .iter()
                .any(|l| l.contains(&2) && l.contains(&4) && !l.contains(&0))
        );

        let mut loops = Vec::new();
        ext.remeshed_polygons.clear();
        ext.search_boundaries(&edges, &mut loops);
        assert_eq!(
            loops.len(),
            2,
            "both pinched loops must be recovered: {loops:?}"
        );
        assert!(loops.iter().all(|l| l.len() == 4), "{loops:?}");
    }

    #[test]
    fn remove_isolated_faces_keeps_nontrivial_component_and_prunes_scrap() {
        let (wverts, wtris, wuvs) = closed_cube_working_mesh();
        let mut ext = QuadExtractor::new(&wverts, &wtris, &wuvs);
        let mut verts = Vec::new();
        let mut faces = Vec::new();

        // Component 0: 8 quads (main body)
        for i in 0..8 {
            let x = i as f64;
            let b = verts.len();
            verts.push(Vector3::new(x, 0.0, 0.0));
            verts.push(Vector3::new(x + 1.0, 0.0, 0.0));
            verts.push(Vector3::new(x + 1.0, 1.0, 0.0));
            verts.push(Vector3::new(x, 1.0, 0.0));
            if i == 0 {
                faces.push(vec![b, b + 1, b + 2, b + 3]);
            } else {
                let prev = faces.last().unwrap().clone();
                faces.push(vec![prev[1], b + 1, b + 2, prev[2]]);
            }
        }
        // Component 1: 6 connected quads (valid detached horn/ear)
        let base1 = verts.len();
        for i in 0..=6 {
            verts.push(Vector3::new(i as f64, 10.0, 0.0));
            verts.push(Vector3::new(i as f64, 11.0, 0.0));
        }
        for i in 0..6 {
            let a = base1 + 2 * i;
            faces.push(vec![a, a + 2, a + 3, a + 1]);
        }
        // Component 2: 2 detached scrap quads (should be pruned)
        let base2 = verts.len();
        verts.push(Vector3::new(0.0, 20.0, 0.0));
        verts.push(Vector3::new(1.0, 20.0, 0.0));
        verts.push(Vector3::new(2.0, 20.0, 0.0));
        verts.push(Vector3::new(0.0, 21.0, 0.0));
        verts.push(Vector3::new(1.0, 21.0, 0.0));
        verts.push(Vector3::new(2.0, 21.0, 0.0));
        faces.push(vec![base2, base2 + 1, base2 + 4, base2 + 3]);
        faces.push(vec![base2 + 1, base2 + 2, base2 + 5, base2 + 4]);

        ext.remeshed_vertices = verts;
        ext.remeshed_polygons = faces;
        assert!(ext.remove_isolated_faces());
        assert_eq!(
            ext.remeshed_polygons.len(),
            14,
            "expected main (8) + valid detached (6) = 14 faces kept, scrap (2) dropped"
        );
    }

    #[test]
    fn fix_holes_closes_large_loop_above_65_edges_on_closed_working_mesh() {
        let (wverts, wtris, wuvs) = closed_cube_working_mesh();
        let mut ext = QuadExtractor::new(&wverts, &wtris, &wuvs);
        // Build an 80-edge circular rim of surrounding quads leaving an 80-edge central hole.
        let n = 80usize;
        let mut verts = Vec::with_capacity(2 * n);
        for i in 0..n {
            let ang = 2.0 * std::f64::consts::PI * (i as f64) / (n as f64);
            verts.push(Vector3::new(ang.cos(), ang.sin(), 0.0)); // inner hole ring 0..n
        }
        for i in 0..n {
            let ang = 2.0 * std::f64::consts::PI * (i as f64) / (n as f64);
            verts.push(Vector3::new(2.0 * ang.cos(), 2.0 * ang.sin(), 0.0)); // outer ring n..2n
        }
        let mut faces = Vec::with_capacity(n);
        for i in 0..n {
            let j = (i + 1) % n;
            // Inner edge oriented j -> i? Let's make outer face normal +Z:
            // [outer[i], outer[j], inner[j], inner[i]] -> inner boundary halfedge is (inner[j] -> inner[i]).
            faces.push(vec![n + i, n + j, j, i]);
        }
        ext.remeshed_vertices = verts;
        ext.remeshed_polygons = faces;
        ext.rebuild_half_edges();
        ext.fix_holes();
        // Rebuild and check only the outer rim boundary (n edges) remains; the inner 80-edge hole is capped.
        ext.rebuild_half_edges();
        let mut open_inner = 0usize;
        for &(u, v) in &ext.half_edges {
            if u < n && v < n && !ext.half_edges.contains(&(v, u)) {
                open_inner += 1;
            }
        }
        assert_eq!(open_inner, 0, "80-edge hole must be completely closed");
    }
}
