#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
from collections import deque
from multiprocessing import get_context
from pathlib import Path
import sys
from dh import cprint, get_files

c_files: t.set[str] = {".c", ".h", ".inc"}
cpp_files: t.set[str] = {".cpp", ".cc", ".cxx", ".hpp", ".hpp11", ".hh", ".hxx"}


class Pycppcheck:
    @staticmethod
    def validate_cpp(path: Path) -> tuple[bool, str]:
        cmd = ""
        if path.suffix in c_files:
            cmd = "clang -fsyntax-only str(path)"
        if path.suffix in cpp_files:
            cmd = "clang++ -fsyntax-only str(path)"
        ret, txt, err = run_command(cmd)
        return (path, ret, txt, err)

    @staticmethod
    def run() -> None:
        args = sys.argv[1:]
        cwd = Path.cwd()
        files = list(
            [Path(p) for p in args]
            if args
            else get_files(
                cwd, ext=[".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inc", "hpp11"]
            )
        )
        results = []
        with get_context("spawn").Pool(8) as pool:
            pending = deque()
            for f in files:
                pending.append(pool.apply_async(Pycppcheck.validate_cpp, (f,)))
                if len(pending) > 8:
                    results.append(pending.popleft().get())
            while pending:
                results.append(pending.popleft().get())
        for result in results:
            if int(result[1]) == 2:
                cprint(f"[✖] : {result[0].name} has error", "white")
            else:
                cprint(f"[✅] : {result[0].name} is ok", "cyan")


if __name__ == "__main__":
    Pycppcheck.run()
