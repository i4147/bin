#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
import fnmatch
import logging
import mmap
from multiprocessing import Pool
import os
from pathlib import Path
from tqdm import tqdm


class Pydos2unix2:
    @staticmethod
    def is_binary(path: Path) -> bool:
        try:
            with path.open("rb") as f:
                chunk = f.read(4096)
            if b"\x00" in chunk:
                return True
            text_chars = bytearray(range(32, 127)) + b"\n\r\t\x08"
            nontext = sum((1 for b in chunk if b not in text_chars))
            return nontext / max(len(chunk), 1) > 0.3
        except Exception:
            return True

    @staticmethod
    def needs_conversion(path: Path) -> bool:
        try:
            with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return mm.find(b"\r\n") != -1
        except Exception:
            return False

    @staticmethod
    def convert_in_place(path: Path) -> None:
        with path.open("r+b") as f, mmap.mmap(f.fileno(), 0) as mm:
            data = mm[:]
            new = data.replace(b"\r\n", b"\n")
            if new == data:
                return
            mm.seek(0)
            mm.write(new)
            mm.flush()
            f.truncate(len(new))

    @staticmethod
    def convert_with_temp(path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as src, tmp.open(
            "w", encoding="utf-8", newline=""
        ) as dst:
            for line in src:
                dst.write(line.replace("\r\n", "\n"))
        os.replace(tmp, path)

    @staticmethod
    def safe_convert(path: Path, dry_run: bool = False) -> str:
        if not path.is_file():
            return "SKIP_NOT_FILE"
        if Pydos2unix2.is_binary(path):
            return "SKIP_BINARY"
        if not Pydos2unix2.needs_conversion(path):
            return "SKIP_ALREADY_UNIX"
        if dry_run:
            return "DRY_RUN"
        try:
            Pydos2unix2.convert_in_place(path)
            return "CONVERTED_MMAP"
        except Exception:
            try:
                Pydos2unix2.convert_with_temp(path)
                return "CONVERTED_TEMP"
            except Exception:
                return "ERROR"

    @staticmethod
    def scan_paths(inputs: t.Iterable[t.Any], recursive: bool, excludes: t.Iterable[t.Any]) -> list[Path]:
        result = []
        for inp in inputs:
            p = Path(inp)
            if p.is_dir():
                if recursive:
                    result.extend(p.rglob("*"))
                else:
                    result.extend(p.glob("*"))
            else:
                result.append(p)
        out = []
        for p in result:
            if any((fnmatch.fnmatch(str(p), pat) for pat in excludes)):
                continue
            out.append(p)
        return out

    @staticmethod
    def worker(args: t.Any) -> t.Any:
        path, dry = args
        res = Pydos2unix2.safe_convert(path, dry_run=dry)
        if res == "ERROR":
            logging.error(f"Failed to convert: {path}")
        return res

    @staticmethod
    def parse_args() -> t.Any:
        parser = argparse.ArgumentParser(
            description="Fast dos2unix converter with mmap, tqdm, error logging."
        )
        parser.add_argument("paths", nargs="*", help="Files or directories.")
        parser.add_argument("--recursive", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--parallel", type=int, default=1)
        parser.add_argument("--chunksize", type=int, default=50)
        parser.add_argument("--exclude", nargs="*", default=[])
        parser.add_argument("--verbose", action="store_true")
        return parser.parse_args()

    @staticmethod
    def main() -> None:
        args = Pydos2unix2.parse_args()
        if not args.paths:
            args.paths = ["."]
            args.recursive = True
        log_dir = Path.home() / "tmp"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "pydos2unix.log"
        logging.basicConfig(
            filename=str(log_file), level=logging.ERROR, format="%(asctime)s %(levelname)s: %(message)s"
        )
        files = Pydos2unix2.scan_paths(args.paths, args.recursive, args.exclude)
        tasks = [(p, args.dry_run) for p in files]
        if args.parallel > 1:
            with Pool(args.parallel) as pool, tqdm(total=len(tasks), unit="file") as bar:
                for _ in pool.imap_unordered(Pydos2unix2.worker, tasks, chunksize=args.chunksize):
                    bar.update(1)
        else:
            for task in tqdm(tasks, unit="file"):
                Pydos2unix2.worker(task)


if __name__ == "__main__":
    Pydos2unix2.main()
