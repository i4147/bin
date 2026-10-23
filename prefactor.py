#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
import ast
from collections import deque
from dataclasses import dataclass
from multiprocessing import Pool, cpu_count
from pathlib import Path
import sys

MAX_DEFAULT: int = 10


@dataclass
class ModuleInfo:
    path: Path
    fullname: str
    source: str
    deps: set[str]


class Prefactor:
    @staticmethod
    def find_py_files(root: Path, exclude: Path | None = None) -> list[Path]:
        files = []
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            if exclude:
                try:
                    if p.resolve().is_relative_to(exclude.resolve()):
                        continue
                except Exception:
                    if str(p.resolve()).startswith(str(exclude.resolve())):
                        continue
            files.append(p)
        return files

    @staticmethod
    def module_fullname_for_path(root: Path, path: Path, package_mode: bool, package_name: str | None) -> str:
        rel = path.relative_to(root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
            return package_name or root.name if package_mode else ".".join(parts) if parts else root.name
        elif package_mode:
            prefix = package_name or root.name
            return ".".join([prefix] + parts)
        else:
            return ".".join(parts)

    @staticmethod
    def resolve_relative_import(curr_fullname: str, module: str | None, level: int) -> str | None:
        if level == 0:
            return module
        cur_parts = curr_fullname.split(".")
        parent_parts = cur_parts[:-1]
        if level - 1 > len(parent_parts):
            return None
        target_parts = parent_parts[: len(parent_parts) - (level - 1)]
        if module:
            target_parts += module.split(".")
        if not target_parts:
            return None
        return ".".join(target_parts)

    @staticmethod
    def analyze_file(args: t.Any) -> ModuleInfo:
        path, root, package_mode, package_name, full_map = args
        src = path.read_text(encoding="utf8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return ModuleInfo(path=path, fullname="", source=src, deps=set())
        fullname = Prefactor.module_fullname_for_path(root, path, package_mode, package_name)
        deps: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if package_mode:
                        prefix = package_name or root.name
                        if name == prefix or name.startswith(prefix + "."):
                            deps.add(name)
                    else:
                        base = name.split(".")[0]
                        if base in full_map:
                            deps.add(name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module
                level = node.level or 0
                resolved = Prefactor.resolve_relative_import(fullname, mod, level)
                if resolved:
                    deps.add(resolved)
                elif package_mode and mod:
                    prefix = package_name or root.name
                    if mod == prefix or mod.startswith(prefix + "."):
                        deps.add(mod)
        normalized: set[str] = set()
        for d in deps:
            if d in full_map:
                normalized.add(d)
            else:
                for candidate in full_map:
                    if candidate == d or candidate.startswith(d + ".") or d.startswith(candidate + "."):
                        normalized.add(candidate)
        return ModuleInfo(path=path, fullname=fullname, source=src, deps=normalized)

    @staticmethod
    def topological_sort(modules: dict[str, ModuleInfo]) -> tuple[list[str], list[set[str]]]:
        edges = {name: set(info.deps) for name, info in modules.items()}
        for name in edges:
            edges[name] = {d for d in edges[name] if d in modules}
        in_deg = {n: len(ds) for n, ds in edges.items()}
        q = deque([n for n, deg in in_deg.items() if deg == 0])
        ordered: list[str] = []
        while q:
            n = q.popleft()
            ordered.append(n)
            for m, ds in edges.items():
                if n in ds:
                    ds.remove(n)
                    in_deg[m] -= 1
                    if in_deg[m] == 0:
                        q.append(m)
        cycles = []
        if len(ordered) != len(edges):
            remaining = set(edges) - set(ordered)
            ordered += sorted(remaining)
            cycles = [remaining]
        return (ordered, cycles)

    @staticmethod
    def build_merged_source(modules: dict[str, ModuleInfo], ordered: list[str], out_module_name: str) -> str:
        lines: list[str] = []
        lines.append("# Auto-generated single-file package by merge_to_single.py")
        lines.append(f"# Reconstructed modules: {', '.join(ordered)}")
        lines.append("import sys, types")
        lines.append("")
        lines.append("_orig_sources = {")
        for name in ordered:
            src = modules[name].source
            lines.append(f"    {name!r}: {src!r},")
        lines.append("}")
        lines.append("")
        lines.append("def get_original_source(module_name):")
        lines.append('    """Return the original source (as a string) for a merged module, or None."""')
        lines.append("    return _orig_sources.get(module_name)")
        lines.append("")
        lines.append("# Pre-create module objects and insert into sys.modules")
        lines.append("for _name in _orig_sources:")
        lines.append("    mod = types.ModuleType(_name)")
        lines.append('    mod.__file__ = f"<merged:{_name}>"')
        lines.append("    mod.__package__ = _name.rpartition('.')[0]")
        lines.append("    sys.modules[_name] = mod")
        lines.append("")
        lines.append("_order = [")
        for name in ordered:
            lines.append(f"    {name!r},")
        lines.append("]")
        lines.append("")
        lines.append("for _name in _order:")
        lines.append("    src = _orig_sources[_name]")
        lines.append("    mod = sys.modules[_name]")
        lines.append("    # compile with a synthetic filename so tracebacks mention the original module name")
        lines.append("    exec(compile(src, f\"<merged:{_name}>\", 'exec'), mod.__dict__)")
        lines.append("")
        if out_module_name in modules:
            lines.append("try:")
            lines.append(f"    import {out_module_name} as _top")
            lines.append(
                "    # re-export public names (non-underscore) into the merged-file global namespace"
            )
            lines.append("    for _k, _v in vars(_top).items():")
            lines.append("        if not _k.startswith('_'):")
            lines.append("            globals()[_k] = _v")
            lines.append("except Exception:")
            lines.append("    # best-effort; ignore runtime errors during re-export")
            lines.append("    pass")
        lines.append("")
        lines.append("# End of merged package")
        return "\n".join(lines)

    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(
            description="Merge a small Python library into a single-file package."
        )
        parser.add_argument(
            "input",
            nargs="?",
            type=Path,
            default=Path.cwd(),
            help="Input directory containing .py files or a package. Defaults to current directory.",
        )
        parser.add_argument(
            "output_dir",
            nargs="?",
            type=Path,
            default=Path.cwd() / "out",
            help="Directory to write merged file to. Defaults to ./out.",
        )
        parser.add_argument(
            "--out-name", type=str, default=None, help="Output filename (defaults to <pkg>_single.py)."
        )
        parser.add_argument(
            "--max-files", type=int, default=MAX_DEFAULT, help="Max number of files allowed (default: 10)."
        )
        parser.add_argument(
            "--package-name",
            type=str,
            default=None,
            help="Force package name (defaults to input folder name if package).",
        )
        parser.add_argument(
            "--force", action="store_true", help="Force merge even if file count > max-files."
        )
        parser.add_argument(
            "-j",
            "--jobs",
            type=int,
            default=0,
            help="Number of worker processes to use (default: auto cpu count).",
        )
        args = parser.parse_args()
        root: Path = args.input.resolve()
        if not root.exists() or not root.is_dir():
            print("Input must be an existing directory.", file=sys.stderr)
            sys.exit(2)
        out_dir: Path = args.output_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        files = Prefactor.find_py_files(root, exclude=out_dir)
        if not files:
            print("No Python files found under input directory.", file=sys.stderr)
            sys.exit(2)
        package_mode = (root / "__init__.py").exists()
        package_name = args.package_name or (root.name if package_mode else None)
        if len(files) > args.max_files and (not args.force):
            print(
                f"Found {len(files)} files which is > {args.max_files}. Use --force to override.",
                file=sys.stderr,
            )
            sys.exit(2)
        full_map_candidates: dict[str, Path] = {}
        for p in files:
            name = Prefactor.module_fullname_for_path(root, p, package_mode, package_name)
            full_map_candidates[name] = p
        pool_args = [(p, root, package_mode, package_name, full_map_candidates) for p in files]
        if args.jobs and args.jobs > 0:
            workers = min(args.jobs, max(1, len(files)))
        else:
            workers = min(cpu_count(), max(1, len(files)))
        if workers == 1:
            results = list(map(Prefactor.analyze_file, pool_args))
        else:
            with Pool(processes=workers) as pool:
                results = pool.map(Prefactor.analyze_file, pool_args)
        modules: dict[str, ModuleInfo] = {}
        for mi in results:
            if not mi.fullname:
                mi.fullname = Prefactor.module_fullname_for_path(root, mi.path, package_mode, package_name)
            modules[mi.fullname] = mi
        ordered, cycles = Prefactor.topological_sort(modules)
        if cycles:
            print("Warning: cycles detected among modules; attempting best-effort merge.", file=sys.stderr)
        out_name = args.out_name or f"{package_name or root.name}_single.py"
        out_path = out_dir / out_name
        merged = Prefactor.build_merged_source(modules, ordered, package_name or root.name)
        out_path.write_text(merged, encoding="utf8")
        print(f"Wrote merged file to: {out_path}")
        print(f"Modules merged ({len(modules)}): {', '.join(ordered)}")
        if cycles:
            print("Cycles (approx):", cycles)


if __name__ == "__main__":
    raise SystemExit(Prefactor.main())
