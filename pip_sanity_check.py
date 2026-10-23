#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import sys
import importlib
import importlib.metadata
from dh import PKG_MAPPING as pkg_mapping

GREEN: str = "\x1b[32m"
YELLOW: str = "\x1b[33m"
RESET: str = "\x1b[0m"


class PipSanityCheck:
    @staticmethod
    def sanity_check_packages() -> None:
        print(f"Python Version: {sys.version.split()[0]}")
        print("Starting system site-packages sanity check in Termux...\n")
        installed_dists = sorted(importlib.metadata.distributions(), key=lambda d: d.name.lower())
        healthy_packages = []
        problematic_packages = []
        for dist in installed_dists:
            pkg_name = dist.name
            version = dist.version
            top_levels = []
            try:
                top_level_text = dist.read_text("top_level.txt")
                if top_level_text:
                    top_levels = [line.strip() for line in top_level_text.splitlines() if line.strip()]
                    print(f"{pkg_name}: {top_levels}")
            except Exception:
                pass
            if not top_levels:
                top_levels = [pkg_name.replace("-", "_").lower()]
            pkg_ok = True
            error_details = ""
            if pkg_name in pkg_mapping.keys():
                mod_name = pkg_mapping.get(pkg_name)
                try:
                    importlib.import_module(mod_name)
                    continue
                except:
                    pass
            for mod_name in top_levels:
                try:
                    importlib.import_module(mod_name)
                    importlib.import_module(mod_name)
                except Exception as e:
                    pkg_ok = False
                    error_details = f"{type(e).__name__}: {e}"
                    break
            if pkg_ok:
                healthy_packages.append(f"{pkg_name}=={version}")
                print(f"{GREEN}[✓] {pkg_name}   {version}{RESET}")
            else:
                problematic_packages.append({"name": pkg_name, "version": version, "error": error_details})
                print(f"{YELLOW}[✗]  {pkg_name}{RESET}")
        print("\n" + "=" * 50)
        print("Sanity Check Complete:")
        print(f"  - Healthy packages: {len(healthy_packages)}")
        print(f"  - Problematic packages: {len(problematic_packages)}")
        print("=" * 50)
        with open("requirements.txt", "w", encoding="utf-8") as f:
            f.write("# Verified healthy packages\n")
            for pkg in healthy_packages:
                f.write(f"{pkg}\n")
        print("Saved healthy package list to 'requirements.txt'")
        if problematic_packages:
            with open("problematic_packages.log", "w", encoding="utf-8") as f:
                f.write("Package,Version,Error\n")
                for item in problematic_packages:
                    f.write(f"{item['name']},{item['version']},{item['error']}\n")
            print("Saved problematic packages to 'problematic_packages.log'")


if __name__ == "__main__":
    PipSanityCheck.sanity_check_packages()
