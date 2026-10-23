from __future__ import annotations

import abc
import ast
import importlib.util
from dataclasses import dataclass, field
from typing import ClassVar, Iterable

from .models import BackendError, HintOptions, Outcome, StripOptions, TextEdit, UnsupportedOperation
from .text import (
    LineIndex,
    apply_edits,
    arrow_before,
    byte_to_char,
    collapse_blank_lines,
    colon_before,
    comment_edit,
    is_directive,
    is_type_comment,
    iter_comment_tokens,
    iter_docstrings,
    normalize_newlines,
    removal_edit,
    strip_trailing_space,
)


@dataclass(slots=True)
class Plan:
    edits: list[TextEdit] = field(default_factory=list)
    comments: int = 0
    docstrings: int = 0
    annotations: int = 0

    def merge(self, other: "Plan") -> "Plan":
        self.edits.extend(other.edits)
        self.comments += other.comments
        self.docstrings += other.docstrings
        self.annotations += other.annotations
        return self

    def render(self, source: str, options: StripOptions, backend: str) -> Outcome:
        if not self.edits:
            return Outcome(source, backend=backend)
        code = apply_edits(source, self.edits)
        if options.collapse_blank_lines:
            code = collapse_blank_lines(code)
        code = strip_trailing_space(code)
        return Outcome(code, self.comments, self.docstrings, self.annotations, backend)


def merge_plans(*plans: Plan) -> Plan:
    merged = Plan()
    for plan in plans:
        merged.merge(plan)
    return merged


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def removable_comment(text: str, options: StripOptions, is_first_line: bool) -> bool:
    if is_type_comment(text):
        return options.annotations or (options.comments and not options.keep_directives)
    if not options.comments:
        return False
    if options.keep_directives and is_directive(text):
        return False
    return not (options.keep_shebang and is_first_line and text.startswith("#!"))


def wants(docstrings: bool, module_only: bool, is_module: bool) -> bool:
    if docstrings:
        return True
    return module_only and is_module


class Backend(abc.ABC):
    name: ClassVar[str]
    aliases: ClassVar[tuple[str, ...]] = ()
    supports_hints: ClassVar[bool] = False
    requires: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def available(cls) -> bool:
        return all(module_available(module) for module in cls.requires)

    def strip(self, source: str, options: StripOptions) -> Outcome:
        raise NotImplementedError

    def add_hints(self, source: str, stub: str, options: HintOptions) -> Outcome:
        raise UnsupportedOperation(f"backend {self.name!r} cannot add type hints")

    def _comment_plan(self, source: str, index: LineIndex, options: StripOptions) -> Plan:
        plan = Plan()
        if not (options.comments or options.annotations):
            return plan
        for start_line, start_col, end_line, end_col, text in iter_comment_tokens(source):
            type_comment = is_type_comment(text)
            if not removable_comment(text, options, start_line == 1):
                continue
            start = index.offset(start_line, start_col)
            end = index.offset(end_line, end_col)
            plan.edits.append(comment_edit(index, start, end))
            plan.comments += 1
            if type_comment:
                plan.annotations += 1
        return plan

    def _docstring_plan(self, source: str, index: LineIndex, options: StripOptions) -> Plan:
        plan = Plan()
        if not options.wants_docstrings:
            return plan
        tree = ast.parse(source)
        module_body = getattr(tree, "body", [])
        for statement, body in iter_docstrings(tree):
            is_module = body is module_body
            if not wants(options.docstrings, options.module_docstring, is_module):
                continue
            start = index.offset(statement.lineno, statement.col_offset)
            end = index.offset(statement.end_lineno or statement.lineno, statement.end_col_offset or 0)
            plan.edits.append(removal_edit(index, start, end, keep_body=len(body) == 1))
            plan.docstrings += 1
        return plan

    def _annotation_plan(self, source: str, index: LineIndex, options: StripOptions) -> Plan:
        plan = Plan()
        if not options.annotations:
            return plan
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    add_annotation(plan, index, node.returns, arrow_before)
                for argument in iter_arguments(node.args):
                    if argument.annotation is not None:
                        add_annotation(plan, index, argument.annotation, colon_before)
            elif isinstance(node, ast.AnnAssign):
                add_annotation(plan, index, node.annotation, colon_before)
        return plan

    def _prepare(self, source: str) -> tuple[str, LineIndex]:
        text = normalize_newlines(source)
        return text, LineIndex.build(text)

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


