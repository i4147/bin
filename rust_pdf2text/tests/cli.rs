//! End-to-end tests: run the compiled binary on `tests/fixtures/sample.pdf`
//! and check the `.txt` file written beside the copied input.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_rust_pdf2text")
}

fn fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/sample.pdf")
}

/// Unique empty directory under the system temp dir for one test run.
fn scratch_dir(tag: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system time before Unix epoch")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "rust_pdf2text-{tag}-{}-{nanos}",
        std::process::id()
    ));
    fs::create_dir_all(&dir).expect("create scratch dir");
    dir
}

fn run(args: &[&std::ffi::OsStr]) -> Output {
    Command::new(binary())
        .args(args)
        .output()
        .expect("run rust_pdf2text binary")
}

#[test]
fn writes_txt_beside_input_pdf() {
    let dir = scratch_dir("beside");
    let pdf = dir.join("sample.pdf");
    fs::copy(fixture(), &pdf).expect("copy fixture PDF");

    let output = run(&[pdf.as_os_str()]);
    assert!(
        output.status.success(),
        "stdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let txt = dir.join("sample.txt");
    let text = fs::read_to_string(&txt).expect("sample.txt written beside sample.pdf");
    assert!(text.contains("Hello"), "extracted text: {text:?}");
    assert!(text.contains("rust_pdf2text"), "extracted text: {text:?}");
    assert!(text.contains("sample"), "extracted text: {text:?}");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn honours_explicit_output_path() {
    let dir = scratch_dir("explicit");
    let pdf = dir.join("sample.pdf");
    fs::copy(fixture(), &pdf).expect("copy fixture PDF");
    let out = dir.join("elsewhere.txt");

    let output = run(&[pdf.as_os_str(), out.as_os_str()]);
    assert!(output.status.success());
    assert!(out.exists(), "explicit output file created");
    assert!(!dir.join("sample.txt").exists(), "default output not created");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn missing_input_fails_without_creating_txt() {
    let dir = scratch_dir("missing");
    let pdf = dir.join("missing.pdf");

    let output = run(&[pdf.as_os_str()]);
    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("not found"),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!dir.join("missing.txt").exists());

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn no_arguments_is_a_usage_error() {
    let output = run(&[]);
    assert_eq!(output.status.code(), Some(2));
}
