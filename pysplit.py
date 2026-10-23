#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from pathlib import Path
import sys
from dh import is_binary


class Pysplit:
    @staticmethod
    def split_file_into_parts(path: Path, n: int) -> None:
        if n <= 0:
            msg = "n must be a positive integer"
            raise ValueError(msg)
        if is_binary(path):
            print(f"Error: binary file '{path}' detected. Aborting.", file=sys.stderr)
            sys.exit(1)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        num_lines = len(lines)
        padding_width = len(str(n))
        base = num_lines // n
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        start = 0
        for i in range(1, n + 1):
            if i == n:
                end = num_lines
            else:
                end = start + base
            index_str = str(i).zfill(padding_width)
            part_name = f"{stem}_{index_str}{suffix}"
            part_path = parent / part_name
            part_path.write_text("".join(lines[start:end]), encoding="utf-8")
            print(f"Created: {part_path}")
            start = end

    @staticmethod
    def main() -> None:
        if len(sys.argv) != 3:
            print("Usage: python script.py <n> <path>")
            sys.exit(1)
        try:
            path = Path(sys.argv[1])
            n = int(sys.argv[2])
        except ValueError:
            print("Error: n must be an integer.", file=sys.stderr)
            sys.exit(1)
        if not path.is_file():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        Pysplit.split_file_into_parts(path, n)


if __name__ == "__main__":
    raise SystemExit(Pysplit.main())
