from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import backends
from .engine import FileFinder, Processor, run_paths
from .models import BackendError, FileResult, HintOptions, StripOptions
from .parallel import MODES_BY_NAME, joblib_available

EXIT_OK = 0
EXIT_CHANGED = 1
EXIT_ERROR = 2
DEFAULT_PATTERN = "*.py"


def add_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", type=Path, default=None, metavar="PATH")
    parser.add_argument("-a", "--all", action="store_true", help="walk the given directories (default: .)")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN, help="glob used with --all")
    parser.add_argument("--exclude", action="append", default=[], metavar="DIR", help="directory name to skip")
    parser.add_argument("-j", "--workers", type=int, default=0, help="worker processes (0 = all cores)")
    parser.add_argument(
        "-m",
        "--mode",
        default="mp",
        choices=sorted({key for key in MODES_BY_NAME if key}),
        help="parallel execution strategy",
    )


def add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-n", "--dry-run", action="store_true", help="do not write files")
    parser.add_argument("-d", "--diff", action="store_true", help="print unified diffs")
    parser.add_argument("--backup", action="store_true", help="keep a .bak copy before writing")
    parser.add_argument("--check", action="store_true", help="fail if any file would change")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--no-verify", action="store_true", help="skip the ast.parse safety gate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pystrip",
        description="One comment, docstring, and type-annotation tool for Python sources.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    strip = subparsers.add_parser("strip", help="remove comments, docstrings, or annotations")
    add_target_arguments(strip)
    add_output_arguments(strip)
    strip.add_argument("-b", "--backend", default="ast", help="ast, libcst, or ts")
    strip.add_argument("--comments", action=argparse.BooleanOptionalAction, default=True)
    strip.add_argument("--docstrings", action="store_true", help="remove function and class docstrings")
    strip.add_argument("--module-docstring", action="store_true", help="remove the module docstring only")
    strip.add_argument("--annotations", action="store_true", help="remove type annotations and type comments")
    strip.add_argument("--keep-directives", action=argparse.BooleanOptionalAction, default=True)
    strip.add_argument("--keep-shebang", action=argparse.BooleanOptionalAction, default=True)
    strip.add_argument("--collapse-blank-lines", action="store_true")
    strip.set_defaults(handler=strip_command)

    annotate = subparsers.add_parser("annotate", help="apply type hints from a stub")
    add_target_arguments(annotate)
    add_output_arguments(annotate)
    annotate.add_argument("-b", "--backend", default="libcst", help="only libcst can add hints")
    annotate.add_argument("-s", "--stub-file", type=Path, default=None, help="a .pyi stub for every source")
    annotate.add_argument("-g", "--generate-stub", action="store_true", help="run mypy stubgen per file")
    annotate.add_argument("--no-overwrite", action="store_true", help="keep annotations already present")
    annotate.add_argument("--future-annotations", action="store_true")
    annotate.set_defaults(handler=annotate_command)

    listing = subparsers.add_parser("list", help="show backends and parallel modes")
    listing.set_defaults(handler=list_command)

    finder = subparsers.add_parser("find", help="list the files that would be processed")
    add_target_arguments(finder)
    finder.set_defaults(handler=find_command)
    return parser


def collect_paths(args: argparse.Namespace) -> list[Path]:
    targets = list(args.paths)
    if not targets:
        if not getattr(args, "all", False):
            return []
        targets = [Path(".")]
    pattern = getattr(args, "pattern", DEFAULT_PATTERN)
    roots: list[Path] = []
    for target in targets:
        if target.is_dir() and pattern != DEFAULT_PATTERN:
            roots.extend(sorted(target.rglob(pattern)))
        else:
            roots.append(target)
    finder = FileFinder(exclude=getattr(args, "exclude", ()))
    return finder.discover(roots)


def strip_options(args: argparse.Namespace) -> StripOptions:
    return StripOptions(
        comments=args.comments,
        docstrings=args.docstrings,
        module_docstring=args.module_docstring,
        annotations=args.annotations,
        keep_directives=args.keep_directives,
        keep_shebang=args.keep_shebang,
        collapse_blank_lines=args.collapse_blank_lines,
    )


