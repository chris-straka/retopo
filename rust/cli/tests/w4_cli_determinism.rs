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

#[test]
fn cli_hardening_edge_cases_exit_cleanly_without_panic() {
    let dir = temp_dir("hardening-edge");
    let valid_obj = fixtures().join("nasty-single-tetra.obj");

    // 1. Truncated OBJ and truncated GLB fail cleanly with exit 1 and no output.
    let trunc_glb = dir.join("trunc.glb");
    std::fs::write(&trunc_glb, b"glTF\x02\x00\x00\x00\x40\x00\x00\x00").unwrap();
    let out_trunc = dir.join("out_trunc.obj");
    let res = Command::new(bin())
        .args([
            "-i",
            trunc_glb.to_str().unwrap(),
            "-o",
            out_trunc.to_str().unwrap(),
        ])
        .output()
        .expect("spawn retopo");
    assert_eq!(res.status.code(), Some(1));
    assert!(!out_trunc.exists());

    // 2. Non-existent output directory and non-existent input directory fail cleanly.
    let missing_out = dir.join("no_such_dir").join("out.obj");
    let res = Command::new(bin())
        .args([
            "-i",
            valid_obj.to_str().unwrap(),
            "-o",
            missing_out.to_str().unwrap(),
        ])
        .output()
        .expect("spawn retopo");
    assert_eq!(res.status.code(), Some(1));

    let missing_in_dir = dir.join("missing_indir");
    let res = Command::new(bin())
        .args([
            "-i",
            missing_in_dir.to_str().unwrap(),
            "-o",
            dir.join("batch_out").to_str().unwrap(),
        ])
        .output()
        .expect("spawn retopo");
    assert_eq!(res.status.code(), Some(1));

    // 3. Empty/malformed constraint files fail with usage error (exit 2).
    let empty_guides = dir.join("empty_guides.txt");
    std::fs::write(&empty_guides, "# comment\n\n").unwrap();
    let inf_density = dir.join("inf_density.txt");
    std::fs::write(&inf_density, "1.0\ninf\n").unwrap();
    let bad_skel = dir.join("bad_skel.txt");
    std::fs::write(&bad_skel, "0 0 0 0 1 0 -1 45\n0 1 0 0 2 0 -0 45\n").unwrap();

    for (flag, file) in [
        ("--guides", &empty_guides),
        ("--density", &inf_density),
        ("--skeleton", &bad_skel),
    ] {
        let res = Command::new(bin())
            .args([
                "-i",
                valid_obj.to_str().unwrap(),
                "-o",
                dir.join("unused.obj").to_str().unwrap(),
                flag,
                file.to_str().unwrap(),
            ])
            .output()
            .expect("spawn retopo");
        assert_eq!(
            res.status.code(),
            Some(2),
            "{flag} bad file stderr={}",
            String::from_utf8_lossy(&res.stderr)
        );
    }

    // 4. --lods overflow & invalid --dipole-* flags fail with exit 2.
    for args in [
        vec!["--lods", "100,4294967297"],
        vec!["--lods", "100,,50"],
        vec!["--dipole-every", "4294967297"],
        vec!["--dipole-ratio", "-1.0"],
        vec!["--keep-odd-faces=yes"],
    ] {
        let mut full = vec![
            "-i".to_string(),
            valid_obj.to_str().unwrap().to_string(),
            "-o".to_string(),
            dir.join("unused2.obj").to_str().unwrap().to_string(),
        ];
        full.extend(args.iter().map(|s| s.to_string()));
        let res = Command::new(bin()).args(&full).output().expect("spawn");
        assert_eq!(res.status.code(), Some(2), "expected exit 2 for {args:?}");
    }

    // 5. Out-of-range numeric flags clamp with warnings on stderr and still run.
    let sphere_obj = fixtures().join("sphere-pole.obj");
    let clamped_out = dir.join("clamped.obj");
    let res = Command::new(bin())
        .args([
            "-i",
            sphere_obj.to_str().unwrap(),
            "-o",
            clamped_out.to_str().unwrap(),
            "--target-quads",
            "400",
            "--edge-scaling",
            "99.0",
            "--adaptivity",
            "-3.0",
            "--quiet",
        ])
        .output()
        .expect("spawn");
    assert_eq!(
        res.status.code(),
        Some(0),
        "clamped run failed: {}",
        String::from_utf8_lossy(&res.stderr)
    );
    let stderr = String::from_utf8_lossy(&res.stderr);
    assert!(stderr.contains("--edge-scaling"));
    assert!(stderr.contains("--adaptivity"));
    assert!(clamped_out.is_file());

    // 6. Valid --lods emits expected rung files cleanly.
    let lods_base = dir.join("sphere.obj");
    let res = Command::new(bin())
        .args([
            "-i",
            sphere_obj.to_str().unwrap(),
            "-o",
            lods_base.to_str().unwrap(),
            "--lods",
            "300,150",
            "--quiet",
        ])
        .output()
        .expect("spawn");
    assert_eq!(
        res.status.code(),
        Some(0),
        "lods run failed: {}",
        String::from_utf8_lossy(&res.stderr)
    );
    assert!(dir.join("sphere_lod0.obj").is_file());
    assert!(dir.join("sphere_lod1.obj").is_file());

    let _ = std::fs::remove_dir_all(&dir);
}
