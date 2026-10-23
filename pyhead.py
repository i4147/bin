#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from pathlib import Path
import sys


class Pyhead:
    @staticmethod
    def run() -> None:
        fn = Path(sys.argv[1])
        try:
            with fn.open(encoding="utf-8", errors="ignore") as f:
                print(f.read(1024))
        except:
            with fn.open("rb") as f:
                print(f.read(1024))


if __name__ == "__main__":
    Pyhead.run()
