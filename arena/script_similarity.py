#!/usr/bin/env python3
"""Measure how similar the root ``*.py`` scripts are to each other.

Similarity is computed from five cheap, explainable signals:

=========================  ======  ================================================
signal                     weight  what it captures
=========================  ======  ================================================
``shingles``               0.30    3-gram Jaccard of the normalised token stream
``tokens``                 0.25    Jaccard of identifier names (functions, vars, attrs)
``definitions``            0.20    Jaccard of function/class names
``imports``                0.15    Jaccard of imported modules (recipes/bindings)
``flags``                  0.10    Jaccard of ``--cli`` options
=========================  ======  ================================================

The output is a JSON document with the per-file features and every pair above
``--min-score``, sorted by score.  ``build_similarity_report.py`` consumes it.

Usage::

    python3 arena/script_similarity.py --min-score 0.2 --output /tmp/pairs.json
    python3 arena/script_similarity.py --top 25
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

WEIGHTS = {
    "shingles": 0.30,
    "tokens": 0.25,
    "definitions": 0.20,
    "imports": 0.15,
    "flags": 0.10,
}
STANDARD_LIBRARY = {
    "__future__",
    "abc",
    "argparse",
    "array",
    "ast",
    "atexit",
    "base64",
    "bisect",
    "builtins",
    "bz2",
    "calendar",
    "codecs",
    "collections",
    "compileall",
    "concurrent",
    "configparser",
    "contextlib",
    "copy",
    "csv",
    "ctypes",
    "dataclasses",
    "datetime",
    "decimal",
    "difflib",
    "dis",
    "email",
    "enum",
    "errno",
    "fnmatch",
    "fractions",
    "functools",
    "glob",
    "grp",
    "gzip",
    "hashlib",
    "heapq",
    "html",
    "http",
    "importlib",
    "inspect",
    "io",
    "itertools",
    "json",
    "locale",
    "logging",
    "lzma",
    "math",
    "mimetypes",
    "mmap",
    "multiprocessing",
    "operator",
    "os",
    "pathlib",
    "pickle",
    "pkgutil",
    "platform",
    "pprint",
    "pwd",
    "queue",
    "random",
    "re",
    "secrets",
    "select",
    "shlex",
    "shutil",
    "signal",
    "site",
    "socket",
    "sqlite3",
    "ssl",
    "stat",
    "statistics",
    "string",
    "struct",
    "subprocess",
    "sys",
    "sysconfig",
    "tarfile",
    "tempfile",
    "termios",
    "textwrap",
    "threading",
    "time",
    "tokenize",
    "tomllib",
    "traceback",
    "tty",
    "types",
    "typing",
    "unicodedata",
    "urllib",
    "uuid",
    "warnings",
    "webbrowser",
    "xml",
    "zipfile",
    "zlib",
}
NOISE_TOKENS = {
    "self",
    "cls",
    "args",
    "kwargs",
    "main",
    "run",
    "path",
    "paths",
    "name",
    "value",
    "data",
    "result",
    "return",
    "none",
    "true",
    "false",
    "print",
    "len",
    "str",
    "int",
    "list",
    "dict",
    "set",
    "bool",
    "any",
    "iter",
    "next",
    "open",
    "read",
    "write",
}
FLAG_RE = re.compile(r"^--?[A-Za-z]")


@dataclass
class Features:
    name: str
    lines: int
    imports: set[str] = field(default_factory=set)
    third_party: set[str] = field(default_factory=set)
    definitions: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    shingles: set[int] = field(default_factory=set)
    docstring: str = ""
    classes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "lines": self.lines,
            "third_party": sorted(self.third_party),
            "definitions": len(self.definitions),
            "flags": sorted(self.flags),
            "classes": self.classes,
        }


def jaccard(left: set[object], right: set[object]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def normalise_token(token: str) -> str:
    token = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", token)
    return token.lower()


def extract(path: Path) -> Features:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source, filename=str(path))
    features = Features(name=path.name, lines=source.count("\n") + 1)
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
        value = tree.body[0].value.value
        features.docstring = value.strip().splitlines()[0][:120] if isinstance(value, str) else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                features.imports.add(root)
                if root not in STANDARD_LIBRARY:
                    features.third_party.add(root)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            root = node.module.split(".")[0]
            features.imports.add(root)
            if root not in STANDARD_LIBRARY:
                features.third_party.add(root)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            features.definitions.add(node.name)
        elif isinstance(node, ast.ClassDef):
            features.definitions.add(node.name)
            features.classes.append(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and FLAG_RE.match(node.value):
            features.flags.add(node.value.split("=")[0])
    tokens: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, ast.arg):
            tokens.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and not FLAG_RE.match(node.value):
            tokens.extend(re.findall(r"[A-Za-z_]{3,}", node.value))
    normalised = [normalise_token(token) for token in tokens]
    features.tokens = {token for token in normalised if token not in NOISE_TOKENS}
    stream = [hashlib.blake2b(token.encode(), digest_size=8).hexdigest() for token in normalised]
    features.shingles = {"".join(stream[i : i + 3]) for i in range(len(stream) - 2)}
    if len(features.shingles) > 40000:
        features.shingles = set(list(features.shingles)[:40000])
    return features


def compare(left: Features, right: Features) -> dict[str, float]:
    scores = {
        "shingles": jaccard(left.shingles, right.shingles),
        "tokens": jaccard(left.tokens, right.tokens),
        "definitions": jaccard(left.definitions, right.definitions),
        "imports": jaccard(left.imports, right.imports),
        "flags": jaccard(left.flags, right.flags),
    }
    scores["score"] = round(sum(WEIGHTS[key] * value for key, value in scores.items()), 4)
    return {key: round(value, 4) for key, value in scores.items()}


def analyse(root: Path, pattern: str) -> tuple[dict[str, Features], list[dict[str, object]]]:
    features: dict[str, Features] = {}
    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        try:
            features[path.name] = extract(path)
        except SyntaxError as error:
            print(f"skip {path.name}: {error}", file=sys.stderr)
    names = sorted(features)
    pairs: list[dict[str, object]] = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            scores = compare(features[left], features[right])
            pairs.append({"left": left, "right": right, **scores})
    pairs.sort(key=lambda pair: pair["score"], reverse=True)
    return features, pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pairwise similarity of p*.py scripts")
    parser.add_argument("--root", type=Path, default=Path("."), help="directory holding the scripts")
    parser.add_argument("--pattern", default="p*.py", help="glob for the scripts")
    parser.add_argument("--min-score", type=float, default=0.18, help="keep pairs at or above this score")
    parser.add_argument("--top", type=int, default=30, help="how many pairs to print")
    parser.add_argument("--output", type=Path, help="write the full result as JSON")
    args = parser.parse_args(argv)
    features, pairs = analyse(args.root, args.pattern)
    kept = [pair for pair in pairs if pair["score"] >= args.min_score]
    print(f"{len(features)} scripts, {len(pairs)} pairs, {len(kept)} above {args.min_score}\n")
    print(f"{'left':26} {'right':26} {'score':>6} {'shing':>6} {'tok':>6} {'defs':>6} {'imp':>6} {'flags':>6}")
    for pair in kept[: args.top]:
        print(
            f"{pair['left']:26} {pair['right']:26} {pair['score']:6.3f} {pair['shingles']:6.3f} "
            f"{pair['tokens']:6.3f} {pair['definitions']:6.3f} {pair['imports']:6.3f} {pair['flags']:6.3f}"
        )
    if args.output:
        payload = {
            "root": str(args.root.resolve()),
            "weights": WEIGHTS,
            "files": {name: feature.summary() for name, feature in features.items()},
            "pairs": kept,
            "all_pairs": len(pairs),
        }
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
