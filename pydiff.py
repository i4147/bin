#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
from pathlib import Path
import shutil
from dh import cprint, read_lines


class Pydiff:
    @staticmethod
    def count_lines(path: Path) -> int:
        return path.read_bytes().count(b"\n") + 1

    @staticmethod
    def file_hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def read_file_task(path: Path, use_mmap: bool, strip: bool) -> tuple[Path, list[str]]:
        lines = read_lines(path, ke=False)
        if strip:
            lines = [line.strip() for line in lines]
        return (path, lines)

    @staticmethod
    def filter_diff_chunk(chunk: list[str], exclude_set: frozenset[str], mode: str) -> list[str]:
        return (
            [p for p in chunk if p not in exclude_set]
            if mode == "only_in_first"
            else [p for p in chunk if p in exclude_set]
        )

    @staticmethod
    def next_group_dir(base: Path) -> Path:
        n = 1
        while (base / f"group_{n}").exists():
            n += 1
        return base / f"group_{n}"

    @staticmethod
    def move_files_to_group(path1: Path, path2: Path) -> None:
        base = Path.cwd()
        target = Pydiff.next_group_dir(base)
        target.mkdir()
        shutil.move(str(path1.resolve()), str(target / path1.name))
        shutil.move(str(path2.resolve()), str(target / path2.name))
        cprint(f"moved to {target.name}/", "magenta")

    @staticmethod
    def report_diff_lines(
        path1: Path, path2: Path, strip: bool = False, move: bool = False, num_workers: int = 2
    ) -> None:
        if Pydiff.file_hash(path1) == Pydiff.file_hash(path2) and (not strip):
            cprint(f"{path1.name} and {path2.name} are identical", "blue")
            if move:
                Pydiff.move_files_to_group(path1, path2)
            return
        lines1_count = Pydiff.count_lines(path1)
        lines2_count = Pydiff.count_lines(path2)
        use_mmap1 = lines1_count > 5000
        use_mmap2 = lines2_count > 5000
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            future1 = executor.submit(Pydiff.read_file_task, path1, use_mmap1, strip)
            future2 = executor.submit(Pydiff.read_file_task, path2, use_mmap2, strip)
            _, lines1 = future1.result()
            _, lines2 = future2.result()
        set1 = set(lines1)
        set2 = set(lines2)
        if lines1_count > 10000 and lines2_count > 10000:
            chunk_size = max(1000, len(lines1) // num_workers)
            chunks = [lines1[i : i + chunk_size] for i in range(0, len(lines1), chunk_size)]
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                futures = [
                    executor.submit(Pydiff.filter_diff_chunk, chunk, frozenset(set2), "only_in_first")
                    for chunk in chunks
                ]
                only_in_first = []
                for future in as_completed(futures):
                    only_in_first.extend(future.result())
        else:
            only_in_first = [p for p in lines1 if p not in set2]
        only_in_second = [p for p in lines2 if p not in set1]
        common_count = len(set1 & set2)
        if move and strip and (not only_in_first) and (not only_in_second):
            Pydiff.move_files_to_group(path1, path2)
        if only_in_first:
            cprint(f"only in {path1.name}:", "cyan")
            for line in only_in_first[:5]:
                cprint(f"  - {line}", "green")
            if len(only_in_first) > 5:
                cprint(f"  ... and {len(only_in_first) - 5} lines more", "green")
        if only_in_second:
            cprint(f"only in {path2.name}:", "cyan")
            for line in only_in_second[:5]:
                cprint(f"  - {line}", "yellow")
            if len(only_in_second) > 5:
                cprint(f"  ... and {len(only_in_second) - 5} lines more", "yellow")
        cprint(
            f"common lines: {common_count}\nonly in {path1.name}: {len(only_in_first)}\nonly in {path2.name}: {len(only_in_second)}",
            "blue",
        )

    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(prog="pydiff", description="Diff two files line by line")
        parser.add_argument("file1", type=Path)
        parser.add_argument("file2", type=Path)
        parser.add_argument(
            "-s",
            "--strip",
            action="store_true",
            help="strip leading and trailing whitespace from lines before comparing",
        )
        parser.add_argument(
            "-m",
            "--move",
            action="store_true",
            help="with -s, if files match, move both into a group_N subdir in the current folder",
        )
        parser.add_argument("-w", "--workers", type=int, default=2, help="number of worker processes")
        args = parser.parse_args()
        Pydiff.report_diff_lines(
            args.file1, args.file2, strip=args.strip, move=args.move, num_workers=args.workers
        )


if __name__ == "__main__":
    Pydiff.main()
