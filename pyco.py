#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from pathlib import Path
import shutil
from dh import fsz, gsz


class Pyco:
    @staticmethod
    def clean_pycache(start_dir: Path = Path.cwd()) -> None:
        removed = 0
        sz = 0
        for path in start_dir.rglob("__pycache__"):
            if path.exists():
                sz += gsz(path)
                removed += 1
                shutil.rmtree(str(path))
        if removed:
            print(f"   • Total size freed: {fsz(sz)}")
            print(f"   • dirs removed: {removed}")
        else:
            print("nothing found.")

    @staticmethod
    def run() -> None:
        cwd = Path.cwd()
        Pyco.clean_pycache(cwd)


if __name__ == "__main__":
    Pyco.run()
