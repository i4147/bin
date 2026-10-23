#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import sys
import argparse
import importlib.metadata
import multiprocessing
from pathlib import Path
from pip._internal.commands import main as pip_main


class PipReinstaller:
    @staticmethod
    def installer(batch: t.Any) -> None:
        if not batch:
            return
        print(f"[+] Reinstalling batch: {batch}")
        args = ["install", "--no-deps", "--force-reinstall"] + list(batch)
        try:
            pip_main(args)
        except TypeError:
            pip_main("install", "--no-deps", "--force-reinstall", *batch)

    @staticmethod
    def _inspect_distribution(dist: t.Any) -> t.Any:
        try:
            name = dist.metadata.get("Name", "")
            if not name:
                return None
            if name.lower() in {"numpy", "scipy", "pandas"}:
                return None
            files = dist.files
            if not files:
                return None
            total_size = 0
            is_pure = True
            for f in files:
                if f.suffix.lower() in {".so", ".pyd", ".dll"}:
                    is_pure = False
                    break
                try:
                    p = f.locate()
                    if p.exists() and p.is_file():
                        total_size += p.stat().st_size
                except Exception:
                    pass
            if is_pure:
                return (name, total_size)
        except Exception:
            pass

    @staticmethod
    def get_pure_python_packages_sorted() -> t.Any:
        dists = list(importlib.metadata.distributions())
        print(f"[*] Scanning {len(dists)} distributions using multiprocessing...")
        with multiprocessing.Pool(processes=min(4, multiprocessing.cpu_count())) as pool:
            results = pool.map(PipReinstaller._inspect_distribution, dists)
        packages_info = [res for res in results if res is not None]
        packages_info.sort(key=lambda x: x[1])
        return [pkg[0] for pkg in packages_info]

    @staticmethod
    def chunked(iterable: t.Any, n: t.Any) -> t.Iterator[t.Any]:
        for i in range(0, len(iterable), n):
            yield iterable[i : i + n]

    @staticmethod
    def parse_package_file(file_path: t.Any) -> t.Any:
        path = Path(file_path)
        if not path.exists():
            print(f"[!] Error: File '{file_path}' not found.")
            sys.exit(1)
        pkgs = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and (not line.startswith("#")):
                    pkgs.append(line)
        return pkgs

    @staticmethod
    def run() -> None:
        multiprocessing.freeze_support()
        parser = argparse.ArgumentParser(description="Reinstall pure Python packages or from a list file.")
        parser.add_argument(
            "-f", "--file", type=str, help="Path to a package list file (e.g., requirements.txt)"
        )
        args = parser.parse_args()
        if args.file:
            print(f"[*] Reading packages from file: {args.file}")
            pkgs_to_process = PipReinstaller.parse_package_file(args.file)
            print(f"[*] Loaded {len(pkgs_to_process)} packages from file. Skipping filters and sort.")
        else:
            print("[*] Scanning site-packages folder for pure Python packages...")
            pkgs_to_process = PipReinstaller.get_pure_python_packages_sorted()
            print(f"[*] Found {len(pkgs_to_process)} pure Python packages to reinstall.")
        batches = list(PipReinstaller.chunked(pkgs_to_process, 10))
        if not batches:
            print("[!] No packages found to process.")
            sys.exit(0)
        print(f"[*] Processing {len(batches)} batches sequentially (10 pkgs per batch)...")
        for batch in batches:
            PipReinstaller.installer(batch)
        print("[✓] Reinstallation complete!")


if __name__ == "__main__":
    PipReinstaller.run()
