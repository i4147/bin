#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Final
import urllib.error
import urllib.request
from dotenv import load_dotenv

ENV_PATH: Final[Path] = Path.home() / ".env"
"Location of the .env file that holds GITHUB_TOKEN."
GITHUB_API: Final[str] = "https://api.github.com"
"Base URL for the GitHub REST API."
DEFAULT_BRANCH: Final[str] = "main"
"Branch name used for the initial commit and remote tracking."
VERSION: Final[str] = "1.4.7"
"Version stamped into pyproject.toml, __init__.py, <pkgname>.py, <pkgname>.pyx."
load_dotenv(dotenv_path=ENV_PATH)
GITIGNORE: Final[str] = (
    "__pycache__/\n*.py[codz]\n*$py.class\n*.so\nbuild/\ndist/\neggs/\n.eggs/\n*.egg-info/\n*.egg\nMANIFEST\nhtmlcov/\n.tox/\n.nox/\n.coverage\n.coverage.*\ncoverage.xml\n.hypothesis/\n.pytest_cache/\n.env\n.envrc\n.venv\nuv.lock\n.ruff_cache/\n.mypy_cache/\n.dmypy.json\ndmypy.json\n.pyre/\n.pytype/\nsite/\n"
)
SETUP_PY: Final[str] = (
    '"""Legacy shim for tools that still expect a setup.py.\nAll project metadata lives in pyproject.toml and setup.cfg.\n"""\nfrom setuptools import setup\nsetup()\n'
)
PYPROJECT_PKG_TMPL: Final[str] = (
    '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "{pkgname}"\nversion = "{version}"\nreadme = "README.md"\nauthors = [\n  {{name = "isaac onagh", email = "mkalafsaz@gmail.com"}}\n]\nrequires-python = ">= 3.12"\n[project.scripts]\n{pkgname} = "{pkgname}.cli:main"\n'
)
SETUP_CFG_PKG_TMPL: Final[str] = (
    "[options]\npackage_dir =\n    = src\npackages = find:\npython_requires = >= 3.12\n[options.packages.find]\nwhere = src\n[mypy]\npython_version = 3.12\nstrict = True\nwarn_unused_ignores = True\nwarn_redundant_casts = True\nwarn_unreachable = True\nfiles = src\n[ruff]\nline-length = 120\ntarget-version = py312\n[tool:pytest]\ntestpaths = tests\naddopts = -ra -q\n"
)
INIT_PY_TMPL: Final[str] = '__version__ = "{version}"\n'
CLI_PY_TMPL: Final[str] = (
    'import typer\nfrom rich.console import Console\napp = typer.Typer()\nconsole = Console()\n@app.command()\ndef main() -> None:\n    console.print(\n        "Replace this message by putting your code into {pkgname}.cli.main"\n    )\nif __name__ == "__main__":\n    app()\n'
)
PYPROJECT_SINGLE_TMPL: Final[str] = (
    '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "{pkgname}"\nversion = "{version}"\nreadme = "README.md"\nauthors = [\n  {{name = "isaac onagh", email = "mkalafsaz@gmail.com"}}\n]\nrequires-python = ">= 3.12"\n[project.scripts]\n{pkgname} = "{pkgname}:main"\n'
)
SETUP_CFG_SINGLE_TMPL: Final[str] = (
    "[options]\npy_modules = {pkgname}\npython_requires = >= 3.12\ninstall_requires =\n[mypy]\npython_version = 3.12\nstrict = True\nwarn_unused_ignores = True\nwarn_redundant_casts = True\nwarn_unreachable = True\nfiles = {pkgname}.py\n[ruff]\nline-length = 120\ntarget-version = py312\n[tool:pytest]\ntestpaths = tests\naddopts = -ra -q\n"
)
SINGLE_FILE_PY_TMPL: Final[str] = (
    '"""{pkgname} -- a single-file Python module."""\n__version__ = "{version}"\ndef main() -> None:\n    """Console-script entry point for the ``{pkgname}`` command."""\n    print("Hello from {pkgname}")\nif __name__ == "__main__":\n    main()\n'
)
PYPROJECT_CYTHON_TMPL: Final[str] = (
    '[build-system]\nrequires = ["setuptools", "Cython"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "{pkgname}"\nversion = "{version}"\nreadme = "README.md"\nauthors = [\n  {{name = "isaac onagh", email = "mkalafsaz@gmail.com"}}\n]\nrequires-python = ">= 3.12"\n[project.scripts]\n{pkgname} = "{pkgname}:main"\n'
)
SETUP_CFG_CYTHON_TMPL: Final[str] = (
    "[options]\npython_requires = >= 3.12\ninstall_requires =\n[ruff]\nline-length = 120\ntarget-version = py312\n[tool:pytest]\ntestpaths = tests\naddopts = -ra -q\n"
)
SETUP_PY_CYTHON_TMPL: Final[str] = (
    '"""Build configuration for the Cython extension module ``{pkgname}``.\nUnlike the pure-Python layouts, this file is *required*: setuptools must be\ntold about the C extension via ``ext_modules``, which has no declarative\nequivalent in ``pyproject.toml`` / ``setup.cfg``. All other project metadata\nstill lives in ``pyproject.toml`` and ``setup.cfg``.\n"""\nfrom setuptools import Extension, setup\nfrom Cython.Build import cythonize\n# ``Extension("<name>", ["<src>"])`` builds ``{pkgname}`` from the single\n# Cython source file ``{pkgname}.pyx`` at the project root. Language level 3\n# is the recommended default for Python 3-only code.\nextensions = [\n    Extension("{pkgname}", ["{pkgname}.pyx"]),\n]\nsetup(\n    ext_modules=cythonize(\n        extensions,\n        compiler_directives={{"language_level": "3"}},\n    ),\n)\n'
)
CYTHON_PYX_TMPL: Final[str] = (
    '# cython: language_level=3\n"""{pkgname} -- a Cython extension module."""\n__version__ = "{version}"\ndef main() -> None:\n    """Console-script entry point for the ``{pkgname}`` command."""\n    print("Hello from {pkgname}")\n'
)


