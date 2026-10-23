#!/usr/bin/env python3
"""Class-based rewrite tool for the root-level ``p*.py`` scripts.

Pipeline per module:

1. drop comments and docstrings,
2. move module-level functions (and loose ``__main__`` code) into a class:
   the module's only plain class, or a new class named after the module,
3. rewrite references to the lifted names,
4. annotate every signature, module constant and ``__init__`` attribute,
5. simplify constant control flow and remove imports that are now dead,
6. re-emit through ``ast.unparse`` and Black.

Run with ``--dry-run`` to inspect, or with ``--report`` for statistics.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_NAMES = set(dir(builtins))
SIDE_EFFECT_MODULES = {
    "readline",
    "rlcompleter",
    "site",
    "sitecustomize",
    "usercustomize",
    "this",
    "antigravity",
    "setuptools",
    "pkg_resources",
}
EXCEPTION_NAMES = {
    "Exception",
    "BaseException",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "OSError",
    "IOError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "NameError",
    "NotImplementedError",
    "SystemExit",
    "LookupError",
    "ArithmeticError",
    "StopIteration",
    "Warning",
    "Enum",
    "IntEnum",
    "StrEnum",
    "Flag",
    "IntFlag",
}
DYNAMIC_CALL_NAMES = {"globals", "locals", "vars", "exec", "eval", "__import__", "dir", "getattr", "setattr"}
TYPING_CONSTRUCTS = (
    "Any",
    "Optional",
    "Iterable",
    "Iterator",
    "Sequence",
    "Mapping",
    "Callable",
    "NoReturn",
)
SCALAR_CTORS = {"int", "str", "float", "bool", "bytes", "bytearray", "complex"}
SEQ_CTORS = {"list": "list", "tuple": "tuple", "set": "set", "frozenset": "frozenset"}
MAP_CTORS = {"dict": "dict"}
NUMERIC_FUNCS = {"len", "abs", "round", "sum", "min", "max", "hash", "ord", "id"}
TEXT_METHODS = {
    "strip",
    "lstrip",
    "rstrip",
    "lower",
    "upper",
    "replace",
    "join",
    "format",
    "encode",
    "decode",
    "splitlines",
    "title",
    "capitalize",
    "casefold",
    "zfill",
    "center",
    "ljust",
    "rjust",
    "removeprefix",
    "removesuffix",
}
LIST_METHODS = {"append", "extend", "insert", "sort", "reverse", "count", "index"}
SET_METHODS = {"add", "discard", "difference", "intersection", "issubset", "issuperset", "symmetric_difference"}
DICT_METHODS = {"keys", "values", "items", "get", "setdefault", "popitem", "update", "fromkeys"}
PATH_METHODS = {
    "iterdir",
    "glob",
    "rglob",
    "relative_to",
    "mkdir",
    "unlink",
    "resolve",
    "as_posix",
    "write_text",
    "read_text",
    "read_bytes",
    "write_bytes",
    "is_symlink",
    "is_file",
    "is_dir",
    "exists",
    "suffix",
    "stem",
    "parts",
    "parent",
}
PATH_ATTRS = {"suffix", "suffixes", "stem", "parts", "anchor", "parent", "name"}


def first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:140]
    return ""


def camel_case(stem: str) -> str:
    parts = re.split(r"[_\-\s]+", stem)
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "App"


def unparse(node: ast.AST | None) -> str:
    return "" if node is None else ast.unparse(node)


def walk_scope(node: ast.AST):
    """Yield a node and its descendants, without entering nested scopes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield from walk_scope(child)


def target_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    names: set[str] = set()
    for child in ast.iter_child_nodes(node):
        names |= target_names(child)
    return names


