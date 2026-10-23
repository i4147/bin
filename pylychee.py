#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import base64
import mimetypes
from multiprocessing import Pool
from pathlib import Path
import re
from typing import TYPE_CHECKING, Final
import urllib.error
from urllib.parse import unquote, urldefrag
import urllib.request
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterable
EXT_IMAGE: Final[frozenset[str]] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
)
HTTP_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://")
HTML_ATTR_RE: Final[re.Pattern[str]] = re.compile(
    "(?P<attr>href|src)\\s*=\\s*(?P<q>[\"\\'])(?P<url>.*?)(?P=q)", re.IGNORECASE
)
MD_IMAGE_RE: Final[re.Pattern[str]] = re.compile(
    '!\\[([^\\]]*?)\\]\\((?P<url>[^)\\s]+)(?:\\s+\\"[^\\"]*\\")?\\)'
)
MD_LINK_RE: Final[re.Pattern[str]] = re.compile(
    '\\[(?P<text>[^\\]]*?)\\]\\((?P<url>[^)\\s]+)(?:\\s+\\"[^\\"]*\\")?\\)'
)
DEFAULT_TIMEOUT: Final[int] = 8
POOL_WORKERS: Final[int] = 8
TARGET_EXTS: Final[tuple[str, ...]] = (".md", ".html", ".htm")