class Pyproj:
    @staticmethod
    def run(cmd: list[str], cwd: Path) -> None:
        print(f"$ {' '.join(cmd)}")
        subprocess.run(cmd, cwd=cwd, check=True)

    @staticmethod
    def github_request(
        method: str, url: str, token: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data: bytes | None = json.dumps(payload).encode() if payload is not None else None
        req: urllib.request.Request = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                body: str = resp.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail: str = e.read().decode(errors="replace")
            msg = f"GitHub API error {e.code} on {method} {url}:\n{detail}"
            raise SystemExit(msg) from e

    @staticmethod
    def get_github_token() -> str:
        token: str | None = os.environ.get("GITHUB_TOKEN")
        if not token:
            msg = f"GITHUB_TOKEN not found. Add it to {ENV_PATH}, e.g.\n    GITHUB_TOKEN=ghp_xxx"
            raise SystemExit(msg)
        return token

    @staticmethod
    def get_github_user(token: str) -> str:
        data: dict[str, Any] = Pyproj.github_request("GET", f"{GITHUB_API}/user", token)
        return str(data["login"])

    @staticmethod
    def create_github_repo(
        token: str, name: str, *, private: bool = True, description: str = ""
    ) -> dict[str, Any]:
        return Pyproj.github_request(
            "POST",
            f"{GITHUB_API}/user/repos",
            token,
            {"name": name, "private": private, "description": description, "auto_init": False},
        )

    @staticmethod
    def get_or_create_github_repo(
        token: str, user: str, name: str, *, private: bool = True
    ) -> dict[str, Any]:
        try:
            return Pyproj.github_request("GET", f"{GITHUB_API}/repos/{user}/{name}", token)
        except SystemExit as exc:
            if "404" not in str(exc):
                raise
        print(f"Repo {user}/{name} not found, creating ...")
        return Pyproj.create_github_repo(token, name, private=private)

    @staticmethod
    def render_package_files(pkgname: str) -> dict[Path, str]:
        src_pkg: Path = Path("src") / pkgname
        return {
            Path(".gitignore"): GITIGNORE,
            Path("README.md"): f"# {pkgname}\n",
            Path("pyproject.toml"): PYPROJECT_PKG_TMPL.format(pkgname=pkgname, version=VERSION),
            Path("setup.py"): SETUP_PY,
            Path("setup.cfg"): SETUP_CFG_PKG_TMPL,
            src_pkg / "__init__.py": INIT_PY_TMPL.format(version=VERSION),
            src_pkg / "cli.py": CLI_PY_TMPL.format(pkgname=pkgname),
        }

    @staticmethod
    def render_single_file_files(pkgname: str) -> dict[Path, str]:
        return {
            Path(".gitignore"): GITIGNORE,
            Path("README.md"): f"# {pkgname}\n",
            Path("pyproject.toml"): PYPROJECT_SINGLE_TMPL.format(pkgname=pkgname, version=VERSION),
            Path("setup.py"): SETUP_PY,
            Path("setup.cfg"): SETUP_CFG_SINGLE_TMPL.format(pkgname=pkgname),
            Path(f"{pkgname}.py"): SINGLE_FILE_PY_TMPL.format(pkgname=pkgname, version=VERSION),
        }

    @staticmethod
    def render_cython_files(pkgname: str) -> dict[Path, str]:
        return {
            Path(".gitignore"): GITIGNORE,
            Path("README.md"): f"# {pkgname}\n",
            Path("pyproject.toml"): PYPROJECT_CYTHON_TMPL.format(pkgname=pkgname, version=VERSION),
            Path("setup.py"): SETUP_PY_CYTHON_TMPL.format(pkgname=pkgname),
            Path("setup.cfg"): SETUP_CFG_CYTHON_TMPL,
            Path(f"{pkgname}.pyx"): CYTHON_PYX_TMPL.format(pkgname=pkgname, version=VERSION),
        }

    @staticmethod
    def init_project(pkgname: str, *, layout: str) -> tuple[Path, list[Path], list[Path]]:
        root: Path = Path.cwd() / pkgname
        root.mkdir(parents=True, exist_ok=True)
        if layout == LAYOUT_CYTHON:
            files: dict[Path, str] = Pyproj.render_cython_files(pkgname)
        elif layout == LAYOUT_SINGLE:
            files = Pyproj.render_single_file_files(pkgname)
        elif layout == LAYOUT_PACKAGE:
            files = Pyproj.render_package_files(pkgname)
        else:
            msg = f"Unknown layout: {layout!r}"
            raise ValueError(msg)
        created: list[Path] = []
        skipped: list[Path] = []
        for rel_path, content in files.items():
            abs_path: Path = root / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            if abs_path.exists():
                skipped.append(abs_path)
                continue
            abs_path.write_text(content, encoding="utf-8")
            created.append(abs_path)
        return (root, created, skipped)

    @staticmethod
    def git_init_and_push(root: Path, remote_url: str) -> None:
        Pyproj.run(["git", "init", "-b", DEFAULT_BRANCH], cwd=root)
        Pyproj.run(["git", "add", "."], cwd=root)
        Pyproj.run(["git", "commit", "--allow-empty", "-m", "Initial commit"], cwd=root)
        existing = subprocess.run(
            ["git", "remote"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.split()
        if "origin" in existing:
            Pyproj.run(["git", "remote", "set-url", "origin", remote_url], cwd=root)
        else:
            Pyproj.run(["git", "remote", "add", "origin", remote_url], cwd=root)
        Pyproj.run(["git", "push", "-u", "origin", DEFAULT_BRANCH], cwd=root)

    @staticmethod
    def scrub_remote_token(root: Path, user: str, pkgname: str) -> None:
        clean_url: str = f"https://github.com/{user}/{pkgname}.git"
        Pyproj.run(["git", "remote", "set-url", "origin", clean_url], cwd=root)

    @staticmethod
    def parse_args(argv: list[str]) -> argparse.Namespace:
        parser: argparse.ArgumentParser = argparse.ArgumentParser(
            prog=Path(sys.argv[0]).name,
            description="Scaffold a new Python project. Choose the default src-layout package, a single-file module (-s), or a Cython extension module (-c). With -g, also create a GitHub repo and push the initial commit. Existing files are never overwritten.",
        )
        parser.add_argument("pkgname", help="Python package / module name (also used as the directory name)")
        layout_group = parser.add_mutually_exclusive_group()
        layout_group.add_argument(
            "-s",
            "--single-file",
            action="store_true",
            dest="single_file",
            help="Create a single-file module (<pkgname>.py) instead of the default src-layout package",
        )
        layout_group.add_argument(
            "-c",
            "--cython",
            action="store_true",
            dest="cython",
            help="Create a Cython extension module (<pkgname>.pyx) with a build-enabled setup.py. Mutually exclusive with -s.",
        )
        parser.add_argument(
            "-g",
            "--git",
            action="store_true",
            dest="git",
            help="Also perform git init / commit / push and create the GitHub repository. When omitted, only the local project is scaffolded.",
        )
        return parser.parse_args(argv)

    @staticmethod
    def main() -> None:
        args: argparse.Namespace = Pyproj.parse_args(sys.argv[1:])
        pkgname: str = args.pkgname
        single_file: bool = args.single_file
        cython: bool = args.cython
        do_git: bool = args.git
        if not pkgname.isidentifier():
            msg = f"Error: {pkgname!r} is not a valid Python identifier"
            raise SystemExit(msg)
        if cython:
            layout: str = LAYOUT_CYTHON
            layout_desc: str = "Cython extension module"
        elif single_file:
            layout = LAYOUT_SINGLE
            layout_desc = "single-file module"
        else:
            layout = LAYOUT_PACKAGE
            layout_desc = "src-layout package"
        print(f"Scaffolding {layout_desc} for {pkgname!r}")
        root, created, skipped = Pyproj.init_project(pkgname, layout=layout)
        print(f"Project root: {root}")
        print(f"  created: {len(created)} file(s)")
        for path in created:
            print(f"    + {path.relative_to(root)}")
        if skipped:
            print(f"  skipped (already exist): {len(skipped)} file(s)")
            for path in skipped:
                print(f"    = {path.relative_to(root)}")
        if not do_git:
            print(
                f"\nSkipping git init / commit / push and GitHub repo creation (pass -g to enable).\nLocal project ready at {root}"
            )
            return
        token: str = Pyproj.get_github_token()
        user: str = Pyproj.get_github_user(token)
        print(f"Authenticated as GitHub user: {user}")
        Pyproj.get_or_create_github_repo(token, user, pkgname, private=True)
        push_url: str = f"https://{token}@github.com/{user}/{pkgname}.git"
        Pyproj.git_init_and_push(root, push_url)
        Pyproj.scrub_remote_token(root, user, pkgname)
        print(f"\nDone: https://github.com/{user}/{pkgname}")


LAYOUT_PACKAGE: Final[str] = "package"
LAYOUT_SINGLE: Final[str] = "single"
LAYOUT_CYTHON: Final[str] = "cython"
if __name__ == "__main__":
    Pyproj.main()