def bound_names(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        names: set[str] = set()
        for target in targets:
            names |= target_names(target)
        return names
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return {item.asname or item.name.split(".")[0] for item in stmt.names}
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return target_names(stmt.target)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        names = set()
        for item in stmt.items:
            names |= target_names(item.optional_vars)
        return names
    if isinstance(stmt, ast.Try):
        names = set()
        for handler in stmt.handlers:
            if handler.name:
                names.add(handler.name)
        for sub in (*stmt.body, *stmt.orelse, *stmt.finalbody):
            names |= bound_names(sub)
        return names
    if isinstance(stmt, (ast.If, ast.While)):
        names = set()
        for sub in (*stmt.body, *stmt.orelse):
            names |= bound_names(sub)
        return names
    return set()


def is_main_guard(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If):
        return False
    test = stmt.test
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def module_docstring(tree: ast.Module) -> str:
    if tree.body and isinstance(tree.body[0], ast.Expr):
        value = tree.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return ""


def strip_docstrings(tree: ast.Module) -> int:
    removed = 0

    def strip(body: list[ast.stmt]) -> None:
        nonlocal removed
        while body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            del body[0]
            removed += 1

    strip(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            strip(node.body)
            if not node.body:
                node.body.append(ast.Pass())
    return removed


@dataclass
class Scope:
    bound: set[str] = field(default_factory=set)
    globals: set[str] = field(default_factory=set)


class ScopeAnalysis(ast.NodeVisitor):
    """Tracks function scopes so bare names can be rewritten safely."""

    def __init__(self) -> None:
        self.parents: dict[int, list[ast.AST]] = {}
        self.scopes: dict[int, Scope] = {}
        self._stack: list[ast.AST] = []

    def analyze(self, tree: ast.Module) -> ScopeAnalysis:
        self.visit(tree)
        return self

    def visit(self, node: ast.AST):
        self.parents[id(node)] = list(self._stack)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            scope = Scope()
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, node.args.vararg, node.args.kwarg):
                if arg is not None:
                    scope.bound.add(arg.arg)
            self.scopes[id(node)] = scope
            self._stack.append(node)
            try:
                return super().visit(node)
            finally:
                self._stack.pop()
        if isinstance(node, ast.ClassDef):
            self._stack.append(node)
            try:
                return super().visit(node)
            finally:
                self._stack.pop()
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            scope = Scope()
            for generator in node.generators:
                scope.bound |= target_names(generator.target)
            self.scopes[id(node)] = scope
            self._stack.append(node)
            try:
                return super().visit(node)
            finally:
                self._stack.pop()
        return super().visit(node)

    def _current(self) -> Scope | None:
        for node in reversed(self._stack):
            scope = self.scopes.get(id(node))
            if scope is not None:
                return scope
        return None

    def visit_Global(self, node: ast.Global) -> None:
        scope = self._current()
        if scope is not None:
            scope.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        scope = self._current()
        if scope is not None:
            scope.bound.update(node.names)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            return
        scope = self._current()
        if scope is not None:
            scope.bound.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        scope = self._current()
        if scope is not None:
            for item in node.names:
                scope.bound.add(item.asname or item.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.visit_Import(node)  # type: ignore[arg-type]

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            scope = self._current()
            if scope is not None:
                scope.bound.add(node.name)
        self.generic_visit(node)

    def shapowed(self, node: ast.Name) -> bool:
        passed_function = False
        for scope_node in reversed(self.parents.get(id(node), [])):
            if isinstance(scope_node, ast.ClassDef):
                if passed_function:
                    continue
                if any(getattr(item, "name", None) == node.id for item in scope_node.body):
                    return True
                continue
            scope = self.scopes.get(id(scope_node))
            if scope is None:
                continue
            passed_function = True
            if node.id in scope.globals:
                return False
            if node.id in scope.bound:
                return True
        return False


class DocReplacer(ast.NodeTransformer):
    """Points ``__doc__`` uses at the module summary instead of ``None``."""

    def __init__(self, summary: str) -> None:
        self.summary = summary

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if node.id == "__doc__" and isinstance(node.ctx, ast.Load):
            return ast.Constant(value=self.summary)
        return node


class TypingEnv:
    """Renders typing constructs, reusing what the module already imports."""

    def __init__(self, tree: ast.Module) -> None:
        self.alias: str | None = None
        self.imported: dict[str, str] = {}
        self.from_import: ast.ImportFrom | None = None
        self.typing_import: ast.Import | None = None
        self.needed: set[str] = set()
        bound: set[str] = set()
        for stmt in tree.body:
            bound |= bound_names(stmt)
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for item in stmt.names:
                    if item.name == "typing":
                        self.typing_import = stmt
                        self.alias = item.asname or "typing"
            elif isinstance(stmt, ast.ImportFrom) and stmt.module == "typing":
                self.from_import = stmt
                for item in stmt.names:
                    self.imported[item.name] = item.asname or item.name
        if self.alias is None and not self.imported:
            for candidate in ("t", "_t", "typing_", "_typing"):
                if candidate not in bound:
                    self.alias = candidate
                    break
        self.any = self.bare("Any")

    def bare(self, construct: str) -> str:
        if construct in self.imported:
            return self.imported[construct]
        if self.alias is not None:
            return f"{self.alias}.{construct}"
        self.needed.add(construct)
        return construct

    def generic(self, construct: str, *args: str) -> str:
        return f"{self.bare(construct)}[{', '.join(args)}]"

    def optional(self, inner: str) -> str:
        if inner in {"None", self.any}:
            return self.any
        if "|" in inner:
            return f"{inner} | None"
        return self.generic("Optional", inner)

    def imports(self) -> list[ast.stmt]:
        created: list[ast.stmt] = []
        if self.needed:
            if self.from_import is not None:
                existing = {item.name for item in self.from_import.names}
                for name in sorted(self.needed - existing):
                    self.from_import.names.append(ast.alias(name=name))
            else:
                created.append(ast.ImportFrom(module="typing", names=[ast.alias(name=n) for n in sorted(self.needed)], level=0))
            self.needed.clear()
        if self.alias is not None and self.typing_import is None:
            created.insert(0, ast.Import(names=[ast.alias(name="typing", asname=self.alias)]))
            self.typing_import = created[0]  # type: ignore[assignment]
        return created


class NameRewriter(ast.NodeTransformer):
    """Turns bare references to lifted functions into ``Owner.name``."""

    def __init__(self, owner: str, lifted: set[str], scopes: ScopeAnalysis) -> None:
        self.owner = owner
        self.lifted = lifted
        self.scopes = scopes
        self.rewritten = 0

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if isinstance(node.ctx, ast.Load) and node.id in self.lifted and not self.scopes.shapowed(node):
            self.rewritten += 1
            return ast.Attribute(value=ast.Name(id=self.owner, ctx=ast.Load()), attr=node.id, ctx=ast.Load())
        return node


class UsageHints:
    """Guesses a parameter's type from how the body uses it."""

    def __init__(self, typing: TypingEnv, imported: set[str]) -> None:
        self.typing = typing
        self.imported = imported
        self.any = typing.any

    def of_parameter(self, name: str, body: list[ast.stmt]) -> str | None:
        if name in {"args", "options", "opts", "ns", "namespace"} and "argparse" in self.imported:
            attributes = {
                node.attr
                for stmt in body
                for node in walk_scope(stmt)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == name
            }
            if len(attributes) >= 2:
                return "argparse.Namespace"
        votes: list[str] = []
        for stmt in body:
            for node in walk_scope(stmt):
                votes.extend(self._signals(name, node))
        if not votes:
            return None
        concrete = [vote for vote in votes if vote != self.any]
        if not concrete:
            return self.any
        counts: dict[str, int] = {}
        for vote in concrete:
            counts[vote] = counts.get(vote, 0) + 1
        best, seen = max(counts.items(), key=lambda item: item[1])
        return best if seen >= 2 or len(counts) == 1 else self.any

    def _signals(self, name: str, node: ast.AST) -> list[str]:
        votes: list[str] = []
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Name) and operand.id == name:
                    if any(isinstance(item, ast.Constant) and item.value is None for item in operands):
                        votes.append(self.typing.optional(self.any))
                    for other in operands:
                        if isinstance(other, ast.Constant) and isinstance(other.value, str):
                            votes.append("str")
                        if isinstance(other, ast.Constant) and isinstance(other.value, int) and not isinstance(other.value, bool):
                            votes.append("int")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == name:
                for arg in node.args[1:]:
                    for item in arg.elts if isinstance(arg, ast.Tuple) else [arg]:
                        if isinstance(item, ast.Name) and item.id in BUILTIN_NAMES:
                            votes.append(item.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == name:
            if node.attr in PATH_ATTRS and "Path" in self.imported:
                votes.append("Path")
            if node.attr in {"group", "groupdict", "span", "start", "end"} and "Match" in self.imported:
                votes.append("Match[str]")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == name:
                attr = node.func.attr
                if attr in LIST_METHODS:
                    votes.append(self.typing.generic("list", self.any))
                elif attr in SET_METHODS:
                    votes.append(self.typing.generic("set", self.any))
                elif attr in DICT_METHODS:
                    votes.append(self.typing.generic("dict", "str", self.any))
                elif attr in PATH_METHODS and "Path" in self.imported:
                    votes.append("Path")
                elif attr in TEXT_METHODS:
                    votes.append("str")
        if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.iter, ast.Name) and node.iter.id == name:
            votes.append(self.typing.generic("Iterable", self.any))
        if isinstance(node, ast.BinOp) and isinstance(node.left, ast.Name) and node.left.id == name:
            if isinstance(node.op, (ast.Add, ast.Mod)) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
                votes.append("str")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"open", "print"}:
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == name:
                votes.append("str")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr in {"path", "listdir", "rename", "remove", "stat", "walk", "makedirs", "mkdir"}:
                if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == name:
                    votes.append("str")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "os" and node.func.attr == "join":
                if any(isinstance(arg, ast.Name) and arg.id == name for arg in node.args):
                    votes.append("str")
        return votes


