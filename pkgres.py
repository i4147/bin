#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
from multiprocessing import Pool
from pathlib import Path
import re
from typing import Any

SKIPPED_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".venv", "venv", "env", ".tox", "build", "dist", ".eggs"}
)
REPLACEMENTS: list[tuple[re.Pattern[str], str, bool, bool]] = [
    (
        re.compile("pkg_resources\\.get_distribution\\(\\s*([^)]+?)\\s*\\)\\.version"),
        "importlib.metadata.version(\\1)",
        True,
        False,
    ),
    (
        re.compile("pkg_resources\\.get_distribution\\(\\s*([^)]+?)\\s*\\)"),
        "importlib.metadata.distribution(\\1)",
        True,
        False,
    ),
    (
        re.compile("pkg_resources\\.resource_string\\(\\s*([^,]+?)\\s*,\\s*([^)]+?)\\s*\\)"),
        "importlib.resources.files(\\1).joinpath(\\2).read_bytes()",
        False,
        True,
    ),
    (
        re.compile("pkg_resources\\.resource_text\\(\\s*([^,]+?)\\s*,\\s*([^)]+?)\\s*\\)"),
        "importlib.resources.files(\\1).joinpath(\\2).read_text(encoding='utf-8')",
        False,
        True,
    ),
    (
        re.compile("pkg_resources\\.resource_filename\\(\\s*([^,]+?)\\s*,\\s*([^)]+?)\\s*\\)"),
        "str(importlib.resources.files(\\1).joinpath(\\2))",
        False,
        True,
    ),
    (
        re.compile("pkg_resources\\.resource_stream\\(\\s*([^,]+?)\\s*,\\s*([^)]+?)\\s*\\)"),
        "importlib.resources.files(\\1).joinpath(\\2).open('rb')",
        False,
        True,
    ),
    (
        re.compile("pkg_resources\\.require\\(\\s*([^)]+?)\\s*\\)"),
        "packaging.requirements.Requirement(\\1)",
        False,
        False,
    ),
    (re.compile("pkg_resources\\.parse_version\\("), "packaging.version.Version(", False, False),
    (re.compile("pkg_resources\\.iter_entry_points\\("), "importlib.metadata.entry_points(", True, False),
    (
        re.compile("pkg_resources\\.DistributionNotFound"),
        "importlib.metadata.PackageNotFoundError",
        True,
        False,
    ),
]
IMPORT_RE = re.compile(
    "^(?P<indent>\\s*)(?P<stmt>(?:import|from)\\s+pkg_resources(?:\\s+import\\s+(?P<names>[^\\n#]+))?)\\s*(?P<comment>#.*)?$",
    re.MULTILINE,
)
GENERIC_USAGE_RE = re.compile("pkg_resources\\.([A-Za-z_][A-Za-z0-9_]*)")


