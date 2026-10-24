from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from .models import BackendError


def typeshed_sanitizer(libcst):
    class TypeshedSanitizer(libcst.CSTTransformer):
        def leave_ImportFrom(self, original_node, updated_node):
            if original_node.module and libcst.helpers.get_full_name_for_node(original_node.module) == "_typeshed":
                return libcst.ImportFrom(
                    module=libcst.Name("typing"),
                    names=[libcst.ImportAlias(name=libcst.Name("Any"))],
                )
            return updated_node

        def leave_Import(self, original_node, updated_node):
            names = [
                alias.with_changes(name=libcst.Name("typing")) if is_typeshed(libcst, alias.name) else alias
                for alias in original_node.names
            ]
            return updated_node.with_changes(names=names)

        def leave_Attribute(self, original_node, updated_node):
            if is_incomplete(libcst, original_node):
                return libcst.Attribute(value=libcst.Name("typing"), attr=libcst.Name("Any"))
            return updated_node

        def leave_Name(self, original_node, updated_node):
            if original_node.value == "Incomplete":
                return libcst.Name("Any")
            return updated_node

    return TypeshedSanitizer


def is_typeshed(libcst, node) -> bool:
    return libcst.helpers.get_full_name_for_node(node) == "_typeshed"


def is_incomplete(libcst, node) -> bool:
    value = node.value
    return (
        isinstance(value, libcst.Name)
        and value.value == "_typeshed"
        and node.attr.value == "Incomplete"
    )


def sanitize_stub(stub_code: str, libcst) -> object:
    sanitizer = typeshed_sanitizer(libcst)
    return libcst.parse_module(stub_code).visit(sanitizer())


def apply_hints(source: str, stub_code: str, overwrite: bool = True, future_annotations: bool = False) -> str:
    import libcst as cst
    from libcst.codemod import CodemodContext
    from libcst.codemod.visitors import ApplyTypeAnnotationsVisitor

    try:
        module = cst.parse_module(source)
    except Exception as error:
        raise BackendError(f"cannot parse source with libcst: {error}") from error
    try:
        stub = sanitize_stub(stub_code, cst)
    except Exception as error:
        raise BackendError(f"cannot parse stub with libcst: {error}") from error
    context = CodemodContext()
    ApplyTypeAnnotationsVisitor.store_stub_in_context(
        context=context,
        stub=stub,
        overwrite_existing_annotations=overwrite,
        use_future_annotations=future_annotations,
    )
    return ApplyTypeAnnotationsVisitor(context).transform_module(module).code


def stubs_from_module_file(path: Path, extra_args: tuple[str, ...] = (), timeout: float = 300.0) -> str:
    with tempfile.TemporaryDirectory() as workdir:
        command = [
            sys.executable,
            "-c",
            "from mypy.stubgen import main; main()",
            *extra_args,
            "-o",
            workdir,
            str(path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise BackendError(f"stubgen failed for {path}: {message}")
        generated = sorted(Path(workdir).rglob("*.pyi"))
        if not generated:
            raise BackendError(f"stubgen produced no .pyi for {path}")
        match = next((item for item in generated if item.stem == path.stem), generated[0])
        return match.read_text(encoding="utf-8")


def stubs_from_source(source: str, name: str, extra_args: tuple[str, ...] = ()) -> str:
    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / f"{name}.py"
        target.write_text(source, encoding="utf-8")
        return stubs_from_module_file(target, extra_args)
