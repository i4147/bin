#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pysource.cli import main as run_cli


class PyStrip:
    @staticmethod
    def main(argv: Sequence[str] | None = None) -> int:
        return run_cli(argv)


if __name__ == "__main__":
    sys.exit(PyStrip.main())
