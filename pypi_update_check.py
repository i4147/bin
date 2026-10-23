#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import importlib.metadata
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import signal
import sys
import sysconfig
import time
from typing import Any, Optional, Sequence
from packaging import version
import requests

try:
    import pycurl
except ImportError:
    pycurl = None


class PypiUpdateCheck:
    @staticmethod
    def normalize_name(name: str, mode: str) -> str:
        if mode == "pypi":
            return re.sub("[-_.]+", "-", name).lower()
        if mode == "underscore":
            return name.lower().replace("-", "_")
        return name

    @staticmethod
    def get_installed_packages(normalize: str = "none") -> dict[str, str]:
        packages: dict[str, str] = {}
        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name")
            ver = dist.metadata.get("Version")
            if name and ver:
                packages[PypiUpdateCheck.normalize_name(name, normalize)] = ver
        return dict(sorted(packages.items()))

    @staticmethod
    def load_state(path: Optional[Path]) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "processed_packages" in data:
                return data["processed_packages"]
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def save_state(path: Path, state: dict[str, Any], wrapper: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if wrapper:
            payload = {"last_updated": time.strftime("%Y-%m-%d %H:%M:%S"), "processed_packages": state}
        else:
            payload = state
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def fallback_compare(installed: str, latest: str) -> bool:
        try:
            a = tuple((int(x) for x in installed.split(".")[:3]))
            b = tuple((int(x) for x in latest.split(".")[:3]))
            return b > a
        except Exception:
            return False

    @staticmethod
    def is_upgradable(installed: str, latest: str) -> bool:
        try:
            return version.parse(latest) > version.parse(installed)
        except Exception:
            return PypiUpdateCheck.fallback_compare(installed, latest)

    @staticmethod
    def _fetch_requests(name: str, timeout: int, user_agent: str) -> Optional[dict[str, Any]]:
        url = f"https://pypi.org/pypi/{name}/json"
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            latest = data.get("info", {}).get("version")
            if not latest:
                return None
            download_url = None
            for item in data.get("urls", []):
                if item.get("packagetype") == "sdist":
                    download_url = item.get("url")
                    break
            if not download_url:
                for item in data.get("urls", []):
                    if item.get("url", "").endswith((".tar.gz", ".zip")):
                        download_url = item.get("url")
                        break
            if not download_url:
                info = data.get("info", {})
                download_url = info.get("home_page") or info.get("project_url")
            return {"latest_version": latest, "download_url": download_url, "pypi_name": name}
        except Exception:
            return None

    @staticmethod
    def _fetch_pycurl(name: str, timeout: int, user_agent: str) -> Optional[dict[str, Any]]:
        if pycurl is None:
            msg = "pycurl is not installed"
            raise RuntimeError(msg)
        url = f"https://pypi.org/pypi/{name}/json"
        buf = io.BytesIO()
        curl = pycurl.Curl()
        try:
            curl.setopt(curl.URL, url)
            curl.setopt(curl.WRITEDATA, buf)
            curl.setopt(curl.FOLLOWLOCATION, 1)
            curl.setopt(curl.TIMEOUT, timeout)
            curl.setopt(curl.USERAGENT, user_agent)
            curl.setopt(curl.SSL_VERIFYPEER, 1)
            curl.setopt(curl.SSL_VERIFYHOST, 2)
            curl.perform()
            status = curl.getinfo(curl.RESPONSE_CODE)
        finally:
            curl.close()
        if status != 200:
            return None
        try:
            data = json.loads(buf.getvalue().decode("utf-8"))
        except Exception:
            return None
        latest = data.get("info", {}).get("version")
        if not latest:
            return None
        download_url = None
        for item in data.get("urls", []):
            if item.get("packagetype") == "sdist":
                download_url = item.get("url")
                break
        if not download_url:
            for item in data.get("urls", []):
                if item.get("url", "").endswith((".tar.gz", ".zip")):
                    download_url = item.get("url")
                    break
        if not download_url:
            info = data.get("info", {})
            download_url = info.get("home_page") or info.get("project_url")
        return {"latest_version": latest, "download_url": download_url, "pypi_name": name}

    @staticmethod
    def fetch_pypi_info(
        name: str, backend: str, timeout: int, user_agent: str, name_fallback: bool
    ) -> Optional[dict[str, Any]]:
        candidates = [name]
        if name_fallback:
            alt = name.replace("_", "-")
            if alt != name:
                candidates.append(alt)
        for cand in candidates:
            if backend == "pycurl":
                info = PypiUpdateCheck._fetch_pycurl(cand, timeout, user_agent)
            else:
                info = PypiUpdateCheck._fetch_requests(cand, timeout, user_agent)
            if info:
                return info

    @staticmethod
    def check_one(
        name: str, installed: str, backend: str, timeout: int, user_agent: str, name_fallback: bool
    ) -> dict[str, Any]:
        info = PypiUpdateCheck.fetch_pypi_info(name, backend, timeout, user_agent, name_fallback)
        result: dict[str, Any] = {
            "package": name,
            "installed_version": installed,
            "latest_version": None,
            "download_url": None,
            "pypi_name": None,
            "needs_update": False,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": None,
        }
        if not info:
            result["error"] = "Package not found on PyPI"
            return result
        latest = info["latest_version"]
        result["latest_version"] = latest
        result["download_url"] = info.get("download_url")
        result["pypi_name"] = info.get("pypi_name")
        result["needs_update"] = PypiUpdateCheck.is_upgradable(installed, latest)
        return result

    @staticmethod
    def process_packages(packages: dict[str, str], args: argparse.Namespace) -> dict[str, dict[str, Any]]:
        items = list(packages.items())
        if not items:
            return {}
        if args.workers > 1:
            with multiprocessing.Pool(processes=args.workers) as pool:
                results = pool.starmap(
                    PypiUpdateCheck.check_one,
                    [
                        (name, ver, args.backend, args.timeout, args.user_agent, args.name_fallback)
                        for name, ver in items
                    ],
                    chunksize=max(1, len(items) // args.workers),
                )
        else:
            results = []
            for name, ver in items:
                if args.sleep:
                    time.sleep(args.sleep)
                res = PypiUpdateCheck.check_one(
                    name, ver, args.backend, args.timeout, args.user_agent, args.name_fallback
                )
                results.append(res)
                if args.color:
                    latest = res.get("latest_version") or "?"
                    print(f"{name}: {latest}")
        return {res["package"]: res for res in results}

    @staticmethod
    def filter_pending(
        installed: dict[str, str], state: dict[str, Any], resume: bool, resume_mode: str
    ) -> dict[str, str]:
        if not resume:
            return dict(installed)
        pending: dict[str, str] = {}
        for name, ver in installed.items():
            if name not in state:
                pending[name] = ver
            elif resume_mode == "version" and state[name].get("installed_version") != ver:
                pending[name] = ver
        return pending

    @staticmethod
    def write_output(path: Path, results: dict[str, dict[str, Any]], fmt: str) -> None:
        if fmt == "none":
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            return
        upgradable = [r for r in results.values() if r.get("needs_update")]
        if fmt == "requirements":
            lines = [
                f"{r['package']}=={r['latest_version']}\n"
                for r in sorted(upgradable, key=lambda x: x["package"])
            ]
            path.write_text("".join(lines), encoding="utf-8")
            return
        if fmt == "text":
            lines = ["Package Updates Available\n", "=" * 50 + "\n\n"]
            if not upgradable:
                lines.append("No packages need updating.\n")
            else:
                for r in sorted(upgradable, key=lambda x: x["package"]):
                    lines.append(f"Package: {r['package']}\n")
                    lines.append(f"  Current version: {r['installed_version']}\n")
                    lines.append(f"  Latest version: {r['latest_version']}\n")
                    lines.append(f"  Download URL: {r.get('download_url') or 'N/A'}\n")
                    lines.append("-" * 40 + "\n")
            path.write_text("".join(lines), encoding="utf-8")

    @staticmethod
    def print_summary(results: dict[str, dict[str, Any]]) -> None:
        total = len(results)
        upgradable = [r for r in results.values() if r.get("needs_update")]
        print("=" * 40)
        print("SUMMARY")
        print(f"Total packages: {total}")
        print(f"Upgradable: {len(upgradable)}")
        print(f"Up-to-date: {total - len(upgradable)}")
        if upgradable:
            print("Packages with updates:")
            for r in sorted(upgradable, key=lambda x: x["package"]):
                print(f"  {r['package']}: {r['installed_version']} -> {r['latest_version']}")
        print("=" * 40)

    @staticmethod
    def setup_logging(log_file: Path, level: str) -> None:
        logger = logging.getLogger("pkg_updater")
        logger.setLevel(getattr(logging, level))
        formatter = logging.Formatter("[%(asctime)s]%(levelname)-8s|%(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    @staticmethod
    def add_common_args(parser: argparse.ArgumentParser, defaults: dict[str, Any]) -> None:
        parser.add_argument("--backend", choices=["requests", "pycurl"], default=defaults["backend"])
        parser.add_argument("--workers", type=int, default=defaults["workers"])
        parser.add_argument("--timeout", type=int, default=defaults["timeout"])
        parser.add_argument("--user-agent", default=defaults["user_agent"])
        parser.add_argument(
            "--normalize", choices=["none", "pypi", "underscore"], default=defaults["normalize"]
        )
        parser.add_argument(
            "--name-fallback", action=argparse.BooleanOptionalAction, default=defaults["name_fallback"]
        )
        parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=defaults["resume"])
        parser.add_argument("--resume-mode", choices=["any", "version"], default=defaults["resume_mode"])
        parser.add_argument("--state-file", type=Path, default=defaults["state_file"])
        parser.add_argument(
            "--state-wrapper", action=argparse.BooleanOptionalAction, default=defaults["state_wrapper"]
        )
        parser.add_argument("--output", type=Path, default=defaults["output"])
        parser.add_argument(
            "--output-format",
            choices=["requirements", "json", "text", "none"],
            default=defaults["output_format"],
        )
        parser.add_argument("--log-file", type=Path, default=defaults["log_file"])
        parser.add_argument(
            "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=defaults["log_level"]
        )
        parser.add_argument("--sleep", type=float, default=defaults["sleep"])
        parser.add_argument("--color", action=argparse.BooleanOptionalAction, default=defaults["color"])
        parser.add_argument(
            "--interrupt-handler",
            action=argparse.BooleanOptionalAction,
            default=defaults["interrupt_handler"],
        )

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Unified PyPI package update checker")
        sub = parser.add_subparsers(dest="command", required=True)
        c4u_defaults = {
            "backend": "requests",
            "workers": min(os.cpu_count() or 1, 8),
            "timeout": 10,
            "user_agent": "Package-Updater/1.0",
            "normalize": "none",
            "name_fallback": False,
            "resume": True,
            "resume_mode": "any",
            "state_file": Path("pkgs_state.json"),
            "state_wrapper": False,
            "output": Path("requirements_upgradable.txt"),
            "output_format": "requirements",
            "log_file": Path("pkg_updater.log"),
            "log_level": "DEBUG",
            "sleep": 0.0,
            "color": False,
            "interrupt_handler": False,
        }
        p_c4u = sub.add_parser("c4u")
        PypiUpdateCheck.add_common_args(p_c4u, c4u_defaults)
        c4u2_defaults = {
            "backend": "pycurl",
            "workers": 1,
            "timeout": 15,
            "user_agent": "Package-Updater/1.0",
            "normalize": "none",
            "name_fallback": False,
            "resume": True,
            "resume_mode": "version",
            "state_file": Path("/sdcard/upgradable.json"),
            "state_wrapper": False,
            "output": Path("/sdcard/upgradable.json"),
            "output_format": "json",
            "log_file": None,
            "log_level": "INFO",
            "sleep": 0.0,
            "color": True,
            "interrupt_handler": False,
        }
        p_c4u2 = sub.add_parser("c4u2")
        PypiUpdateCheck.add_common_args(p_c4u2, c4u2_defaults)
        check4update_defaults = {
            "backend": "pycurl",
            "workers": 8,
            "timeout": 30,
            "user_agent": "Package-Checker/1.0",
            "normalize": "pypi",
            "name_fallback": False,
            "resume": False,
            "resume_mode": "any",
            "state_file": None,
            "state_wrapper": False,
            "output": Path(sysconfig.get_paths()["purelib"]) / "requirements.txt",
            "output_format": "requirements",
            "log_file": None,
            "log_level": "INFO",
            "sleep": 0.0,
            "color": False,
            "interrupt_handler": False,
        }
        p_check4update = sub.add_parser("check4update")
        PypiUpdateCheck.add_common_args(p_check4update, check4update_defaults)
        checkforupdate_defaults = {
            "backend": "requests",
            "workers": 1,
            "timeout": 10,
            "user_agent": "Package-Updater/1.0",
            "normalize": "underscore",
            "name_fallback": True,
            "resume": True,
            "resume_mode": "any",
            "state_file": Path.home() / ".package_updates" / "updates_state.json",
            "state_wrapper": True,
            "output": Path.home() / ".package_updates" / "updates.txt",
            "output_format": "text",
            "log_file": None,
            "log_level": "INFO",
            "sleep": 0.5,
            "color": False,
            "interrupt_handler": True,
        }
        p_checkforupdate = sub.add_parser("checkforupdate")
        PypiUpdateCheck.add_common_args(p_checkforupdate, checkforupdate_defaults)
        check_defaults = {
            "backend": "requests",
            "workers": 1,
            "timeout": 10,
            "user_agent": "Package-Updater/1.0",
            "normalize": "none",
            "name_fallback": False,
            "resume": False,
            "resume_mode": "any",
            "state_file": None,
            "state_wrapper": False,
            "output": None,
            "output_format": "requirements",
            "log_file": None,
            "log_level": "INFO",
            "sleep": 0.0,
            "color": False,
            "interrupt_handler": False,
        }
        p_check = sub.add_parser("check")
        PypiUpdateCheck.add_common_args(p_check, check_defaults)
        return parser

    @staticmethod
    def main(argv: Optional[Sequence[str]] = None) -> int:
        parser = PypiUpdateCheck.build_parser()
        args = parser.parse_args(argv)
        if args.log_file:
            PypiUpdateCheck.setup_logging(args.log_file, args.log_level)
        if args.interrupt_handler:

            def _handler(signum: int, frame: Any) -> None:
                print("\nInterrupted by user. State saved.")
                sys.exit(0)

            signal.signal(signal.SIGINT, _handler)
        installed = PypiUpdateCheck.get_installed_packages(args.normalize)
        print(f"Found {len(installed)} installed packages.")
        state = PypiUpdateCheck.load_state(args.state_file) if args.resume and args.state_file else {}
        pending = PypiUpdateCheck.filter_pending(installed, state, args.resume, args.resume_mode)
        print(f"Will check {len(pending)} packages.")
        if pending:
            new_results = PypiUpdateCheck.process_packages(pending, args)
            if args.state_file:
                merged = dict(state)
                merged.update(new_results)
                PypiUpdateCheck.save_state(args.state_file, merged, args.state_wrapper)
                results = merged
            else:
                results = new_results
        else:
            results = state
        if args.output:
            PypiUpdateCheck.write_output(args.output, results, args.output_format)
        PypiUpdateCheck.print_summary(results)
        return 0


if __name__ == "__main__":
    raise SystemExit(PypiUpdateCheck.main())
