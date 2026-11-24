//! Constraint files: `--guides`/`--features` polylines, `--density`
//! multipliers and the `--skeleton` bone list.
//!
//! Guide files hold one `x y z` point per line, blank lines separate
//! polylines, `#` starts a comment. Density files hold one multiplier
//! per line with the same comment/blank rules. Error messages echo the
//! offending line's raw bytes (it may not be UTF-8), verbatim plus a
//! newline. All bytes are pinned by the CLI contract goldens.

use crate::error::CliError;
use crate::error::os_reason;
use retopo_core::skeleton::Bone;
use retopo_core::vector3::Vector3;
use std::path::Path;

/// The loaded `--guides` / `--features` / `--density` inputs.
#[derive(Default)]
pub(crate) struct Constraints {
    pub(crate) guides: Vec<Vec<Vector3>>,
    pub(crate) features: Vec<Vec<Vector3>>,
    pub(crate) density: Vec<f64>,
    pub(crate) skeleton: Vec<Bone>,
}

/// Split file bytes into `getline`-style lines: split on `\n`, drop the
/// phantom segment a trailing newline would add, keep `\r` (blank-line
/// detection treats it as blank, and error echoes keep it).
fn split_lines(data: &[u8]) -> Vec<&[u8]> {
    if data.is_empty() {
        return Vec::new();
    }
    let mut lines: Vec<&[u8]> = data.split(|&b| b == b'\n').collect();
    if data.ends_with(b"\n") {
        lines.pop();
    }
    lines
}

fn is_c_space(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\x0b' | b'\x0c' | b'\r')
}

fn is_blank_line(line: &[u8]) -> bool {
    !line.iter().any(|&b| b != b' ' && b != b'\t' && b != b'\r')
}

/// Split a comment-stripped line on C whitespace.
fn split_c_tokens(line: &[u8]) -> Vec<&[u8]> {
    let mut tokens: Vec<&[u8]> = Vec::new();
    let mut i = 0;
    while i < line.len() {
        while i < line.len() && is_c_space(line[i]) {
            i += 1;
        }
        let start = i;
        while i < line.len() && !is_c_space(line[i]) {
            i += 1;
        }
        if i > start {
            tokens.push(&line[start..i]);
        }
    }
    tokens
}

/// Trailing-garbage rule: past the Nth whitespace-separated token only
/// `[ \t\r]` may follow — any other byte (including vertical tab)
/// fails the line.
fn trailing_blank_after(line: &[u8], tokens: usize) -> bool {
    let mut seen = 0;
    let mut j = 0;
    while j < line.len() {
        if is_c_space(line[j]) {
            j += 1;
            continue;
        }
        while j < line.len() && !is_c_space(line[j]) {
            j += 1;
        }
        seen += 1;
        if seen == tokens {
            return line[j..]
                .iter()
                .all(|&b| b == b' ' || b == b'\t' || b == b'\r');
        }
    }
    // Unreachable: callers only ask after checking the arity.
    true
}

/// One full-consumption finite double token: `_`, hex, and `inf`/`nan`
/// are rejected, matching argv number parsing.
fn parse_double_token(token: &[u8]) -> Option<f64> {
    if token.is_empty() || token.contains(&b'_') {
        return None;
    }
    let text = std::str::from_utf8(token).ok()?;
    let body = text.trim_start_matches(['+', '-']);
    if body.starts_with("0x") || body.starts_with("0X") {
        return None;
    }
    text.parse::<f64>().ok().filter(|v| v.is_finite())
}

fn strip_comment(line: &[u8]) -> &[u8] {
    match line.iter().position(|&b| b == b'#') {
        Some(hash) => &line[..hash],
        None => line,
    }
}

fn raw_error(prefix: String, line: &[u8]) -> CliError {
    let mut bytes = prefix.into_bytes();
    bytes.extend_from_slice(line);
    bytes.extend_from_slice(b"'");
    CliError::UsageRaw(bytes)
}

/// Parse a `--guides`/`--features` polyline file. Short (< 2 point)
/// fragments between blank lines are dropped; at least one surviving
/// polyline is required.
pub(crate) fn parse_guides_file(
    path: &Path,
    guides: &mut Vec<Vec<Vector3>>,
    flag_label: &str,
) -> Result<(), CliError> {
    guides.clear();
    let path_display = path.display().to_string();
    let data = std::fs::read(path).map_err(|err| {
        CliError::usage(format!(
            "retopo: error: cannot open {flag_label} file '{path_display}': {}.",
            os_reason(&err)
        ))
    })?;
    let mut current: Vec<Vector3> = Vec::new();
    for (index, raw) in split_lines(&data).iter().enumerate() {
        let line_no = index + 1;
        let line = strip_comment(raw);
        if is_blank_line(line) {
            if current.len() >= 2 {
                guides.push(std::mem::take(&mut current));
            } else {
                current.clear();
            }
            continue;
        }
        let tokens = split_c_tokens(line);
        let mut values = [0.0f64; 3];
        let mut ok = tokens.len() == 3;
        if ok {
            for (k, token) in tokens.iter().enumerate() {
                match parse_double_token(token) {
                    Some(v) => values[k] = v,
                    None => {
                        ok = false;
                        break;
                    }
                }
            }
        }
        if ok {
            ok = trailing_blank_after(line, 3);
        }
        if !ok {
            let prefix = format!(
                "retopo: error: {flag_label} file '{path_display}' line {line_no} expects 'x y z', got '"
            );
            return Err(raw_error(prefix, line));
        }
        current.push(Vector3::new(values[0], values[1], values[2]));
    }
    if current.len() >= 2 {
        guides.push(std::mem::take(&mut current));
    }
    if guides.is_empty() {
        return Err(CliError::usage(format!(
            "retopo: error: {flag_label} file '{path_display}' holds no usable polyline (need 2+ points per polyline)."
        )));
    }
    Ok(())
}

