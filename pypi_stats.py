#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
import atexit
import calendar
from configparser import ConfigParser
import datetime as dt
import json
from pathlib import Path
import re
import sys
import warnings

__version__ = version = "1.14.0"
__version_tuple__ = version_tuple = (1, 14, 0)
BASE_URL: str = "https://pypistats.org/api/"
USER_AGENT: str = f"pypistats/{__version__}"
_verbose: bool = False
import contextlib
from platformdirs import user_cache_dir
from slugify import slugify

CACHE_DIR: Path = Path(user_cache_dir("pypistats"))
cli = argparse.ArgumentParser()
subparsers = cli.add_subparsers(dest="subcommand")
FORMATS: tuple[str, str, str, str, str, str, str] = ("html", "json", "pretty", "md", "markdown", "rst", "tsv")
common_arguments: list[t.Any] = [
    arg_format,
    arg_json,
    arg_start_date,
    arg_end_date,
    arg_month,
    arg_last_month,
    arg_this_month,
    arg_daily,
    arg_monthly,
    arg_sort,
    arg_color,
    arg_verbose,
]


class PypiStats:
    @staticmethod
    def _print_verbose(*args: t.Any, **kwargs: t.Any) -> None:
        if _verbose:
            print(*args, file=sys.stderr, **kwargs)

    @staticmethod
    def _validate_total(total: t.Any) -> None:
        supported_granularities = ("daily", "monthly", "all")
        if total not in supported_granularities:
            msg = f"total must be one of {supported_granularities}"
            raise ValueError(msg)

    @staticmethod
    def pypi_stats_api(
        endpoint: str,
        params: t.Any = None,
        format: str = "pretty",
        start_date: t.Any = None,
        end_date: t.Any = None,
        sort: bool = True,
        total: str = "all",
        color: str = "yes",
    ) -> str:
        PypiStats._validate_total(total)
        if format == "md":
            format = "markdown"
        if params:
            params = "?" + params
        else:
            params = ""
        url = BASE_URL + endpoint.lower() + params
        cache_file = PypiStats._cache_filename(url)
        if _verbose:
            package = endpoint.split("/")[1]
            human_url = f"https://pypistats.org/packages/{package}"
            PypiStats._print_verbose(f"Human URL:\t{human_url}")
            PypiStats._print_verbose(f"API URL:\t{url}")
            PypiStats._print_verbose(f"Cache file:\t{cache_file}")
        res = {}
        if cache_file.is_file():
            PypiStats._print_verbose("Cache file exists")
            res = PypiStats._cache_load(cache_file)
        if res == {}:
            import urllib3

            r = urllib3.request("GET", url, headers={"User-Agent": USER_AGENT})
            PypiStats._print_verbose("HTTP status code:", r.status)
            if r.status >= 400:
                msg = f"HTTP Error {r.status} for url: {url}"
                raise urllib3.exceptions.HTTPError(msg)
            res = json.loads(r.data.decode("utf-8"))
            PypiStats._cache_save(cache_file, res)
        if not res.get("data", []):
            return f"No data found for https://pypi.org/project/{res.get('package', '')}/"
        first, last = PypiStats._date_range(res["data"])
        if end_date:
            assert first is not None
            if end_date < first:
                msg = f"Requested end date ({end_date}) is before earliest available data ({first}), because data is only available for 180 days. See https://pypistats.org/about#data"
                raise ValueError(msg)
        if start_date:
            assert first is not None
            if start_date < first:
                warnings.warn(
                    f"Requested start date ({start_date}) is before earliest available data ({first}), because data is only available for 180 days. See https://pypistats.org/about#data",
                    stacklevel=3,
                )
        if start_date or end_date:
            res["data"] = PypiStats._filter(res["data"], start_date, end_date)
        if start_date:
            first = start_date
        if end_date:
            last = end_date
        if total == "monthly":
            res["data"] = PypiStats._monthly_total(res["data"])
        elif total == "all":
            res["data"] = PypiStats._total(res["data"])
        if format == "json":
            return json.dumps(res)
        data = res["data"]
        data = PypiStats._percent(data)
        if sort:
            data = PypiStats._sort(data, sort)
        data = PypiStats._grand_total(data)
        if format is None:
            return data
        if color != "no" and format in ("markdown", "pretty", "rst", "tsv"):
            data = PypiStats._colourify(data)
        output = PypiStats._tabulate(data, format, color)
        return (
            f"{output}\nDate range: {first} - {last}\n"
            if first and format not in ["numpy", "pandas"]
            else output
        )

    @staticmethod
    def _filter(data: t.Iterable[t.Any], start_date: t.Any = None, end_date: t.Any = None) -> t.Any:
        temp_data = []
        if start_date:
            for row in data:
                if "date" in row and row["date"] >= start_date:
                    temp_data.append(row)
            data = temp_data
        temp_data = []
        if end_date:
            for row in data:
                if "date" in row and row["date"] <= end_date:
                    temp_data.append(row)
            data = temp_data
        return data

    @staticmethod
    def _sort(data: dict, sort: str = True) -> t.Any:
        if isinstance(data, dict):
            return data
        if sort is False:
            return data
        if sort is True:
            sort = "downloads"
        if not data or sort not in data[0]:
            return data
        reverse = sort in ("downloads", "percent")
        if sort == "percent":
            data = sorted(data, key=lambda k: float(k[sort].rstrip("%")), reverse=reverse)
        else:
            data = sorted(data, key=lambda k: k[sort], reverse=reverse)
        return data

    @staticmethod
    def _monthly_total(data: t.Any) -> t.Any:
        totalled = {}
        for row in data:
            category = row["category"]
            downloads = row["downloads"]
            month = row["date"][:7]
            if category in totalled:
                if month in totalled[category]:
                    totalled[category][month] += downloads
                else:
                    totalled[category][month] = downloads
            else:
                totalled[category] = {month: downloads}
        data = []
        for category, month_downloads in totalled.items():
            for month, downloads in month_downloads.items():
                data.append({"category": category, "date": month, "downloads": downloads})
        return data

    @staticmethod
    def _total(data: t.Any) -> t.Any:
        if isinstance(data, dict):
            return data
        totalled = {}
        for row in data:
            try:
                totalled[row["category"]] += row["downloads"]
            except KeyError:
                totalled[row["category"]] = row["downloads"]
        data = []
        for k, v in totalled.items():
            data.append({"category": k, "downloads": v})
        return data

    @staticmethod
    def _date_range(data: t.Any) -> t.Any:
        if isinstance(data, dict):
            return (None, None)
        try:
            dates = {row["date"] for row in data}
        except KeyError:
            return (None, None)
        return (min(dates), max(dates))

    @staticmethod
    def _grand_total_value(data: t.Iterable[t.Any]) -> t.Any:
        if data[0]["category"] in ["with_mirrors", "without_mirrors"]:
            count_with_mirrors = sum((row["downloads"] for row in data if row["category"] == "with_mirrors"))
            count_without_mirrors = sum(
                (row["downloads"] for row in data if row["category"] == "without_mirrors")
            )
            grand_total = max(count_with_mirrors, count_without_mirrors)
        else:
            grand_total = sum((row["downloads"] for row in data))
        return grand_total

    @staticmethod
    def _grand_total(data: t.Any) -> t.Any:
        if isinstance(data, dict):
            return data
        if len(data) == 1:
            return data
        grand_total = PypiStats._grand_total_value(data)
        new_row = {"category": "Total", "downloads": grand_total}
        data.append(new_row)
        return data

    @staticmethod
    def _percent(data: t.Any) -> t.Any:
        if isinstance(data, dict):
            return data
        if len(data) == 1:
            return data
        grand_total = PypiStats._grand_total_value(data)
        for row in data:
            row["percent"] = "{:.2%}".format(row["downloads"] / grand_total)
        return data

    @staticmethod
    def _colourify(data: t.Iterable[t.Any]) -> t.Any:
        for row in data:
            if "percent" not in row:
                continue
            from termcolor import colored

            percent = float(row["percent"].rstrip("%"))
            if percent <= 5:
                colour = "red"
            elif percent <= 15:
                colour = "yellow"
            else:
                colour = "green"
            row["percent"] = colored(row["percent"], colour)
        return data

    @staticmethod
    def _tabulate(data: t.Any, format_: str = "markdown", color: str = "yes") -> t.Any:
        if isinstance(data, dict):
            headers = list(data.keys())
        else:
            headers = sorted(set().union(*(d.keys() for d in data)))
        headers.append("downloads")
        headers.remove("downloads")
        return (
            PypiStats._dataframe(headers, data, format_)
            if format_ in ("numpy", "pandas")
            else PypiStats._prettytable(headers, data, format_, color)
        )

    @staticmethod
    def _prettytable(headers: t.Iterable[t.Any], data: t.Any, format_: str, color: str = "yes") -> str:
        from prettytable import PrettyTable, TableStyle

        table = PrettyTable()
        if format_ == "html":
            table.border = False
        elif format_ == "markdown":
            table.set_style(TableStyle.MARKDOWN)
        elif format_ == "rst":
            table.set_style(TableStyle.RST)
        elif format_ == "pretty":
            table.set_style(TableStyle.SINGLE_BORDER)
        if isinstance(data, dict):
            data = [data]

        def h(header: t.Any) -> t.Any:
            if color != "no" and format_ == "pretty":
                from termcolor import colored

                return colored(header, attrs=["bold"])
            return header

        for header in headers:
            col_data = [row.get(header, "") for row in data]
            table.add_column(h(header), col_data)
        table.align[h("last_day")] = "r"
        table.align[h("last_month")] = "r"
        table.align[h("last_week")] = "r"
        table.align[h("category")] = "l"
        table.align[h("percent")] = "r"
        table.align[h("downloads")] = "r"
        table.custom_format[h("last_day")] = lambda f, v: f"{v:,}"
        table.custom_format[h("last_month")] = lambda f, v: f"{v:,}"
        table.custom_format[h("last_week")] = lambda f, v: f"{v:,}"
        table.custom_format[h("downloads")] = lambda f, v: f"{v:,}"
        return (
            table.get_html_string(format=True) + "\n"
            if format_ == "html"
            else (
                table.get_csv_string(delimiter="\t", lineterminator="\n")
                if format_ == "tsv"
                else table.get_string() + "\n"
            )
        )

    @staticmethod
    def _dataframe(headers: t.Iterable[t.Any], data: t.Any, format_: str) -> t.Any:
        if isinstance(data, dict):
            data = [data]
        rows = [[row.get(header, "") for header in headers] for row in data]
        if format_ == "numpy":
            import numpy as np

            return np.array(rows, dtype=object)
        import pandas as pd

        return pd.DataFrame(rows, columns=headers)

    @staticmethod
    def _paramify(param_name: t.Any, param_value: bool) -> str:
        if isinstance(param_value, bool):
            param_value = str(param_value).lower()
        if param_value:
            return "&" + param_name + "=" + str(param_value)
        return ""

    @staticmethod
    def recent(package: t.Any, period: t.Any = None, **kwargs: t.Any) -> t.Any:
        endpoint = f"packages/{package}/recent"
        params = PypiStats._paramify("period", period)
        return PypiStats.pypi_stats_api(endpoint, params, **kwargs)

    @staticmethod
    def overall(package: t.Any, mirrors: t.Any = None, **kwargs: t.Any) -> t.Any:
        endpoint = f"packages/{package}/overall"
        params = PypiStats._paramify("mirrors", mirrors)
        return PypiStats.pypi_stats_api(endpoint, params, **kwargs)

    @staticmethod
    def python_major(package: t.Any, version: t.Any = None, **kwargs: t.Any) -> t.Any:
        endpoint = f"packages/{package}/python_major"
        params = PypiStats._paramify("version", version)
        return PypiStats.pypi_stats_api(endpoint, params, **kwargs)

    @staticmethod
    def python_minor(package: t.Any, version: t.Any = None, **kwargs: t.Any) -> t.Any:
        endpoint = f"packages/{package}/python_minor"
        params = PypiStats._paramify("version", version)
        return PypiStats.pypi_stats_api(endpoint, params, **kwargs)

    @staticmethod
    def system(package: t.Any, os: t.Any = None, **kwargs: t.Any) -> t.Any:
        endpoint = f"packages/{package}/system"
        params = PypiStats._paramify("os", os)
        return PypiStats.pypi_stats_api(endpoint, params, **kwargs)

    @staticmethod
    def _cache_filename(url: t.Any) -> float:
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        slug = slugify(url)
        return CACHE_DIR / f"{today}-{slug}.json"

    @staticmethod
    def _cache_load(cache_file: Path) -> t.dict[str, t.Any]:
        if not cache_file.exists():
            return {}
        with cache_file.open("r") as f:
            try:
                data = json.load(f)
            except json.decoder.JSONDecodeError:
                return {}
        return data

    @staticmethod
    def _cache_save(cache_file: t.Any, data: t.Any) -> None:
        try:
            if not CACHE_DIR.exists():
                CACHE_DIR.mkdir(parents=True)
            with cache_file.open("w") as f:
                json.dump(data, f)
        except OSError:
            pass

    @staticmethod
    def _cache_clear() -> None:
        cache_files = CACHE_DIR.glob("**/*.json")
        this_month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        for cache_file in cache_files:
            if not cache_file.name.startswith(this_month):
                cache_file.unlink()

    @staticmethod
    def argument(*name_or_flags: t.Any, **kwargs: t.Any) -> tuple[list[t.Any], t.Any]:
        return (list(name_or_flags), kwargs)

    @staticmethod
    def subcommand(args: t.Optional[t.Iterable[t.Any]] = None, parent: t.Any = subparsers) -> t.Any:
        if args is None:
            args = []

        def decorator(func: t.Any) -> None:
            func2 = globals()[func.__name__]
            parser = parent.add_parser(
                func.__name__,
                description=func2.__doc__,
                formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            )
            for arg in args:
                parser.add_argument(*arg[0], **arg[1])
            parser.set_defaults(func=func)

        return decorator

    @staticmethod
    def _package(value: t.Any) -> t.Any:
        directory = Path(value)
        if not directory.is_dir():
            return value
        pyproject_toml = directory / Path("pyproject.toml")
        if pyproject_toml.exists():
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            data = tomllib.loads(pyproject_toml.read_text())
            try:
                return data["project"]["name"]
            except KeyError:
                msg = "no 'project.name' in pyproject.toml"
        else:
            msg = "pyproject.toml not found"
        setup_cfg = directory / Path("setup.cfg")
        if setup_cfg.exists():
            config = ConfigParser()
            config.read(setup_cfg)
            try:
                return config["metadata"]["name"]
            except KeyError:
                msg += " and no 'metadata.name' in setup.cfg"
        else:
            msg += " and setup.cfg not found"
        if msg == "pyproject.toml not found and setup.cfg not found":
            msg = "pyproject.toml and setup.cfg not found"
        raise argparse.ArgumentTypeError(msg)

    @staticmethod
    def _month_name_to_yyyy_mm(date_string: t.Any, date_format: t.Any) -> t.Any:
        today = dt.date.today()
        new = dt.datetime.strptime(f"{date_string} {today.year}", f"{date_format} %Y").date()
        if new < today:
            return new.isoformat()[:7]
        new = dt.datetime.strptime(f"{date_string} {today.year - 1}", f"{date_format} %Y").date()
        return new.isoformat()[:7]

    @staticmethod
    def _valid_date(date_string: t.Any, date_format: t.Any) -> t.Any:
        try:
            dt.datetime.strptime(date_string, date_format)
            return date_string
        except ValueError:
            msg = f"Not a valid date: '{date_string}'."
            raise argparse.ArgumentTypeError(msg)

    @staticmethod
    def _valid_yyyy_mm_dd(date_string: t.Any) -> t.Any:
        return PypiStats._valid_date(date_string, "%Y-%m-%d")

    @staticmethod
    def _valid_yyyy_mm(date_string: t.Any) -> t.Any:
        with contextlib.suppress(ValueError):
            date_string = PypiStats._month_name_to_yyyy_mm(date_string, "%b")
        with contextlib.suppress(ValueError):
            date_string = PypiStats._month_name_to_yyyy_mm(date_string, "%B")
        return PypiStats._valid_date(date_string, "%Y-%m")

    @staticmethod
    def _valid_yyyy_mm_optional_dd(date_string: t.Any) -> t.Any:
        try:
            return PypiStats._valid_yyyy_mm_dd(date_string)
        except argparse.ArgumentTypeError:
            return PypiStats._valid_yyyy_mm(date_string)

    @staticmethod
    def _define_format(args: argparse.Namespace) -> str:
        if args.json:
            return "json"
        return args.format

    @staticmethod
    def _python_major_version(value: t.Any) -> t.Any:
        pattern = "^\\d+$"
        if not re.match(pattern, value):
            msg = "Invalid major version format. Expected a positive integer value."
            raise argparse.ArgumentTypeError(msg)
        return value

    @staticmethod
    def _python_minor_version(value: t.Any) -> t.Any:
        pattern = "^\\d+\\.\\d+$"
        if not re.match(pattern, value):
            msg = "Invalid minor version format. Expected a positive float value in x.x pattern."
            raise argparse.ArgumentTypeError(msg)
        return value

    @staticmethod
    @PypiStats.subcommand(
        [
            package_argument,
            PypiStats.argument("-p", "--period", choices=("day", "week", "month")),
            arg_format,
            arg_json,
            arg_verbose,
        ]
    )
    def recent(args: argparse.Namespace) -> None:
        print(PypiStats.recent(args.package, period=args.period, format=args.format))

    @staticmethod
    @PypiStats.subcommand(
        [
            package_argument,
            PypiStats.argument("--mirrors", choices=("true", "false", "with", "without")),
            *common_arguments,
        ]
    )
    def overall(args: argparse.Namespace) -> None:
        if args.mirrors in ["with", "without"]:
            args.mirrors = args.mirrors == "with"
        print(
            PypiStats.overall(
                args.package,
                mirrors=args.mirrors,
                start_date=args.start_date,
                end_date=args.end_date,
                format=args.format,
                total="daily" if args.daily else "monthly" if args.monthly else "all",
                sort=args.sort,
                color="no",
            )
        )

    @staticmethod
    @PypiStats.subcommand(
        [
            package_argument,
            PypiStats.argument("-V", "--version", help="eg. 2 or 3", type=PypiStats._python_major_version),
            *common_arguments,
        ]
    )
    def python_major(args: argparse.Namespace) -> None:
        print(
            PypiStats.python_major(
                args.package,
                version=args.version,
                start_date=args.start_date,
                end_date=args.end_date,
                format=args.format,
                total="daily" if args.daily else "monthly" if args.monthly else "all",
                sort=args.sort,
                color=args.color,
            )
        )

    @staticmethod
    @PypiStats.subcommand(
        [
            package_argument,
            PypiStats.argument(
                "-V", "--version", help="eg. 2.7 or 3.6", type=PypiStats._python_minor_version
            ),
            *common_arguments,
        ]
    )
    def python_minor(args: argparse.Namespace) -> None:
        print(
            PypiStats.python_minor(
                args.package,
                version=args.version,
                start_date=args.start_date,
                end_date=args.end_date,
                format=args.format,
                total="daily" if args.daily else "monthly" if args.monthly else "all",
                sort=args.sort,
                color=args.color,
            )
        )

    @staticmethod
    @PypiStats.subcommand(
        [
            package_argument,
            PypiStats.argument("-o", "--os", help="eg. windows, linux, darwin or other"),
            *common_arguments,
        ]
    )
    def system(args: argparse.Namespace) -> None:
        print(
            PypiStats.system(
                args.package,
                os=args.os,
                start_date=args.start_date,
                end_date=args.end_date,
                format=args.format,
                total="daily" if args.daily else "monthly" if args.monthly else "all",
                sort=args.sort,
                color=args.color,
            )
        )

    @staticmethod
    def _month(yyyy_mm: t.Any) -> tuple[str, str]:
        year, month = list(map(int, yyyy_mm.split("-")))
        first = dt.date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        last = dt.date(year, month, last_day)
        return (str(first), str(last))

    @staticmethod
    def _last_month() -> t.Any:
        today = dt.date.today()
        if today.month == 1:
            year, month = (today.year - 1, 12)
        else:
            year, month = (today.year, today.month - 1)
        return PypiStats._month(f"{year}-{month:02d}")

    @staticmethod
    def _this_month() -> t.Any:
        today = dt.date.today()
        return PypiStats._month(today.isoformat()[:7])[0]

    @staticmethod
    def main() -> None:
        cli.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
        args = cli.parse_args()
        if args.subcommand is None:
            cli.print_help()
        else:
            if hasattr(args, "start_date") and args.start_date:
                with contextlib.suppress(ValueError):
                    args.start_date, _ = PypiStats._month(args.start_date)
            if hasattr(args, "end_date") and args.end_date:
                with contextlib.suppress(ValueError):
                    _, args.end_date = PypiStats._month(args.end_date)
            if hasattr(args, "month") and args.month:
                args.start_date, args.end_date = PypiStats._month(args.month)
            elif hasattr(args, "last_month") and args.last_month:
                args.start_date, args.end_date = PypiStats._last_month()
            elif hasattr(args, "this_month") and args.this_month:
                args.start_date = PypiStats._this_month()
            args.format = PypiStats._define_format(args)
            globals()["_verbose"] = args.verbose
            args.func(args)


