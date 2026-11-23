//! Wave 4H gate-first RED -> GREEN tests: repeat-run bit/byte determinism
//! (especially `quad_fill::ease_flat_corners` and `AutoRemesher` + GLB export)
//! and degenerate/empty/out-of-range/corrupt edge-case handling.

use retopo_core::auto_remesher::AutoRemesher;
use retopo_core::decimator;
use retopo_core::glb::{load_glb_positions_and_triangles, save_glb_with_uvs};
use retopo_core::quad_fill::pair_odd_faces;
use retopo_core::vector3::Vector3;
use std::path::PathBuf;

struct RemeshSnapshot {
    vbits: Vec<[u64; 3]>,
    quads: Vec<Vec<usize>>,
    uvbits: Vec<[u64; 2]>,
    glb_bytes: Vec<u8>,
}

fn temp_file(name: &str) -> PathBuf {
    let p = std::env::temp_dir().join(format!("retopo-w4-{}-{name}", std::process::id()));
    let _ = std::fs::remove_file(&p);
    p
}

fn build_glb(json_str: &str, bin: &[u8]) -> Vec<u8> {
    let mut jb = json_str.as_bytes().to_vec();
    while !jb.len().is_multiple_of(4) {
        jb.push(b' ');
    }
    let mut bb = bin.to_vec();
    while !bb.len().is_multiple_of(4) {
        bb.push(0);
    }
    let total = (12 + 8 + jb.len() + 8 + bb.len()) as u32;
    let mut out = Vec::with_capacity(total as usize);
    out.extend_from_slice(b"glTF");
    out.extend_from_slice(&2u32.to_le_bytes());
    out.extend_from_slice(&total.to_le_bytes());
    out.extend_from_slice(&(jb.len() as u32).to_le_bytes());
    out.extend_from_slice(&0x4E4F534Au32.to_le_bytes());
    out.extend_from_slice(&jb);
    out.extend_from_slice(&(bb.len() as u32).to_le_bytes());
    out.extend_from_slice(&0x004E4942u32.to_le_bytes());
    out.extend_from_slice(&bb);
    out
}

fn build_asymmetric_icosahedron(offset: Vector3) -> (Vec<Vector3>, Vec<Vec<usize>>) {
    let phi = (1.0 + 5.0_f64.sqrt()) * 0.5;
    let raw = [
        (-1.0, phi, 0.0),
        (1.0, phi, 0.0),
        (-1.0, -phi, 0.0),
        (1.0, -phi, 0.0),
        (0.0, -1.0, phi),
        (0.0, 1.0, phi),
        (0.0, -1.0, -phi),
        (0.0, 1.0, -phi),
        (phi, 0.0, -1.0),
        (phi, 0.0, 1.0),
        (-phi, 0.0, -1.0),
        (-phi, 0.0, 1.0),
    ];
    let verts = raw
        .iter()
        .enumerate()
        .map(|(i, &(x, y, z))| {
            let s = 1.0 + 0.017 * (i as f64);
            Vector3::new(x * s + offset.x(), y * s + offset.y(), z * s + offset.z())
        })
        .collect();
    let tris = vec![
        vec![0, 11, 5],
        vec![0, 5, 1],
        vec![0, 1, 7],
        vec![0, 7, 10],
        vec![0, 10, 11],
        vec![1, 5, 9],
        vec![5, 11, 4],
        vec![11, 10, 2],
        vec![10, 7, 6],
        vec![7, 1, 8],
        vec![3, 9, 4],
        vec![3, 4, 2],
        vec![3, 2, 6],
        vec![3, 6, 8],
        vec![3, 8, 9],
        vec![4, 9, 5],
        vec![2, 4, 11],
        vec![6, 2, 10],
        vec![8, 6, 7],
        vec![9, 8, 1],
    ];
    (verts, tris)
}