class Pkgres:
    @staticmethod
    def collect_python_files(paths: list[str]) -> list[Path]:
        py_files: list[Path] = []
        for path_str in paths:
            path = Path(path_str)
            if path.is_file() and path.suffix == ".py":
                py_files.append(path)
            elif path.is_dir():
                for p in path.rglob("*.py"):
                    if not any((part in SKIPPED_DIRS for part in p.parts)):
                        py_files.append(p)
        return sorted(set(py_files))

    @staticmethod
    def scan_file(path: Path) -> dict[str, Any]:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"file": path, "error": str(e), "found": False}
        has_import = bool(IMPORT_RE.search(content))
        usages: list[dict[str, Any]] = []
        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            if IMPORT_RE.search(line):
                usages.append({"pattern": line.strip(), "line": i, "kind": "import"})
            for pat, _, _, _ in REPLACEMENTS:
                for m in pat.finditer(line):
                    usages.append({"pattern": m.group(0), "line": i, "kind": "usage"})
            for m in GENERIC_USAGE_RE.finditer(line):
                matched_str = m.group(0)
                if not any((u["pattern"] == matched_str and u["line"] == i for u in usages)):
                    usages.append({"pattern": matched_str, "line": i, "kind": "generic"})
        imports_needed: set[str] = set()
        if has_import or usages:
            for line in lines:
                if any(
                    (
                        k in line
                        for k in ["resource_filename", "resource_string", "resource_text", "resource_stream"]
                    )
                ):
                    imports_needed.add("importlib.resources")
                if any(
                    (k in line for k in ["get_distribution", "iter_entry_points", "DistributionNotFound"])
                ):
                    imports_needed.add("importlib.metadata")
                if "parse_version" in line:
                    imports_needed.add("packaging.version")
                if "require" in line:
                    imports_needed.add("packaging.requirements")
        return {
            "file": path,
            "found": bool(usages) or has_import,
            "usages": usages,
            "imports_needed": sorted(imports_needed),
        }

    @staticmethod
    def autofix_file(path: Path) -> dict[str, Any]:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"file": path, "error": str(e), "fixed": False}
        original_content = content
        content = IMPORT_RE.sub("", content)
        imports_needed: set[str] = set()
        for pat, repl, needs_meta, needs_res in REPLACEMENTS:
            if pat.search(content):
                content = pat.sub(repl, content)
                if needs_meta or "importlib.metadata" in repl:
                    imports_needed.add("importlib.metadata")
                if needs_res or "importlib.resources" in repl:
                    imports_needed.add("importlib.resources")
                if "packaging.version" in repl:
                    imports_needed.add("packaging.version")
                if "packaging.requirements" in repl:
                    imports_needed.add("packaging.requirements")
        if imports_needed:
            import_lines = "\n".join((f"import {imp}" for imp in sorted(imports_needed)))
            match = re.search('^(#!.*\\n)?(\\"\\"\\".*?\\"\\"\\")?', content, re.MULTILINE | re.DOTALL)
            if match:
                insert_pos = match.end()
                content = content[:insert_pos] + import_lines + "\n" + content[insert_pos:]
            else:
                content = import_lines + "\n" + content
        if content != original_content:
            try:
                path.write_text(content, encoding="utf-8")
                return {"file": path, "fixed": True, "imports_added": sorted(imports_needed)}
            except OSError as e:
                return {"file": path, "error": f"Write failed: {e}", "fixed": False}
        return {"file": path, "fixed": False, "reason": "No changes needed"}

    @staticmethod
    def main(argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="Detect and autofix deprecated pkg_resources usage.")
        parser.add_argument("paths", nargs="*", help="Python files or directories to process")
        parser.add_argument("-a", "--autofix", action="store_true", help="Autofix pkg_resources usage")
        parser.add_argument("-j", "--jobs", type=int, default=4, help="Number of parallel processes")
        args = parser.parse_args(argv)
        paths = args.paths or ["."]
        py_files = Pkgres.collect_python_files(paths)
        if not py_files:
            print("No .py files found.")
            return 0
        print(f"Found {len(py_files)} file(s) | Mode: {('AUTOFIX' if args.autofix else 'REPORT')}\n")
        try:
            if args.autofix:
                with Pool(processes=args.jobs) as pool:
                    results = pool.map(Pkgres.autofix_file, py_files)
                fixed_count = 0
                for result in results:
                    if result.get("error"):
                        print(f"❌ {result['file']}: {result['error']}")
                    elif result.get("fixed"):
                        fixed_count += 1
                        imports = ", ".join(result.get("imports_added", []))
                        print(f"✓ {result['file']} | Added: {imports}")
                    else:
                        reason = result.get("reason", "No pkg_resources found")
                        if reason != "No pkg_resources found":
                            print(f"- {result['file']}: {reason}")
                print(f"\nFixed {fixed_count}/{len(py_files)} file(s)")
            else:
                with Pool(processes=args.jobs) as pool:
                    results = pool.map(Pkgres.scan_file, py_files)
                found_count = 0
                for result in results:
                    if result.get("error"):
                        print(f"❌ {result['file']}: {result['error']}")
                    elif result.get("found"):
                        found_count += 1
                        usages = result.get("usages", [])
                        imports = ", ".join(result.get("imports_needed", []))
                        print(f"⚠ {result['file']}")
                        for usage in usages:
                            print(f"    Line {usage['line']}: {usage['pattern']}")
                        print(f"    Imports: {imports}\n")
                print(f"\nFound pkg_resources in {found_count}/{len(py_files)} file(s)")
                if found_count > 0:
                    print("Run with -a to autofix.")
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            return 1
        except Exception as e:
            print(f"Error: {e}")
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(Pkgres.main())
