#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
from dataclasses import dataclass
import json
import sys
import requests


@dataclass
class Package:
    name: str
    version: str
    summary: str
    url: str

    def __str__(self) -> str:
        return f"{self.name} ({self.version})\n  {self.summary}\n  {self.url}"


class PyPISearch:
    BASE_URL = "https://pypi.org/pypi"
    SEARCH_URL = "https://pypi.org/pypi/_/json"

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def search(self, query: str, limit: int | None = None) -> list[Package]:
        try:
            response = requests.get(self.SEARCH_URL, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching PyPI data: {e}", file=sys.stderr)
            return []
        query_lower = query.lower()
        results = []
        for pkg_name, pkg_data in data.get("packages", {}).items():
            if query_lower in pkg_name.lower():
                info = pkg_data.get("latest", {})
                results.append(
                    Package(
                        name=pkg_name,
                        version=info.get("version", "unknown"),
                        summary=info.get("summary", "No description"),
                        url=f"{self.BASE_URL}/{pkg_name}/",
                    )
                )
            if limit and len(results) >= limit:
                break
        return sorted(results, key=lambda p: p.name.lower())

    def search_json(self, query: str, limit: int | None = None) -> str:
        results = self.search(query, limit)
        return json.dumps(
            [
                {"name": pkg.name, "version": pkg.version, "summary": pkg.summary, "url": pkg.url}
                for pkg in results
            ],
            indent=2,
        )


class Psearch:
    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(
            description="Search PyPI packages", formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument("query", help="Package name or keyword to search")
        parser.add_argument(
            "-l", "--limit", type=int, default=None, help="Limit results (default: all matches)"
        )
        parser.add_argument("-j", "--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "-t", "--timeout", type=int, default=10, help="Request timeout in seconds (default: 10)"
        )
        args = parser.parse_args()
        searcher = PyPISearch(timeout=args.timeout)
        if args.json:
            print(searcher.search_json(args.query, args.limit))
        else:
            results = searcher.search(args.query, args.limit)
            if not results:
                print(f"No packages found for '{args.query}'", file=sys.stderr)
                sys.exit(1)
            print(f"Found {len(results)} package(s):\n")
            for pkg in results:
                print(pkg)
                print()


if __name__ == "__main__":
    raise SystemExit(Psearch.main())
