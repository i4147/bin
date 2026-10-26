//! rust_pdf2text — extract text from a PDF into a `.txt` file beside it.
//!
//! Usage:
//!     rust_pdf2text <input.pdf> [output.txt]
//!
//! Without an explicit output path the text is written next to the input
//! with its extension replaced by `.txt`, e.g. `docs/report.pdf` becomes
//! `docs/report.txt`.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

/// Command-line arguments after parsing.
struct Cli {
    input: PathBuf,
    output: Option<PathBuf>,
}

enum ParseResult {
    Run(Cli),
    Exit(ExitCode),
}

/// Program name for usage messages: argv[0]'s file name, or the crate name.
fn prog_name() -> String {
    env::args_os()
        .next()
        .and_then(|arg0| {
            Path::new(&arg0)
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
        })
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| env!("CARGO_PKG_NAME").to_string())
}

fn usage(prog: &str) -> String {
    format!(
        "Extract text from a PDF file and save it beside the input as .txt.\n\
         \n\
         Usage:\n\
         \x20   {prog} <input.pdf> [output.txt]\n\
         \x20   {prog} -h | --help\n\
         \x20   {prog} -V | --version\n\
         \n\
         Arguments:\n\
         \x20   <input.pdf>    PDF file to extract text from (argv[1])\n\
         \x20   [output.txt]   Optional output path.\n\
         \x20                  Default: <input> with its extension replaced by .txt\n\
         \x20                  (e.g. docs/report.pdf -> docs/report.txt)\n\
         \n\
         Options:\n\
         \x20   -h, --help     Show this help and exit\n\
         \x20   -V, --version  Show version and exit\n\
         \x20   --             Treat the next argument as a path, even if it starts with '-'"
    )
}

/// Parse argv (excluding argv[0]). `--help`/`--version` and usage errors
/// print their message here and return `ParseResult::Exit`.
fn parse_args(prog: &str, raw_args: Vec<OsString>) -> ParseResult {
    let mut positional: Vec<PathBuf> = Vec::new();
    let mut end_of_flags = false;

    for arg in raw_args {
        if !end_of_flags {
            match arg.to_str() {
                Some("-h") | Some("--help") => {
                    println!("{}", usage(prog));
                    return ParseResult::Exit(ExitCode::SUCCESS);
                }
                Some("-V") | Some("--version") => {
                    println!("{} {}", env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"));
                    return ParseResult::Exit(ExitCode::SUCCESS);
                }
                Some("--") => {
                    end_of_flags = true;
                    continue;
                }
                Some(flag) if flag.len() > 1 && flag.starts_with('-') => {
                    eprintln!("error: unknown option: {flag}\n\n{}", usage(prog));
                    return ParseResult::Exit(ExitCode::from(2));
                }
                _ => {}
            }
        }
        positional.push(PathBuf::from(arg));
    }

    match positional.len() {
        1 | 2 => {
            let mut args = positional.into_iter();
            let input = args.next().expect("len checked: at least one argument");
            ParseResult::Run(Cli {
                input,
                output: args.next(),
            })
        }
        0 => {
            eprintln!("error: missing <input.pdf> argument\n\n{}", usage(prog));
            ParseResult::Exit(ExitCode::from(2))
        }
        _ => {
            eprintln!("error: too many arguments\n\n{}", usage(prog));
            ParseResult::Exit(ExitCode::from(2))
        }
    }
}

/// Default output path: beside the input, with the extension set to `txt`.
fn output_path_for(input: &Path) -> PathBuf {
    input.with_extension("txt")
}

/// True if both paths refer to the same file (lexically, or after
/// canonicalizing). The canonical check catches spellings like
/// `./report.pdf` vs `report.pdf` when the output path already exists.
fn is_same_file(a: &Path, b: &Path) -> bool {
    if a == b {
        return true;
    }
    match (fs::canonicalize(a), fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Extract the text of `input` and write it to `output` (or to the default
/// `.txt` path beside `input`). On success returns the output path and the
/// number of bytes written. The output file is only touched after the text
/// has been extracted successfully.
fn convert(input: &Path, output: Option<&Path>) -> Result<(PathBuf, usize), String> {
    if !input.exists() {
        return Err(format!("input file not found: {}", input.display()));
    }
    if !input.is_file() {
        return Err(format!("input is not a regular file: {}", input.display()));
    }

    let is_pdf = input
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| ext.eq_ignore_ascii_case("pdf"))
        .unwrap_or(false);
    if !is_pdf {
        eprintln!(
            "warning: input does not have a .pdf extension: {}",
            input.display()
        );
    }

    let output = match output {
        Some(path) => path.to_path_buf(),
        None => output_path_for(input),
    };
    if is_same_file(input, &output) {
        return Err(format!(
            "output would overwrite the input file: {}",
            output.display()
        ));
    }

    let text = pdf_extract::extract_text(input)
        .map_err(|e| format!("failed to extract text from {}: {e}", input.display()))?;

    let bytes = text.len();
    fs::write(&output, text)
        .map_err(|e| format!("failed to write {}: {e}", output.display()))?;

    Ok((output, bytes))
}

fn main() -> ExitCode {
    let prog = prog_name();
    let cli = match parse_args(&prog, env::args_os().skip(1).collect()) {
        ParseResult::Run(cli) => cli,
        ParseResult::Exit(code) => return code,
    };

    match convert(&cli.input, cli.output.as_deref()) {
        Ok((output, bytes)) => {
            println!(
                "Text extracted and saved to {} ({bytes} bytes)",
                output.display()
            );
            if bytes == 0 {
                eprintln!(
                    "warning: no text was extracted; the PDF may be scanned \
                     images (needs OCR) or empty"
                );
            }
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("error: {message}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replaces_pdf_extension_and_keeps_directory() {
        assert_eq!(
            output_path_for(Path::new("/docs/report.pdf")),
            PathBuf::from("/docs/report.txt")
        );
    }

    #[test]
    fn keeps_dotted_stem_and_handles_uppercase_extension() {
        assert_eq!(
            output_path_for(Path::new("a/archive.v2.PDF")),
            PathBuf::from("a/archive.v2.txt")
        );
    }

    #[test]
    fn appends_extension_when_input_has_none() {
        assert_eq!(
            output_path_for(Path::new("notes")),
            PathBuf::from("notes.txt")
        );
    }

    #[test]
    fn same_path_is_same_file() {
        assert!(is_same_file(
            Path::new("no_such_input.pdf"),
            Path::new("no_such_input.pdf")
        ));
        assert!(!is_same_file(
            Path::new("no_such_input.pdf"),
            Path::new("no_such_input.txt")
        ));
    }
}