def iter_arguments(arguments: ast.arguments) -> Iterable[ast.arg]:
    for group in (arguments.posonlyargs, arguments.args, arguments.kwonlyargs):
        yield from group
    for extra in (arguments.vararg, arguments.kwarg):
        if extra is not None:
            yield extra


def add_annotation(plan: Plan, index: LineIndex, node: ast.expr, locate) -> None:
    start = index.offset(node.lineno, node.col_offset)
    end = index.offset(node.end_lineno or node.lineno, node.end_col_offset or 0)
    cut = locate(index, start)
    if cut < 0:
        return
    plan.edits.append(removal_edit(index, cut, end, keep_body=False))
    plan.annotations += 1


class AstBackend(Backend):
    name = "ast"

    def strip(self, source: str, options: StripOptions) -> Outcome:
        text, index = self._prepare(source)
        plan = merge_plans(
            self._comment_plan(text, index, options),
            self._docstring_plan(text, index, options),
            self._annotation_plan(text, index, options),
        )
        return plan.render(text, options, self.name)


class LibcstBackend(Backend):
    name = "libcst"
    requires = ("libcst",)
    supports_hints = True

    def strip(self, source: str, options: StripOptions) -> Outcome:
        import libcst as cst
        from libcst.metadata import MetadataWrapper, PositionProvider

        text, index = self._prepare(source)
        wrapper = MetadataWrapper(cst.parse_module(text))
        positions = wrapper.resolve(PositionProvider)
        plan = self._comment_plan(text, index, options)
        if options.wants_docstrings:
            plan.merge(self._docstring_plan_cst(cst, wrapper.module, positions, index, options))
        if options.annotations:
            plan.merge(self._annotation_plan_cst(cst, wrapper.module, positions, index))
        return plan.render(text, options, self.name)

    @staticmethod
    def _docstring_plan_cst(cst, module, positions, index: LineIndex, options: StripOptions) -> Plan:
        plan = Plan()
        for block, is_module in iter_blocks(cst, module):
            if not block.body:
                continue
            first = block.body[0]
            if isinstance(first, cst.SimpleStatementLine):
                small = first.body
            elif isinstance(first, cst.Expr):
                small = (first,)
            else:
                continue
            if len(small) != 1 or not isinstance(small[0], cst.Expr):
                continue
            if not isinstance(small[0].value, (cst.SimpleString, cst.ConcatenatedString)):
                continue
            if not wants(options.docstrings, options.module_docstring, is_module):
                continue
            span = positions[small[0]]
            start = index.offset(span.start.line, span.start.column)
            end = index.offset(span.end.line, span.end.column)
            plan.edits.append(removal_edit(index, start, end, keep_body=len(block.body) == 1))
            plan.docstrings += 1
        return plan

    @staticmethod
    def _annotation_plan_cst(cst, module, positions, index: LineIndex) -> Plan:
        plan = Plan()
        for node in ast_walk_cst(cst, module):
            if isinstance(node, (cst.FunctionDef, cst.Lambda)):
                if isinstance(node, cst.FunctionDef) and node.returns is not None:
                    add_cst_annotation(plan, positions, index, node.returns, arrow_before)
                for parameter in iter_params(node.params):
                    if parameter.annotation is not None:
                        add_cst_annotation(plan, positions, index, parameter.annotation, colon_before)
            elif isinstance(node, cst.AnnAssign):
                add_cst_annotation(plan, positions, index, node.annotation, colon_before)
        return plan

    def add_hints(self, source: str, stub: str, options: HintOptions) -> Outcome:
        from .hints import apply_hints

        text = normalize_newlines(source)
        code = apply_hints(text, stub, overwrite=options.overwrite, future_annotations=options.future_annotations)
        if code == text:
            return Outcome(text, backend=self.name)
        return Outcome(code, backend=self.name)


def iter_blocks(cst, module) -> Iterable[tuple[object, bool]]:
    yield module, True
    for node in ast_walk_cst(cst, module):
        if isinstance(node, (cst.FunctionDef, cst.ClassDef)):
            body = node.body
            if isinstance(body, (cst.IndentedBlock, cst.SimpleStatementSuite)):
                yield body, False


def ast_walk_cst(cst, module) -> Iterable[object]:
    stack: list[object] = [module]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(list(node.children)))


def iter_params(params) -> Iterable[object]:
    groups = (params.posonly_params, params.params, params.kwonly_params)
    for group in groups:
        yield from group
    for extra in (params.star_arg, params.star_kwarg):
        if extra is not None and hasattr(extra, "annotation"):
            yield extra


