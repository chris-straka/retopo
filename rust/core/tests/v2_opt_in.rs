// The v2/v2.1 engine kept behind `AutoRemesher::set_v2_engine(true)`
// (gates/retopo-v21). These are v2/v2.1's own pins from its last commit
// (5e15a666): with the option on, the engine must still do exactly what
// v2.1 did. They show the opt-in keeps v2.1 intact; they say nothing about
// whether v2.1 is better than the default (pre-v2) path.
//
// - beast@1000 covers the appendage on attempt 0 and embeds on the input
//   mesh (v2.1's coverage_retry.rs; needs bench/fetch_models.sh);
// - the thin claw at wr=2.0 fires a working-side retry that recovers, and
//   keeps box and claw at wr 1.0 / 1.75 / 2.0 (moved here from
//   coverage_retry.rs, where they ran on the default path);
// - the timing grid makes 623 quads / 668 verts with v2.1's checksum
//   (v2.1's auto_remesher_timing.rs).
use retopo_core::auto_remesher::{AutoRemesher, ModelType};
use retopo_core::obj_reader;
use retopo_core::quad_parameterizer::DipoleConfig;
use retopo_core::vector3::Vector3;
use std::path::Path;

const BEAST: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../bench/models/beast.obj");

/// Mirrors the CLI load path (`mesh_io::load_with` for .obj): parse,
/// weld, f32 -> f64.
fn load_cli_like(filename: &Path) -> Option<(Vec<Vector3>, Vec<Vec<usize>>)> {
    let mut positions: Vec<f32> = Vec::new();
    let mut triangles: Vec<Vec<usize>> = Vec::new();
    let ok = obj_reader::load_obj_positions_and_triangles(
        filename,
        &mut positions,
        &mut triangles,
        None,
        None,
    );
    if !ok {
        return None;
    }
    obj_reader::weld_positions_and_triangles(&mut positions, &mut triangles, None);
    let mut vertices = Vec::with_capacity(positions.len() / 3);
    for i in 0..positions.len() / 3 {
        vertices.push(Vector3::new(
            positions[3 * i] as f64,
            positions[3 * i + 1] as f64,
            positions[3 * i + 2] as f64,
        ));
    }
    Some((vertices, triangles))
}

/// Mirrors the CLI bench invocation (defaults + `--target-quads`).
fn remesh_like_cli(
    vertices: &[Vector3],
    triangles: &[Vec<usize>],
    target_quads: usize,
) -> AutoRemesher {
    let mut remesher = AutoRemesher::new(vertices, triangles);
    remesher.set_target_triangle_count(2 * target_quads);
    remesher.set_symmetry_enabled(false);
    remesher.set_scaling(1.0);
    remesher.set_model_type(ModelType::Organic);
    remesher.set_gradient_adaptivity(1.0);
    remesher.set_anisotropy(1.0);
    remesher.set_sharp_edge_degrees(90.0);
    remesher.set_smooth_normal_degrees(0.0);
    remesher.set_dipoles(DipoleConfig::automatic());
    remesher.set_quiet(true);
    remesher
}

#[test]
fn beast_native_1000_covers_appendage_on_first_attempt() {
    if !Path::new(BEAST).exists() {
        println!("SKIP: {BEAST} absent (bench/fetch_models.sh); regression needs the corpus");
        return;
    }
    let (vertices, triangles) = load_cli_like(Path::new(BEAST)).expect("beast.obj loads");
    let mut remesher = remesh_like_cli(&vertices, &triangles, 1000);
    remesher.set_v2_engine(true);
    assert!(remesher.remesh(), "remesh succeeds");

    // The v2 engine covers the appendage cleanly on attempt 0 without
    // firing the coverage retry.
    let reports = remesher.coverage_reports();
    assert!(
        reports.is_empty(),
        "beast@1000 covers appendage on attempt 0 without retries, got {reports:?}"
    );

    // Ground truth: output reaches into the appendage (x < -62 held 416
    // working tris emitting 0 quads pre-v2; covered mesh spans past -100).
    let min_x = remesher
        .remeshed_vertices()
        .iter()
        .map(|v| v.x())
        .fold(f64::INFINITY, f64::min);
    assert!(
        min_x < -100.0,
        "output covers the appendage (min_x = {min_x:.1})"
    );

    // Output vertices project onto the original input mesh (`project_mesh_onto_surface`):
    // median output-to-input distance is < 1e-3 on a ~390-unit mesh.
    assert_eq!(remesher.island_output_quad_counts().len(), 1);
    let mut distances: Vec<f64> = remesher
        .remeshed_vertices()
        .iter()
        .map(|p| {
            triangles
                .iter()
                .map(|t| point_triangle_dist(p, &vertices[t[0]], &vertices[t[1]], &vertices[t[2]]))
                .fold(f64::INFINITY, f64::min)
        })
        .collect();
    distances.sort_by(|a, b| a.total_cmp(b));
    let median = distances[distances.len() / 2];
    assert!(
        median < 1e-3,
        "output embeds on the input mesh (median output-to-input distance = {median:.2e})"
    );
}

