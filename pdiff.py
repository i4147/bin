#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import ast
import difflib
import opcode
import pprint
import types
import sys


class Pdiff:
    @staticmethod
    def diff_bytecode_of_files(filename_a: t.Any, filename_b: t.Any) -> t.Any:
        with Pdiff.open_with_encoding(filename_a) as file_a:
            source_a = file_a.read()
        with Pdiff.open_with_encoding(filename_b) as file_b:
            source_b = file_b.read()
        return Pdiff.diff_bytecode(source_a, source_b, filename_a, filename_b)

    @staticmethod
    def diff_bytecode(source_a: t.Any, source_b: t.Any, filename_a: str = "", filename_b: str = "") -> str:
        if Pdiff.dump_ast(source_a, filename_a) == Pdiff.dump_ast(source_b, filename_b):
            return ""
        bytecode_a = Pdiff.disassemble(source_a, filename_a)
        bytecode_b = Pdiff.disassemble(source_b, filename_b)
        return "".join(
            difflib.unified_diff(
                pprint.pformat(bytecode_a).splitlines(True),
                pprint.pformat(bytecode_b).splitlines(True),
                filename_a,
                filename_b,
            )
        )

    @staticmethod
    def disassemble(source: t.Any, filename: t.Any = None) -> t.Any:
        return Pdiff.tree(compile(source, filename or "<string>", "exec", dont_inherit=True))

    @staticmethod
    def dump_ast(source: t.Any, filename: t.Any = None) -> t.Any:
        return ast.dump(ast.parse(source, filename or "<string>", "exec"))

    @staticmethod
    def tree(code: t.Any) -> t.Any:
        dictionary = {"co_consts": [], "co_code": []}
        for name in dir(code):
            if name.startswith("co_") and name not in [
                "co_code",
                "co_consts",
                "co_lnotab",
                "co_filename",
                "co_firstlineno",
            ]:
                dictionary[name] = getattr(code, name)
        for index, _object in enumerate(code.co_consts):
            if isinstance(_object, types.CodeType):
                _object = Pdiff.tree(_object)
            if index < 2 and isinstance(_object, str):
                _object = " ".join([line.strip() for line in _object.splitlines()])
            dictionary["co_consts"].append(_object)
        for op in code.co_code:
            try:
                op = ord(op)
            except TypeError:
                pass
            dictionary["co_code"].append(opcode.opname[op])
        return dictionary

    @staticmethod
    def open_with_encoding(filename: t.Any, encoding: t.Any = None, mode: str = "r") -> t.Any:
        if not encoding:
            encoding = Pdiff.detect_encoding(filename)
        import io

        return io.open(filename, mode=mode, encoding=encoding, newline="")

    @staticmethod
    def detect_encoding(filename: str) -> str:
        try:
            with open(filename, "rb") as input_file:
                import tokenize

                encoding = tokenize.detect_encoding(input_file.readline)[0]
            with Pdiff.open_with_encoding(filename, encoding) as test_file:
                test_file.read()
            return encoding
        except (LookupError, SyntaxError, UnicodeDecodeError):
            return "latin-1"

    @staticmethod
    def parse_args(argv: t.Any) -> t.Any:
        import argparse

        parser = argparse.ArgumentParser(description=__doc__, prog="pydiff")
        parser.add_argument("files", nargs=2, help="files to compare")
        args = parser.parse_args(argv[1:])
        return args

    @staticmethod
    def main(argv: t.Any, standard_out: t.Any, standard_error: t.Any) -> None:
        args = Pdiff.parse_args(argv)
        try:
            diff = Pdiff.diff_bytecode_of_files(args.files[0], args.files[1])
            if diff:
                standard_out.write(diff + "\n")
        except SyntaxError as exception:
            standard_error.write("{0}:{1} invalid syntax\n".format(exception.filename, exception.lineno))
            standard_error.write(str(exception.text))
            standard_error.write((exception.offset * " ")[:-1] + "^\n")


if __name__ == "__main__":
    Pdiff.main(sys.argv, sys.stdout, sys.stderr)
