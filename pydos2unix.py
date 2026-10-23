#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
from multiprocessing import Pool
from pathlib import Path
from dh import is_binary
from dos2unix import dos2unix
from loguru import logger

MAX_WORKERS: int = 8
CHUNK_SIZE: int = 32768
SKIP_DIRS: t.set[str] = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".coverage",
    ".egg-info",
    ".idea",
}


class Pydos2unix:
    @staticmethod
    def should_skip_dir(directory: Path) -> bool:
        return directory.name in SKIP_DIRS

    @staticmethod
    def convert_file(path: Path) -> tuple[str, bool, str]:
        path = Path(path)
        try:
            if not path.is_file():
                return (str(path), False, "Not a file")
            if is_binary(path):
                return (str(path), False, "Binary file (skipped)")
            try:
                data = f.read_bytes()
                new_data = dos2unix(data)
                if new_data != data:
                    path.write_bytes(new_data)
                    return (str(path), True, "Converted")
                else:
                    return (str(path), True, "Already Unix format")
            except OSError as e:
                return (str(path), False, f"Read/Write error: {e}")
        except Exception as e:
            return (str(path), False, f"Error: {e}")

    @staticmethod
    def find_text_files(paths: list[Path]) -> list[Path]:
        files = []
        for path in paths:
            if path.is_file():
                if not is_binary(path):
                    files.append(path)
            elif path.is_dir():
                for text_file in path.rglob("*"):
                    if any((Pydos2unix.should_skip_dir(parent) for parent in text_file.parents)):
                        continue
                    if text_file.is_file() and (not is_binary(text_file)):
                        files.append(text_file)
        return files

    @staticmethod
    def get_input_paths(input_args: list[str] | None) -> list[Path]:
        if not input_args:
            return [Path.cwd()]
        paths = []
        for arg in input_args:
            path = Path(arg).resolve()
            if path.exists():
                paths.append(path)
            else:
                logger.warning(f"Path does not exist: {arg}")
        return paths

    @staticmethod
    def main() -> int:
        parser = argparse.ArgumentParser(
            description="Convert DOS/Windows line endings (CRLF) to Unix (LF)",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="\nExamples:\n  %(prog)s file.txt\n  %(prog)s file1.txt file2.txt file3.txt\n  %(prog)s /path/to/folder\n  %(prog)s /path/to/folder file.txt\n  %(prog)s                    # Process current directory recursively\n\nSkip Directories: .git, __pycache__, .venv, venv, node_modules, .env, \n                  .pytest_cache, .tox, .mypy_cache, .coverage, dist, build\n        ",
        )
        parser.add_argument(
            "paths", nargs="*", help="Files or folders to process (default: current directory)"
        )
        parser.add_argument("-q", "--quiet", action="store_true", help="Suppress output messages")
        parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output for each file")
        args = parser.parse_args()
        input_paths = Pydos2unix.get_input_paths(args.paths or None)
        if not input_paths:
            logger.error("No valid paths provided")
            return 1
        files_to_process = Pydos2unix.find_text_files(input_paths)
        if not files_to_process:
            if not args.quiet:
                print("No text files found to process")
            return 0
        if not args.quiet and (not args.verbose):
            print(f"Processing {len(files_to_process)} file(s) with 8 worker(s)...")
        converted_count = 0
        skipped_count = 0
        error_count = 0
        try:
            with Pool(processes=MAX_WORKERS) as pool:
                results = pool.map(Pydos2unix.convert_file, files_to_process)
            for path, success, message in results:
                if args.verbose:
                    status = "✓" if success else "✗"
                    print(f"{status} {path}: {message}")
                if success:
                    if "Converted" in message:
                        converted_count += 1
                    else:
                        skipped_count += 1
                else:
                    error_count += 1
            if not args.quiet:
                print()
                print(f"Converted: {converted_count} file(s)")
                print(f"Already Unix format: {skipped_count} file(s)")
                if error_count > 0:
                    logger.warning(f"Errors: {error_count} file(s)")
            return 0 if error_count == 0 else 1
        except KeyboardInterrupt:
            logger.error("\nInterrupted by user")
            return 130
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            return 1


if __name__ == "__main__":
    raise SystemExit(Pydos2unix.main())
