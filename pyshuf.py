#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import mmap
import os
from pathlib import Path
import random
import secrets
import sys
from typing import Any

MMAP_THRESHOLD_BYTES: int = 5 * 1024 * 1024


class Pyshuf:
    @staticmethod
    def get_line_offsets(path: Path) -> list[int]:
        offsets: list[int] = []
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            offset = 0
            while True:
                offsets.append(offset)
                newline_pos = mm.find(b"\n", offset)
                if newline_pos == -1:
                    break
                offset = newline_pos + 1
        return offsets

    @staticmethod
    def crypto_shuffle(lst: list[Any]) -> None:
        n = len(lst)
        for i in range(n - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            lst[i], lst[j] = (lst[j], lst[i])

    @staticmethod
    def shuffle3(lst: list[Any]) -> None:
        sys_random = random.SystemRandom()
        n = len(lst)
        for i in range(n - 1, 0, -1):
            j = sys_random.randint(0, i)
            lst[i], lst[j] = (lst[j], lst[i])

    @staticmethod
    def weighted_shuffle(lst: list[Any]) -> None:
        n = len(lst)
        for i in range(n - 1, 0, -1):
            j = random.randint(0, i)
            lst[i], lst[j] = (lst[j], lst[i])
        if n > 1:
            for i in range(n - 1):
                swap_pos = random.randint(i + 1, n - 1)
                lst[i], lst[swap_pos] = (lst[swap_pos], lst[i])

    @staticmethod
    def shuffle_lines(lines: list[str], method: str) -> None:
        if method == "basic":
            random.shuffle(lines)
        elif method == "crypto":
            Pyshuf.crypto_shuffle(lines)
        elif method == "shuffle3":
            Pyshuf.shuffle3(lines)
        elif method == "weighted":
            Pyshuf.weighted_shuffle(lines)

    @staticmethod
    def process_small_file(input_path: Path, output_path: Path, methods: list[str], repeats: int) -> bool:
        try:
            with input_path.open(encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error reading input file: {e}", file=sys.stderr)
            return False
        if not lines:
            output_path.touch()
            return True
        for method in methods:
            shuffled_lines = lines.copy()
            for _ in range(repeats):
                Pyshuf.shuffle_lines(shuffled_lines, method)
            target_path = output_path
            if output_path == input_path and len(methods) > 1:
                base, ext = os.path.splitext(input_path)
                target_path = Path(f"{base}_{method}{ext}")
            try:
                with target_path.open("w", encoding="utf-8") as f:
                    f.writelines(shuffled_lines)
            except Exception as e:
                print(f"Error writing output file: {e}", file=sys.stderr)
                return False
        return True

    @staticmethod
    def process_large_file(input_path: Path, output_path: Path) -> bool:
        file_size = input_path.stat().st_size
        line_offsets = Pyshuf.get_line_offsets(input_path)
        original_line_count = len(line_offsets)
        if original_line_count == 0:
            output_path.touch()
            return True
        Pyshuf.crypto_shuffle(line_offsets)
        Pyshuf.shuffle3(line_offsets)
        Pyshuf.weighted_shuffle(line_offsets)
        random.shuffle(line_offsets)
        try:
            with input_path.open("rb") as infile, mmap.mmap(
                infile.fileno(), 0, access=mmap.ACCESS_READ
            ) as mm, output_path.open("wb") as outfile:
                for offset in line_offsets:
                    actual_end_of_line = mm.find(b"\n", offset)
                    line_data = (
                        mm[offset:file_size]
                        if actual_end_of_line == -1
                        else mm[offset : actual_end_of_line + 1]
                    )
                    outfile.write(line_data)
            return True
        except Exception as e:
            print(f"Error writing output file: {e}", file=sys.stderr)
            if output_path.exists() and output_path != input_path:
                output_path.unlink()
            return False

    @staticmethod
    def test_randomness(input_file: Path) -> None:
        try:
            with input_file.open(encoding="utf-8") as f:
                lines_to_test = [line.strip() for line in f.readlines()[:100]]
        except Exception as e:
            print(f"Error reading file for testing: {e}")
            return
        if not lines_to_test:
            return
        original_order = lines_to_test.copy()
        for i in range(5):
            current_lines = original_order.copy()
            Pyshuf.crypto_shuffle(current_lines)
            changes = sum((1 for a, b in zip(original_order, current_lines, strict=False) if a != b))
            print(f"Shuffle {i + 1}: {changes} out of {len(current_lines)} positions changed")

    @staticmethod
    def main() -> int:
        parser = argparse.ArgumentParser()
        parser.add_argument("input_file")
        parser.add_argument("-o", "--output")
        parser.add_argument("-r", "--repeats", type=int, default=3)
        parser.add_argument("-t", "--test", action="store_true")
        parser.add_argument("-m", "--methods", nargs="+", default=["crypto", "shuffle3", "weighted"])
        args = parser.parse_args()
        input_path = Path(args.input_file)
        if not input_path.exists():
            print(f"Error: Input file '{input_path}' not found.", file=sys.stderr)
            return 1
        if args.test:
            Pyshuf.test_randomness(input_path)
            return 0
        output_path = Path(args.output) if args.output else input_path
        file_size = input_path.stat().st_size
        if file_size > MMAP_THRESHOLD_BYTES:
            success = Pyshuf.process_large_file(input_path, output_path)
        else:
            success = Pyshuf.process_small_file(input_path, output_path, args.methods, args.repeats)
        return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(Pyshuf.main())
