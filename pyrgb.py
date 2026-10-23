#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import os
import select
import sys
import termios
import tty

RESET: str = "\x1b[0m"
SEQ_MAP: t.dict[bytes, str] = {
    b"\x1b[5~": "pgup",
    b"\x1b[6~": "pgdn",
    b"\x1b[A": "up",
    b"\x1b[B": "down",
    b"\x1b[H": "home",
    b"\x1b[F": "end",
    b"\x1b[1~": "home",
    b"\x1b[4~": "end",
}
CELL_W: int = 15
CELL_PITCH: int = 17


class Pyrgb:
    @staticmethod
    def parse_args(argv: t.Any) -> t.dict[str, t.Any]:
        demo = False
        step = 17
        step_given = False
        positional = []
        i = 0
        args = list(argv)
        while i < len(args):
            a = args[i]
            if a in ("-d", "--demo"):
                demo = True
            elif a == "--step":
                i += 1
                if i >= len(args):
                    sys.exit("error: --step requires a value")
                try:
                    step = int(args[i])
                except ValueError:
                    sys.exit(f"error: --step needs an integer, got {args[i]!r}")
                if step < 1:
                    sys.exit("error: --step must be >= 1")
                step_given = True
            elif a in ("-h", "--help"):
                Pyrgb.print_usage()
                sys.exit(0)
            else:
                positional.append(a)
            i += 1
        if demo:
            return {"demo": True, "step": step}
        if step_given:
            sys.exit("error: --step is only used with -d/--demo")
        if len(positional) != 3:
            sys.exit("usage: rgb.py R G B   or   rgb.py -d [--step N]")
        try:
            r, g, b = (int(v) for v in positional)
        except ValueError:
            sys.exit(f"error: not integers: {' '.join(positional)}")
        for name, v in zip("RGB", (r, g, b)):
            if not 0 <= v <= 255:
                sys.exit(f"error: {name}={v} out of range 0-255")
        return {"demo": False, "rgb": (r, g, b)}

    @staticmethod
    def print_usage() -> None:
        print("usage: rgb.py R G B           show one color")
        print("       rgb.py -d [--step N]    browse all colors page by page")
        print()
        print("options:")
        print("  -d, --demo     page through the RGB color space")
        print("  --step N       demo resolution step (default 17)")
        print("                   51 → 216 web-safe colors, usually 1 page")
        print("                   17 → 4096 colors, ~7-50 pages depending on screen")
        print("                    1 → all 16,777,216 colors")
        print("  -h, --help     show this help")
        print()
        print("keys in demo mode:")
        print("  PgUp / PgDn    previous / next page")
        print("  ↑ / ↓          previous / next page")
        print("  b / space      previous / next page")
        print("  g / G          first / last page")
        print("  q / Esc        quit")

    @staticmethod
    def get_terminal_size() -> t.Any:
        try:
            sz = os.get_terminal_size()
            return (sz.columns, sz.lines)
        except OSError:
            return (80, 24)

    @staticmethod
    def compute_layout() -> tuple[t.Any, t.Any]:
        term_w, term_h = Pyrgb.get_terminal_size()
        cols = max(1, term_w // CELL_PITCH)
        rows = max(1, term_h - 4)
        return (cols, cols * rows)

    @staticmethod
    def show_color(r: t.Any, g: t.Any, b: t.Any) -> None:
        hexcode = f"#{r:02x}{g:02x}{b:02x}"
        fg = f"\x1b[38;2;{r};{g};{b}m"
        bg = f"\x1b[48;2;{r};{g};{b}m"
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        text = "\x1b[38;2;0;0;0m" if lum > 128 else "\x1b[38;2;255;255;255m"
        print(f"RGB   {r:>3} {g:>3} {b:>3}")
        print(f"HEX   {hexcode}")
        print()
        print(f"  {bg}                    {RESET}  solid block")
        print(f"  {bg}{text}    {hexcode}    {RESET}  labeled block")
        print(f"  {fg}████████████████████{RESET}  foreground text")
        print()

    @staticmethod
    def get_values(step: t.Any) -> t.Any:
        vals = list(range(0, 256, step))
        if vals[-1] != 255:
            vals.append(255)
        return vals

    @staticmethod
    def color_at(idx: t.Any, vals: t.Any) -> tuple[t.Any, t.Any, t.Any]:
        n = len(vals)
        return (vals[idx // (n * n)], vals[idx // n % n], vals[idx % n])

    @staticmethod
    def render_page(
        vals: t.Any, page: t.Any, total_pages: t.Any, step: t.Any, cols: t.Any, page_size: t.Any
    ) -> None:
        n = len(vals)
        total = n**3
        start = page * page_size
        end = min(start + page_size, total)
        out = ["\x1b[H\x1b[2J"]
        out.append(
            f"\x1b[1mRGB Demo\x1b[0m  —  page {page + 1} / {total_pages}  —  colors {start}..{end - 1} of {total:,}  (step {step})"
        )
        out.append("")
        idx = start
        while idx < end:
            cells = []
            for _ in range(cols):
                if idx >= end:
                    break
                r, g, b = Pyrgb.color_at(idx, vals)
                cells.append(f"\x1b[38;2;{r};{g};{b}m███\x1b[0m {r:3d} {g:3d} {b:3d}")
                idx += 1
            out.append("  ".join(cells).rstrip())
        out.append("")
        out.append("\x1b[2m[PgUp/PgDn] page  [↑/↓] page  [g] first  [G] last  [q] quit\x1b[0m")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()

    @staticmethod
    def read_key(fd: t.Any) -> str:
        ch = os.read(fd, 1)
        if ch == b"\x1b":
            if not select.select([fd], [], [], 0.02)[0]:
                return "esc"
            ch2 = os.read(fd, 1)
            if ch2 == b"[":
                seq = b"["
                while True:
                    if not select.select([fd], [], [], 0.02)[0]:
                        return "unknown"
                    b = os.read(fd, 1)
                    seq += b
                    if 64 <= b[0] <= 126:
                        break
                return SEQ_MAP.get(b"\x1b" + seq, "unknown")
            if ch2 == b"O":
                if not select.select([fd], [], [], 0.02)[0]:
                    return "unknown"
                b = os.read(fd, 1)
                return SEQ_MAP.get(b"\x1bO" + b, "unknown")
            return "unknown"
        try:
            return ch.decode("utf-8")
        except UnicodeDecodeError:
            return "unknown"

    @staticmethod
    def run_demo(step: t.Any) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            sys.exit("error: demo mode needs an interactive terminal")
        vals = Pyrgb.get_values(step)
        n = len(vals)
        total = n**3
        cols, page_size = Pyrgb.compute_layout()
        total_pages = (total + page_size - 1) // page_size
        if total_pages > 1000:
            print(f"--step {step} → {total:,} colors, {total_pages:,} pages.", file=sys.stderr)
            print("That's a lot to page through. Try --step 17 instead.", file=sys.stderr)
            print("Continue anyway? [y/N] ", end="", file=sys.stderr, flush=True)
            try:
                ans = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                sys.stderr.write("\n")
                return
            if ans != "y":
                return
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        page = 0
        try:
            tty.setraw(fd)
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            sys.stdout.flush()
            while True:
                Pyrgb.render_page(vals, page, total_pages, step, cols, page_size)
                key = Pyrgb.read_key(fd)
                if key in ("q", "Q", "esc"):
                    break
                elif key in ("pgup", "up", "b", "B"):
                    page = max(0, page - 1)
                elif key in ("pgdn", "down", " ", "n", "N"):
                    page = min(total_pages - 1, page + 1)
                elif key == "g":
                    page = 0
                elif key == "G":
                    page = total_pages - 1
        finally:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    @staticmethod
    def main(argv: t.Any) -> None:
        opts = Pyrgb.parse_args(argv)
        if opts["demo"]:
            Pyrgb.run_demo(opts["step"])
        else:
            Pyrgb.show_color(*opts["rgb"])

    @staticmethod
    def run() -> None:
        try:
            Pyrgb.main(sys.argv[1:])
        except KeyboardInterrupt:
            sys.exit(130)


if __name__ == "__main__":
    Pyrgb.run()
