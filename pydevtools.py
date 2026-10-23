#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import configparser
from dataclasses import dataclass, field
from email.parser import Parser
import json
from pathlib import Path
import pprint
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Sequence
import zipfile

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None
README_SUFFIXES: set[str] = {".md", ".markdown", ".mdown", ".mkdn"}
BINARY_SUFFIXES: tuple[str, str, str] = (".so", ".pyd", ".dll")


class Pydevtools:
    @staticmethod
    def die(msg: str, code: int = 1) -> "NoReturn":
        print(f"error: {msg}", file=sys.stderr)
        raise SystemExit(code)

    @staticmethod
    def info(msg: str) -> None:
        print(msg)

    @staticmethod
    def read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    @staticmethod
    def write_text(path: Path, content: str, *, force: bool = False) -> bool:
        if path.exists() and (not force):
            Pydevtools.info(f"warning: {path} exists; use --force to overwrite.")
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return True

    @staticmethod
    def load_toml(path: Path) -> dict[str, Any]:
        if tomllib is None:
            Pydevtools.die("TOML support requires Python >=3.11 or the 'tomli' package.")
        try:
            with path.open("rb") as fh:
                return tomllib.load(fh)
        except FileNotFoundError:
            Pydevtools.die(f"file not found: {path}")
        except Exception as exc:
            Pydevtools.die(f"invalid TOML in {path}: {exc}")
        return {}

    @staticmethod
    def py_repr(value: Any) -> str:
        return pprint.pformat(value, indent=4, width=88, sort_dicts=False)

    @staticmethod
    def module_from_name(name: str) -> str:
        return name.replace("-", "_").replace(".", "_").lower()

    @staticmethod
    def load_user_info() -> dict[str, str]:
        info_path = Path.home() / ".myinfo"
        if not info_path.exists():
            return {}
        text = info_path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            pass
        out: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    @staticmethod
    def parse_cargo_lock(text: str) -> dict[str, Any]:
        m = re.search("^version\\s*=\\s*(\\d+)", text, re.MULTILINE)
        version = int(m.group(1)) if m else 3
        packages: list[dict[str, Any]] = []
        chunks = re.split("\\n\\[\\[package\\]\\]\\n", text)
        for chunk in chunks[1:]:
            pkg = (
                Pydevtools._parse_lock_pkg_v2(chunk) if version >= 2 else Pydevtools._parse_lock_pkg_v1(chunk)
            )
            if pkg:
                packages.append(pkg)
        return {"version": version, "packages": packages}

    @staticmethod
    def _parse_lock_pkg_v2(chunk: str) -> dict[str, Any] | None:
        name = re.search('^name\\s*=\\s*"([^"]*)"', chunk, re.MULTILINE)
        version = re.search('^version\\s*=\\s*"([^"]*)"', chunk, re.MULTILINE)
        source = re.search('^source\\s*=\\s*"([^"]*)"', chunk, re.MULTILINE)
        if not name or not version:
            return None
        pkg: dict[str, Any] = {"name": name.group(1), "version": version.group(1)}
        if source:
            pkg["source"] = source.group(1)
        deps: list[str] = []
        in_deps = False
        for line in chunk.split("\n"):
            if line.strip().startswith("dependencies = ["):
                in_deps = True
                deps.extend(re.findall('"([^"]*)"', line))
                if "]" in line:
                    in_deps = False
            elif in_deps:
                deps.extend(re.findall('"([^"]*)"', line))
                if "]" in line:
                    in_deps = False
        if deps:
            pkg["dependencies"] = deps
        return pkg

    @staticmethod
    def _parse_lock_pkg_v1(chunk: str) -> dict[str, Any] | None:
        name = re.search('^name\\s*=\\s*"([^"]*)"', chunk, re.MULTILINE)
        version = re.search('^version\\s*=\\s*"([^"]*)"', chunk, re.MULTILINE)
        if not name or not version:
            return None
        pkg: dict[str, Any] = {"name": name.group(1), "version": version.group(1)}
        deps: list[str] = []
        for line in chunk.split("\n"):
            m = re.match('^\\s*"([^"]+)\\s+([^"]+)"', line)
            if m:
                deps.append(f"{m.group(1)} {m.group(2)}")
        if deps:
            pkg["dependencies"] = deps
        return pkg

    @staticmethod
    def render_cargo_toml(
        packages: list[dict[str, Any]], *, root_name: str | None = None, root_version: str = "0.1.0"
    ) -> str:
        out: list[str] = ["[package]"]
        if root_name:
            out.append(f'name = "{root_name}"')
        elif packages:
            out.append(f'''name = "{packages[0]['name']}"''')
        else:
            out.append('name = "generated-project"')
        out.append(f'version = "{root_version}"')
        out.append('edition = "2021"')
        out.append("")
        if packages:
            out.append("[dependencies]")
            seen: set[str] = set()
            if "dependencies" in packages[0]:
                seen.update(packages[0]["dependencies"])
            if seen:
                for dep in sorted(seen):
                    match = next((p for p in packages if p["name"] == dep), None)
                    if match:
                        out.append(f'''{match['name']} = "{match['version']}"''')
                    else:
                        out.append(f'{dep} = "*"  # Version not found in lock file')
            else:
                for pkg in packages[1:]:
                    out.append(f'''{pkg['name']} = "{pkg['version']}"''')
        return "\n".join(out)

    @staticmethod
    def cmd_cargo_toml(args: argparse.Namespace) -> int:
        src = Path(args.input)
        if not src.exists():
            Pydevtools.die(f"{src} not found")
        Pydevtools.info(f"Parsing {src}...")
        data = Pydevtools.parse_cargo_lock(src.read_text(encoding="utf-8"))
        if not data["packages"]:
            Pydevtools.die("no packages found in lock file")
        Pydevtools.info(f"Found {len(data['packages'])} packages (lock file v{data['version']})")
        rendered = Pydevtools.render_cargo_toml(
            data["packages"], root_name=args.root_name, root_version=args.root_version
        )
        dst = Path(args.output)
        dst.write_text(rendered, encoding="utf-8")
        Pydevtools.info(f"Generated {dst}")
        if args.preview:
            Pydevtools.info("\nPreview:\n" + "-" * 40)
            Pydevtools.info(rendered)
        return 0

    @staticmethod
    def _authors_from_pep621(raw: Iterable[Any]) -> list[Author]:
        out: list[Author] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(Author(name=item.get("name"), email=item.get("email")))
            elif isinstance(item, str):
                m = re.match("^\\s*(.*?)\\s*<([^>]+)>\\s*$", item)
                if m:
                    out.append(Author(name=m.group(1), email=m.group(2)))
                else:
                    out.append(Author(name=item))
        return out

    @staticmethod
    def _read_optional_file(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _readme_content(readme: Any, root: Path) -> tuple[str, str]:
        if isinstance(readme, dict):
            content_type = readme.get("content-type", "text/markdown")
            if "file" in readme:
                return (Pydevtools._read_optional_file(root / readme["file"]), content_type)
            return (readme.get("text", ""), content_type)
        if isinstance(readme, str):
            suffix = Path(readme).suffix.lower()
            ctype = (
                "text/markdown"
                if suffix in README_SUFFIXES
                else "text/x-rst" if suffix == ".rst" else "text/plain"
            )
            return (Pydevtools._read_optional_file(root / readme), ctype)
        return ("", "text/plain")

    @staticmethod
    def extract_pep621_meta(pyproject: dict[str, Any], root: Path) -> ProjectMeta:
        v1 = pyproject.get("project", {})
        bs = pyproject.get("build-system", {})
        tool = pyproject.get("tool", {}) or {}
        if not isinstance(v1, dict):
            msg = "[project] must be a table"
            raise ValueError(msg)
        readme_content, readme_ctype = Pydevtools._readme_content(v1.get("readme", ""), root)
        readme_file = (
            v1.get("readme", {}).get("file")
            if isinstance(v1.get("readme"), dict)
            else v1.get("readme") if isinstance(v1.get("readme"), str) else None
        )
        license_field = v1.get("license", "")
        license_text = ""
        license_file: str | None = None
        if isinstance(license_field, dict):
            if "text" in license_field:
                license_text = license_field["text"]
            if "file" in license_field:
                license_file = license_field["file"]
                if not license_text:
                    license_text = Pydevtools._read_optional_file(root / license_field["file"])
        elif isinstance(license_field, str):
            license_text = license_field
        entry_points: dict[str, dict[str, str]] = {}
        for sec, val in (v1.get("entry-points") or {}).items():
            if isinstance(val, dict):
                entry_points[sec] = dict(val)
        return ProjectMeta(
            name=str(v1.get("name", "")),
            version=str(v1.get("version", "0.0.0")),
            description=str(v1.get("description", "")),
            readme_content=readme_content,
            readme_content_type=readme_ctype,
            readme_file=readme_file,
            requires_python=str(v1.get("requires-python", "")),
            license_text=license_text,
            license_file=license_file,
            authors=Pydevtools._authors_from_pep621(v1.get("authors", []) or []),
            maintainers=Pydevtools._authors_from_pep621(v1.get("maintainers", []) or []),
            keywords=list(v1.get("keywords", []) or []),
            classifiers=list(v1.get("classifiers", []) or []),
            urls=dict(v1.get("urls", {}) or {}),
            scripts=dict(v1.get("scripts", {}) or {}),
            gui_scripts=dict(v1.get("gui-scripts", {}) or {}),
            entry_points=entry_points,
            dependencies=list(v1.get("dependencies", []) or []),
            optional_dependencies={k: list(v) for k, v in (v1.get("optional-dependencies") or {}).items()},
            build_backend=str(bs.get("build-backend", "")),
            build_requires=list(bs.get("requires", []) or []),
            tool=tool,
        )

    @staticmethod
    def extract_poetry_meta(pyproject: dict[str, Any], root: Path) -> ProjectMeta:
        poetry = pyproject.get("tool", {}).get("poetry", {})
        if not isinstance(poetry, dict):
            msg = "[tool.poetry] missing or malformed"
            raise ValueError(msg)
        authors = Pydevtools._authors_from_pep621(poetry.get("authors", []) or [])
        deps_dict = poetry.get("dependencies", {}) or {}
        deps: list[str] = []
        for name, spec in deps_dict.items():
            if name == "python":
                continue
            if isinstance(spec, str):
                deps.append(name if spec == "*" else f"{name}{spec}")
            elif isinstance(spec, dict):
                version = spec.get("version", "")
                extras = spec.get("extras", [])
                token = name
                if extras:
                    token += f"[{','.join(extras)}]"
                if version and version != "*":
                    token += version
                deps.append(token)
        readme = poetry.get("readme", "")
        readme_content, readme_ctype = (
            Pydevtools._readme_content(readme, root) if readme else ("", "text/plain")
        )
        return ProjectMeta(
            name=str(poetry.get("name", "")),
            version=str(poetry.get("version", "0.0.0")),
            description=str(poetry.get("description", "")),
            readme_content=readme_content,
            readme_content_type=readme_ctype,
            readme_file=readme if isinstance(readme, str) else None,
            requires_python=str(poetry.get("dependencies", {}).get("python", "")),
            license_text=str(poetry.get("license", "")),
            authors=authors,
            keywords=list(poetry.get("keywords", []) or []),
            classifiers=list(poetry.get("classifiers", []) or []),
            urls={"Homepage": poetry["homepage"]} if poetry.get("homepage") else {},
            scripts=dict(poetry.get("scripts", {}) or {}),
            dependencies=deps,
            build_backend="poetry.core.masonry.api",
            build_requires=["poetry-core"],
            tool=pyproject.get("tool", {}) or {},
        )

    @staticmethod
    def parse_setup_cfg(path: Path) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser(interpolation=None, delimiters=("=",))
        if path.is_file():
            try:
                cfg.read(path, encoding="utf-8")
            except configparser.Error as exc:
                msg = f"invalid setup.cfg: {exc}"
                raise ValueError(msg) from exc
        return cfg

    @staticmethod
    def render_package_finder(package_name: str | None) -> str:
        if package_name:
            return f'from pathlib import Path\nfrom setuptools import find_packages\n_project_root = Path(__file__).parent\n_package_name = {package_name!r}\nif (_project_root / _package_name).is_dir():\n    packages = find_packages(\n        where=str(_project_root),\n        include=(_package_name, f"{{_package_name}}.*"),\n    )\n    package_dir = {{}}\nelif (_project_root / "src" / _package_name).is_dir():\n    packages = find_packages(\n        where=str(_project_root / "src"),\n        include=(_package_name, f"{{_package_name}}.*"),\n    )\n    package_dir = {{"": "src"}}\nelse:\n    packages = []\n    package_dir = {{}}\n'
        return 'from pathlib import Path\nfrom setuptools import find_packages\n_project_root = Path(__file__).parent\nif (_project_root / "src").is_dir():\n    packages = find_packages(where=str(_project_root / "src"))\n    package_dir = {"": "src"}\nelse:\n    packages = find_packages(where=str(_project_root))\n    package_dir = {}\n'

    @staticmethod
    def _setup_kwargs_simple(meta: ProjectMeta, package_name: str | None, *, use_src: bool) -> str:
        lines: list[str] = []
        lines.append(f"    name={meta.name!r},")
        lines.append(f"    version={meta.version!r},")
        if meta.description:
            lines.append(f"    description={meta.description!r},")
        a_names = ", ".join((a.name for a in meta.authors if a.name))
        a_mails = ", ".join((a.email for a in meta.authors if a.email))
        if a_names:
            lines.append(f"    author={a_names!r},")
        if a_mails:
            lines.append(f"    author_email={a_mails!r},")
        if meta.readme_file:
            lines.append(f"    long_description=read_text({meta.readme_file!r}),")
            lines.append(f"    long_description_content_type={meta.readme_content_type!r},")
        elif meta.readme_content:
            lines.append(f"    long_description={meta.readme_content!r},")
            lines.append(f"    long_description_content_type={meta.readme_content_type!r},")
        if meta.license_text:
            lines.append(f"    license={meta.license_text!r},")
        if meta.requires_python:
            lines.append(f"    python_requires={meta.requires_python!r},")
        if meta.keywords:
            lines.append(f"    keywords={meta.keywords!r},")
        if meta.classifiers:
            lines.append(f"    classifiers={meta.classifiers!r},")
        if meta.urls:
            lines.append(f"    project_urls={meta.urls!r},")
        if meta.dependencies:
            lines.append(f"    install_requires={meta.dependencies!r},")
        if meta.optional_dependencies:
            lines.append(f"    extras_require={meta.optional_dependencies!r},")
        entry_points: dict[str, list[str]] = {}
        if meta.scripts:
            entry_points["console_scripts"] = [f"{k} = {v}" for k, v in meta.scripts.items()]
        if meta.gui_scripts:
            entry_points["gui_scripts"] = [f"{k} = {v}" for k, v in meta.gui_scripts.items()]
        for sec, items in meta.entry_points.items():
            entry_points[sec] = [f"{k} = {v}" for k, v in items.items()]
        if entry_points:
            lines.append(f"    entry_points={entry_points!r},")
        if not meta.scripts and (not meta.gui_scripts) and (not meta.entry_points):
            pass
        lines.append("    packages=packages,")
        lines.append("    package_dir=package_dir,")
        return "\n".join(lines)

    @staticmethod
    def render_setup_py_simple(meta: ProjectMeta, root: Path) -> str:
        finder = Pydevtools.render_package_finder(meta.name)
        header = f'"""Generated setup.py.\nOriginal build backend: {meta.build_backend}\nGenerated by pydevkit.py.\n"""\nfrom setuptools import setup\n'
        readme_helper = "from pathlib import Path\ndef read_text(p):\n    q = Path(__file__).parent / p\n    return q.read_text(encoding='utf-8') if q.is_file() else ''\n"
        kwargs = Pydevtools._setup_kwargs_simple(meta, meta.name, use_src=True)
        return header + readme_helper + finder + "\nsetup(\n" + kwargs + "\n)\n"

    @staticmethod
    def render_setup_py_detailed(
        meta: ProjectMeta, root: Path, *, cfg: configparser.ConfigParser | None = None, with_cfg: bool = False
    ) -> str:
        cfg = cfg or configparser.ConfigParser()
        backend = Pydevtools._detect_ext_backend(meta)
        author_names = ", ".join((a.name for a in meta.authors if a.name))
        author_mails = ", ".join((a.email for a in meta.authors if a.email))
        maint_names = ", ".join((a.name for a in meta.maintainers if a.name))
        maint_mails = ", ".join((a.email for a in meta.maintainers if a.email))
        url = next(iter(meta.urls.values()), "") if meta.urls else ""
        packages_lines: list[str] = []
        if with_cfg and cfg.has_section("options.packages.find"):
            where = cfg.get("options.packages.find", "where", fallback="").strip()
            if where:
                packages_lines.append(f"        packages=find_packages(where={where!r}),")
                packages_lines.append(f"        package_dir={{'': {where!r}}},")
            else:
                packages_lines.append("        packages=find_packages(),")
        else:
            packages_lines.append("        packages=find_packages(),")
        entry_points: dict[str, list[str]] = {}
        if meta.scripts:
            entry_points["console_scripts"] = [f"{k} = {v}" for k, v in meta.scripts.items()]
        if meta.gui_scripts:
            entry_points["gui_scripts"] = [f"{k} = {v}" for k, v in meta.gui_scripts.items()]
        for sec, items in meta.entry_points.items():
            entry_points[sec] = [f"{k} = {v}" for k, v in items.items()]
        lines: list[str] = []
        lines.append(f"        name={meta.name!r},")
        lines.append(f"        version={meta.version!r},")
        lines.append(f"        description={meta.description!r},")
        lines.append("        long_description=long_description,")
        lines.append(f"        long_description_content_type={meta.readme_content_type!r},")
        if author_names:
            lines.append(f"        author={author_names!r},")
        if author_mails:
            lines.append(f"        author_email={author_mails!r},")
        if maint_names:
            lines.append(f"        maintainer={maint_names!r},")
        if maint_mails:
            lines.append(f"        maintainer_email={maint_mails!r},")
        if meta.license_text:
            lines.append(f"        license={meta.license_text!r},")
        elif meta.license_file:
            lines.append(f"        license_files=[{meta.license_file!r}],")
        if url:
            lines.append(f"        url={url!r},")
        if meta.keywords:
            lines.append(f"        keywords={meta.keywords!r},")
        if meta.classifiers:
            lines.append(f"        classifiers={meta.classifiers!r},")
        if meta.requires_python:
            lines.append(f"        python_requires={meta.requires_python!r},")
        if meta.dependencies:
            lines.append(f"        install_requires={meta.dependencies!r},")
        if meta.optional_dependencies:
            lines.append(f"        extras_require={meta.optional_dependencies!r},")
        if entry_points:
            lines.append(f"        entry_points={entry_points!r},")
        lines.append("        include_package_data=True,")
        lines.extend(packages_lines)
        if backend == "setuptools" and meta.tool.get("setuptools", {}).get("ext-modules"):
            lines.append("        ext_modules=ext_modules,")
        header = textwrap.dedent(
            f'        #!/usr/bin/env python3\n        """\n        Auto-generated from {root.name}/pyproject.toml.\n        Detected extension backend: {backend}\n        """\n        from pathlib import Path\n        from setuptools import Extension, find_packages, setup\n        ROOT = Path(__file__).resolve().parent\n        README_FILE = ROOT / {meta.readme_file!r}\n        def read_text(relative_path):\n            path = ROOT / relative_path\n            return path.read_text(encoding="utf-8") if path.is_file() else ""\n        long_description = read_text({meta.readme_file!r}) if {meta.readme_file!r} else {meta.readme_content!r}\n        '
        )
        ext_block = ""
        if backend == "setuptools" and meta.tool.get("setuptools", {}).get("ext-modules"):
            exts = []
            for ext in meta.tool["setuptools"]["ext-modules"]:
                exts.append(f"Extension({ext['name']!r}, sources={ext.get('sources', [])!r})")
            ext_block = f"ext_modules = [\n    " + ",\n    ".join(exts) + "\n]\n\n"
        return header + ext_block + "setup(\n" + "\n".join(lines) + "\n)\n"

    @staticmethod
    def _detect_ext_backend(meta: ProjectMeta) -> str:
        if meta.tool.get("setuptools", {}).get("ext-modules"):
            return "setuptools"
        lowered = " ".join([meta.build_backend.lower(), *(str(r).lower() for r in meta.build_requires)])
        if "scikit-build" in lowered:
            return "scikit-build"
        if "meson" in lowered:
            return "meson"
        if "cmake" in lowered:
            return "cmake"
        return "none"

    @staticmethod
    def render_setup_py_runtime(meta: ProjectMeta) -> str:
        return textwrap.dedent(
            '        #!/usr/bin/env python3\n        """\n        Runtime setup.py shim generated by pydevkit.py.\n        Reads pyproject.toml and dispatches to the configured build backend\n        so legacy tooling (`pip install -e .`, `python setup.py ...`) keeps\n        working even when the project uses a PEP 517 backend such as\n        flit_core or poetry-core.\n        """\n        from __future__ import annotations\n        import importlib\n        import subprocess\n        import sys\n        from pathlib import Path\n        try:\n            import tomllib\n        except ModuleNotFoundError:\n            import tomli as tomllib\n        ROOT = Path(__file__).resolve().parent\n        def _load_config():\n            with (ROOT / "pyproject.toml").open("rb") as fh:\n                return tomllib.load(fh)\n        def _backend(name: str):\n            return importlib.import_module(name)\n        def _run_setuptools(cfg):\n            from setuptools import setup\n            setup()\n        def _run_flit(cfg):\n            flit = _backend("flit_core.buildapi")\n            flit.build_wheel(str(ROOT / "dist"))\n        def _run_poetry(cfg):\n            poetry_core = _backend("poetry.core.masonry.api")\n            poetry_core.build_wheel(str(ROOT / "dist"))\n        def main():\n            cfg = _load_config()\n            backend = cfg["build-system"]["build-backend"]\n            dispatch = {\n                "setuptools.build_meta": _run_setuptools,\n                "setuptools.build_meta:__legacy__": _run_setuptools,\n                "flit_core.buildapi": _run_flit,\n                "flit.buildapi": _run_flit,\n                "poetry.core.masonry.api": _run_poetry,\n                "poetry.masonry.api": _run_poetry,\n            }\n            handler = dispatch.get(backend)\n            if handler is None:\n                print(f"Unsupported build backend: {backend}", file=sys.stderr)\n                return 1\n            handler(cfg)\n            return 0\n        if __name__ == "__main__":\n            raise SystemExit(main())\n        '
        )

    @staticmethod
    def detect_entry_points(project_dir: Path, package_name: str) -> list[dict[str, str]]:
        entry: list[dict[str, str]] = []
        pkg_main = project_dir / package_name / "__main__.py"
        pkg_cli = project_dir / package_name / "cli.py"
        root_main = project_dir / "__main__.py"
        root_cli = project_dir / "cli.py"

        def guess_func(path: Path) -> str:
            text = Pydevtools.read_text(path) or ""
            if re.search("def main\\(", text) or "import click" in text:
                return "main"
            if re.search("def cli\\(", text):
                return "cli"
            return "main"

        if pkg_main.exists():
            entry.append(
                {"module": f"{package_name}.__main__", "function": "main", "script_name": package_name}
            )
        if pkg_cli.exists():
            entry.append(
                {
                    "module": f"{package_name}.cli",
                    "function": guess_func(pkg_cli),
                    "script_name": package_name if not pkg_main.exists() else f"{package_name}-cli",
                }
            )
        if not entry and root_main.exists():
            entry.append({"module": "__main__", "function": "main", "script_name": package_name})
        if not entry and root_cli.exists():
            entry.append({"module": "cli", "function": guess_func(root_cli), "script_name": package_name})
        return entry

    @staticmethod
    def parse_requirements(project_dir: Path) -> list[str]:
        for name in ("requirements.txt", "requirements.in", "Pipfile"):
            p = project_dir / name
            if not p.exists():
                continue
            deps: list[str] = []
            for line in (Pydevtools.read_text(p) or "").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                if name == "Pipfile":
                    if "=" in line and (not line.startswith("[")):
                        deps.append(line.split("=", 1)[0].strip())
                else:
                    deps.append(line)
            return deps
        return []

    @staticmethod
    def render_setup_py_for_dir(project_dir: Path, package_name: str) -> str:
        entry_points = Pydevtools.detect_entry_points(project_dir, package_name)
        requirements = Pydevtools.parse_requirements(project_dir)
        ep_block = ""
        if entry_points:
            ep_block = "    entry_points={\n        'console_scripts': [\n"
            for ep in entry_points:
                ep_block += f"            '{ep['script_name']}={ep['module']}:{ep['function']}',\n"
            ep_block += "        ],\n    },\n"
        req_block = "    install_requires=[],\n"
        if requirements:
            req_block = "    install_requires=[\n"
            for r in requirements:
                req_block += f"        {r!r},\n"
            req_block += "    ],\n"
        readme_block = ""
        if (project_dir / "README.md").exists():
            readme_block = "    long_description=open('README.md').read(),\n    long_description_content_type='text/markdown',\n"
        return f"from setuptools import setup, find_packages\n\nsetup(\n    name={package_name!r},\n    version='0.1.0',\n    description={package_name + ' - A Python project'!r},\n    author='Your Name',\n    author_email='your.email@example.com',\n    url='',\n    packages=find_packages(),\n{req_block}{readme_block}{ep_block}    python_requires='>=3.6',\n    classifiers=[\n        'Development Status :: 3 - Alpha',\n        'Intended Audience :: Developers',\n        'Programming Language :: Python :: 3',\n    ],\n)\n"

    @staticmethod
    def _read_wheel_metadata(root: Path) -> dict[str, Any]:
        dist_info = next(root.glob("*.dist-info"), None)
        if dist_info is None:
            msg = "no .dist-info directory found"
            raise RuntimeError(msg)
        meta = Parser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8"))
        return {
            "name": meta["Name"],
            "version": meta["Version"],
            "summary": meta.get("Summary", ""),
            "install_requires": meta.get_all("Requires-Dist") or [],
        }

    @staticmethod
    def _read_wheel_entry_points(root: Path) -> dict[str, list[str]]:
        dist_info = next(root.glob("*.dist-info"), None)
        if dist_info is None:
            return {}
        ep_file = dist_info / "entry_points.txt"
        if not ep_file.exists():
            return {}
        groups: dict[str, list[str]] = {}
        section: str | None = None
        for line in ep_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                groups[section] = []
            elif section:
                groups[section].append(line)
        return groups

    @staticmethod
    def _find_extensions(root: Path) -> list[str]:
        out: list[str] = []
        for path in root.rglob("*"):
            if path.suffix in BINARY_SUFFIXES:
                dotted = ".".join(path.relative_to(root).with_suffix("").parts)
                out.append(dotted)
        return out

    @staticmethod
    def render_setup_py_from_wheel(
        meta: dict[str, Any], ext_modules: list[str], entry_points: dict[str, list[str]]
    ) -> str:
        ext_block = ""
        if ext_modules:
            lines = ",\n".join(
                (f"    Extension({m!r}, sources=[{m.replace('.', '/')!r} + '.*'])" for m in ext_modules)
            )
            ext_block = f"from setuptools import Extension\n\next_modules = [\n{lines}\n]\n"
        else:
            ext_block = "ext_modules = []\n"
        ep_block = ""
        if entry_points:
            rendered = "{\n"
            for group, items in entry_points.items():
                rendered += f"        {group!r}: [\n"
                for item in items:
                    rendered += f"            {item!r},\n"
                rendered += "        ],\n"
            rendered += "    }"
            ep_block = f"    entry_points={rendered},\n"
        return f"from setuptools import setup, find_packages\n{ext_block}\nsetup(\n    name={meta['name']!r},\n    version={meta['version']!r},\n    description={meta['summary']!r},\n    packages=find_packages() or ['.'],\n    install_requires={meta['install_requires']!r},\n    ext_modules=ext_modules,\n{ep_block})\n"

    @staticmethod
    def scaffold_setuptools_src(args: argparse.Namespace, target: Path) -> None:
        name = args.name
        module = Pydevtools.module_from_name(name)
        version = args.version
        user = Pydevtools.load_user_info()
        author = args.author or user.get("name", "Your Name")
        email = args.email or user.get("email", "")
        github = user.get("github_username", "")
        url = args.url or (f"https://github.com/{github}/{name}" if github else "")
        description_line = f'description = "{args.description}"\n' if args.description else ""
        email_part = f', email = "{email}"' if email else ""
        urls_block = f'\n[project.urls]\nHomepage = "{url}"\n' if url else ""
        scripts_block = f'\n[project.scripts]\n{name} = "{module}:main"\n' if args.simple_cli else ""
        (target / "pyproject.toml").write_text(
            PYPROJECT_SETUPTOOLS_SRC.format(
                name=name,
                version=version,
                description_line=description_line,
                author=author,
                email_part=email_part,
                requires_python=args.requires_python,
                urls_block=urls_block,
                scripts_block=scripts_block,
            ),
            encoding="utf-8",
        )
        (target / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        (target / ".gitignore").write_text(GITIGNORE_DEFAULT, encoding="utf-8")
        (target / "LICENSE").write_text("", encoding="utf-8")
        pkg_dir = target / "src" / module
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        if args.simple_cli:
            (pkg_dir / "__main__.py").write_text(
                f'"""CLI entry point for {name}."""\nimport sys\n\n\ndef main() -> int:\n    """Main entry point. Returns process exit code."""\n    print("Hello from {name}!")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
                encoding="utf-8",
            )
        tests = target / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / f"test_{module}.py").write_text(
            f'def test_version():\n    from {module} import __version__\n    assert __version__ == "{version}"\n',
            encoding="utf-8",
        )

    @staticmethod
    def scaffold_hatchling_typer(args: argparse.Namespace, target: Path) -> None:
        name = args.name
        module = Pydevtools.module_from_name(name)
        version = args.version
        user = Pydevtools.load_user_info()
        author = args.author or user.get("name", "Your Name")
        email = args.email or user.get("email", "author@example.com")
        (target / "pyproject.toml").write_text(
            PYPROJECT_HATCHLING_TYPER.format(name=name, module=module, author=author, email=email),
            encoding="utf-8",
        )
        (target / "README.md").write_text(
            f'\nA cookiecutter-pypackage styled boilerplate library including dual CLI entrypoints powered by Typer.\n```bash\npip install .\n```\nFor development installations:\n```bash\npip install -e ".[dev]"\n```\n```bash\n{name} hello --name Alice\n```\n```bash\n{name}-admin setup\n```\n- Run tests: `pytest`\n- Format code: `black .`\n',
            encoding="utf-8",
        )
        pkg_dir = target / "src" / module
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        (pkg_dir / "cli.py").write_text(
            f'import typer\nfrom rich import print\n\napp = typer.Typer(help="Main CLI for {name}")\n\n@app.command()\ndef hello(name: str = typer.Argument("World", help="The name to greet")):\n    """Greet someone politely."""\n    print(f"[bold green]Hello[/bold green] [cyan]{{name}}[/cyan]! Welcome to {name}.")\n\n@app.command()\ndef version():\n    """Show tool version."""\n    from {module} import __version__\n    print(f"{name} version: [yellow]{{__version__}}[/yellow]")\n\nif __name__ == "__main__":\n    app()\n',
            encoding="utf-8",
        )
        (pkg_dir / "admin.py").write_text(
            f'import typer\nfrom rich import print\n\napp = typer.Typer(help="Administrative commands for {name}")\n\n@app.command()\ndef setup():\n    """Initialize application system configs."""\n    print("[bold yellow]Initializing secure admin layout... Done.[/bold yellow]")\n\nif __name__ == "__main__":\n    app()\n',
            encoding="utf-8",
        )
        tests = target / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_cli.py").write_text(
            f'from typer.testing import CliRunner\nfrom {module}.cli import app\n\nrunner = CliRunner()\n\n\ndef test_hello_endpoint():\n    result = runner.invoke(app, ["hello", "Tester"])\n    assert result.exit_code == 0\n    assert "Hello Tester!" in result.stdout\n',
            encoding="utf-8",
        )

    @staticmethod
    def scaffold_setuptools_cfg(args: argparse.Namespace, target: Path) -> None:
        name = args.name
        user = Pydevtools.load_user_info()
        author = args.author or user.get("name", "")
        email = args.email or user.get("email", "")
        github = user.get("github_username", "")
        url = args.url or (f"https://github.com/{github}/{name}" if github else "")
        author_lines = ""
        if author:
            author_lines += f"author = {author}\n"
        if email:
            author_lines += f"author_email = {email}\n"
        if url:
            author_lines += f"url = {url}\n"
        (target / "setup.py").write_text('__import__("setuptools").setup()\n', encoding="utf-8")
        (target / "setup.cfg").write_text(
            SETUP_CFG_TEMPLATE.format(name=name, version=args.version, author_lines=author_lines),
            encoding="utf-8",
        )
        (target / "pyproject.toml").write_text(PYPROJECT_SETUPTOOLS_CFG.format(name=name), encoding="utf-8")
        (target / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        (target / f"{name}.py").write_text(
            f'"""Main module for {name}."""\n\n\ndef main() -> None:\n    print("Hello from {name}!")\n\n\nif __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )

    @staticmethod
    def cmd_init(args: argparse.Namespace) -> int:
        target = Path(args.name)
        if target.exists() and (not args.force):
            Pydevtools.die(f"directory '{target}' already exists (use --force)")
        target.mkdir(parents=True, exist_ok=True)
        if args.style == "setuptools-src":
            Pydevtools.scaffold_setuptools_src(args, target)
        elif args.style == "hatchling-typer":
            Pydevtools.scaffold_hatchling_typer(args, target)
        elif args.style == "setuptools-cfg":
            Pydevtools.scaffold_setuptools_cfg(args, target)
        else:
            Pydevtools.die(f"unknown style: {args.style}")
        Pydevtools.info(f"Project '{args.name}' initialized in {target}")
        return 0

    @staticmethod
    def cmd_make_setup(args: argparse.Namespace) -> int:
        source = args.source
        target = Path(args.path).expanduser().resolve()
        if source == "pyproject":
            pyproject_path = target if target.is_file() else target / "pyproject.toml"
            if not pyproject_path.is_file():
                Pydevtools.die(f"pyproject.toml not found at {pyproject_path}")
            root = pyproject_path.parent
            data = Pydevtools.load_toml(pyproject_path)
            if "project" in data:
                meta = Pydevtools.extract_pep621_meta(data, root)
            elif "tool" in data and "poetry" in data["tool"]:
                meta = Pydevtools.extract_poetry_meta(data, root)
            else:
                Pydevtools.die("pyproject.toml has neither [project] nor [tool.poetry]")
            if args.style == "runtime":
                content = Pydevtools.render_setup_py_runtime(meta)
            elif args.style == "detailed":
                cfg = Pydevtools.parse_setup_cfg(root / "setup.cfg") if args.with_cfg else None
                content = Pydevtools.render_setup_py_detailed(meta, root, cfg=cfg, with_cfg=args.with_cfg)
            else:
                content = Pydevtools.render_setup_py_simple(meta, root)
            out = root / "setup.py"
            if not Pydevtools.write_text(out, content, force=args.force):
                return 1
            Pydevtools.info(f"Created {out}")
            return 0
        if source == "dir":
            if not target.is_dir():
                Pydevtools.die(f"{target} is not a directory")
            package = args.package or target.name.replace("-", "_")
            content = Pydevtools.render_setup_py_for_dir(target, package)
            out = target / "setup.py"
            if not Pydevtools.write_text(out, content, force=args.force):
                return 1
            Pydevtools.info(f"Created {out}")
            if args.preview:
                Pydevtools.info(content)
            return 0
        if source == "wheel":
            wheel = target
            if not wheel.is_file() or wheel.suffix != ".whl":
                Pydevtools.die(f"{wheel} is not a .whl file")
            tmp = Path(tempfile.mkdtemp(prefix="pydevkit-wheel-"))
            with zipfile.ZipFile(wheel) as zf:
                zf.extractall(tmp)
            try:
                meta = Pydevtools._read_wheel_metadata(tmp)
            except RuntimeError as exc:
                Pydevtools.die(str(exc))
            entry_points = Pydevtools._read_wheel_entry_points(tmp)
            extensions = Pydevtools._find_extensions(tmp)
            out_dir = Path(args.output_dir) if args.output_dir else Path("output") / meta["name"]
            if out_dir.exists() and (not args.force):
                Pydevtools.die(f"{out_dir} exists (use --force)")
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(tmp, out_dir, dirs_exist_ok=True)
            (out_dir / "setup.py").write_text(
                Pydevtools.render_setup_py_from_wheel(meta, extensions, entry_points), encoding="utf-8"
            )
            (out_dir / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["setuptools>=61", "wheel"]\nbuild-backend = "setuptools.build_meta"\n',
                encoding="utf-8",
            )
            Pydevtools.info(f"Generated setup.py for {meta['name']} in {out_dir}")
            Pydevtools.info("binary extensions detected" if extensions else "pure Python package")
            return 0
        Pydevtools.die(f"unknown --from value: {source}")
        return 1

    @staticmethod
    def cmd_dev(args: argparse.Namespace) -> int:
        project = Path(args.path).expanduser().resolve()
        if not project.exists():
            project.mkdir(parents=True)
            Pydevtools.info(f"Created project directory: {project}")
        Pydevtools.info("=" * 40)
        Pydevtools.info("  Python Development Environment Setup")
        Pydevtools.info("=" * 40)
        layout = {
            "src": None,
            "tests": ["__init__.py", "conftest.py"],
            "docs": None,
            "scripts": None,
            ".github/workflows": ["ci.yml"],
        }
        for rel, files in layout.items():
            d = project / rel
            d.mkdir(parents=True, exist_ok=True)
            if files:
                for f in files:
                    (d / f).touch(exist_ok=True)
            else:
                (d / "__init__.py").touch(exist_ok=True)
        pyproject = project / "pyproject.toml"
        if pyproject.exists():
            Pydevtools.info("  pyproject.toml already exists")
        else:
            pyproject.write_text(DEV_PYPROJECT.format(name=project.name), encoding="utf-8")
            Pydevtools.info("  Created: pyproject.toml")
        (project / "requirements.txt").touch(exist_ok=True)
        (project / "requirements-dev.txt").write_text(
            "# Development dependencies\n" + "\n".join(DEV_PACKAGES) + "\n", encoding="utf-8"
        )
        gitignore = project / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(GITIGNORE_DEV, encoding="utf-8")
            Pydevtools.info("  Created: .gitignore")
        if args.install_hooks:
            try:
                (project / ".pre-commit-config.yaml").write_text(PRE_COMMIT_CONFIG, encoding="utf-8")
                subprocess.run(["pre-commit", "install"], cwd=project, check=True)
                Pydevtools.info("  Pre-commit hooks installed")
            except Exception as exc:
                Pydevtools.info(f"  warning: pre-commit setup failed: {exc}")
        Pydevtools.info("")
        Pydevtools.info("Setup complete. Next steps:")
        Pydevtools.info(f"  cd {project}")
        Pydevtools.info("  python -m venv .venv && source .venv/bin/activate")
        Pydevtools.info("  pip install -e '.[dev]'")
        return 0

    @staticmethod
    def cmd_new_script(args: argparse.Namespace) -> int:
        target = Path(args.path)
        if target.exists() and (not args.force):
            Pydevtools.die(f"{target} exists (use --force)")
        target.write_text(NEW_SCRIPT_TEMPLATE, encoding="utf-8")
        target.chmod(target.stat().st_mode | 73)
        Pydevtools.info(f"{target.name} created.")
        return 0

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="pydevkit",
            description="Unified Python/Rust project scaffolding & packaging toolkit.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent(
                "            Original script mapping:\n              create_cargo_toml.py -> cargo-toml\n              create_setuppy.py    -> make-setup --from pyproject --style simple\n              generate_setuppy.py  -> make-setup --from pyproject --style detailed\n              new2old.py           -> make-setup --from pyproject --style detailed --with-cfg\n              mk_setup.py          -> make-setup --from dir <dir>\n              mksetup.py           -> make-setup --from wheel <wheel.whl>\n              mksetuppy.py         -> make-setup --from pyproject --style runtime\n              init_project.py      -> init <name> --style setuptools-src\n              initproj.py          -> init <name> --style hatchling-typer\n              pyproj2.py           -> init <name> --style setuptools-cfg\n              pnew.py              -> new-script <path>\n              py_dev.py            -> dev [path]\n            "
            ),
        )
        sub = parser.add_subparsers(dest="command", required=True)
        p_init = sub.add_parser("init", help="Scaffold a new Python project.")
        p_init.add_argument("name", help="Project / package directory name.")
        p_init.add_argument(
            "--style",
            choices=["setuptools-src", "hatchling-typer", "setuptools-cfg"],
            default="setuptools-src",
        )
        p_init.add_argument("--version", default="0.1.0")
        p_init.add_argument("--description", default="")
        p_init.add_argument("--author", default=None)
        p_init.add_argument("--email", default=None)
        p_init.add_argument("--url", default=None)
        p_init.add_argument("--requires-python", default=">=3.11")
        p_init.add_argument("--simple-cli", action="store_true")
        p_init.add_argument("--force", action="store_true")
        p_init.set_defaults(func=Pydevtools.cmd_init)
        p_ms = sub.add_parser("make-setup", help="Generate setup.py from various sources.")
        p_ms.add_argument("--from", dest="source", choices=["pyproject", "dir", "wheel"], default="pyproject")
        p_ms.add_argument("path", nargs="?", default=".", help="Target path or wheel.")
        p_ms.add_argument("--style", choices=["simple", "detailed", "runtime"], default="detailed")
        p_ms.add_argument("--with-cfg", action="store_true", help="Read setup.cfg (detailed style only).")
        p_ms.add_argument("--package", default=None, help="Package name for --from dir.")
        p_ms.add_argument("--output-dir", default=None, help="Output dir for --from wheel.")
        p_ms.add_argument("--force", action="store_true")
        p_ms.add_argument("--preview", action="store_true")
        p_ms.set_defaults(func=Pydevtools.cmd_make_setup)
        p_ct = sub.add_parser("cargo-toml", help="Generate Cargo.toml from Cargo.lock.")
        p_ct.add_argument("-i", "--input", default="Cargo.lock")
        p_ct.add_argument("-o", "--output", default="Cargo.toml")
        p_ct.add_argument("--root-name", default=None)
        p_ct.add_argument("--root-version", default="0.1.0")
        p_ct.add_argument("--preview", action="store_true")
        p_ct.set_defaults(func=Pydevtools.cmd_cargo_toml)
        p_dev = sub.add_parser("dev", help="Bootstrap dev environment.")
        p_dev.add_argument("path", nargs="?", default=".")
        p_dev.add_argument(
            "--install-hooks", action="store_true", help="Install pre-commit hooks (requires pre-commit)."
        )
        p_dev.set_defaults(func=Pydevtools.cmd_dev)
        p_ns = sub.add_parser("new-script", help="Emit a Python script template.")
        p_ns.add_argument("path")
        p_ns.add_argument("--force", action="store_true")
        p_ns.set_defaults(func=Pydevtools.cmd_new_script)
        return parser

    @staticmethod
    def main(argv: Sequence[str] | None = None) -> int:
        parser = Pydevtools.build_parser()
        args = parser.parse_args(argv)
        try:
            return args.func(args)
        except SystemExit:
            raise
        except (FileNotFoundError, FileExistsError, ValueError, KeyError, RuntimeError, OSError) as exc:
            Pydevtools.die(str(exc))
            return 1


