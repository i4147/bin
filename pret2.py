#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from multiprocessing.pool import Pool
from pathlib import Path
import shutil
import subprocess
from typing import Final, NamedTuple
from loguru import logger

MAX_WORKERS: Final[int] = 8
PRETTIER_TIMEOUT_SECONDS: Final[int] = 900
ERROR_DIR_NAME: Final[str] = "error"
EXTENSIONS: Final[tuple[str, ...]] = (
    ".js",
    ".css",
    ".html",
    ".json",
    ".mjs",
    ".cjs",
    ".ts",
    ".jsx",
    ".tsx",
    ".tsm",
    ".jsm",
)
EXCLUDE_PATTERNS: Final[tuple[str, ...]] = (".min.js", ".min.css", ".d.ts")


class FormatResult(NamedTuple):
    path: Path
    success: bool
    error_msg: str | None


class Pret2:
    @staticmethod
    def should_format(path: Path) -> bool:
        if path.suffix not in EXTENSIONS:
            return False
        return all((not path.name.endswith(p) for p in EXCLUDE_PATTERNS))

    @staticmethod
    def get_files_to_format(cwd: Path) -> list[Path]:
        files: list[Path] = []
        for path in cwd.rglob("*"):
            if path.is_dir():
                continue
            if ERROR_DIR_NAME in path.parts:
                continue
            if Pret2.should_format(path):
                files.append(path)
        return files

    @staticmethod
    def unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem: str = path.stem
        suffix: str = path.suffix
        parent: Path = path.parent
        counter: int = 1
        while True:
            candidate: Path = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def move_to_error_folder(path: Path) -> Path | None:
        error_dir: Path = path.parent / ERROR_DIR_NAME
        error_dir.mkdir(exist_ok=True)
        dest: Path = Pret2.unique_path(error_dir / path.name)
        try:
            shutil.move(str(path), str(dest))
            return dest
        except Exception as e:
            logger.error("Failed to move {} to {}: {}", path, dest, e)
            return None

    @staticmethod
    def format_file(path: Path) -> FormatResult:
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                ["prettier", "--write", str(path)],
                capture_output=True,
                text=True,
                timeout=PRETTIER_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode == 0:
                return FormatResult(path, True, None)
            return FormatResult(path, False, result.stderr or result.stdout or "Unknown error")
        except Exception as e:
            return FormatResult(path, False, str(e))

    @staticmethod
    def process_file_wrapper(path: Path) -> FormatResult:
        result: FormatResult = Pret2.format_file(path)
        if not result.success:
            Pret2.move_to_error_folder(result.path)
        return result

    @staticmethod
    def main() -> None:
        cwd: Path = Path.cwd()
        files: list[Path] = Pret2.get_files_to_format(cwd)
        if not files:
            logger.info("No files found to format.")
            return
        logger.info("{} files found", len(files))
        success_count: int = 0
        error_count: int = 0
        with Pool(processes=MAX_WORKERS) as pool:
            for result in pool.imap_unordered(Pret2.process_file_wrapper, files):
                if result.success:
                    logger.info("✅ Formatted: {}", result.path.name)
                    success_count += 1
                else:
                    logger.error("❌ Error: {} | Reason: {}", result.path.name, result.error_msg)
                    error_count += 1
        logger.info("Summary: {} success, {} errors.", success_count, error_count)


if __name__ == "__main__":
    raise SystemExit(Pret2.main())
