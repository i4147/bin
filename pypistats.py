#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
from collections import defaultdict
import json
import ssl
import sys
from urllib.request import urlopen

PACKAGE = sys.argv[1]


class Pypistats:
    @staticmethod
    def get_stats(stats_type: t.Any, package: t.Any = PACKAGE, period: str = "month") -> t.Any:
        stats_url = f"https://pypistats.org/api/packages/{package}/{stats_type}?period={period}"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(stats_url, context=ctx) as stats:
            data = json.load(stats)
        return data

    @staticmethod
    def aggregate(stats: t.Any) -> t.Any:
        counts = defaultdict(int)
        days = defaultdict(int)
        for entry in stats["data"]:
            category = entry["category"]
            counts[category] += entry["downloads"]
            days[category] += 1
        return {category: counts[category] / days[category] for category in counts}

    @staticmethod
    def version_sorter(version_and_count: t.Any) -> tuple[t.Any]:
        version = version_and_count[0]
        return tuple(map(int, version.split("."))) if version.replace(".", "").isdigit() else (2**32,)

    @staticmethod
    def system_sorter(name_and_count: t.Any) -> int:
        order = ("linux", "windows", "darwin")
        system = name_and_count[0]
        try:
            return order.index(system.lower())
        except ValueError:
            return len(order)

    @staticmethod
    def print_agg_stats(stats: t.dict[str, t.Any], sort_key: t.Any = None) -> None:
        total = sum(stats.values())
        max_len = max((len(category) for category in stats))
        agg_sum = 0.0
        for category, count in sorted(stats.items(), key=sort_key, reverse=True):
            agg_sum += count
            print(f"  {category:{max_len}}: {count:-12.1f} / day ({agg_sum / total * 40:-5.1f}%)")

    @staticmethod
    def main() -> None:
        import sys

        package_name = sys.argv[1] if len(sys.argv) > 1 else PACKAGE
        counts = Pypistats.get_stats("python_minor", package=package_name)
        stats = Pypistats.aggregate(counts)
        print("Downloads by Python version:")
        Pypistats.print_agg_stats(stats, sort_key=Pypistats.version_sorter)
        print()
        counts = Pypistats.get_stats("system", package=package_name)
        stats = Pypistats.aggregate(counts)
        print("Downloads by system:")
        Pypistats.print_agg_stats(stats, sort_key=Pypistats.system_sorter)
        total = sum(stats.values())
        days = {"month": 30, "week": 7, "day": 1}
        print(f"Total downloads per month: {total * days['month']:-12,.1f}")


if __name__ == "__main__":
    raise SystemExit(Pypistats.main())