class Pylychee:
    @staticmethod
    def _strip_fragment_query(u: str) -> str:
        v, _ = urldefrag(u)
        if "?" in v:
            v = v.split("?", 1)[0]
        return v

    @staticmethod
    def _guess_ext(u: str) -> str:
        p = Pylychee._strip_fragment_query(u)
        return Path(p).suffix.lower()

    @staticmethod
    def _is_remote(u: str) -> bool:
        s = u.strip()
        return any((s.startswith(x) for x in HTTP_SCHEMES))

    @staticmethod
    def _is_local_ref(u: str) -> bool:
        s = u.strip()
        if not s:
            return False
        if s.startswith("#"):
            return False
        return not Pylychee._is_remote(s)

    @staticmethod
    def _is_imageish(u: str) -> bool:
        return Pylychee._guess_ext(u) in EXT_IMAGE

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        return path.read_bytes()

    @staticmethod
    def _local_target_resolve(base_dir: Path, u: str) -> Path | None:
        s = u.strip()
        if not s:
            return None
        if s.startswith("/"):
            return None
        if "#" in s or "?" in s:
            s = Pylychee._strip_fragment_query(s)
        s = unquote(s)
        try:
            return (base_dir / s).resolve()
        except OSError:
            return None

    @staticmethod
    def _mime_for_local(path: Path) -> str:
        if path.suffix.lower() == ".svg":
            return "image/svg+xml"
        m, _ = mimetypes.guess_type(str(path))
        return m or "application/octet-stream"

    @staticmethod
    def _to_data_uri(path: Path) -> str:
        mime = Pylychee._mime_for_local(path)
        raw = Pylychee._read_bytes(path)
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _http_check(url: str, timeout: int) -> bool:
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = getattr(resp, "status", 200)
                return code < 400
        except Exception:
            req = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code = getattr(resp, "status", 200)
                    return code < 400
            except Exception:
                return False

    @staticmethod
    def _replace_html(html: str, file_dir: Path, timeout: int) -> tuple[str, list[str]]:
        removals: list[str] = []

        def html_attr_repl(m: re.Match[str]) -> str:
            attr = m.group("attr")
            q = m.group("q")
            url = m.group("url")
            raw = url
            target = url.strip()
            if not target or target.startswith(("#", "data:")):
                return m.group(0)
            if Pylychee._is_remote(target):
                if Pylychee._is_imageish(target) and (not Pylychee._http_check(target, timeout)):
                    removals.append(f"REMOTE_UNAVAILABLE_IMAGE {attr}={raw}")
                    return f"{attr}={q}{q}"
                return m.group(0)
            if Pylychee._is_local_ref(target):
                local_path = Pylychee._local_target_resolve(file_dir, target)
                if local_path is None or not local_path.is_file():
                    removals.append(f"LOCAL_UNAVAILABLE {attr}={raw}")
                    return m.group(0)
                if Pylychee._is_imageish(target):
                    try:
                        data_uri = Pylychee._to_data_uri(local_path)
                    except Exception:
                        removals.append(f"LOCAL_INLINE_FAILED {attr}={raw}")
                        return m.group(0)
                    return f"{attr}={q}{data_uri}{q}"
            return m.group(0)

        out = HTML_ATTR_RE.sub(html_attr_repl, html)
        return (out, removals)

    @staticmethod
    def _replace_md(md: str, file_dir: Path, timeout: int) -> tuple[str, list[str]]:
        removals: list[str] = []

        def replace_md_images(match: re.Match[str]) -> str:
            url = match.group("url").strip()
            if not url or url.startswith(("#", "data:")):
                return match.group(0)
            if Pylychee._is_remote(url):
                if Pylychee._is_imageish(url) and (not Pylychee._http_check(url, timeout)):
                    removals.append(f"REMOTE_UNAVAILABLE_IMAGE_MD {url}")
                    return ""
                return match.group(0)
            if Pylychee._is_local_ref(url) and Pylychee._is_imageish(url):
                local_path = Pylychee._local_target_resolve(file_dir, url)
                if local_path is None or not local_path.is_file():
                    removals.append(f"LOCAL_UNAVAILABLE_MD {url}")
                    return match.group(0)
                try:
                    data_uri = Pylychee._to_data_uri(local_path)
                except Exception:
                    removals.append(f"LOCAL_INLINE_FAILED_MD {url}")
                    return match.group(0)
                text = match.group(1) or ""
                return f"![{text}]({data_uri})"
            return match.group(0)

        def replace_md_links(match: re.Match[str]) -> str:
            url = match.group("url").strip()
            if not url or url.startswith(("#", "data:")):
                return match.group(0)
            if Pylychee._is_remote(url):
                if Pylychee._is_imageish(url) and (not Pylychee._http_check(url, timeout)):
                    removals.append(f"REMOTE_UNAVAILABLE_IMAGE_MD_LINK {url}")
                    return match.group(0).replace(f"({url})", "()")
                return match.group(0)
            if Pylychee._is_local_ref(url):
                local_path = Pylychee._local_target_resolve(file_dir, url)
                if local_path is None or not local_path.is_file():
                    removals.append(f"LOCAL_UNAVAILABLE_MD_LINK {url}")
                return match.group(0)
            return match.group(0)

        out = MD_IMAGE_RE.sub(replace_md_images, md)
        out = MD_LINK_RE.sub(replace_md_links, out)
        return (out, removals)

    @staticmethod
    def process_file(args: tuple[str, int]) -> list[str]:
        path_str, timeout = args
        path = Path(path_str)
        file_dir = path.parent
        content = path.read_text(encoding="utf-8", errors="replace")
        report: list[str] = []
        new_content: str = content
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            new_content, report = Pylychee._replace_html(content, file_dir, timeout)
        elif suffix == ".md":
            new_content, report = Pylychee._replace_md(content, file_dir, timeout)
        else:
            return []
        if new_content != content:
            path.write_text(new_content, encoding="utf-8", errors="replace")
        return report

    @staticmethod
    def iter_files(root: Path, exts: tuple[str, ...]) -> list[str]:
        out: list[str] = []
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                out.append(str(p))
        return out

    @staticmethod
    def main() -> int:
        parser = argparse.ArgumentParser()
        parser.add_argument("root", help="Folder to scan")
        parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
        args = parser.parse_args()
        root = Path(args.root).resolve()
        if not root.is_dir():
            logger.error("root is not a directory: {}", root)
            return 2
        files = Pylychee.iter_files(root, TARGET_EXTS)
        if not files:
            return 0
        timeout: int = args.timeout
        tasks: list[tuple[str, int]] = [(fp, timeout) for fp in files]
        all_reports: list[str] = []
        with Pool(processes=POOL_WORKERS) as pool:
            results: Iterable[object] = [pool.apply_async(Pylychee.process_file, (task,)) for task in tasks]
            for fp, res in zip(files, results):
                try:
                    r = res.get()
                except Exception as e:
                    all_reports.append(f"FAILED {fp} {e}")
                    continue
                if isinstance(r, list):
                    all_reports.extend(r)
        for line in all_reports:
            print(line)
        return 0


if __name__ == "__main__":
    raise SystemExit(Pylychee.main())
