#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import ast
from collections import defaultdict
import contextlib
from dataclasses import dataclass
from functools import lru_cache
import multiprocessing as mp
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable

WORKERS: int = 6
BACKUP_SUFFIX: str = ".bak"


@dataclass(frozen=True)
class Options:
    mode: str = "single"
    root: str = "."
    backup: bool = True
    format: bool = False
    verbose: bool = True
    undo: bool = False
    single: bool = False
    target_consts: str = ""
    target_classes: str = ""
    target_funcs: str = ""

    def __post_init__(self) -> None:
        if not self.target_funcs:
            object.__setattr__(self, "target_funcs", "funcs.py")
        if not self.target_consts:
            object.__setattr__(self, "target_consts", "consts.py")
        if not self.target_classes:
            object.__setattr__(self, "target_classes", "classes.py")


class PyRefactor:
    @staticmethod
    def list_py_files(root: str, recursive: bool = True) -> list[Path]:
        root_path = Path(root)
        if recursive:
            return sorted((p for p in root_path.rglob("*.py") if not p.name.endswith(f"{BACKUP_SUFFIX}.py")))
        return sorted(
            (
                p
                for p in root_path.iterdir()
                if p.is_file() and p.suffix == ".py" and (not p.name.endswith(f"{BACKUP_SUFFIX}.py"))
            )
        )

    @staticmethod
    def ensure_backups(paths: Iterable[Path]) -> None:
        for p in paths:
            if p.exists():
                shutil.copy2(p, p.with_name(p.name + BACKUP_SUFFIX))

    @staticmethod
    def restore_backups(paths: Iterable[Path]) -> list[Path]:
        restored = []
        for p in paths:
            backup = p.with_name(p.name + BACKUP_SUFFIX)
            if backup.exists():
                shutil.copy2(backup, p)
                restored.append(p)
        return restored

    @staticmethod
    def overwrite_file(path: Path, content: str, dry_run: bool) -> None:
        if dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def try_format(path: Path) -> None:
        with contextlib.suppress(Exception):
            subprocess.run(["ruff", "format", str(path)], check=False)

    @staticmethod
    def parse_name_from_block(block: str) -> str | None:
        block = block.strip()
        if block.startswith("def "):
            return block[4:].split("(")[0].strip()
        if block.startswith("class "):
            return block[6:].split("(")[0].split(":")[0].strip()
        if "=" in block:
            target = block.split("=", 1)[0].strip()
            if target.isupper():
                return target

    @staticmethod
    def extract_referenced_names(block: str) -> set[str]:
        return set(re.findall("\\b([A-Za-z_][A-Za-z0-9_]*)\\b", block))

    @staticmethod
    def topological_sort(blocks: list[str], known_names: set[str]) -> list[str]:
        name_to_block: dict[str, str] = {}
        for block in blocks:
            name = PyRefactor.parse_name_from_block(block)
            if name:
                name_to_block[name] = block
        if not name_to_block:
            return blocks
        dependencies: dict[str, set[str]] = {}
        for name, block in name_to_block.items():
            refs = PyRefactor.extract_referenced_names(block) & known_names - {name}
            dependencies[name] = refs
        in_degree = dict.fromkeys(name_to_block, 0)
        for name, refs in dependencies.items():
            for ref in refs:
                if ref in in_degree:
                    in_degree[name] += 1
        dependents: dict[str, list[str]] = {name: [] for name in name_to_block}
        for name, refs in dependencies.items():
            for ref in refs:
                if ref in dependents:
                    dependents[ref].append(name)
        queue = [name for name, deg in in_degree.items() if deg == 0]
        ordered: list[str] = []
        while queue:
            name = queue.pop(0)
            ordered.append(name)
            for dependent in dependents[name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        if len(ordered) != len(name_to_block):
            return blocks
        return [name_to_block[name] for name in ordered]

    @staticmethod
    def _optimize_imports(import_lines: list[str]) -> str:
        seen: set[str] = set()
        from_imports: defaultdict[str, list[str] | None] = defaultdict(list)
        for line in import_lines:
            line = line.strip()
            if not line or line in seen:
                continue
            if line.startswith(("from .", "import .")):
                continue
            seen.add(line)
            if line.startswith("from "):
                parts = line.split()
                module = parts[1]
                names_part = line.split("import", 1)[1].strip()
                names = [
                    n.strip() for n in names_part.replace("(", "").replace(")", "").split(",") if n.strip()
                ]
                if from_imports[module] is None:
                    from_imports[module] = []
                from_imports[module].extend(names)
            elif line.startswith("import "):
                modules = [m.strip() for m in line[7:].split(",")]
                for module in modules:
                    if module not in from_imports:
                        from_imports[module] = None
        stdlib_plain: list[tuple[str, list[str] | None]] = []
        dotted: list[tuple[str, list[str] | None]] = []
        for module, names in from_imports.items():
            if module.startswith("."):
                continue
            if "." in module:
                dotted.append((module, names))
            else:
                stdlib_plain.append((module, names))

        def render(entries: list[tuple[str, list[str] | None]]) -> list[str]:
            rendered: list[str] = []
            for module, names in sorted(entries):
                if names is None:
                    rendered.append(f"import {module}")
                    continue
                unique_names = sorted({n for n in names if n})
                if not unique_names:
                    rendered.append(f"import {module}")
                    continue
                joined = ", ".join(unique_names)
                prefix = f"from {module} import "
                if len(prefix) + len(joined) > 79:
                    body = ",\n    ".join(unique_names)
                    rendered.append(f"from {module} import (\n    {body},\n)")
                else:
                    rendered.append(f"{prefix}{joined}")
            return rendered

        lines = render(stdlib_plain) + render(dotted)
        return "\n".join(lines) + "\n\n" if lines else ""

    @staticmethod
    @lru_cache(maxsize=256)
    def _parse_import_names(line: str) -> tuple[str, ...]:
        if line.startswith("import "):
            return tuple((n.strip().split()[0] for n in line[7:].split(",")))
        if line.startswith("from "):
            return (line.split()[1],)
        return ()

    @staticmethod
    def _extract_block(lines: list[str], node: ast.AST) -> str:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", start + 1)
        return "\n".join(lines[start:end]) + "\n"

    @staticmethod
    def parse_top_level_items(path: Path) -> tuple[list[str], list[str], list[str], list[str]]:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            return ([], [], [], [])
        lines = source.splitlines()
        funcs, consts, classes, imports = ([], [], [], [])
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(PyRefactor._extract_block(lines, node))
            elif isinstance(node, ast.FunctionDef):
                funcs.append(PyRefactor._extract_block(lines, node))
            elif isinstance(node, ast.ClassDef):
                classes.append(PyRefactor._extract_block(lines, node))
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.isupper()
            ):
                consts.append(PyRefactor._extract_block(lines, node))
        return (funcs, consts, classes, imports)

    @staticmethod
    def collect_from_files(
        paths: list[Path],
    ) -> tuple[list[str], list[str], list[str], list[str], dict[Path, dict]]:
        try:
            with mp.Pool(WORKERS) as pool:
                results = list(pool.imap(PyRefactor.parse_top_level_items, paths))
        finally:
            pass
        file_map: dict[Path, dict] = {}
        all_funcs, all_consts, all_classes, all_imports = ([], [], [], [])
        for path, (funcs, consts, classes, imports) in zip(paths, results, strict=False):
            file_map[path] = {"funcs": funcs, "consts": consts, "classes": classes, "imports": imports}
            all_funcs += funcs
            all_consts += consts
            all_classes += classes
            all_imports += imports
        return (all_funcs, all_consts, all_classes, all_imports, file_map)

    @staticmethod
    def make_all_list(names: list[str]) -> str:
        unique_sorted = sorted(set(names))
        body = ",\n    ".join((f'"{n}"' for n in unique_sorted))
        return f"__all__ = [\n    {body}\n]\n"

    @staticmethod
    def build_merged_module(file_map: dict[Path, dict]) -> str:
        all_imports: list[str] = []
        func_blocks, const_blocks, class_blocks = ([], [], [])
        func_names, const_names, class_names = ([], [], [])
        seen_names: dict[str, Path] = {}
        for path, entry in file_map.items():
            all_imports.extend(entry["imports"])
            for block in entry["consts"]:
                name = PyRefactor.parse_name_from_block(block)
                if not name:
                    continue
                if name in seen_names:
                    print(f'WARNING: Constant "{name}" defined in both {seen_names[name]} and {path}')
                    continue
                seen_names[name] = path
                const_blocks.append(block)
                const_names.append(name)
            for block in entry["funcs"]:
                name = PyRefactor.parse_name_from_block(block)
                if not name:
                    continue
                if name in seen_names:
                    print(f'WARNING: Function "{name}" defined in both {seen_names[name]} and {path}')
                    continue
                seen_names[name] = path
                func_blocks.append(block)
                func_names.append(name)
            for block in entry["classes"]:
                name = PyRefactor.parse_name_from_block(block)
                if not name:
                    continue
                if name in seen_names:
                    print(f'WARNING: Class "{name}" defined in both {seen_names[name]} and {path}')
                    continue
                seen_names[name] = path
                class_blocks.append(block)
                class_names.append(name)
        filtered_imports = [
            line for line in all_imports if not line.strip().startswith(("from .", "import ."))
        ]
        optimized_imports = PyRefactor._optimize_imports(filtered_imports)
        known_names = set(const_names + func_names + class_names)
        if func_blocks:
            func_blocks = PyRefactor.topological_sort(func_blocks, known_names)
        if class_blocks:
            class_blocks = PyRefactor.topological_sort(class_blocks, known_names)
        sections = []
        if const_blocks:
            sections.append("\n".join(const_blocks).strip())
        if func_blocks:
            sections.append("\n\n".join(func_blocks).strip())
        if class_blocks:
            sections.append("\n\n".join(class_blocks).strip())
        body = "\n\n\n".join((s for s in sections if s))
        all_declaration = PyRefactor.make_all_list(const_names + func_names + class_names)
        parts = []
        if optimized_imports.strip():
            parts.append(optimized_imports.strip())
        if body:
            parts.append(body)
        parts.append(all_declaration)
        return "\n\n\n".join(parts) + "\n"

    @staticmethod
    def build_top_level_modules(file_map: dict[Path, dict]) -> tuple[str, str, str]:
        func_blocks, const_blocks, class_blocks = ([], [], [])
        for entry in file_map.values():
            func_blocks.extend(entry["funcs"])
            const_blocks.extend(entry["consts"])
            class_blocks.extend(entry["classes"])
        return ("\n\n".join(func_blocks), "\n\n".join(const_blocks), "\n\n".join(class_blocks))

    @staticmethod
    def build_subpkg_modules(root: str, file_map: dict[Path, dict]) -> dict[str, tuple[str, str, str, dict]]:
        root_path = Path(root)
        grouped: dict[str, dict[str, list[str]]] = {}
        for path, entry in file_map.items():
            try:
                rel = path.relative_to(root_path)
            except ValueError:
                rel = path
            subpkg = str(rel.parent) if rel.parent != Path() else ""
            if subpkg not in grouped:
                grouped[subpkg] = {"funcs": [], "consts": [], "classes": []}
            grouped[subpkg]["funcs"].extend(entry["funcs"])
            grouped[subpkg]["consts"].extend(entry["consts"])
            grouped[subpkg]["classes"].extend(entry["classes"])
        result: dict[str, tuple[str, str, str, dict]] = {}
        for subpkg, entry in grouped.items():
            result[subpkg] = (
                "\n\n".join(entry["funcs"]),
                "\n\n".join(entry["consts"]),
                "\n\n".join(entry["classes"]),
                entry,
            )
        return result

    @staticmethod
    def get_merged_module_path(opts: Options) -> Path:
        root_path = Path(opts.root).resolve()
        package_name = root_path.name
        return Path(opts.root) / f"{package_name}.py"

    @staticmethod
    def get_category_paths(opts: Options) -> dict[str, Path]:
        root_path = Path(opts.root)
        return {
            "funcs": root_path / opts.target_funcs,
            "consts": root_path / opts.target_consts,
            "classes": root_path / opts.target_classes,
        }

    @staticmethod
    def write_init_with_reexport(imports: list[str], root: str, dry_run: bool, opts: Options) -> None:
        root_path = Path(root)
        package_name = root_path.name
        optimized = PyRefactor._optimize_imports(imports)
        content = optimized.rstrip() + f"\n\nfrom .{package_name} import *\n"
        init_path = root_path / "__init__.py"
        PyRefactor.overwrite_file(init_path, content, dry_run)
        if opts.format and (not dry_run):
            PyRefactor.try_format(init_path)

    @staticmethod
    def run_single_file_mode(opts: Options) -> None:
        if opts.verbose:
            print("Running single_file mode: merging all Python files into one module")
        root_path = Path(opts.root)
        all_files = PyRefactor.list_py_files(opts.root, recursive=True)
        init_path = root_path / "__init__.py"
        init_resolved = init_path.resolve()
        other_files: list[Path] = []
        init_imports: list[str] = []
        for path in all_files:
            if path.resolve() == init_resolved:
                _, _, _, init_imports = PyRefactor.parse_top_level_items(path)
            else:
                other_files.append(path)
        if opts.verbose:
            print(f"Processing {len(other_files)} Python files...")
        _, _, _, _, file_map = PyRefactor.collect_from_files(other_files)
        merged_source = PyRefactor.build_merged_module(file_map)
        if init_imports:
            merged_source = PyRefactor._optimize_imports(init_imports) + merged_source
        package_name = root_path.resolve().name
        merged_path = root_path / f"{package_name}.py"
        if opts.verbose:
            print(f"Creating merged module: {merged_path}")
        if opts.backup:
            PyRefactor.ensure_backups([merged_path, init_path])
        dry_run = opts.mode == "dry-run"
        PyRefactor.overwrite_file(merged_path, merged_source, dry_run)
        init_content = f"from .{package_name} import *\n"
        PyRefactor.overwrite_file(init_path, init_content, dry_run)
        if opts.format and (not dry_run):
            if opts.verbose:
                print("Formatting files...")
            PyRefactor.try_format(merged_path)
            PyRefactor.try_format(init_path)
        if opts.verbose and (not dry_run):
            funcs, consts, classes, _, _ = PyRefactor.collect_from_files([merged_path])
            print(
                f"Successfully merged {len(funcs)} functions, {len(classes)} classes, and {len(consts)} constants into {package_name}.py"
            )

    @staticmethod
    def run_legacy_single_mode(opts: Options) -> None:
        root_path = Path(opts.root)
        all_files = PyRefactor.list_py_files(opts.root, recursive=True)
        init_path = root_path / "__init__.py"
        init_resolved = init_path.resolve()
        other_files = [p for p in all_files if p.resolve() != init_resolved]
        _, _, _, _, file_map = PyRefactor.collect_from_files(other_files)
        merged_source = PyRefactor.build_merged_module(file_map)
        package_name = root_path.resolve().name
        merged_path = root_path / f"{package_name}.py"
        if opts.backup:
            PyRefactor.ensure_backups([merged_path, init_path])
        dry_run = opts.mode == "dry-run"
        PyRefactor.overwrite_file(merged_path, merged_source, dry_run)
        PyRefactor.overwrite_file(init_path, f"from .{package_name} import *\n", dry_run)
        if opts.format and (not dry_run):
            PyRefactor.try_format(merged_path)
            PyRefactor.try_format(init_path)

    @staticmethod
    def run_small_package_mode(opts: Options) -> None:
        all_files = PyRefactor.list_py_files(opts.root, recursive=False)
        init_path = Path(opts.root) / "__init__.py"
        other_files = [p for p in all_files if p != init_path]
        funcs, consts, classes, _, file_map = PyRefactor.collect_from_files(other_files)
        if opts.verbose:
            print(f"Collected {len(funcs)} funcs, {len(classes)} classes, {len(consts)} consts")
        merged_source = PyRefactor.build_merged_module(file_map)
        merged_path = PyRefactor.get_merged_module_path(opts)
        package_name = merged_path.stem
        if opts.backup:
            PyRefactor.ensure_backups([merged_path, init_path])
        dry_run = opts.mode == "dry-run"
        PyRefactor.overwrite_file(merged_path, merged_source, dry_run)
        PyRefactor.overwrite_file(init_path, f"from .{package_name} import *\n", dry_run)
        if opts.format and (not dry_run):
            try:
                with mp.Pool(WORKERS) as pool:
                    list(pool.imap_unordered(PyRefactor.try_format, [merged_path, init_path]))
            finally:
                pass

    @staticmethod
    def run_merge_mode(opts: Options) -> None:
        root_path = Path(opts.root)
        all_files = PyRefactor.list_py_files(opts.root, recursive=True)
        category_paths = PyRefactor.get_category_paths(opts)
        category_resolved = {p.resolve() for p in category_paths.values()}
        init_path = root_path / "__init__.py"
        init_resolved = init_path.resolve()
        other_files = [
            p for p in all_files if p.resolve() not in category_resolved and p.resolve() != init_resolved
        ]
        funcs, consts, classes, imports, file_map = PyRefactor.collect_from_files(other_files)
        init_funcs, init_consts, init_classes, init_imports = PyRefactor.parse_top_level_items(init_path)
        if opts.verbose:
            print(f"Found {len(imports) + len(init_imports)} imports")
            print(f"Found {len(funcs) + len(init_funcs)} functions")
            print(f"Found {len(consts) + len(init_consts)} constants")
            print(f"Found {len(classes) + len(init_classes)} classes")
        funcs_content, consts_content, classes_content = PyRefactor.build_top_level_modules(file_map)
        if init_funcs:
            funcs_content = (funcs_content + "\n\n" + "\n\n".join(init_funcs)).strip()
        if init_consts:
            consts_content = (consts_content + "\n\n" + "\n\n".join(init_consts)).strip()
        if init_classes:
            classes_content = (classes_content + "\n\n" + "\n\n".join(init_classes)).strip()
        if opts.backup:
            PyRefactor.ensure_backups([*category_paths.values(), init_path])
        dry_run = opts.mode == "dry-run"
        contents = {"funcs": funcs_content, "consts": consts_content, "classes": classes_content}
        for category, path in category_paths.items():
            PyRefactor.overwrite_file(path, contents[category], dry_run)
        PyRefactor.write_init_with_reexport(init_imports, opts.root, dry_run, opts)
        if opts.format and (not dry_run):
            try:
                with mp.Pool(WORKERS) as pool:
                    targets = [*category_paths.values(), init_path]
                    list(pool.imap_unordered(PyRefactor.try_format, targets))
            finally:
                pass

    @staticmethod
    def run_subpkg_mode(opts: Options) -> None:
        root_path = Path(opts.root)
        all_files = PyRefactor.list_py_files(opts.root, recursive=True)
        init_path = (root_path / "__init__.py").resolve()
        other_files = [p for p in all_files if p.resolve() != init_path]
        _, _, _, _, file_map = PyRefactor.collect_from_files(other_files)
        subpkg_modules = PyRefactor.build_subpkg_modules(opts.root, file_map)
        for subpkg_name, (funcs_content, consts_content, classes_content, _) in subpkg_modules.items():
            subpkg_dir = root_path / subpkg_name if subpkg_name else root_path
            category_paths = {
                "funcs": subpkg_dir / opts.target_funcs,
                "consts": subpkg_dir / opts.target_consts,
                "classes": subpkg_dir / opts.target_classes,
            }
            if opts.backup:
                PyRefactor.ensure_backups([*category_paths.values(), subpkg_dir / "__init__.py"])
            dry_run = opts.mode == "dry-run"
            contents = {"funcs": funcs_content, "consts": consts_content, "classes": classes_content}
            for category, path in category_paths.items():
                PyRefactor.overwrite_file(path, contents[category], dry_run)
            grouped_names = {"funcs": [], "consts": [], "classes": []}
            for path, entry in file_map.items():
                try:
                    rel = path.relative_to(root_path)
                except ValueError:
                    rel = path
                parent = str(rel.parent) if rel.parent != Path() else ""
                if parent == subpkg_name:
                    grouped_names["funcs"].extend(entry["funcs"])
                    grouped_names["consts"].extend(entry["consts"])
                    grouped_names["classes"].extend(entry["classes"])
            if grouped_names["funcs"] or grouped_names["consts"] or grouped_names["classes"]:
                import_lines = []
                all_names = []
                if grouped_names["funcs"]:
                    func_names = [
                        n for n in (PyRefactor.parse_name_from_block(b) for b in grouped_names["funcs"]) if n
                    ]
                    if func_names:
                        module_name = Path(opts.target_funcs).stem
                        import_lines.append(f"from .{module_name} import {','.join(func_names)}")
                        all_names.extend(func_names)
                if grouped_names["consts"]:
                    const_names = [
                        n for n in (PyRefactor.parse_name_from_block(b) for b in grouped_names["consts"]) if n
                    ]
                    if const_names:
                        module_name = Path(opts.target_consts).stem
                        import_lines.append(f"from .{module_name} import {','.join(const_names)}")
                        all_names.extend(const_names)
                if grouped_names["classes"]:
                    class_names = [
                        n
                        for n in (PyRefactor.parse_name_from_block(b) for b in grouped_names["classes"])
                        if n
                    ]
                    if class_names:
                        module_name = Path(opts.target_classes).stem
                        import_lines.append(f"from .{module_name} import {','.join(class_names)}")
                        all_names.extend(class_names)
                init_content = "\n".join(import_lines) + f"\n\n{PyRefactor.make_all_list(all_names)}"
                PyRefactor.overwrite_file(subpkg_dir / "__init__.py", init_content, dry_run)

    @staticmethod
    def run(opts: Options) -> None:
        if opts.undo:
            package_name = Path(opts.root).resolve().name
            root_path = Path(opts.root)
            targets = [
                root_path / f"{package_name}.py",
                root_path / "__init__.py",
                root_path / opts.target_funcs,
                root_path / opts.target_consts,
                root_path / opts.target_classes,
            ]
            restored = PyRefactor.restore_backups(targets)
            if opts.verbose:
                print(f"Restored {len(restored)} files from backups")
            return
        dispatch = {
            "single_file": PyRefactor.run_single_file_mode,
            "single": PyRefactor.run_legacy_single_mode,
            "small": PyRefactor.run_small_package_mode,
            "merge": PyRefactor.run_merge_mode,
            "subpkg": PyRefactor.run_subpkg_mode,
        }
        if opts.mode == "dry-run":
            PyRefactor.run_merge_mode(Options(**{**opts.__dict__, "mode": "dry-run"}))
            return
        handler = dispatch.get(opts.mode)
        if not handler:
            msg = f"Unknown mode: {opts.mode}"
            raise ValueError(msg)
        handler(opts)

    @staticmethod
    def parse_cli_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(prog="pyrefactor", description="Refactor small python packages")
        parser.add_argument("run", nargs="?", default="run")
        parser.add_argument(
            "--mode",
            choices=["small", "merge", "subpkg", "dry-run", "single", "single_file"],
            default="single",
        )
        parser.add_argument("--root", default=".")
        parser.add_argument("--single", action="store_true")
        parser.add_argument("--no-backup", action="store_true")
        parser.add_argument("--no-format", action="store_true")
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument("--undo", action="store_true")
        return parser.parse_args()

    @staticmethod
    def main() -> None:
        args = PyRefactor.parse_cli_args()
        cwd = Path.cwd()
        package_name = cwd.name
        opts = Options(
            mode=args.mode,
            root=args.root,
            backup=not args.no_backup,
            format=not args.no_format,
            verbose=not args.quiet,
            undo=args.undo,
            single=args.single or args.mode in ("single", "single_file"),
            target_funcs=f"{package_name}_func.py",
            target_consts=f"{package_name}_const.py",
            target_classes=f"{package_name}_class.py",
        )
        print(f"Running pyrefactor mode={opts.mode} root={opts.root}")
        PyRefactor.run(opts)


if __name__ == "__main__":
    PyRefactor.main()
