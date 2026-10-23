#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import ast
from collections import deque
import importlib
import inspect
from multiprocessing import get_context
from pathlib import Path
import sys
from textwrap import dedent
from dh import get_files, unique_path

cwd = Path.cwd()
cwdname = cwd.name
BASE_DIR: Path = Path(f"{cwdname}_doc")


class Pydocr:
    @staticmethod
    def format_markdown(
        module_name: str, module_doc: str, functions: t.Iterable[t.Any], classes: t.Iterable[t.Any]
    ) -> str:
        parts = [f"# Module `{module_name}`\n"]
        if module_doc:
            parts.extend(("## Module Doc\n", module_doc + "\n"))
        if functions:
            parts.append("## Functions\n")
            for name, doc in functions:
                parts.extend((f"### `{name}()`\n", doc + "\n"))
        if classes:
            parts.append("## Classes\n")
            for name, doc in classes:
                parts.extend((f"### `{name}`\n", doc + "\n"))
        return "\n".join(parts).strip() + "\n"

    @staticmethod
    def extract_ast_docs(src: str) -> tuple[str, list, list]:
        try:
            tree = ast.parse(src)
        except Exception:
            return ("", [], [])
        module_doc = dedent(ast.get_docstring(tree) or "").strip()
        functions = []
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node) or ""
                doc = dedent(doc).strip()
                if doc:
                    functions.append((node.name, doc))
            elif isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                doc = dedent(doc).strip()
                if doc:
                    classes.append((node.name, doc))
        return (module_doc, functions, classes)

    @staticmethod
    def extract_from_file(py_path: str) -> tuple[str, str, str, list, list]:
        try:
            src = Path(py_path).read_text(encoding="utf-8")
        except Exception:
            return None
        module_doc, functions, classes = Pydocr.extract_ast_docs(src)
        if not module_doc and (not functions) and (not classes):
            return None
        return (module_doc, functions, classes)

    @staticmethod
    def extract_from_importable(name: str) -> t.Any:
        try:
            module = importlib.import_module(name)
        except Exception:
            return None
        try:
            src = inspect.getsource(module)
            return Pydocr.extract_ast_docs(src)
        except Exception:
            doc = dedent(inspect.getdoc(module) or "").strip()
            if not doc:
                return None
            return (doc, [], [])

    @staticmethod
    def module_to_md_paths(name: str) -> tuple[str, str]:
        parts = name.split(".")
        folder = BASE_DIR.joinpath(*parts[:-1])
        filename = f"{parts[-1]}.md"
        return (str(folder), str(folder / filename))

    @staticmethod
    def file_to_md_paths(py_file: str, root: str) -> tuple[str, str]:
        rel = Path(py_file).relative_to(root)
        parts = list(rel.parts)
        parts[-1] = parts[-1].replace(".py", ".md")
        outfile = BASE_DIR.joinpath(*parts)
        print(outfile)
        input("press any key to continue...")
        return (str(outfile.parent), str(outfile))

    @staticmethod
    def save_markdown(folder: str, path: str, content: str) -> None:
        folderpath = Path(folder)
        if not folderpath.exists():
            folderpath.mkdir(parents=True, exist_ok=True)
        outpath = Path(path)
        if outpath.exists():
            outpath = unique_path(outpath)
        outpath.write_text(content, encoding="utf-8")

    @staticmethod
    def process_importable_task(name: str) -> None:
        print(f"processing module {name}")
        result = Pydocr.extract_from_importable(name)
        if not result:
            return
        module_doc, functions, classes = result
        folder, out_path = Pydocr.module_to_md_paths(name)
        md = Pydocr.format_markdown(name, module_doc, functions, classes)
        Pydocr.save_markdown(folder, out_path, md)

    @staticmethod
    def process_file_task(py_file: t.Any) -> None:
        path = Path(py_file)
        root = str(path.parent)
        print(f"processing file {path.name} from {path.parent.name}")
        result = Pydocr.extract_from_file(str(py_file))
        if not result:
            return
        module_doc, functions, classes = result
        rel = path.resolve().relative_to(Path.cwd().resolve())
        module_name = ".".join(rel.with_suffix("").parts)
        folder, out_path = Pydocr.file_to_md_paths(py_file, root)
        md = Pydocr.format_markdown(module_name, module_doc, functions, classes)
        Pydocr.save_markdown(folder, out_path, md)

    @staticmethod
    def main() -> None:
        if not BASE_DIR.exists():
            BASE_DIR.mkdir(exist_ok=True)
        cwd = Path.cwd()
        args = sys.argv[1:]
        files = [Path(arg) for arg in args] if args else get_files(cwd, ext=[".py", ".pyi", ".pyx", ".pxd"])
        print(f"processing {len(files)} files")
        with get_context("spawn").Pool(8) as pool:
            pending = deque()
            for f in files:
                pending.append(pool.apply_async(Pydocr.process_file_task, (f,)))
                if len(pending) > 32:
                    pending.popleft().get()
            while pending:
                pending.popleft().get()


if __name__ == "__main__":
    raise SystemExit(Pydocr.main())