/// Thin-claw input-side fixture: 3x3x3 body with a dense 0.25-wide,
/// 6-tall claw (16 around x 24 rings + tip cap). At 50 target quads
/// decimation thins the claw past what the working-side check can see
/// (few working verts, all inside the bar), while 49 connected input
/// verts strand beyond it — the input side fires (and only it) and a
/// jitter re-tiling re-passes the bar. Recovery here is bar-relative,
/// not geometric: the 0.25-wide claw sits below what 50 quads can
/// resolve, so the retry wins by re-tiling inside a coarser bar rather
/// than by covering the tip (no max-z ground truth is asserted — the
/// bar verdict is the contract). No corpus needed (procedural).
fn thin_claw_obj() -> String {
    let mut out = String::new();
    let mut v = |x: f64, y: f64, z: f64| {
        out.push_str(&format!("v {x} {y} {z}\n"));
    };
    for (z, h) in [(-1.5, 1.5), (1.5, 1.5)] {
        for (sx, sy) in [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)] {
            v(sx * h, sy * h, z);
        }
    }
    let sides = 16usize;
    let rings = 24usize;
    let base = 8usize;
    for r in 0..=rings {
        let z = 1.5 + 6.0 * r as f64 / rings as f64;
        for s in 0..sides {
            let a = 2.0 * std::f64::consts::PI * s as f64 / sides as f64;
            v(0.125 * a.cos(), 0.125 * a.sin(), z);
        }
    }
    let tip = base + (rings + 1) * sides;
    v(0.0, 0.0, 7.5);
    let mut f = |a: usize, b: usize, c: usize| {
        out.push_str(&format!("f {} {} {}\n", a + 1, b + 1, c + 1));
    };
    f(0, 2, 1);
    f(0, 3, 2);
    for (a0, b0) in [(0, 1), (1, 2), (2, 3), (3, 0)] {
        f(a0, 4 + b0, 4 + a0);
        f(a0, b0, 4 + b0);
        f(4 + a0, 4 + b0, base + b0);
        f(4 + a0, base + b0, base + a0);
    }
    for r in 0..rings {
        for s in 0..sides {
            let a0 = base + r * sides + s;
            let a1 = base + r * sides + (s + 1) % sides;
            let b0 = base + (r + 1) * sides + s;
            let b1 = base + (r + 1) * sides + (s + 1) % sides;
            f(a0, b1, b0);
            f(a0, a1, b1);
        }
    }
    for s in 0..sides {
        f(
            base + rings * sides + s,
            base + rings * sides + (s + 1) % sides,
            tip,
        );
    }
    out
}

fn load_thin_claw() -> (Vec<Vector3>, Vec<Vec<usize>>) {
    let path = std::env::temp_dir().join(format!(
        "retopo_input_claw_{}_{}.obj",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0),
    ));
    std::fs::write(&path, thin_claw_obj()).expect("claw obj writes");
    let loaded = load_cli_like(&path);
    let _ = std::fs::remove_file(&path);
    loaded.expect("claw obj loads")
}

