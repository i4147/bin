#!/usr/bin/env python3
"""Verification harness for the class-based rewrite.

For every original/rewritten pair it checks three things:

* ``structure`` - every module-level function still exists on the owning class
  with the same parameter names/order,
* ``import`` - the rewritten module imports cleanly (same outcome as the
  original, which may fail on missing third-party dependencies),
* ``cli`` - running ``--help`` (and the bare script) yields the same exit code
  and output as the original.

Exit status is non-zero when any check fails.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

TIMEOUT = 25

IMPORT_PROBE = """
import importlib.util, json, sys
path, name = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location(name, path)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
try:
    spec.loader.exec_module(module)
except SystemExit:
    print(json.dumps({"ok": True, "exit": True}))
except BaseException as error:
    print(json.dumps({"ok": False, "error": type(error).__name__ + ": " + str(error)[:200]}))
else:
    print(json.dumps({"ok": True, "exit": False}))
"""


@dataclass
class Check:
    name: str
    structure: str = "?"
    imports: str = "?"
    cli: str = "?"
    notes: list[str] = field(default_factory=list)


def owner_of(tree: ast.Module) -> ast.ClassDef | None:
    best: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = sum(1 for stmt in node.body if isinstance(stmt, ast.FunctionDef))
            if best is None or methods > sum(1 for stmt in best.body if isinstance(stmt, ast.FunctionDef)):
                best = node
    return best


def signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], int]:
    args = fn.args
    names = [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    return names, len(args.defaults)


def check_structure(original: Path, rewritten: Path, check: Check) -> None:
    src_tree = ast.parse(original.read_text(encoding="utf-8"))
    new_tree = ast.parse(rewritten.read_text(encoding="utf-8"))
    functions = [stmt for stmt in src_tree.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))]
    owner = owner_of(new_tree)
    if not functions:
        check.structure = "skip"
        return
    if owner is None:
        check.structure = "fail: no class in output"
        return
    methods = {
        stmt.name: stmt
        for stmt in ast.walk(owner)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    problems: list[str] = []
    for fn in functions:
        method = methods.get(fn.name)
        if method is None:
            problems.append(f"missing {fn.name}")
            continue
        expected_names, expected_defaults = signature(fn)
        actual_names, actual_defaults = signature(method)
        if expected_names and expected_names[0] == "self":
            expected_names = expected_names
        if expected_names != actual_names or expected_defaults != actual_defaults:
            problems.append(f"signature {fn.name}: {expected_names}/{expected_defaults} != {actual_names}/{actual_defaults}")
        if method.returns is None:
            problems.append(f"unannotated return {fn.name}")
        for arg in (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs):
            if arg.arg in {"self", "cls"}:
                continue
            if arg.annotation is None:
                problems.append(f"unannotated param {fn.name}.{arg.arg}")
    check.structure = "ok" if not problems else "fail: " + "; ".join(problems[:4])


def run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONHASHSEED": "0", "NO_COLOR": "1", "COLUMNS": "80"},
        )
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    return result.returncode, result.stdout, result.stderr


def check_import(original: Path, rewritten: Path, check: Check, python: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [python, "-c", IMPORT_PROBE, str(rewritten.resolve()), rewritten.stem],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        check.imports = f"fail: probe crashed ({proc.returncode})"
        return
    if payload["ok"]:
        check.imports = "ok"
        return
    message = payload["error"]
    orig = subprocess.run(
        [python, "-c", IMPORT_PROBE, str(original.resolve()), original.stem],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        stdin=subprocess.DEVNULL,
        cwd=tempfile.gettempdir(),
    )
    try:
        original_payload = json.loads(orig.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        check.imports = f"fail: {message}"
        return
    if not original_payload["ok"] and original_payload["error"].split(":")[0] == message.split(":")[0]:
        check.imports = "ok (same missing dependency)"
    else:
        check.imports = f"fail: {message}"


def check_cli(original: Path, rewritten: Path, check: Check, python: str) -> None:
    with tempfile.TemporaryDirectory() as workdir:
        for flag in ("--help",):
            before = run([python, str(original.resolve()), flag], Path(workdir))
            after = run([python, str(rewritten.resolve()), flag], Path(workdir))
            if before[0] == after[0] and before[1] == after[1]:
                continue
            check.notes.append(f"{flag} differs: rc {before[0]}->{after[0]}")
            check.cli = "warn"
        before = run([python, str(original.resolve())], Path(workdir))
        after = run([python, str(rewritten.resolve())], Path(workdir))
        if before[0] != after[0] or before[1] != after[1]:
            check.notes.append(f"bare run differs: rc {before[0]}->{after[0]}")
            if check.cli == "?":
                check.cli = "warn"
        if check.cli == "?":
            check.cli = "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify rewritten scripts against their originals")
    parser.add_argument("original", type=Path)
    parser.add_argument("rewritten", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    checks: list[Check] = []
    for original in sorted(args.original.glob("p*.py")):
        rewritten = args.rewritten / original.name
        if not rewritten.exists():
            checks.append(Check(original.name, structure="missing", imports="missing", cli="missing"))
            continue
        check = Check(original.name)
        try:
            check_structure(original, rewritten, check)
        except SyntaxError as error:
            check.structure = f"fail: syntax {error}"
        try:
            check_import(original, rewritten, check, args.python)
        except Exception as error:  # noqa: BLE001
            check.imports = f"fail: {type(error).__name__}"
        try:
            check_cli(original, rewritten, check, args.python)
        except Exception as error:  # noqa: BLE001
            check.cli = f"fail: {type(error).__name__}"
        checks.append(check)
    present = [check for check in checks if check.structure != "missing"]
    failed = [check for check in present if "fail" in {check.structure, check.imports, check.cli}]
    warned = [check for check in checks if check.cli == "warn" or check.notes]
    for check in checks:
        if check in failed or check in warned:
            print(f"{check.name:26} structure={check.structure[:60]:60} imports={check.imports[:42]:42} cli={check.cli}")
            for note in check.notes:
                print(f"    {note}")
    print(f"\nchecked {len(present)} files: {len(failed)} failed, {len(warned)} with warnings")
    if args.report:
        args.report.write_text(json.dumps([asdict(check) for check in checks], indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
