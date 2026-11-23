//! Wave 2 hardening gates: malformed GLB OOM/bounds guards, malformed/NaN
//! engine inputs, and density slice mismatches must return cleanly without panics.

use retopo_core::auto_remesher::AutoRemesher;
use retopo_core::glb::load_glb_positions_and_triangles;
use retopo_core::vector3::Vector3;
use std::path::PathBuf;

fn temp_glb_path(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("retopo-w2-{}-{name}", std::process::id()));
    let _ = std::fs::remove_file(&dir);
    dir
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

#[test]
fn giant_glb_accessor_count_against_tiny_buffer_fails_without_oom() {
    // 2 billion POSITION count backed by 12 bytes: must fail before reserving.
    let json = r#"{
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 12}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 12}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 2000000000, "type": "VEC3"}
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]
    }"#;
    let path = temp_glb_path("giant-count.glb");
    std::fs::write(&path, build_glb(json, &[0u8; 12])).unwrap();
    let mut pos = Vec::new();
    let mut tris = Vec::new();
    let mut err = String::new();
    let ok = load_glb_positions_and_triangles(&path, &mut pos, &mut tris, None, Some(&mut err));
    let _ = std::fs::remove_file(&path);
    assert!(!ok, "giant count GLB must fail cleanly instead of OOM");
    assert!(!err.is_empty());
}

#[test]
fn huge_zero_view_glb_accessor_count_fails_without_oom() {
    // No bufferView + 500M vertices must not allocate gigabytes.
    let json = r#"{
        "asset": {"version": "2.0"},
        "accessors": [
            {"componentType": 5126, "count": 500000000, "type": "VEC3"}
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]
    }"#;
    let path = temp_glb_path("huge-zeroview.glb");
    std::fs::write(&path, build_glb(json, &[])).unwrap();
    let mut pos = Vec::new();
    let mut tris = Vec::new();
    let mut err = String::new();
    let ok = load_glb_positions_and_triangles(&path, &mut pos, &mut tris, None, Some(&mut err));
    let _ = std::fs::remove_file(&path);
    assert!(!ok, "huge zero-view GLB must fail cleanly instead of OOM");
}

#[test]
fn mismatched_density_multipliers_fails_cleanly_without_panic() {
    let verts = vec![
        Vector3::new(0.0, 0.0, 0.0),
        Vector3::new(1.0, 0.0, 0.0),
        Vector3::new(0.0, 1.0, 0.0),
        Vector3::new(0.0, 0.0, 1.0),
    ];
    let tris = vec![vec![0, 1, 2], vec![0, 2, 3], vec![0, 3, 1], vec![1, 3, 2]];
    let mut remesher = AutoRemesher::new(&verts, &tris);
    remesher.set_target_triangle_count(4);
    remesher.set_density_multipliers(&[1.0]); // shorter than vertex count: clears safely instead of panicking
    assert!(
        remesher.remesh(),
        "mismatched density_multipliers should clear safely and remesh without panic"
    );
}

#[test]
fn non_finite_vertex_coordinates_fail_cleanly_without_nan_poison() {
    let verts = vec![
        Vector3::new(f64::NAN, 0.0, 0.0),
        Vector3::new(1.0, 0.0, 0.0),
        Vector3::new(0.0, 1.0, 0.0),
        Vector3::new(0.0, 0.0, 1.0),
    ];
    let tris = vec![vec![0, 1, 2], vec![0, 2, 3], vec![0, 3, 1], vec![1, 3, 2]];
    let mut remesher = AutoRemesher::new(&verts, &tris);
    remesher.set_target_triangle_count(4);
    assert!(
        !remesher.remesh(),
        "non-finite vertex coordinates must be rejected cleanly"
    );
}
