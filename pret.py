#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from pathlib import Path
import sys
from dh import get_files, mpf, runcmd


class Pret:
    @staticmethod
    def process_file(path: str | Path) -> tuple[bool, Path]:
        path = Path(path)
        if not path.exists() or not path.stat().st_size:
            return (False, path)
        if sum((1 for _ in path.open())) == 1:
            return (False, path)
        ret = runcmd(
            ["prettier", "-w", str(path).replace("/storage/emulated/0", "/sdcard")], show_output=True
        )
        if not ret:
            return (True, path)
        return (False, path)

    @staticmethod
    def main() -> None:
        cwd = Path.cwd()
        args = sys.argv[1:]
        files = list(
            [Path(f) for f in args]
            if args
            else get_files(
                cwd,
                e=[".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".md", ".jsm", ".scss", ".tsm", ".coffee"],
            )
        )
        if len(files) == 1:
            Pret.process_file(files[0])
            sys.exit(1)
        mpf(Pret.process_file, files)


if __name__ == "__main__":
    raise SystemExit(Pret.main())