def hint_options(args: argparse.Namespace) -> HintOptions:
    return HintOptions(
        overwrite=not args.no_overwrite,
        future_annotations=args.future_annotations,
        stub=args.stub_file,
        generate_stub=args.generate_stub,
    )


def build_processor(args: argparse.Namespace) -> Processor:
    return Processor(
        backend_name=args.backend,
        strip=strip_options(args) if args.command == "strip" else StripOptions(comments=False),
        hints=hint_options(args) if args.command == "annotate" else HintOptions(),
        write=not args.dry_run,
        backup=args.backup,
        diff=args.diff,
        verify=not args.no_verify,
    )


def summarize(results: Sequence[FileResult], as_json: bool, quiet: bool) -> None:
    errors = [result for result in results if result.error]
    changed = [result for result in results if result.changed]
    totals = {
        "files": len(results),
        "changed": len(changed),
        "comments": sum(result.comments for result in results),
        "docstrings": sum(result.docstrings for result in results),
        "annotations": sum(result.annotations for result in results),
        "errors": len(errors),
        "bytes_saved": sum(result.before - result.after for result in results),
    }
    if as_json:
        payload = {
            "totals": totals,
            "files": [
                {
                    "path": str(result.path),
                    "backend": result.backend,
                    "comments": result.comments,
                    "docstrings": result.docstrings,
                    "annotations": result.annotations,
                    "before": result.before,
                    "after": result.after,
                    "changed": result.changed,
                    "error": result.error,
                }
                for result in results
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for result in results:
        if result.diff:
            print(result.diff, end="")
    for result in errors:
        print(f"{result.path}: {result.error}", file=sys.stderr)
    if quiet:
        return
    print(
        f"{totals['changed']}/{totals['files']} files changed | "
        f"comments {totals['comments']} | docstrings {totals['docstrings']} | "
        f"annotations {totals['annotations']} | errors {totals['errors']} | "
        f"bytes saved {totals['bytes_saved']}"
    )


def execute(args: argparse.Namespace) -> int:
    paths = collect_paths(args)
    if not paths:
        print("pystrip: no Python files matched", file=sys.stderr)
        return EXIT_ERROR
    processor = build_processor(args)
    try:
        results = run_paths(processor, paths, args.mode, args.workers)
    except (BackendError, ValueError) as error:
        print(f"pystrip: {error}", file=sys.stderr)
        return EXIT_ERROR
    summarize(results, args.as_json, args.quiet)
    if any(result.error for result in results):
        return EXIT_ERROR
    if args.check and any(result.changed for result in results):
        return EXIT_CHANGED
    return EXIT_OK


def strip_command(args: argparse.Namespace) -> int:
    if not (args.comments or args.docstrings or args.module_docstring or args.annotations):
        print("pystrip: pick at least one of --comments, --docstrings, --annotations", file=sys.stderr)
        return EXIT_ERROR
    return execute(args)


def annotate_command(args: argparse.Namespace) -> int:
    if not args.stub_file and not args.generate_stub:
        print("pystrip annotate: pass --stub-file or --generate-stub", file=sys.stderr)
        return EXIT_ERROR
    return execute(args)


def list_command(_args: argparse.Namespace) -> int:
    print("backends:")
    for name in backends.available_backends():
        backend = backends.BACKENDS[name]
        hint_flag = "adds hints" if backend.supports_hints else "strips only"
        print(f"  {name:8} {hint_flag}  ({', '.join(backend.requires) or 'stdlib'})")
    print("modes:")
    for name in sorted({key for key in MODES_BY_NAME if key}):
        note = "" if name not in {"joblib"} or joblib_available() else "  (needs joblib)"
        print(f"  {name}{note}")
    return EXIT_OK


def find_command(args: argparse.Namespace) -> int:
    for path in collect_paths(args):
        print(path)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except BackendError as error:
        print(f"pystrip: {error}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
