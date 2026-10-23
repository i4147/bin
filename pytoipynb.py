#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import json
from pathlib import Path
import sys
import nbformat as nbf


class Pytoipynb:
    @staticmethod
    def simple_convert(py_file: str, ipynb_file: str | None = None) -> None:
        if not ipynb_file:
            ipynb_file = Path(py_file).stem + ".ipynb"
        code = Path(py_file).read_text(encoding="utf-8")
        nb = nbf.v4.new_notebook()
        nb["cells"] = [nbf.v4.new_code_cell(code)]
        with Path(ipynb_file).open("w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1)
        print(f"Converted {py_file} to {ipynb_file}")

    @staticmethod
    def run() -> None:
        if len(sys.argv) < 2:
            print("Usage: python simple_convert.py input.py [output.ipynb]")
            sys.exit(1)
        py_file = sys.argv[1]
        ipynb_file = sys.argv[2] if len(sys.argv) > 2 else None
        Pytoipynb.simple_convert(py_file, ipynb_file)


if __name__ == "__main__":
    Pytoipynb.run()
