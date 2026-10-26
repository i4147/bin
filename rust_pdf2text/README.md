# rust_pdf2text

Extract text from a PDF file and store it **beside the input file** with a
`.txt` extension.

```sh
rust_pdf2text <input.pdf> [output.txt]
```

`argv[1]` is the PDF to read. The output path is derived by replacing the
input's extension with `.txt`, in the same directory:

| Input                 | Output                |
| --------------------- | --------------------- |
| `report.pdf`          | `report.txt`          |
| `docs/report.pdf`     | `docs/report.txt`     |
| `a/archive.v2.PDF`    | `a/archive.v2.txt`    |
| `notes` (no ext)      | `notes.txt`           |

An existing `.txt` file is overwritten. If extraction fails, no output file
is created or truncated. An optional second argument overrides the output
path. `-h`/`--help` and `-V`/`--version` are supported; use `--` before a
path that starts with `-`.

Exit codes: `0` success, `1` error (missing/invalid input, write failure),
`2` usage error (wrong arguments).

## Build

Requires a stable Rust toolchain (https://rustup.rs). On Termux:
`pkg install rust`. The first build downloads the
[`pdf-extract`](https://crates.io/crates/pdf-extract) crate from crates.io.

```sh
cd rust_pdf2text
cargo build --release
# binary: target/release/rust_pdf2text  (rust_pdf2text.exe on Windows)
```

Or install it on your PATH:

```sh
cargo install --path .   # installs to ~/.cargo/bin
```

## Usage

```sh
# run the built binary: creates docs/report.txt (absolute paths work too)
./target/release/rust_pdf2text docs/report.pdf
./target/release/rust_pdf2text /home/user/report.pdf

# explicit output path
./target/release/rust_pdf2text docs/report.pdf /tmp/report.txt

# or run through Cargo (note the `--` before the PDF path)
cargo run --release -- docs/report.pdf
```

Output on success:

```
Text extracted and saved to docs/report.txt (12345 bytes)
```

## Tests

```sh
cargo test
```

Unit tests cover the output-path rules; the integration tests in
`tests/cli.rs` run the binary against `tests/fixtures/sample.pdf` and check
the `.txt` written beside it.

## Notes / limitations

- Works on PDFs with an embedded text layer. Scanned/image-only PDFs have no
  text to extract: the `.txt` will be empty (a warning is printed) — those
  need OCR first.
- PDFs encrypted with a non-empty password are not supported.
- Output is plain text in reading order as provided by `pdf-extract`;
  complex layouts (columns, tables) are not reconstructed.