/// Parse a `--density` multiplier file: one number per line.
pub(crate) fn parse_density_file(path: &Path, multipliers: &mut Vec<f64>) -> Result<(), CliError> {
    multipliers.clear();
    let path_display = path.display().to_string();
    let data = std::fs::read(path).map_err(|err| {
        CliError::usage(format!(
            "retopo: error: cannot open --density file '{path_display}': {}.",
            os_reason(&err)
        ))
    })?;
    for (index, raw) in split_lines(&data).iter().enumerate() {
        let line_no = index + 1;
        let line = strip_comment(raw);
        if is_blank_line(line) {
            continue;
        }
        let tokens = split_c_tokens(line);
        let value = match tokens.as_slice() {
            [token] => parse_double_token(token),
            _ => None,
        };
        let Some(value) = value.filter(|_| trailing_blank_after(line, 1)) else {
            let prefix = format!(
                "retopo: error: --density file '{path_display}' line {line_no} expects a number, got '"
            );
            return Err(raw_error(prefix, line));
        };
        multipliers.push(value);
    }
    if multipliers.is_empty() {
        return Err(CliError::usage(format!(
            "retopo: error: --density file '{path_display}' holds no multipliers."
        )));
    }
    Ok(())
}

/// Parse a `--skeleton` file: one bone per line, `hx hy hz tx ty tz
/// parent bend_degrees` (head and tail in the mesh's frame, the 0-based
/// line index of the parent bone or -1 for a root, and how far the joint
/// at the head bends). Same comment/blank rules as the other constraint
/// files; a parent must refer to an earlier bone.
pub(crate) fn parse_skeleton_file(path: &Path, bones: &mut Vec<Bone>) -> Result<(), CliError> {
    bones.clear();
    let path_display = path.display().to_string();
    let data = std::fs::read(path).map_err(|err| {
        CliError::usage(format!(
            "retopo: error: cannot open --skeleton file '{path_display}': {}.",
            os_reason(&err)
        ))
    })?;
    for (index, raw) in split_lines(&data).iter().enumerate() {
        let line_no = index + 1;
        let line = strip_comment(raw);
        if is_blank_line(line) {
            continue;
        }
        let tokens = split_c_tokens(line);
        let mut values = [0.0f64; 8];
        let mut ok = tokens.len() == 8;
        if ok {
            for (k, token) in tokens.iter().enumerate() {
                match parse_double_token(token) {
                    Some(v) => values[k] = v,
                    None => {
                        ok = false;
                        break;
                    }
                }
            }
        }
        let parent = values[6];
        if ok {
            ok = trailing_blank_after(line, 8)
                && parent.fract() == 0.0
                && (parent == -1.0
                    || (parent >= 0.0
                        && !parent.is_sign_negative()
                        && parent < bones.len() as f64));
        }
        if !ok {
            let prefix = format!(
                "retopo: error: --skeleton file '{path_display}' line {line_no} expects 'hx hy hz tx ty tz parent bend_degrees' (parent: an earlier bone's index or -1), got '"
            );
            return Err(raw_error(prefix, line));
        }
        bones.push(Bone {
            head: Vector3::new(values[0], values[1], values[2]),
            tail: Vector3::new(values[3], values[4], values[5]),
            parent: (parent >= 0.0).then_some(parent as usize),
            bend_degrees: values[7],
        });
    }
    if bones.is_empty() {
        return Err(CliError::usage(format!(
            "retopo: error: --skeleton file '{path_display}' holds no bones."
        )));
    }
    Ok(())
}

#[cfg(test)]
mod constraint_tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_file(name: &str, content: &[u8]) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("retopo-cf-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        std::fs::write(&path, content).unwrap();
        path
    }

    #[test]
    fn rejects_empty_and_malformed_guides() {
        let mut out = Vec::new();
        let empty = temp_file("empty_g.txt", b"# comment only\n\n");
        assert!(parse_guides_file(&empty, &mut out, "--guides").is_err());

        let inf = temp_file("inf_g.txt", b"0 0 0\n1 inf 0\n");
        assert!(parse_guides_file(&inf, &mut out, "--guides").is_err());

        let vtab = temp_file("vtab_g.txt", b"0 0 0\x0b\n1 2 3\n");
        assert!(parse_guides_file(&vtab, &mut out, "--guides").is_err());
    }

    #[test]
    fn rejects_empty_nonfinite_and_hex_density() {
        let mut out = Vec::new();
        let empty = temp_file("empty_d.txt", b"   \n# c\n");
        assert!(parse_density_file(&empty, &mut out).is_err());

        let nan = temp_file("nan_d.txt", b"1.0\nnan\n");
        assert!(parse_density_file(&nan, &mut out).is_err());

        let hex = temp_file("hex_d.txt", b"0x10\n");
        assert!(parse_density_file(&hex, &mut out).is_err());
    }

    #[test]
    fn rejects_invalid_skeleton_parents_and_empty_files() {
        let mut bones = Vec::new();
        let empty = temp_file("empty_s.txt", b"# empty\n");
        assert!(parse_skeleton_file(&empty, &mut bones).is_err());

        let neg_zero = temp_file("negzero_s.txt", b"0 0 0 0 1 0 -1 45\n0 1 0 0 2 0 -0 45\n");
        assert!(parse_skeleton_file(&neg_zero, &mut bones).is_err());

        let frac_parent = temp_file("frac_s.txt", b"0 0 0 0 1 0 -1 45\n0 1 0 0 2 0 0.5 45\n");
        assert!(parse_skeleton_file(&frac_parent, &mut bones).is_err());
    }
}
