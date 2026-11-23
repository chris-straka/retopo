//! Wave 4H gate-first CLI determinism & edge-case tests across separate OS processes.

use std::path::{Path, PathBuf};
use std::process::Command;

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_retopo"))
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures")
}

fn temp_dir(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("retopo-w4-cli-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn strip_timing_lines(report: &str) -> Vec<String> {
    report
        .lines()
        .filter(|ln| !ln.contains("Total time:") && !ln.trim_end().ends_with(" ms"))
        .map(|ln| ln.to_string())
        .collect()
}

#[test]
fn cli_separate_processes_produce_byte_identical_glb_and_report() {
    let dir = temp_dir("repeat");
    let src = fixtures().join("finger-split.obj");
    let r1_dir = dir.join("run1");
    let r2_dir = dir.join("run2");
    std::fs::create_dir_all(&r1_dir).unwrap();
    std::fs::create_dir_all(&r2_dir).unwrap();

    for sub in [&r1_dir, &r2_dir] {
        let res = Command::new(bin())
            .current_dir(sub)
            .args([
                "-i",
                src.to_str().unwrap(),
                "-o",
                "out.glb",
                "--target-quads",
                "400",
                "--uvs",
                "--quiet",
                "--report",
                "rep.txt",
            ])
            .output()
            .expect("run retopo cli");
        assert!(
            res.status.success(),
            "retopo failed: {}",
            String::from_utf8_lossy(&res.stderr)
        );
    }

    let b1 = std::fs::read(r1_dir.join("out.glb")).expect("read run1 out.glb");
    let b2 = std::fs::read(r2_dir.join("out.glb")).expect("read run2 out.glb");
    assert!(!b1.is_empty());
    assert_eq!(b1, b2, "repeat-run GLB output must be byte-identical");

    let r1 = std::fs::read_to_string(r1_dir.join("rep.txt")).expect("read run1 rep.txt");
    let r2 = std::fs::read_to_string(r2_dir.join("rep.txt")).expect("read run2 rep.txt");
    assert_eq!(
        strip_timing_lines(&r1),
        strip_timing_lines(&r2),
        "non-timing report lines must be identical across runs"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn cli_corrupt_glb_and_zero_area_obj_fail_without_writing_corrupt_output() {
    let dir = temp_dir("corrupt");
    let bad_glb = dir.join("bad.glb");
    let out_a = dir.join("out_a.glb");
    std::fs::write(&bad_glb, b"CORRUPT_GLB_BYTES").unwrap();

    let res = Command::new(bin())
        .args([
            "-i",
            bad_glb.to_str().unwrap(),
            "-o",
            out_a.to_str().unwrap(),
        ])
        .output()
        .expect("spawn retopo");
    assert!(!res.status.success(), "corrupt GLB must fail non-zero");
    assert!(res.status.code().is_some(), "must exit cleanly, not signal");
    assert!(!out_a.exists(), "must not write silent corrupt output GLB");

    let zero_obj = fixtures().join("nasty-all-zeroarea.obj");
    let out_b = dir.join("out_b.glb");
    let res2 = Command::new(bin())
        .args([
            "-i",
            zero_obj.to_str().unwrap(),
            "-o",
            out_b.to_str().unwrap(),
        ])
        .output()
        .expect("spawn retopo");
    assert!(
        !res2.status.success(),
        "all-zero-area OBJ must fail non-zero"
    );
    assert!(
        res2.status.code().is_some(),
        "must exit cleanly, not signal"
    );
    assert!(!out_b.exists(), "must not write silent corrupt output GLB");
    let _ = std::fs::remove_dir_all(&dir);
}