#[test]
fn quad_fill_pair_odd_faces_is_bit_identical_across_runs() {
    // Triangle [10, 0, 5] + adjacent pentagon [0, 1, 2, 6, 5] have outline
    // [10, 0, 1, 2, 6, 5]. Putting v[2] slightly below y=0 makes chords (0, 6)
    // and (10, 2) concave so chord (1, 5) is the unique valid quadrangulation
    // ([10, 0, 1, 5] and [1, 2, 6, 5]), giving vertex 0 a ~161.8 deg corner so
    // `ease_flat_corners` runs. Vertex 13 sits at large positive X so adding
    // neighbors {10, 1, 5, 13} in non-deterministic HashSet order would drop low
    // bits of the smaller coordinates vs deterministic BTreeSet order.
    let base_verts = vec![
        Vector3::new(0.0, 0.0, 0.0),      // 0: flat corner eased (~161.8 deg)
        Vector3::new(1.0, 0.16, 0.0),     // 1
        Vector3::new(1.8, -0.05, 0.0),    // 2: ensures (1, 5) is the unique convex cut
        Vector3::new(0.0, 0.0, 0.0),      // 3 (unused placeholder)
        Vector3::new(0.0, 0.0, 0.0),      // 4 (unused placeholder)
        Vector3::new(-0.2, 1.0, 0.0),     // 5
        Vector3::new(1.0, 1.0, 0.0),      // 6
        Vector3::new(0.0, 0.0, 0.0),      // 7 (unused placeholder)
        Vector3::new(0.0, 0.0, 0.0),      // 8 (unused placeholder)
        Vector3::new(0.0, 0.0, 0.0),      // 9 (unused placeholder)
        Vector3::new(-1.0, 0.16, 0.0),    // 10: ~161.8 deg corner at v0
        Vector3::new(-1.11, -1.03, 0.02), // 11
        Vector3::new(1.09, -1.05, 0.03),  // 12
        Vector3::new(1.0e13, -10.0, 0.0), // 13: large neighbor of v0
    ];
    let base_faces = vec![
        vec![10, 0, 5],
        vec![0, 1, 2, 6, 5],
        // Lower quads sharing edges (10, 0) and (0, 1) give v0 neighbors {10, 1, 5, 13}.
        vec![11, 13, 0, 10],
        vec![13, 12, 1, 0],
    ];

    let mut ref_verts = base_verts.clone();
    let mut ref_faces = base_faces.clone();
    let ref_fixed = pair_odd_faces(&mut ref_verts, &mut ref_faces, 8);
    assert_eq!(ref_fixed, 2, "expected triangle+pentagon pair to close");
    assert!(
        ref_verts.iter().zip(&base_verts).any(|(a, b)| {
            a.x().to_bits() != b.x().to_bits()
                || a.y().to_bits() != b.y().to_bits()
                || a.z().to_bits() != b.z().to_bits()
        }),
        "ease_flat_corners must have triggered on the ~173 deg corner"
    );

    for run in 0..64 {
        let mut v = base_verts.clone();
        let mut f = base_faces.clone();
        let fixed = pair_odd_faces(&mut v, &mut f, 8);
        assert_eq!(fixed, ref_fixed, "run {run} paired count drifted");
        assert_eq!(f, ref_faces, "run {run} face topology drifted");
        assert_eq!(v.len(), ref_verts.len());
        for (i, (got, want)) in v.iter().zip(&ref_verts).enumerate() {
            assert_eq!(
                (got.x().to_bits(), got.y().to_bits(), got.z().to_bits()),
                (want.x().to_bits(), want.y().to_bits(), want.z().to_bits()),
                "run {run} vertex {i} drifted at bit level: {got:?} vs {want:?}"
            );
        }
    }
}

#[test]
fn auto_remesher_fill_odd_faces_and_glb_export_repeat_run_byte_identical() {
    let (verts, tris) = build_asymmetric_icosahedron(Vector3::new(0.25, -0.5, 0.75));
    let glb_a = temp_file("det-a.glb");
    let glb_b = temp_file("det-b.glb");

    let run_once = |out_path: &PathBuf| -> RemeshSnapshot {
        let mut remesher = AutoRemesher::new(&verts, &tris);
        remesher.set_target_triangle_count(320);
        remesher.set_fill_odd_faces(true);
        remesher.set_compute_remeshed_uvs(true);
        remesher.set_quiet(true);
        assert!(
            remesher.remesh(),
            "AutoRemesher failed on asymmetric icosahedron"
        );
        assert!(
            save_glb_with_uvs(
                out_path,
                "retopo-w4-test",
                remesher.remeshed_vertices(),
                remesher.remeshed_quads(),
                remesher.remeshed_vertex_uvs(),
            ),
            "save_glb_with_uvs failed"
        );
        let glb_bytes = std::fs::read(out_path).expect("read saved glb");
        let _ = std::fs::remove_file(out_path);
        let vbits = remesher
            .remeshed_vertices()
            .iter()
            .map(|p| [p.x().to_bits(), p.y().to_bits(), p.z().to_bits()])
            .collect();
        let uvbits = remesher
            .remeshed_vertex_uvs()
            .iter()
            .map(|u| [u.x().to_bits(), u.y().to_bits()])
            .collect();
        RemeshSnapshot {
            vbits,
            quads: remesher.remeshed_quads().to_vec(),
            uvbits,
            glb_bytes,
        }
    };

    let a = run_once(&glb_a);
    let b = run_once(&glb_b);
    assert!(!a.quads.is_empty());
    assert_eq!(
        a.vbits, b.vbits,
        "remeshed vertex bits drifted between runs"
    );
    assert_eq!(a.quads, b.quads, "remeshed quad faces drifted between runs");
    assert_eq!(a.uvbits, b.uvbits, "remeshed UV bits drifted between runs");
    assert_eq!(
        a.glb_bytes, b.glb_bytes,
        "saved GLB bytes must be byte-identical across runs"
    );
}