class ExprTyper:
    """Infers annotation text for expressions."""

    def __init__(self, typing: TypingEnv, imported: set[str]) -> None:
        self.typing = typing
        self.imported = imported
        self.any = typing.any

    def of(self, node: ast.AST | None) -> str:
        if node is None:
            return "None"
        if isinstance(node, ast.Constant):
            if node.value is None:
                return "None"
            if isinstance(node.value, bool):
                return "bool"
            for kind, name in ((int, "int"), (float, "float"), (complex, "complex"), (str, "str"), (bytes, "bytes")):
                if type(node.value) is kind:
                    return name
            return self.any
        if isinstance(node, ast.JoinedStr):
            return "str"
        if isinstance(node, ast.List):
            return self._collection("list", node.elts)
        if isinstance(node, ast.Set):
            return self._collection("set", node.elts)
        if isinstance(node, ast.Dict):
            keys = {self.of(key) for key in node.keys}
            values = {self.of(value) for value in node.values}
            key_type = keys.pop() if len(keys) == 1 else "str"
            value_type = values.pop() if len(values) == 1 else self.any
            return self.typing.generic("dict", key_type, value_type)
        if isinstance(node, ast.Tuple):
            if not node.elts:
                return "tuple[()]"
            return "tuple[" + ", ".join(self.of(item) for item in node.elts) + "]"
        if isinstance(node, ast.Name):
            return "bool" if node.id in {"True", "False"} else self.any
        if isinstance(node, ast.UnaryOp):
            return "bool" if isinstance(node.op, ast.Not) else self.of(node.operand)
        if isinstance(node, (ast.BoolOp, ast.Compare)):
            return "bool"
        if isinstance(node, ast.IfExp):
            return self.of(node.body)
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        return self.any

    def _collection(self, kind: str, items: list[ast.expr]) -> str:
        inner = {self.of(item) for item in items}
        element = inner.pop() if len(inner) == 1 else self.any
        return self.typing.generic(kind, element) if kind != "list" else f"list[{element}]"

    def _binop(self, node: ast.BinOp) -> str:
        left, right = self.of(node.left), self.of(node.right)
        if isinstance(node.op, ast.Div):
            return "float"
        if isinstance(node.op, ast.FloorDiv):
            return "int"
        if isinstance(node.op, (ast.Add, ast.Mod)) and "str" in {left, right}:
            return "str"
        if left == right and left != self.any:
            return left
        return self.any

    def _call(self, node: ast.Call) -> str:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in SCALAR_CTORS:
                return func.id
            if func.id in SEQ_CTORS:
                inner = {self.of(arg) for arg in node.args} or {self.any}
                element = inner.pop() if len(inner) == 1 else self.any
                return f"{SEQ_CTORS[func.id]}[{element}]"
            if func.id in MAP_CTORS:
                return self.typing.generic("dict", "str", self.any)
            if func.id in NUMERIC_FUNCS:
                return "int"
            if func.id == "sorted":
                return self.typing.generic("list", self.any)
            if func.id in self.imported:
                return func.id
            return self.any
        if isinstance(func, ast.Attribute):
            if func.attr in {"read_text", "decode", "strip", "lower", "upper", "join", "format", "as_posix"}:
                return "str"
            if func.attr in {"split", "splitlines", "readlines"}:
                return self.typing.generic("list", "str")
            if func.attr in {"read_bytes", "encode"}:
                return "bytes"
            if func.attr in {"exists", "is_file", "is_dir", "is_symlink", "isdigit", "isalpha", "startswith", "endswith"}:
                return "bool"
            if func.attr in {"keys"}:
                return self.typing.generic("list", "str")
            if func.attr in {"values", "items"}:
                return self.typing.generic("list", self.any)
        return self.any


