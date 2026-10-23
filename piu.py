#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from importlib.metadata import distributions
import sys
from pip._internal.cli.main import main as pip_main


class Piu:
    @staticmethod
    def get_installed_packages() -> list[str]:
        return [dist.metadata["Name"] for dist in distributions() if dist.metadata["Name"] is not None]

    @staticmethod
    def uninstall(packages: list[str]) -> int:
        args = ["uninstall", "-y", *packages]
        return pip_main(args)

    @staticmethod
    def main() -> None:
        argv: list[str] = sys.argv[1:]
        if not argv:
            print("Usage: python piu.py [-w] <package> [<package> ...]")
            sys.exit(1)
        wildcard: bool = argv[0] == "-w"
        patterns: list[str] = argv[1:] if wildcard else argv
        if not patterns:
            print("Usage: python piu.py [-w] <package> [<package> ...]")
            sys.exit(1)
        installed: list[str] = Piu.get_installed_packages()
        installed_map: dict[str, str] = {name.lower(): name for name in installed}
        matched: set[str] = set()
        if wildcard:
            for pattern in patterns:
                p: str = pattern.lower()
                for name in installed:
                    if p in name.lower():
                        matched.add(name)
        else:
            for pattern in patterns:
                p = pattern.lower()
                if p in installed_map:
                    matched.add(installed_map[p])
        if not matched:
            print("No matching packages found")
            return
        Piu.uninstall(sorted(matched))


if __name__ == "__main__":
    Piu.main()