#[test]
fn decimator_simplify_degenerate_and_malformed_inputs_do_not_panic() {
    // Empty input
    let (out, err) = decimator::simplify(&[], &[], None, 0);
    assert!(out.is_empty());
    assert_eq!(err, 0.0);

    // Non-multiple-of-3 indices / positions
    let pos = [0.0f32, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
    let (out, _) = decimator::simplify(&pos, &[0, 1], None, 0);
    assert!(out.len() <= 2);

    let (out, _) = decimator::simplify(&[0.0f32, 1.0], &[0, 1, 2], None, 0);
    assert!(out.is_empty());

    // Out-of-bounds vertex index
    let (out, _) = decimator::simplify(&pos, &[0, 1, 999], None, 0);
    assert!(out.len() <= 3);

    // target_index_count > indices.len()
    let (out, _) = decimator::simplify(&pos, &[0, 1, 2], None, 99);
    assert_eq!(out, vec![0, 1, 2]);

    // Mismatched vertex_lock length
    let (out, _) = decimator::simplify(&pos, &[0, 1, 2], Some(&[1]), 0);
    assert!(out.len() <= 3);

    // All zero-area collinear triangles
    let col_pos = [
        0.0f32, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0, 3.0, 0.0, 0.0,
    ];
    let col_idx = [0u32, 1, 2, 1, 2, 3];
    let (out, err) = decimator::simplify(&col_pos, &col_idx, None, 3);
    assert!(out.len().is_multiple_of(3));
    assert!(err.is_finite());
}

#[test]
fn auto_remesher_all_zero_area_and_infinite_vertices_produce_no_corrupt_output() {
    let col_verts = vec![
        Vector3::new(0.0, 0.0, 0.0),
        Vector3::new(1.0, 0.0, 0.0),
        Vector3::new(2.0, 0.0, 0.0),
        Vector3::new(3.0, 0.0, 0.0),
    ];
    let col_tris = vec![vec![0, 1, 2], vec![1, 2, 3]];
    let mut r = AutoRemesher::new(&col_verts, &col_tris);
    r.set_target_triangle_count(4);
    let ok = r.remesh();
    assert!(
        !ok || r.remeshed_quads().is_empty(),
        "all-zero-area mesh must fail or produce empty output"
    );

    let inf_verts = vec![
        Vector3::new(f64::INFINITY, 0.0, 0.0),
        Vector3::new(1.0, 0.0, 0.0),
        Vector3::new(0.0, 1.0, 0.0),
        Vector3::new(0.0, 0.0, 1.0),
    ];
    let tris = vec![vec![0, 1, 2], vec![0, 2, 3], vec![0, 3, 1], vec![1, 3, 2]];
    let mut r2 = AutoRemesher::new(&inf_verts, &tris);
    r2.set_target_triangle_count(4);
    assert!(!r2.remesh(), "infinity vertex must fail cleanly");
}

#[test]
fn glb_loader_corrupt_truncated_and_non_triangle_modes_fail_cleanly() {
    let cases: [(&str, Vec<u8>); 4] = [
        ("empty.glb", vec![]),
        ("badmagic.glb", b"NOT_GLB_FILE_123456".to_vec()),
        (
            "truncated.glb",
            b"glTF\x02\x00\x00\x00\xff\x00\x00\x00\x04\x00\x00\x00JSON".to_vec(),
        ),
        ("badjson.glb", build_glb("{not valid json", &[0u8; 16])),
    ];
    for (name, bytes) in cases {
        let p = temp_file(name);
        std::fs::write(&p, &bytes).unwrap();
        let mut pos = Vec::new();
        let mut tris = Vec::new();
        let mut err = String::new();
        let ok = load_glb_positions_and_triangles(&p, &mut pos, &mut tris, None, Some(&mut err));
        let _ = std::fs::remove_file(&p);
        assert!(!ok, "{name} must fail cleanly");
        assert!(!err.is_empty(), "{name} must report an error");
        assert!(pos.is_empty() && tris.is_empty());
    }

    // Valid GLB header with POINTS (mode=0) primitive: must load 0 triangles without panic.
    let pts_json = r#"{
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 0}]}]
    }"#;
    let p = temp_file("points.glb");
    std::fs::write(&p, build_glb(pts_json, &[0u8; 36])).unwrap();
    let mut pos = Vec::new();
    let mut tris = Vec::new();
    let ok = load_glb_positions_and_triangles(&p, &mut pos, &mut tris, None, None);
    let _ = std::fs::remove_file(&p);
    assert!(
        !ok || tris.is_empty(),
        "POINTS primitive must yield no triangles cleanly"
    );
}
