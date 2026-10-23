#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
import asyncio
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PYPI_BASE: str = "https://pypi.org"
USER_AGENT: str = "pypi-user-packages/1.3"
DEFAULT_MAX_MIB: float = 10.0
CHUNK_SIZE: int = 64 * 1024
ARIA2C_BIN: str = "aria2c"
DEFAULT_BACKEND: str = "httpx"
DEFAULT_JOBS: int = 8
_PROJECT_RE = re.compile("^/project/([^/?#]+)/?$")
_PAGE_RE = re.compile("[?&]page=(\\d+)")


class _ProfileParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._snippet_hrefs: list[str] = []
        self._all_project_hrefs: list[str] = []
        self.page_numbers: set[int] = set()

    def handle_starttag(self, tag: str, attrs: t.dict[str, t.Any]) -> None:
        if tag != "a":
            return
        attrs = dict(attrs)
        href = attrs.get("href") or ""
        m = _PAGE_RE.search(href)
        if m:
            self.page_numbers.add(int(m.group(1)))
        if _PROJECT_RE.match(href):
            self._all_project_hrefs.append(href)
            classes = (attrs.get("class") or "").split()
            if "package-snippet" in classes:
                self._snippet_hrefs.append(href)

    def project_names(self) -> list[str]:
        hrefs = self._snippet_hrefs or self._all_project_hrefs
        names: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            match = _PROJECT_RE.match(href)
            if not match:
                continue
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names


