#!/usr/bin/env python3
"""Report type-annotation coverage for the root scripts.

Counts parameters, `*args`/`**kwargs`, return annotations and how many of the
parameter annotations are concrete vs `Any`. Also flags missing annotations,
comments and docstrings, i.e. the acceptance criteria of the class-based rewrite.

Usage::

    python3 arena/annotation_stats.py
    python3 arena/annotation_stats.py --dir /path/to/scripts --json
    python3 arena/annotation_stats.py --per-file
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def annotation_text(node: ast.expr | None) -> str:
    return ast.unparse(node) if node is not None else ""


def count_comments(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT and not token.string.startswith("#!"):
                count += 1
    return count


def count_docstrings(tree: ast.Module) -> int:
    total = 1 if ast.get_docstring(tree) else 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            total += 1
    return total


def is_any(text: str) -> bool:
    return text.endswith("Any") or text == "MISSING"


def analyse(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    params: collections.Counter[str] = collections.Counter()
    returns: collections.Counter[str] = collections.Counter()
    methods = 0
    missing: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods += 1
            args = node.args
            names = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            for arg in names:
                if arg.arg in {"self", "cls"}:
                    continue
                text = annotation_text(arg.annotation) or "MISSING"
                params[text] += 1
                if text == "MISSING":
                    missing.append(f"{node.name}({arg.arg})")
            for arg in (args.vararg, args.kwarg):
                if arg is not None:
                    text = annotation_text(arg.annotation) or "MISSING"
                    params[text] += 1
                    if text == "MISSING":
                        missing.append(f"{node.name}(*{arg.arg})")
            ret = annotation_text(node.returns) or "MISSING"
            returns[ret] += 1
            if ret == "MISSING":
                missing.append(f"{node.name} -> ?")
    return {
        "file": path.name,
        "lines": source.count("\n") + 1,
        "functions": methods,
        "params": dict(params),
        "returns": dict(returns),
        "missing": missing,
        "comments": count_comments(path),
        "docstrings": count_docstrings(tree),
    }


def merge(total: collections.Counter[str], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] += value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Annotation coverage for p*.py scripts")
    parser.add_argument("--dir", type=Path, default=REPO_ROOT, help="directory to scan")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--per-file", action="store_true", help="include a per-file breakdown")
    parser.add_argument("--exclude", nargs="*", default=[], help="file names to skip")
    args = parser.parse_args(argv)
    excluded = set(args.exclude)
    files = [path for path in sorted(args.dir.glob("p*.py")) if path.name not in excluded]
    if not files:
        print(f"no p*.py under {args.dir}", file=sys.stderr)
        return 1
    params: collections.Counter[str] = collections.Counter()
    returns: collections.Counter[str] = collections.Counter()
    per_file: list[dict[str, object]] = []
    missing: list[str] = []
    comments = 0
    docstrings = 0
    functions = 0
    lines = 0
    for path in files:
        try:
            result = analyse(path)
        except SyntaxError as error:
            print(f"skip {path.name}: {error}", file=sys.stderr)
            continue
        per_file.append(result)
        merge(params, result["params"])  # type: ignore[arg-type]
        merge(returns, result["returns"])  # type: ignore[arg-type]
        missing.extend(f"{path.name}:{item}" for item in result["missing"])  # type: ignore[union-attr]
        comments += int(result["comments"])  # type: ignore[arg-type]
        docstrings += int(result["docstrings"])  # type: ignore[arg-type]
        functions += int(result["functions"])  # type: ignore[arg-type]
        lines += int(result["lines"])  # type: ignore[arg-type]
    annotated = sum(value for key, value in params.items() if key != "MISSING")
    total_params = annotated + params["MISSING"]
    concrete = total_params - sum(value for key, value in params.items() if is_any(key))
    summary = {
        "files": len(per_file),
        "lines": lines,
        "functions": functions,
        "params": total_params,
        "params_annotated": annotated,
        "params_any": sum(value for key, value in params.items() if is_any(key)),
        "params_concrete": concrete,
        "concrete_ratio": round(concrete / total_params, 4) if total_params else 0.0,
        "returns_annotated": sum(value for key, value in returns.items() if key != "MISSING"),
        "returns_total": sum(returns.values()),
        "missing_annotations": len(missing),
        "comment_lines": comments,
        "docstrings": docstrings,
        "top_param_types": params.most_common(12),
        "top_return_types": returns.most_common(8),
    }
    payload = {"summary": summary, "missing": missing[:50], "files": per_file}
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"files              {summary['files']}")
    print(f"lines              {summary['lines']}")
    print(f"functions          {summary['functions']}")
    print(f"params             {summary['params']} ({summary['params_annotated']} annotated)")
    print(f"  concrete         {summary['params_concrete']} ({summary['concrete_ratio'] * 100:.1f}%)")
    print(f"  Any              {summary['params_any']}")
    print(f"returns            {summary['returns_annotated']}/{summary['returns_total']} annotated")
    print(f"missing            {summary['missing_annotations']}")
    print(f"comment lines      {summary['comment_lines']}")
    print(f"docstrings         {summary['docstrings']}")
    print(f"top param types    {summary['top_param_types']}")
    print(f"top return types   {summary['top_return_types']}")
    for item in missing[:15]:
        print(f"  missing: {item}")
    if args.per_file:
        print()
        for entry in per_file:
            flag = "ok " if not entry["missing"] else "!! "
            print(f"{flag}{entry['file']:28} functions={entry['functions']:3} comments={entry['comments']}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