#[test]
fn thin_claw_wr2_fires_working_retry_and_recovers() {
    let (vertices, triangles) = load_thin_claw();
    let mut remesher = remesh_like_cli(&vertices, &triangles, 50);
    remesher.set_v2_engine(true);
    remesher.set_working_refinement(2.0);
    assert!(remesher.remesh(), "remesh succeeds");
    let reports = remesher.coverage_reports();
    assert_eq!(
        reports.len(),
        1,
        "one island fires the coverage retry, got {} reports",
        reports.len()
    );
    let r = reports[0];
    assert!(
        !r.input_side,
        "working-side coverage check fires at wr=2.0: {r:?}"
    );
    assert!(
        r.recovered,
        "retry recovers the claw (initially {} verts uncovered)",
        r.initial_uncovered
    );
    assert_eq!(r.retries_made, 2, "recovery lands on retry 2: {r:?}");
    assert_eq!(r.kept_attempt, 2, "attempt 2 kept: {r:?}");
    assert_eq!(r.initial_uncovered, 47, "attempt 0 strands 47: {r:?}");
    assert_eq!(r.final_uncovered, 0, "exact-zero residuals: {r:?}");

    let min_z = remesher
        .remeshed_vertices()
        .iter()
        .map(|v| v.z())
        .fold(f64::INFINITY, f64::min);
    let max_z = remesher
        .remeshed_vertices()
        .iter()
        .map(|v| v.z())
        .fold(f64::NEG_INFINITY, f64::max);
    assert!(
        min_z < -1.4 && max_z > 4.0,
        "recovered mesh spans body and claw (min_z={min_z:.2}, max_z={max_z:.2})"
    );
    assert!(!remesher.remeshed_quads().is_empty());
}

#[test]
fn thin_claw_organic_preserves_box_and_claw_across_refinements() {
    let (vertices, triangles) = load_thin_claw();
    for wr in [1.0, 1.75, 2.0] {
        let mut remesher = remesh_like_cli(&vertices, &triangles, 100);
        remesher.set_v2_engine(true);
        remesher.set_working_refinement(wr);
        assert!(remesher.remesh(), "remesh succeeds at wr={wr}");
        let min_z = remesher
            .remeshed_vertices()
            .iter()
            .map(|v| v.z())
            .fold(f64::INFINITY, f64::min);
        let max_z = remesher
            .remeshed_vertices()
            .iter()
            .map(|v| v.z())
            .fold(f64::NEG_INFINITY, f64::max);
        assert!(
            min_z < -1.4 && max_z > 4.0,
            "Organic mode keeps both box body and claw at wr={wr} (min_z={min_z:.2}, max_z={max_z:.2})"
        );
    }
}

fn timing_mesh() -> (Vec<Vector3>, Vec<Vec<usize>>) {
    const W: usize = 64;
    const H: usize = 64;
    let mut vertices = Vec::with_capacity((W + 1) * (H + 1));
    for j in 0..=H {
        for i in 0..=W {
            vertices.push(Vector3::new(
                i as f64,
                j as f64,
                0.1 * (i as f64).sin() * (j as f64).cos(),
            ));
        }
    }
    let mut triangles = Vec::with_capacity(2 * W * H);
    for j in 0..H {
        for i in 0..W {
            let a = j * (W + 1) + i;
            let b = a + 1;
            let c = a + W + 1;
            let d = c + 1;
            triangles.push(vec![a, b, d]);
            triangles.push(vec![a, d, c]);
        }
    }
    (vertices, triangles)
}

#[test]
fn timing_grid_matches_v21() {
    let (vertices, triangles) = timing_mesh();
    for _ in 0..2 {
        let mut remesher = AutoRemesher::new(&vertices, &triangles);
        remesher.set_v2_engine(true);
        remesher.set_target_triangle_count(2000);
        assert!(remesher.remesh(), "timing remesh must succeed");
        assert_eq!(remesher.remeshed_quads().len(), 623, "timing quad count");
        assert_eq!(remesher.remeshed_vertices().len(), 668, "timing vert count");
        let mut checksum = 0.0;
        for v in remesher.remeshed_vertices() {
            checksum += v.x() + v.y() + v.z();
        }
        const PINNED_CHECKSUM: f64 = 43_746.013_510_457_47;
        let tol = 1e-6 * PINNED_CHECKSUM.abs().max(1.0);
        assert!(
            (checksum - PINNED_CHECKSUM).abs() <= tol,
            "checksum diverged: rust={checksum:.17e} pinned={PINNED_CHECKSUM:.17e}"
        );
    }
}