atexit.register(PypiStats._cache_clear)
arg_start_date = PypiStats.argument(
    "-sd",
    "--start-date",
    metavar="yyyy-mm[-dd]|name",
    type=PypiStats._valid_yyyy_mm_optional_dd,
    help="Start date",
)
arg_end_date = PypiStats.argument(
    "-ed",
    "--end-date",
    metavar="yyyy-mm[-dd]|name",
    type=PypiStats._valid_yyyy_mm_optional_dd,
    help="End date",
)
arg_month = PypiStats.argument(
    "-m",
    "--month",
    metavar="yyyy-mm|name",
    type=PypiStats._valid_yyyy_mm,
    help="Shortcut for -sd & -ed for a single month",
)
arg_last_month = PypiStats.argument(
    "-l", "--last-month", help="Shortcut for -sd & -ed for last month", action="store_true"
)
arg_this_month = PypiStats.argument(
    "-t", "--this-month", help="Shortcut for -sd for this month", action="store_true"
)
arg_json = PypiStats.argument("-j", "--json", action="store_true", help='Shortcut for "-f json"')
arg_daily = PypiStats.argument("-d", "--daily", action="store_true", help="Show daily downloads")
arg_monthly = PypiStats.argument("--monthly", action="store_true", help="Show monthly downloads")
arg_format = PypiStats.argument(
    "-f", "--format", default="pretty", choices=FORMATS, help="The format of output"
)
arg_color = PypiStats.argument(
    "-c", "--color", default="auto", choices=("yes", "no", "auto"), help="Color terminal output"
)
arg_verbose = PypiStats.argument(
    "-v", "--verbose", action="store_true", help="Print debug messages to stderr"
)
arg_sort = PypiStats.argument(
    "-s", "--sort", default="downloads", help="Column to sort by (for example: downloads, date, category)"
)
package_argument = PypiStats.argument(
    "package",
    default=".",
    type=PypiStats._package,
    nargs="?",
    help="package name, or dir to check pyproject.toml/setup.cfg",
)
if __name__ == "__main__":
    PypiStats.main()