class Annotator:
    def __init__(self, typing: TypingEnv, imported: set[str]) -> None:
        self.typing = typing
        self.hints = UsageHints(typing, imported)
        self.exprs = ExprTyper(typing, imported)
        self.imported = imported
        self.added = 0

    def run(self, tree: ast.Module) -> None:
        for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for stmt in cls.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stmt._in_class = True
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function(node, tree)

    def function(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module) -> None:
        body = fn.body
        method = self._is_method(fn)
        args = fn.args
        positional = [*args.posonlyargs, *args.args]
        for index, arg in enumerate(positional):
            if method and index == 0 and arg.arg in {"self", "cls"}:
                continue
            if arg.annotation is None:
                default = self._default_for(args, positional, index)
                arg.annotation = self._parse(self.parameter(arg, default, body))
                self.added += 1
        for arg in (*args.kwonlyargs,):
            if arg.annotation is None:
                arg.annotation = self._parse(self.parameter(arg, None, body))
                self.added += 1
        for arg in (args.vararg, args.kwarg):
            if arg is not None and arg.annotation is None:
                arg.annotation = self._parse(self.typing.any)
                self.added += 1
        if fn.returns is None:
            fn.returns = self._parse(self.return_type(fn))
            self.added += 1

    def _is_method(self, fn: ast.AST) -> bool:
        return getattr(fn, "_in_class", False)

    @staticmethod
    def _default_for(args: ast.arguments, positional: list[ast.arg], index: int) -> ast.expr | None:
        offset = len(positional) - len(args.defaults)
        if index >= offset:
            return args.defaults[index - offset]
        return None

    def _parse(self, text: str) -> ast.expr | None:
        if text in {"", "None"} and text == "":
            return None
        try:
            return ast.parse(text, mode="eval").body
        except SyntaxError:
            return None

    def parameter(self, arg: ast.arg, default: ast.expr | None, body: list[ast.stmt]) -> str:
        if default is not None and isinstance(default, ast.Constant) and default.value is None:
            hint = self.hints.of_parameter(arg.arg, body)
            return self.typing.optional(hint) if hint else self.typing.optional(self.typing.any)
        hint = self.hints.of_parameter(arg.arg, body)
        if hint is not None:
            return hint
        if default is not None:
            if isinstance(default, ast.Constant) and isinstance(default.value, bool):
                return "bool"
            inferred = self.exprs.of(default)
            if inferred != self.typing.any:
                return inferred
        return self.typing.any

    def return_type(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        if fn.name == "__init__":
            return "None"
        if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for stmt in fn.body for node in walk_scope(stmt)):
            inner = {self.exprs.of(node.value) for stmt in fn.body for node in walk_scope(stmt) if isinstance(node, ast.Yield)}
            element = inner.pop() if len(inner) == 1 else self.typing.any
            return self.typing.generic("Iterator", element)
        values: set[str] = set()
        for stmt in fn.body:
            for node in walk_scope(stmt):
                if isinstance(node, ast.Return):
                    values.add(self.exprs.of(node.value))
        if not values:
            return "None"
        if len(values) == 1:
            only = values.pop()
            return "None" if only == "None" and not any(isinstance(node, ast.Return) for stmt in fn.body for node in walk_scope(stmt)) else only
        concrete = {value for value in values if value != self.typing.any}
        if len(concrete) == 1:
            return concrete.pop()
        return self.typing.any

    def constants(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                name = stmt.targets[0].id
                if name.startswith("__"):
                    continue
                inferred = self.exprs.of(stmt.value)
                if inferred == self.typing.any:
                    continue
                annotation = self._parse(inferred)
                if annotation is None:
                    continue
                stmt.__class__ = ast.AnnAssign
                stmt.target = stmt.targets[0]
                stmt.annotation = annotation
                stmt.simple = 1
                del stmt.targets
                self.added += 1

    def attributes(self, tree: ast.Module) -> None:
        for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            seen: set[str] = set()
            for fn in cls.body:
                if not isinstance(fn, ast.FunctionDef) or fn.name != "__init__":
                    continue
                for stmt in fn.body:
                    if not isinstance(stmt, ast.Assign):
                        continue
                    for target in stmt.targets:
                        if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"):
                            continue
                        if target.attr in seen:
                            continue
                        seen.add(target.attr)
                        inferred = self.exprs.of(stmt.value)
                        if inferred == self.typing.any:
                            continue
                        annotation = self._parse(inferred)
                        if annotation is None:
                            continue
                        stmt.__class__ = ast.AnnAssign
                        stmt.target = target
                        stmt.annotation = annotation
                        stmt.simple = 0
                        del stmt.targets
                        self.added += 1
                        break


class Simplifier:
    """Folds provably redundant branches; never changes control flow."""

    def run(self, tree: ast.Module) -> None:
        tree.body = self.block(tree.body, final=False)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.body = self.function_body(node.body)
            elif isinstance(node, ast.ClassDef):
                node.body = self.block(node.body, final=False)

    def function_body(self, body: list[ast.stmt]) -> list[ast.stmt]:
        simplified = self.block(body, final=True)
        return self.drop_trailing_none(simplified)

    def block(self, body: list[ast.stmt], final: bool) -> list[ast.stmt]:
        result: list[ast.stmt] = []
        for index, stmt in enumerate(body):
            is_final = final and index == len(body) - 1
            produced = self.statement(stmt, is_final)
            if isinstance(produced, list):
                result.extend(produced)
            elif produced is not None:
                result.append(produced)
        return result

    def statement(self, stmt: ast.stmt, final: bool) -> ast.stmt | list[ast.stmt] | None:
        if isinstance(stmt, ast.If):
            return self.branch(stmt, final)
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            stmt.body = self.block(stmt.body, final=False)
            stmt.orelse = self.block(stmt.orelse, final=False)
            return stmt
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            stmt.body = self.block(stmt.body, final=False)
            return stmt
        if isinstance(stmt, ast.Try):
            stmt.body = self.block(stmt.body, final=False)
            stmt.orelse = self.block(stmt.orelse, final=False)
            stmt.finalbody = self.block(stmt.finalbody, final=False)
            for handler in stmt.handlers:
                handler.body = self.block(handler.body, final=False)
            return stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stmt.body = self.function_body(stmt.body)
            return stmt
        if isinstance(stmt, ast.ClassDef):
            stmt.body = self.block(stmt.body, final=False)
            return stmt
        return stmt

    def branch(self, stmt: ast.If, final: bool) -> ast.stmt | list[ast.stmt]:
        if isinstance(stmt.test, ast.Constant) and isinstance(stmt.test.value, bool):
            chosen = stmt.body if stmt.test.value else stmt.orelse
            return self.block(chosen, final=final)
        if stmt.orelse:
            stmt.orelse = [item for item in self.block(stmt.orelse, final=False) if not isinstance(item, ast.Pass)]
            folded = self.ternary_tail(stmt)
            if folded is not None:
                return folded
        elif final:
            folded = self.boolean_tail(stmt)
            if folded is not None:
                return folded
        stmt.body = self.block(stmt.body, final=False)
        return stmt

    @staticmethod
    def boolean_tail(stmt: ast.If) -> ast.Return | None:
        if len(stmt.body) == 1 and isinstance(stmt.body[0], ast.Return):
            value = stmt.body[0].value
            if isinstance(value, ast.Constant) and value.value is True:
                return ast.Return(value=ast.Call(func=ast.Name(id="bool", ctx=ast.Load()), args=[stmt.test], keywords=[]))
        return None

    @staticmethod
    def ternary_tail(stmt: ast.If) -> ast.Return | None:
        if len(stmt.body) == 1 and len(stmt.orelse) == 1:
            first, second = stmt.body[0], stmt.orelse[0]
            if isinstance(first, ast.Return) and isinstance(second, ast.Return) and first.value and second.value:
                return ast.Return(value=ast.IfExp(test=stmt.test, body=first.value, orelse=second.value))
        return None

    @staticmethod
    def drop_trailing_none(body: list[ast.stmt]) -> list[ast.stmt]:
        if len(body) > 1 and isinstance(body[-1], ast.Return):
            value = body[-1].value
            if value is None or (isinstance(value, ast.Constant) and value.value is None):
                return body[:-1]
        return body


class ImportPruner:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.removed = 0

    def run(self) -> int:
        if self._is_dynamic():
            return 0
        used = self._used_names()
        self._prune(self.tree.body, used)
        return self.removed

    def _is_dynamic(self) -> bool:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in DYNAMIC_CALL_NAMES:
                if node.func.id in {"getattr", "setattr", "dir"}:
                    continue
                return True
            if isinstance(node, ast.Name) and node.id == "__dict__":
                return True
        return False

    def _used_names(self) -> set[str]:
        used: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                current: ast.AST = node
                while isinstance(current, ast.Attribute):
                    current = current.value
                if isinstance(current, ast.Name):
                    used.add(current.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.update(re.findall(r"[A-Za-z_]\w*", node.value))
        return used

    def _prune(self, body: list[ast.stmt], used: set[str]) -> None:
        kept: list[ast.stmt] = []
        for stmt in body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                names = self._filter(stmt, used)
                if names:
                    kept.append(stmt)
                continue
            if isinstance(stmt, ast.Try):
                self._prune(stmt.body, used)
                self._prune(stmt.orelse, used)
                self._prune(stmt.finalbody, used)
                for handler in stmt.handlers:
                    self._prune(handler.body, used)
            elif isinstance(stmt, ast.If):
                self._prune(stmt.body, used)
                self._prune(stmt.orelse, used)
            kept.append(stmt)
        body[:] = kept

    def _filter(self, stmt: ast.Import | ast.ImportFrom, used: set[str]) -> bool:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            return True
        if any(item.name == "*" for item in stmt.names):
            return True
        module_names = {item.name for item in stmt.names}
        if module_names & SIDE_EFFECT_MODULES:
            return True
        kept = []
        for item in stmt.names:
            local = item.asname or item.name.split(".")[0]
            if local in used:
                kept.append(item)
            else:
                self.removed += 1
        if not kept:
            return False
        stmt.names = kept
        return True


@dataclass
class FileReport:
    name: str
    owner: str
    methods: int
    lifted: int
    rewritten_refs: int
    annotations: int
    imports_removed: int
    docstrings_removed: int
    status: str = "ok"


class Transformer:
    def __init__(self, source: str, stem: str) -> None:
        self.source = source
        self.stem = stem
        self.tree = ast.parse(source)
        self.typing = TypingEnv(self.tree)
        self.scopes = ScopeAnalysis()

    def run(self) -> tuple[str, FileReport]:
        tree = self.tree
        self.module_summary = first_line(module_docstring(tree))
        docstrings = strip_docstrings(tree)
        self.scopes.analyze(tree)
        imported = {name for stmt in tree.body for name in bound_names(stmt)} - BUILTIN_NAMES
        functions = [stmt for stmt in tree.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [stmt for stmt in tree.body if isinstance(stmt, ast.ClassDef)]
        guard = next((stmt for stmt in tree.body if is_main_guard(stmt)), None)
        blocked = self._blocked_names(functions)
        liftable = [fn for fn in functions if fn.name not in blocked]
        lifted = {fn.name for fn in liftable}
        owner = self._pick_owner(classes, liftable)
        rewriter = NameRewriter(owner.name, lifted, self.scopes)
        for fn in liftable:
            rewritten = rewriter.visit(fn)
            rewritten._in_class = True
            rewritten.decorator_list = [ast.Name(id="staticmethod", ctx=ast.Load()), *rewritten.decorator_list]
            owner.body.append(rewritten)
        owner.body = [stmt for stmt in owner.body if not isinstance(stmt, ast.Pass)]
        entry_name = None
        if guard is not None:
            guard_body = [rewriter.visit(stmt) for stmt in guard.body]
            entry_name = self._entry_method(owner, guard_body)
        for stmt in tree.body:
            if stmt is guard or stmt is owner or stmt in liftable:
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and stmt not in classes:
                continue
            rewriter.visit(stmt)
        if not owner.body:
            owner.body.append(ast.Pass())
        tree.body = self._assemble(tree, owner, guard, entry_name, liftable)
        annotator = Annotator(self.typing, imported)
        annotator.run(tree)
        annotator.constants(tree)
        annotator.attributes(tree)
        self._drop_stale_all(tree, lifted)
        if self.module_summary:
            DocReplacer(self.module_summary).visit(tree)
        Simplifier().run(tree)
        pruner = ImportPruner(tree)
        pruner.run()
        ast.fix_missing_locations(tree)
        return ast.unparse(tree), FileReport(
            name=f"{self.stem}.py",
            owner=owner.name,
            methods=sum(1 for stmt in owner.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))),
            lifted=len(lifted),
            rewritten_refs=rewriter.rewritten,
            annotations=annotator.added,
            imports_removed=pruner.removed,
            docstrings_removed=docstrings,
        )

    def _blocked_names(self, functions: list[ast.AST]) -> set[str]:
        blocked: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                blocked.update(node.names)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
                blocked.add(node.id)
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for generator in node.generators:
                    blocked |= target_names(generator.target)
        return blocked

    def _pick_owner(self, classes: list[ast.ClassDef], liftable: list[ast.AST]) -> ast.ClassDef:
        if len(classes) == 1 and self._is_main_class(classes[0]):
            return classes[0]
        name = camel_case(self.stem)
        taken = {cls.name for cls in classes} | {
            stmt.name for stmt in self.tree.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        while name in taken:
            name += "App"
        return ast.ClassDef(name=name, bases=[], keywords=[], body=[ast.Pass()], decorator_list=[])

    @staticmethod
    def _is_main_class(cls: ast.ClassDef) -> bool:
        """A plain class that owns the module's entry point can absorb the helpers."""
        if cls.bases or cls.decorator_list:
            return False
        methods = {stmt.name for stmt in cls.body if isinstance(stmt, ast.FunctionDef)}
        if not methods & {"run", "main", "cli", "execute", "start", "process"}:
            return False
        fields = sum(1 for stmt in cls.body if isinstance(stmt, ast.AnnAssign))
        return fields * 2 <= len(cls.body)

    def _entry_method(self, owner: ast.ClassDef, body: list[ast.stmt]) -> str | None:
        if self._is_simple_entry(body):
            return None
        taken = {stmt.name for stmt in owner.body if isinstance(stmt, ast.FunctionDef)}
        name = "run"
        while name in taken:
            name += "_"
        method = ast.FunctionDef(
            name=name,
            args=ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
            body=body or [ast.Pass()],
            decorator_list=[ast.Name(id="staticmethod", ctx=ast.Load())],
            returns=None,
            type_comment=None,
        )
        method._in_class = True
        owner.body.append(method)
        return name

    @staticmethod
    def _is_simple_entry(body: list[ast.stmt]) -> bool:
        if len(body) != 1:
            return False
        stmt = body[0]
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            return True
        return isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call)

    def _drop_stale_all(self, tree: ast.Module, lifted: set[str]) -> None:
        kept: list[ast.stmt] = []
        for stmt in tree.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "__all__"
                and isinstance(stmt.value, ast.List)
            ):
                elements = [item for item in stmt.value.elts if not (isinstance(item, ast.Constant) and item.value in lifted)]
                if not elements:
                    continue
                stmt.value.elts = elements
            kept.append(stmt)
        tree.body = kept

    def _assemble(
        self,
        tree: ast.Module,
        owner: ast.ClassDef,
        guard: ast.If | None,
        entry_name: str | None,
        liftable: list[ast.AST],
    ) -> list[ast.stmt]:
        body_before = list(tree.body)
        index_of: dict[str, int] = {}
        for index, stmt in enumerate(body_before):
            for name in bound_names(stmt):
                index_of[name] = index
        lifted = {fn.name for fn in liftable}
        first_lifted = min((index for index, stmt in enumerate(body_before) if stmt in liftable), default=len(body_before))
        owner_index = index_of.get(owner.name, first_lifted)
        for name in self._definition_time_names(owner):
            if name in index_of:
                owner_index = max(owner_index, index_of[name] + 1)
        owner_index = min(owner_index, len(body_before))
        watched = lifted | {owner.name}
        kept: list[tuple[int, ast.stmt]] = []
        deferred: list[ast.stmt] = []
        for index, stmt in enumerate(body_before):
            if stmt is guard or stmt in liftable or stmt is owner:
                continue
            if index < owner_index and not isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and self._references(stmt, watched):
                deferred.append(stmt)
            else:
                kept.append((index, stmt))
        before = [stmt for index, stmt in kept if index < owner_index]
        after = [stmt for index, stmt in kept if index >= owner_index]
        future = next(
            (stmt for stmt in before if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"),
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        )
        before = [stmt for stmt in before if not (isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__")]
        body: list[ast.stmt] = [future, *self.typing.imports(), *before, owner, *deferred, *after]
        if guard is not None:
            if entry_name is not None:
                call = ast.Call(
                    func=ast.Attribute(value=ast.Name(id=owner.name, ctx=ast.Load()), attr=entry_name, ctx=ast.Load()),
                    args=[],
                    keywords=[],
                )
                body.append(ast.If(test=guard.test, body=[ast.Expr(value=call)], orelse=[]))
            else:
                body.append(guard)
        return body

    @staticmethod
    def _definition_time_names(owner: ast.ClassDef) -> set[str]:
        names: set[str] = set()

        def collect(node: ast.AST | None) -> None:
            if node is None:
                return
            names.update(item.id for item in ast.walk(node) if isinstance(item, ast.Name))

        for decorator in owner.decorator_list:
            collect(decorator)
        for base in owner.bases:
            collect(base)
        local: set[str] = set()
        for stmt in owner.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local.add(stmt.name)
                for decorator in stmt.decorator_list:
                    collect(decorator)
                for default in (*stmt.args.defaults, *stmt.args.kw_defaults):
                    collect(default)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    local |= target_names(target)
                collect(stmt.value)
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                local |= target_names(stmt.target)
                collect(stmt.value)
        return names - local - BUILTIN_NAMES

    @staticmethod
    def _references(stmt: ast.stmt, names: set[str]) -> bool:
        if not names:
            return False
        return any(isinstance(node, ast.Name) and node.id in names for node in ast.walk(stmt))

def format_with_black(source: str) -> str:
    try:
        import black
    except ImportError:
        return source
    try:
        return black.format_str(source, mode=black.Mode(line_length=110))
    except Exception:
        return source


def rewrite_file(path: Path, target: Path, dry_run: bool) -> FileReport:
    source = path.read_text(encoding="utf-8")
    try:
        rewritten, report = Transformer(source, path.stem).run()
    except Exception as error:  # noqa: BLE001
        return FileReport(path.name, "-", 0, 0, 0, 0, 0, 0, f"parse-error: {type(error).__name__}: {error}")
    try:
        ast.parse(rewritten)
    except SyntaxError as error:
        return FileReport(path.name, report.owner, 0, 0, 0, 0, 0, 0, f"unparse-error: {error}")
    formatted = format_with_black(rewritten)
    try:
        ast.parse(formatted)
    except SyntaxError:
        formatted = rewritten
    formatted = re.sub(r"(?m)^(class [^\n:]+:)\n\n", r"\1\n", formatted)
    header = source.splitlines()[0] if source.startswith("#!") else ""
    output = f"{header}\n{formatted}" if header else formatted
    if not output.endswith("\n"):
        output += "\n"
    if not dry_run:
        target.write_text(output, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rewrite p*.py scripts with a class-based structure")
    parser.add_argument("source", type=Path, help="directory holding the original scripts")
    parser.add_argument("target", type=Path, help="directory that receives the rewritten scripts")
    parser.add_argument("--exclude", nargs="*", default=["pysearch.py", "py7zmax.py"], help="file names to skip")
    parser.add_argument("--pattern", default="p*.py", help="glob for the source directory")
    parser.add_argument("--report", type=Path, help="write a JSON report")
    parser.add_argument("--dry-run", action="store_true", help="do not write files")
    args = parser.parse_args(argv)
    excluded = set(args.exclude)
    reports: list[FileReport] = []
    for path in sorted(args.source.glob(args.pattern)):
        if not path.is_file() or path.name in excluded:
            continue
        reports.append(rewrite_file(path, args.target / path.name, args.dry_run))
    failures = [report for report in reports if report.status != "ok"]
    print(f"rewritten: {len(reports) - len(failures)}/{len(reports)}")
    for report in failures:
        print(f"  FAIL {report.name}: {report.status}")
    totals = {
        "files": len(reports),
        "methods": sum(report.methods for report in reports),
        "lifted": sum(report.lifted for report in reports),
        "refs": sum(report.rewritten_refs for report in reports),
        "annotations": sum(report.annotations for report in reports),
        "imports_removed": sum(report.imports_removed for report in reports),
        "docstrings_removed": sum(report.docstrings_removed for report in reports),
    }
    print(json.dumps(totals, indent=2))
    if args.report:
        args.report.write_text(json.dumps({"totals": totals, "files": [r.__dict__ for r in reports]}, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