@dataclass
class Author:
    name: str | None = None
    email: str | None = None

    def to_string(self) -> str:
        if self.name and self.email:
            return f"{self.name} <{self.email}>"
        return self.name or self.email or ""


@dataclass
class ProjectMeta:
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    readme_content: str = ""
    readme_content_type: str = "text/plain"
    readme_file: str | None = None
    requires_python: str = ""
    license_text: str = ""
    license_file: str | None = None
    authors: list[Author] = field(default_factory=list)
    maintainers: list[Author] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    classifiers: list[str] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)
    gui_scripts: dict[str, str] = field(default_factory=dict)
    entry_points: dict[str, dict[str, str]] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: dict[str, list[str]] = field(default_factory=dict)
    build_backend: str = ""
    build_requires: list[str] = field(default_factory=list)
    tool: dict[str, Any] = field(default_factory=dict)


GITIGNORE_DEFAULT: str = (
    "__pycache__/\n*.py[cod]\n*.egg-info/\ndist/\nbuild/\n.venv/\nvenv/\nenv/\n.mypy_cache/\n.ruff_cache/\n.pytest_cache/\n.coverage\n"
)
GITIGNORE_DEV = textwrap.dedent(
    "    # Byte-compiled / optimized / DLL files\n    __pycache__/\n    *.py[cod]\n    *$py.class\n    *.so\n    .Python\n    build/\n    develop-eggs/\n    dist/\n    downloads/\n    eggs/\n    .eggs/\n    lib/\n    lib64/\n    parts/\n    sdist/\n    var/\n    wheels/\n    *.egg-info/\n    .installed.cfg\n    *.egg\n    *.manifest\n    *.spec\n    pip-log.txt\n    pip-delete-this-directory.txt\n    htmlcov/\n    .tox/\n    .nox/\n    .coverage\n    .coverage.*\n    .cache\n    nosetests.xml\n    coverage.xml\n    *.cover\n    *.py,cover\n    .hypothesis/\n    .pytest_cache/\n    *.mo\n    *.pot\n    .env\n    .venv\n    env/\n    venv/\n    ENV/\n    env.bak/\n    venv.bak/\n    .idea/\n    .vscode/\n    *.swp\n    *.swo\n    *~\n    .DS_Store\n    Thumbs.db\n    *.log\n    logs/\n    .env.local\n    .env.*.local\n    "
)
PYPROJECT_SETUPTOOLS_SRC: str = (
    '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "{name}"\nversion = "{version}"\n{description_line}authors = [{{name = "{author}"{email_part}}}]\nrequires-python = "{requires_python}"\ndependencies = []\n{urls_block}\n[tool.setuptools.packages.find]\nwhere = ["src"]\n{scripts_block}'
)
PYPROJECT_HATCHLING_TYPER: str = (
    '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n[project]\nname = "{name}"\ndynamic = ["version"]\ndescription = "A standard library and CLI tool built with Typer."\nreadme = "README.md"\nrequires-python = ">=3.10"\nauthors = [\n    {{ name = "{author}", email = "{email}" }}\n]\nclassifiers = [\n    "Programming Language :: Python :: 3",\n    "License :: OSI Approved :: MIT License",\n    "Operating System :: OS Independent",\n]\ndependencies = [\n    "typer>=0.12.0",\n    "rich>=13.0.0",\n]\n[project.optional-dependencies]\ndev = [\n    "pytest>=8.0.0",\n    "black>=24.0.0",\n    "flake8>=7.0.0",\n]\n[project.scripts]\n{name} = "{module}.cli:app"\n{name}-admin = "{module}.admin:app"\n[tool.hatch.version]\npath = "src/{module}/__init__.py"\n[tool.black]\nline-length = 88\ntarget-version = [\'py310\']\n'
)
PYPROJECT_SETUPTOOLS_CFG: str = (
    '[build-system]\nrequires = ["setuptools>=69.0", "wheel"]\nbuild-backend = "setuptools.build_meta"\n[project.scripts]\n{name} = "{name}:main"\n'
)
SETUP_CFG_TEMPLATE: str = (
    "[metadata]\nname = {name}\nversion = {version}\n{author_lines}\n[options]\npy_modules = {name}\npython_requires = >=3.11\n[options.entry_points]\nconsole_scripts =\n    {name} = {name}:main\n"
)
PRE_COMMIT_CONFIG = textwrap.dedent(
    '    repos:\n      - repo: https://github.com/pre-commit/pre-commit-hooks\n        rev: v4.5.0\n        hooks:\n          - id: trailing-whitespace\n          - id: end-of-file-fixer\n          - id: check-yaml\n          - id: check-added-large-files\n          - id: check-merge-conflict\n          - id: debug-statements\n          - id: check-ast\n      - repo: https://github.com/astral-sh/ruff-pre-commit\n        rev: v0.3.7\n        hooks:\n          - id: ruff\n            args: [--fix, --exit-non-zero-on-fix]\n      - repo: https://github.com/psf/black-pre-commit\n        rev: 24.2.0\n        hooks:\n          - id: black\n      - repo: https://github.com/pycqa/isort\n        rev: 5.13.2\n        hooks:\n          - id: isort\n            args: ["--profile", "black"]\n    '
)
DEV_PACKAGES: list[str] = ["ruff", "black", "isort", "mypy", "pytest", "pytest-cov", "pre-commit"]
DEV_PYPROJECT: str = (
    '[build-system]\nrequires = ["setuptools>=61.0", "wheel"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "{name}"\nversion = "0.1.0"\ndescription = "Python project"\nreadme = "README.md"\nrequires-python = ">=3.11"\nlicense = {{text = "MIT"}}\nauthors = [\n    {{name = "Your Name", email = "your.email@example.com"}}\n]\n[project.optional-dependencies]\ndev = [\n    "pyright",\n    "black",\n    "isort",\n    "mypy",\n    "debugpy",\n    "pytest",\n    "pytest-cov",\n    "pre-commit",\n    "pynvim",\n]\n[tool.setuptools.packages.find]\nwhere = ["src"]\n[tool.ruff]\nline-length = 120\ntarget-version = "py312"\nselect = ["E", "F", "I", "N", "W", "UP"]\nignore = ["E501"]\n[tool.ruff.isort]\nprofile = "black"\n[tool.black]\nline-length = 120\ntarget-version = ["py312"]\n[tool.isort]\nprofile = "black"\nline_length = 120\n[tool.mypy]\npython_version = "3.12"\nwarn_return_any = true\nwarn_unused_configs = true\ndisallow_untyped_defs = true\nignore_missing_imports = true\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\npython_files = ["test_*.py"]\npython_functions = ["test_*"]\naddopts = "-v --tb=short"\n'
)
NEW_SCRIPT_TEMPLATE: str = (
    '#!/usr/bin/env python3\n"""Standalone script scaffold."""\nfrom pathlib import Path\nimport sys\ndef process_file(path: Path) -> None:\n    pass\ndef collect_files(args: list[str]) -> list[Path]:\n    if not args:\n        return list(Path.cwd().rglob("*.py"))\n    out: list[Path] = []\n    for arg in args:\n        p = Path(arg)\n        if p.is_file():\n            out.append(p)\n        elif p.is_dir():\n            out.extend(p.rglob("*"))\n    return out\ndef main() -> int:\n    files = collect_files(sys.argv[1:])\n    for f in files:\n        process_file(f)\n    return 0\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
)
if __name__ == "__main__":
    raise SystemExit(Pydevtools.main())