/// Distance from point `p` to triangle `(a, b, c)` (Ericson 5.1.5,
/// f64). Test-local copy (the engine's is private); standard
/// algorithm, pinned by known values below.
fn point_triangle_dist(p: &Vector3, a: &Vector3, b: &Vector3, c: &Vector3) -> f64 {
    let ab = (b.x() - a.x(), b.y() - a.y(), b.z() - a.z());
    let ac = (c.x() - a.x(), c.y() - a.y(), c.z() - a.z());
    let ap = (p.x() - a.x(), p.y() - a.y(), p.z() - a.z());
    let d1 = ab.0 * ap.0 + ab.1 * ap.1 + ab.2 * ap.2;
    let d2 = ac.0 * ap.0 + ac.1 * ap.1 + ac.2 * ap.2;
    if d1 <= 0.0 && d2 <= 0.0 {
        return (ap.0 * ap.0 + ap.1 * ap.1 + ap.2 * ap.2).sqrt();
    }
    let bp = (p.x() - b.x(), p.y() - b.y(), p.z() - b.z());
    let d3 = ab.0 * bp.0 + ab.1 * bp.1 + ab.2 * bp.2;
    let d4 = ac.0 * bp.0 + ac.1 * bp.1 + ac.2 * bp.2;
    if d3 >= 0.0 && d4 <= d3 {
        return (bp.0 * bp.0 + bp.1 * bp.1 + bp.2 * bp.2).sqrt();
    }
    let vc = d1 * d4 - d3 * d2;
    if vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0 {
        let v = d1 / (d1 - d3);
        let q = (
            a.x() + v * ab.0 - p.x(),
            a.y() + v * ab.1 - p.y(),
            a.z() + v * ab.2 - p.z(),
        );
        return (q.0 * q.0 + q.1 * q.1 + q.2 * q.2).sqrt();
    }
    let cp = (p.x() - c.x(), p.y() - c.y(), p.z() - c.z());
    let d5 = ab.0 * cp.0 + ab.1 * cp.1 + ab.2 * cp.2;
    let d6 = ac.0 * cp.0 + ac.1 * cp.1 + ac.2 * cp.2;
    if d6 >= 0.0 && d5 <= d6 {
        return (cp.0 * cp.0 + cp.1 * cp.1 + cp.2 * cp.2).sqrt();
    }
    let vb = d5 * d2 - d1 * d6;
    if vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0 {
        let w = d2 / (d2 - d6);
        let q = (
            a.x() + w * ac.0 - p.x(),
            a.y() + w * ac.1 - p.y(),
            a.z() + w * ac.2 - p.z(),
        );
        return (q.0 * q.0 + q.1 * q.1 + q.2 * q.2).sqrt();
    }
    let va = d3 * d6 - d5 * d4;
    if va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0 {
        let w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        let q = (
            b.x() + w * (c.x() - b.x()) - p.x(),
            b.y() + w * (c.y() - b.y()) - p.y(),
            b.z() + w * (c.z() - b.z()) - p.z(),
        );
        return (q.0 * q.0 + q.1 * q.1 + q.2 * q.2).sqrt();
    }
    let n = (
        ab.1 * ac.2 - ab.2 * ac.1,
        ab.2 * ac.0 - ab.0 * ac.2,
        ab.0 * ac.1 - ab.1 * ac.0,
    );
    let n2 = n.0 * n.0 + n.1 * n.1 + n.2 * n.2;
    if n2 == 0.0 {
        return (ap.0 * ap.0 + ap.1 * ap.1 + ap.2 * ap.2).sqrt();
    }
    ((ap.0 * n.0 + ap.1 * n.1 + ap.2 * n.2).abs() / n2.sqrt()).min(
        (ap.0 * ap.0 + ap.1 * ap.1 + ap.2 * ap.2)
            .sqrt()
            .min((bp.0 * bp.0 + bp.1 * bp.1 + bp.2 * bp.2).sqrt())
            .min((cp.0 * cp.0 + cp.1 * cp.1 + cp.2 * cp.2).sqrt()),
    )
}
