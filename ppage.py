#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import pymupdf


class Ppage:
    @staticmethod
    def split_pdf_by_page(pdf_path: Path, output_dir: Path) -> None:
        with pymupdf.open(pdf_path) as document:
            stem: str = pdf_path.stem
            total_pages: int = document.page_count
            padding: int = len(str(total_pages))
            for page_index in range(total_pages):
                new_doc: pymupdf.Document = pymupdf.open()
                new_doc.insert_pdf(document, from_page=page_index, to_page=page_index)
                padded_num: str = str(page_index + 1).zfill(padding)
                output_path: Path = output_dir / f"{stem}_{padded_num}.pdf"
                new_doc.save(str(output_path))
                new_doc.close()

    @staticmethod
    def extract_text_by_page(pdf_path: Path, output_dir: Path) -> None:
        with pymupdf.open(pdf_path) as document:
            stem: str = pdf_path.stem
            total_pages: int = document.page_count
            padding: int = len(str(total_pages))
            for page_index in range(total_pages):
                page: pymupdf.Page = document.load_page(page_index)
                text: str = page.get_text()
                padded_num: str = str(page_index + 1).zfill(padding)
                output_path: Path = output_dir / f"{stem}_{padded_num}.txt"
                output_path.write_text(text, encoding="utf-8")

    @staticmethod
    def process_pdfs(
        input_paths: list[str] | None = None, output_dir: Path | None = None, text_mode: bool = False
    ) -> None:
        if output_dir is None:
            output_dir = Path.cwd() / "output"
        output_dir.mkdir(exist_ok=True)
        pdf_files: list[Path] = []
        if input_paths is None or len(input_paths) == 0:
            pdf_files = list(Path.cwd().rglob("*.pdf"))
        else:
            for path in input_paths:
                p: Path = Path(path)
                if p.is_file() and p.suffix.lower() == ".pdf":
                    pdf_files.append(p)
                elif p.is_dir():
                    pdf_files.extend(p.rglob("*.pdf"))
        if not pdf_files:
            print("No PDF files found.")
            return
        worker = Ppage.extract_text_by_page if text_mode else Ppage.split_pdf_by_page
        with ThreadPoolExecutor() as executor:
            for pdf_file in pdf_files:
                executor.submit(worker, pdf_file, output_dir)
        print(f"Processing complete. Output files in: {output_dir}")

    @staticmethod
    def run() -> None:
        raw_args: list[str] = sys.argv[1:]
        text_mode: bool = False
        if "-t" in raw_args:
            text_mode = True
            raw_args.remove("-t")
        input_paths: list[str] | None = raw_args or None
        Ppage.process_pdfs(input_paths=input_paths, text_mode=text_mode)


if __name__ == "__main__":
    Ppage.run()
