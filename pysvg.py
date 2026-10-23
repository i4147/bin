#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SVG_EXTS: set[str] = {".svg"}
SKIP_DIRS: set[str] = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
GREEN: str = "\x1b[32m"
DIM: str = "\x1b[2m"
RESET: str = "\x1b[0m"


@dataclass
class Result:
    path: Path
    before: int
    after: int
    status: str
    detail: str = ""

    @property
    def saved(self) -> int:
        return max(self.before - self.after, 0)


class Pysvg:
    @staticmethod
    def walk_svgs(root: Path) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in SVG_EXTS and (not p.is_symlink()) and p.is_file():
                    yield p

    @staticmethod
    def run_svgo(
        path: Path, output: Path, multipass: bool, precision: int | None, config: str | None
    ) -> tuple[int, str]:
        cmd = ["svgo", "-q", "-i", str(path), "-o", str(output)]
        if multipass:
            cmd.append("--multipass")
        if precision is not None:
            cmd.extend(["--precision", str(precision)])
        if config:
            cmd.extend(["--config", config])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return (-1, "svgo not found")
        err = (proc.stderr or proc.stdout).strip()[:200]
        return (proc.returncode, err)

    @staticmethod
    def optimize_one(args: tuple[Path, bool, int | None, str | None, bool]) -> Result:
        path, multipass, precision, config, dry_run = args
        tmp_out: Path | None = None
        try:
            before = path.stat().st_size
            with tempfile.NamedTemporaryFile(suffix=path.suffix, dir=path.parent, delete=False) as tmp:
                tmp_out = Path(tmp.name)
            rc, err = Pysvg.run_svgo(path, tmp_out, multipass, precision, config)
            if rc == -1:
                return Result(path, before, before, "error", err)
            if rc != 0:
                return Result(path, before, before, "error", err or f"exit {rc}")
            after = tmp_out.stat().st_size
            if after >= before:
                return Result(path, before, after, "unchanged")
            if not dry_run:
                shutil.copymode(path, tmp_out)
                os.replace(tmp_out, path)
                tmp_out = None
            return Result(path, before, after, "optimized")
        except Exception as e:
            return Result(path, 0, 0, "error", f"{type(e).__name__}: {e}")
        finally:
            if tmp_out is not None:
                try:
                    tmp_out.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def main() -> int:
        ap = argparse.ArgumentParser()
        ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
        ap.add_argument("-n", "--dry-run", action="store_true")
        ap.add_argument("--root", default=".")
        ap.add_argument("--config", default=None, help="path to svgo.config.js")
        ap.add_argument("--precision", type=int, default=None)
        ap.add_argument("--no-multipass", action="store_true")
        args = ap.parse_args()
        if not shutil.which("svgo"):
            print("Error: svgo not found in PATH. Install with: npm i -g svgo", file=sys.stderr)
            return 1
        root = Path(args.root).resolve()
        files = list(Pysvg.walk_svgs(root))
        if not files:
            print(f"No SVGs found under {root}")
            return 0
        multipass = not args.no_multipass
        print(f"Root: {root}")
        print(
            f"Files: {len(files)} | multipass: {multipass} | precision: {args.precision or 'default'} | config: {args.config or 'default'} | jobs: {args.jobs} | dry-run: {args.dry_run}"
        )
        print("-" * 60)
        tasks = [(p, multipass, args.precision, args.config, args.dry_run) for p in files]
        total_saved = 0
        optimized = unchanged = errors = 0
        with mp.Pool(processes=args.jobs) as pool:
            for r in pool.imap_unordered(Pysvg.optimize_one, tasks):
                if r.status == "optimized":
                    optimized += 1
                    total_saved += r.saved
                    print(f"{r.path.name} {GREEN}{r.saved}{RESET}")
                elif r.status == "unchanged":
                    unchanged += 1
                    print(f"{r.path.name} {DIM}unchanged{RESET}")
                else:
                    errors += 1
                    print(f"{r.path.name} {DIM}error: {r.detail}{RESET}", file=sys.stderr)
        print("-" * 60)
        if args.dry_run:
            print(f"Dry run complete. {len(files)} files scanned.")
        else:
            print(f"Optimized: {optimized} | Unchanged: {unchanged} | Errors: {errors}")
            print(f"Space freed: {GREEN}{total_saved:,} bytes{RESET} ({total_saved / 1024 / 1024:.2f} MiB)")
        return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(Pysvg.main())