class PypiUserPkgs:
    @staticmethod
    def _page_of(url: str) -> int:
        m = re.search("[?&]page=(\\d+)", url)
        return int(m.group(1)) if m else 1

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        return f"{url.split('?', 1)[0]}?page={page}"

    @staticmethod
    def check_backend(backend: str) -> None:
        if backend == "requests":
            try:
                import requests
            except ImportError:
                sys.exit("backend 'requests' selected but 'requests' is not installed (pip install requests)")
        elif backend == "pycurl":
            try:
                import pycurl
            except ImportError:
                sys.exit("backend 'pycurl' selected but 'pycurl' is not installed (pip install pycurl)")
        elif backend == "aria2c":
            if shutil.which(ARIA2C_BIN) is None:
                sys.exit(
                    f"backend 'aria2c' selected but '{ARIA2C_BIN}' was not found on PATH (install aria2, e.g. 'apt install aria2' or 'brew install aria2')"
                )
        elif backend == "httpx":
            try:
                import httpx
            except ImportError:
                sys.exit("backend 'httpx' selected but 'httpx' is not installed (pip install httpx)")

    @staticmethod
    def fetch_bytes(url: str, backend: str = DEFAULT_BACKEND, timeout: float = 30.0) -> t.Any:
        if backend == "aria2c":
            backend = "urllib"
        if backend == "pycurl":
            return PypiUserPkgs._fetch_pycurl(url, timeout)
        if backend == "requests":
            return PypiUserPkgs._fetch_requests(url, timeout)
        if backend == "httpx":
            return PypiUserPkgs._fetch_httpx(url, timeout)
        return PypiUserPkgs._fetch_urllib(url, timeout)

    @staticmethod
    def _fetch_urllib(url: str, timeout: float) -> tuple[t.Any, t.Any]:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            return (resp.read(), resp.geturl())

    @staticmethod
    def _fetch_requests(url: str, timeout: float) -> tuple[t.Any, t.Any]:
        import requests

        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        return (resp.content, resp.url)

    @staticmethod
    def _fetch_httpx(url: str, timeout: float) -> tuple[t.Any, str]:
        import httpx

        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return (resp.content, str(resp.url))

    @staticmethod
    def _fetch_pycurl(url: str, timeout: float) -> tuple[t.Any, t.Any]:
        import pycurl

        buf = io.BytesIO()
        curl = pycurl.Curl()
        try:
            curl.setopt(pycurl.URL, url)
            curl.setopt(pycurl.FOLLOWLOCATION, True)
            curl.setopt(pycurl.MAXREDIRS, 10)
            curl.setopt(pycurl.TIMEOUT, int(timeout))
            curl.setopt(pycurl.USERAGENT, USER_AGENT)
            curl.setopt(pycurl.WRITEFUNCTION, buf.write)
            curl.perform()
            status = curl.getinfo(pycurl.RESPONSE_CODE)
            effective = curl.getinfo(pycurl.EFFECTIVE_URL)
        finally:
            curl.close()
        if status >= 400:
            raise HTTPError(url, status, f"HTTP {status}", None, None)
        return (buf.getvalue(), effective)

    @staticmethod
    def download_file(url: str, dest: Path, backend: str, max_bytes: int, timeout: float = 60.0) -> int:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / (dest.name + ".part")
        try:
            if backend == "pycurl":
                written = PypiUserPkgs._download_pycurl(url, tmp, max_bytes, timeout)
            elif backend == "requests":
                written = PypiUserPkgs._download_requests(url, tmp, max_bytes, timeout)
            elif backend == "aria2c":
                written = PypiUserPkgs._download_aria2c(url, tmp, max_bytes, timeout)
            else:
                written = PypiUserPkgs._download_urllib(url, tmp, max_bytes, timeout)
            tmp.replace(dest)
            return written
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _download_urllib(url: str, tmp: Path, max_bytes: int, timeout: float) -> int:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                msg = f"Content-Length {declared} > {max_bytes}"
                raise FileTooLarge(msg)
            written = 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        msg = f"exceeded {max_bytes} bytes"
                        raise FileTooLarge(msg)
                    fh.write(chunk)
        return written

    @staticmethod
    def _download_requests(url: str, tmp: Path, max_bytes: int, timeout: float) -> int:
        import requests

        written = 0
        with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                msg = f"Content-Length {declared} > {max_bytes}"
                raise FileTooLarge(msg)
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        msg = f"exceeded {max_bytes} bytes"
                        raise FileTooLarge(msg)
                    fh.write(chunk)
        return written

    @staticmethod
    def _download_pycurl(url: str, tmp: Path, max_bytes: int, timeout: float) -> int:
        import pycurl

        written = 0
        aborted = False
        fh = open(tmp, "wb")
        curl = pycurl.Curl()

        def _write(data: bytes) -> int:
            nonlocal written, aborted
            written += len(data)
            if written > max_bytes:
                aborted = True
                return 0
            return fh.write(data)

        try:
            curl.setopt(pycurl.URL, url)
            curl.setopt(pycurl.FOLLOWLOCATION, True)
            curl.setopt(pycurl.MAXREDIRS, 10)
            curl.setopt(pycurl.TIMEOUT, int(timeout))
            curl.setopt(pycurl.USERAGENT, USER_AGENT)
            curl.setopt(pycurl.WRITEFUNCTION, _write)
            curl.perform()
            status = curl.getinfo(pycurl.RESPONSE_CODE)
        except pycurl.error as exc:
            if aborted:
                msg = f"exceeded {max_bytes} bytes"
                raise FileTooLarge(msg) from exc
            raise
        finally:
            curl.close()
            fh.close()
        if status >= 400:
            raise HTTPError(url, status, f"HTTP {status}", None, None)
        return written

    @staticmethod
    def _download_aria2c(url: str, tmp: Path, max_bytes: int, timeout: float) -> int:
        cmd = [
            ARIA2C_BIN,
            "--no-conf=true",
            "--quiet=true",
            "--console-log-level=error",
            "--file-allocation=none",
            "--continue=false",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--check-certificate=true",
            f"--user-agent={USER_AGENT}",
            f"--timeout={int(timeout)}",
            "--max-tries=3",
            f"--max-filesize={max_bytes}",
            f"--out={tmp.name}",
            f"--dir={tmp.parent}",
            url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout + 60, check=False)
        except subprocess.TimeoutExpired as exc:
            msg = "aria2c timed out"
            raise RuntimeError(msg) from exc
        stderr_text = result.stderr.decode("utf-8", "replace").strip()
        if result.returncode != 0 or not tmp.exists():
            low = stderr_text.lower()
            if "max-filesize" in low or "too large" in low or "exceeds" in low:
                raise FileTooLarge(stderr_text or f"exceeded {max_bytes} bytes")
            msg = f"aria2c failed (exit {result.returncode}): {stderr_text or '<no stderr>'}"
            raise RuntimeError(msg)
        size = tmp.stat().st_size
        if size > max_bytes:
            tmp.unlink(missing_ok=True)
            msg = f"downloaded {size} bytes > {max_bytes}"
            raise FileTooLarge(msg)
        tmp.with_name(tmp.name + ".aria2").unlink(missing_ok=True)
        return size

    @staticmethod
    async def _download_one_async(
        client: t.Any, url: str, dest: Path, max_bytes: int, sem: asyncio.Semaphore
    ) -> tuple[Path, int | None, Exception | None]:
        async with sem:
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / (dest.name + ".part")
            written = 0
            try:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    declared = resp.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        msg = f"Content-Length {declared} > {max_bytes}"
                        raise FileTooLarge(msg)
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                            if not chunk:
                                continue
                            written += len(chunk)
                            if written > max_bytes:
                                msg = f"exceeded {max_bytes} bytes"
                                raise FileTooLarge(msg)
                            fh.write(chunk)
                tmp.replace(dest)
                return (dest, written, None)
            except BaseException as exc:
                tmp.unlink(missing_ok=True)
                return (dest, None, exc)

    @staticmethod
    async def _download_all_async(
        tasks: list[tuple[str, Path]], max_bytes: int, timeout: float, jobs: int, on_event: t.Any
    ) -> None:
        import httpx

        sem = asyncio.Semaphore(jobs)
        limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True, limits=limits
        ) as client:
            coros = [
                PypiUserPkgs._download_one_async(client, url, dest, max_bytes, sem) for url, dest in tasks
            ]
            for coro in asyncio.as_completed(coros):
                dest, written, exc = await coro
                on_event("done", dest, written, exc)

    @staticmethod
    def download_all(
        tasks: list[tuple[str, Path]], backend: str, max_bytes: int, timeout: float, on_event: t.Any
    ) -> None:
        if backend == "httpx" and tasks:
            asyncio.run(PypiUserPkgs._download_all_async(tasks, max_bytes, timeout, DEFAULT_JOBS, on_event))
            return
        for url, dest in tasks:
            try:
                written = PypiUserPkgs.download_file(url, dest, backend, max_bytes, timeout=timeout)
            except BaseException as exc:
                on_event("done", dest, None, exc)
            else:
                on_event("done", dest, written, None)

    @staticmethod
    def resolve_profile_url(target: str) -> str:
        target = target.strip()
        if "://" in target:
            return target
        return f"{PYPI_BASE}/user/{target.strip('/')}/"

    @staticmethod
    def extract_username(target: str) -> str:
        target = target.strip().rstrip("/")
        if "://" in target:
            target = target.rsplit("/", 1)[-1]
        safe = re.sub("[^A-Za-z0-9._-]", "_", target)
        return safe or "pypi_user"

    @staticmethod
    def save_package_list(names: list[str], username: str) -> Path:
        out_path = Path.cwd() / f"{username}.txt"
        out_path.write_text("\n".join(names) + "\n", encoding="utf-8")
        return out_path

    @staticmethod
    def collect_packages(
        start_url: str, backend: str, timeout: float, quiet: bool = False
    ) -> tuple[t.Any, t.Any]:
        names: list[str] = []
        seen: set[str] = set()
        visited_pages: set[int] = set()
        page_url: str | None = start_url
        final_url = start_url
        while page_url:
            body, final_url = PypiUserPkgs.fetch_bytes(page_url, backend, timeout)
            parser = _ProfileParser()
            parser.feed(body.decode("utf-8", "replace"))
            new = [n for n in parser.project_names() if n not in seen]
            seen.update(new)
            names.extend(new)
            current = PypiUserPkgs._page_of(page_url)
            visited_pages.add(current)
            highest = max(parser.page_numbers) if parser.page_numbers else current
            if new and highest > current and (current + 1 not in visited_pages):
                page_url = PypiUserPkgs._with_page(final_url, current + 1)
                if not quiet:
                    print(f"  ... fetching page {current + 1}", file=sys.stderr)
            else:
                page_url = None
        return (names, final_url)

    @staticmethod
    def fetch_package_files(name: str, backend: str, timeout: float) -> tuple[t.Any, t.Any]:
        body, _ = PypiUserPkgs.fetch_bytes(f"{PYPI_BASE}/pypi/{name}/json", backend, timeout)
        meta = json.loads(body)
        return (meta["info"]["version"], meta.get("urls", []))

    @staticmethod
    def _mib(n: int) -> str:
        return f"{n / (1024 * 1024):.2f} MiB"

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(
            prog="pypi_user_packages.py",
            description="List the packages published by a PyPI user/org and optionally download them. The package list is always saved to <user>.txt in the current directory.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument("user", help="PyPI user/org name (e.g. micropython-lib) or a full profile URL")
        p.add_argument(
            "-d", "--download", action="store_true", help="download the packages (latest release of each)"
        )
        p.add_argument(
            "-b",
            "--backend",
            choices=("httpx", "urllib", "requests", "pycurl", "aria2c"),
            default=DEFAULT_BACKEND,
            help=f"HTTP backend to use (default: {DEFAULT_BACKEND}). 'httpx' runs parallel async downloads with {DEFAULT_JOBS} concurrent jobs; 'aria2c' shells out to the aria2c binary.",
        )
        p.add_argument(
            "-o",
            "--output",
            type=Path,
            default=Path("pypi-packages"),
            help="directory for downloads (default: ./pypi-packages)",
        )
        p.add_argument(
            "-m",
            "--max-size",
            type=float,
            default=DEFAULT_MAX_MIB,
            metavar="MIB",
            help=f"skip files larger than this, in MiB (default: {DEFAULT_MAX_MIB:g})",
        )
        p.add_argument(
            "-t", "--timeout", type=float, default=30.0, help="per-request timeout in seconds (default: 30)"
        )
        p.add_argument("-q", "--quiet", action="store_true", help="suppress progress messages on stderr")
        return p

    @staticmethod
    def main(argv: t.Any = None) -> int:
        args = PypiUserPkgs.build_parser().parse_args(argv)
        PypiUserPkgs.check_backend(args.backend)
        max_bytes = int(args.max_size * 1024 * 1024)
        profile_url = PypiUserPkgs.resolve_profile_url(args.user)
        username = PypiUserPkgs.extract_username(args.user)
        if not args.quiet:
            print(f"Fetching {profile_url} ...", file=sys.stderr)
        try:
            names, final_url = PypiUserPkgs.collect_packages(
                profile_url, args.backend, args.timeout, args.quiet
            )
        except Exception as exc:
            print(f"error: could not fetch the profile page: {exc}", file=sys.stderr)
            return 1
        if final_url.rstrip("/") != profile_url.rstrip("/") and (not args.quiet):
            print(f"  resolved to {final_url}", file=sys.stderr)
        if not names:
            print("No packages found on that profile page.", file=sys.stderr)
            return 1
        list_path = PypiUserPkgs.save_package_list(names, username)
        print(f"{len(names)} package(s) -> {list_path}")
        for name in names:
            print(f"  {name}")
        if not args.download:
            return 0
        outdir: Path = args.output
        outdir.mkdir(parents=True, exist_ok=True)
        parallel = args.backend == "httpx"
        print(
            f"\nDownloading into {outdir.resolve()} (backend={args.backend}, limit={args.max_size:g} MiB"
            + (f", jobs={DEFAULT_JOBS}" if parallel else "")
            + ")"
        )
        tasks: list[tuple[str, Path]] = []
        have_count = 0
        for index, name in enumerate(names, 1):
            prefix = f"[{index}/{len(names)}] {name}"
            try:
                version, files = PypiUserPkgs.fetch_package_files(name, args.backend, args.timeout)
            except Exception as exc:
                print(f"{prefix}: metadata error: {exc}", file=sys.stderr)
                continue
            if not files:
                print(f"{prefix}: no downloadable files for {version}")
                continue
            print(f"{prefix} {version}")
            for entry in files:
                filename = entry["filename"]
                size = int(entry.get("size") or 0)
                url = entry["url"]
                if size and size > max_bytes:
                    print(f"    skip {filename} ({PypiUserPkgs._mib(size)} > limit)")
                    continue
                dest = outdir / name / filename
                if dest.exists():
                    print(f"    have {filename}")
                    have_count += 1
                    continue
                tasks.append((url, dest))
        total_files = 0
        failures = 0

        def on_event(kind: str, dest: Path, written: t.Any, exc: t.Any) -> None:
            nonlocal total_files, failures
            if exc is None:
                total_files += 1
                print(f"    got  {dest.name} ({PypiUserPkgs._mib(written)})")
            elif isinstance(exc, FileTooLarge):
                print(f"    skip {dest.name} ({exc})")
            else:
                failures += 1
                print(f"    fail {dest.name}: {exc}", file=sys.stderr)

        PypiUserPkgs.download_all(
            tasks, args.backend, max_bytes, timeout=max(args.timeout, 60.0), on_event=on_event
        )
        print(
            f"\nDone. {total_files} file(s) downloaded{(f', {have_count} already present' if have_count else '')}{(f', {failures} failed' if failures else '')} -> {outdir.resolve()}"
        )
        return 0 if failures == 0 else 1

    @staticmethod
    def run() -> None:
        try:
            sys.exit(PypiUserPkgs.main())
        except KeyboardInterrupt:
            sys.exit(130)


class FileTooLarge(Exception):
    pass


if __name__ == "__main__":
    PypiUserPkgs.run()