def add_cst_annotation(plan: Plan, positions, index: LineIndex, node, locate) -> None:
    span = positions[node]
    start = index.offset(span.start.line, span.start.column)
    end = index.offset(span.end.line, span.end.column)
    cut = locate(index, start)
    if cut < 0:
        return
    plan.edits.append(removal_edit(index, cut, end, keep_body=False))
    plan.annotations += 1


class TreeSitterBackend(Backend):
    name = "ts"
    aliases = ("tree-sitter", "treesitter", "tree_sitter")
    requires = ("tree_sitter", "tree_sitter_python")

    def __init__(self) -> None:
        self._parser = None

    @property
    def parser(self):
        if self._parser is None:
            import tree_sitter
            import tree_sitter_python

            try:
                language = tree_sitter.Language(tree_sitter_python.language())
            except AttributeError as error:
                raise BackendError("tree-sitter >= 0.22 is required") from error
            self._parser = tree_sitter.Parser(language)
        return self._parser

    def strip(self, source: str, options: StripOptions) -> Outcome:
        text, index = self._prepare(source)
        chars = byte_to_char(text)
        root = self.parser.parse(text.encode("utf-8")).root_node
        plan = self._comment_plan_ts(root, chars, index, options)
        if options.wants_docstrings:
            plan.merge(self._docstring_plan_ts(root, chars, index, options))
        if options.annotations:
            plan.merge(self._annotation_plan_ts(root, chars, index))
        return plan.render(text, options, self.name)

    def _comment_plan_ts(self, root, chars, index: LineIndex, options: StripOptions) -> Plan:
        plan = Plan()
        if not (options.comments or options.annotations):
            return plan
        for node in walk_tree(root):
            if node.type != "comment":
                continue
            text = node.text.decode("utf-8", "replace")
            type_comment = is_type_comment(text)
            if not removable_comment(text, options, node.start_point[0] == 0):
                continue
            plan.edits.append(comment_edit(index, chars[node.start_byte], chars[node.end_byte]))
            plan.comments += 1
            if type_comment:
                plan.annotations += 1
        return plan

    @staticmethod
    def _docstring_plan_ts(root, chars, index: LineIndex, options: StripOptions) -> Plan:
        plan = Plan()
        for node in walk_tree(root):
            if node.type not in {"module", "block"}:
                continue
            statements = [child for child in node.children if child.type != "comment"]
            if not statements:
                continue
            first = statements[0]
            if first.type != "expression_statement" or not first.children:
                continue
            if first.children[0].type not in {"string", "concatenated_string"}:
                continue
            if not wants(options.docstrings, options.module_docstring, node.type == "module"):
                continue
            plan.edits.append(
                removal_edit(
                    index, chars[first.start_byte], chars[first.end_byte], keep_body=len(statements) == 1
                )
            )
            plan.docstrings += 1
        return plan

    @staticmethod
    def _annotation_plan_ts(root, chars, index: LineIndex) -> Plan:
        plan = Plan()
        for node in walk_tree(root):
            if node.type in {"typed_parameter", "typed_default_parameter", "assignment"}:
                target = node.child_by_field_name("type")
                locate = colon_before
            elif node.type == "function_definition":
                target = return_type(node)
                locate = arrow_before
            else:
                continue
            if target is None:
                continue
            start = chars[target.start_byte]
            cut = locate(index, start)
            if cut < 0:
                continue
            plan.edits.append(removal_edit(index, cut, chars[target.end_byte], keep_body=False))
            plan.annotations += 1
        return plan


def walk_tree(root) -> Iterable[object]:
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def return_type(node):
    seen_arrow = False
    for child in node.children:
        if child.type == "->":
            seen_arrow = True
        elif seen_arrow and child.type == "type":
            return child
    return None


BACKENDS: dict[str, type[Backend]] = {}


def register(*backends: type[Backend]) -> None:
    for backend in backends:
        BACKENDS[backend.name] = backend
        for alias in backend.aliases:
            BACKENDS[alias] = backend


register(AstBackend, LibcstBackend, TreeSitterBackend)


def resolve(name: str) -> Backend:
    key = name.strip().lower()
    backend = BACKENDS.get(key)
    if backend is None:
        choices = ", ".join(available_backends()) or ", ".join(sorted(BACKENDS))
        raise BackendError(f"unknown backend {name!r}; choose one of: {choices}")
    if not backend.available():
        missing = ", ".join(module for module in backend.requires if not module_available(module))
        raise BackendError(f"backend {backend.name!r} needs: {missing}")
    return backend()


def available_backends() -> list[str]:
    return sorted({backend.name for backend in BACKENDS.values() if backend.available()})
