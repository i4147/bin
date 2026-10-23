#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
import io
import os
import sys
from typing import TYPE_CHECKING, Any
from cairosvg import svg2png
from PIL import Image

if TYPE_CHECKING:
    from PIL.ImageFile import ImageFile
SVG_SUPPORT: bool = True


class Pycatimg:
    @staticmethod
    def resize_image(
        img: ImageFile,
        terminal_width: int,
        terminal_height: int,
        max_width: Any = None,
        max_height: Any = None,
    ) -> Any:
        orig_width, orig_height = img.size
        if max_width and max_height:
            target_width = min(max_width, terminal_width)
            target_height = min(max_height, terminal_height)
        else:
            target_width = terminal_width
            target_height = terminal_height
        aspect = orig_height / orig_width
        new_width = target_width
        new_height = int(target_width * aspect)
        if new_height > target_height:
            new_height = target_height
            new_width = int(target_height / aspect)
        new_width = max(1, new_width)
        new_height = max(1, new_height)
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    @staticmethod
    def load_svg(svg_path: Any, width: Any = None, height: Any = None) -> ImageFile:
        if not SVG_SUPPORT:
            print(
                "Error: SVG support requires 'cairosvg' library. Install with: pip install cairosvg",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            if not width or not height:
                term_width, term_height = Pycatimg.get_terminal_size()
                term_height *= 2
                width = width or term_width
                height = height or term_height
            png_data = svg2png(url=svg_path, output_width=width, output_height=height)
            img = Image.open(io.BytesIO(png_data))
            return img
        except Exception as e:
            print(f"Error loading SVG: {e}", file=sys.stderr)
            sys.exit(1)

    @staticmethod
    def load_image(image_path: str, width: Any = None, height: Any = None) -> ImageFile:
        if image_path.lower().endswith(".svg"):
            return Pycatimg.load_svg(image_path, width, height)
        try:
            return Image.open(image_path)
        except Exception as e:
            print(f"Error loading image: {e}", file=sys.stderr)
            sys.exit(1)

    @staticmethod
    def rgb_to_ansi(r: Any, g: Any, b: Any) -> str:
        return f"\x1b[38;2;{r};{g};{b}m"

    @staticmethod
    def image_to_ansi(img: Any) -> str:
        img = img.convert("RGB")
        width, height = img.size
        output_lines = []
        for y in range(height):
            line = []
            for x in range(width):
                r, g, b = img.getpixel((x, y))
                line.append(f"{Pycatimg.rgb_to_ansi(r, g, b)}██")
            output_lines.append("".join(line))
        output = "\n".join(output_lines) + "\x1b[0m"
        return output

    @staticmethod
    def image_to_ansi_blocks(img: Any) -> str:
        img = img.convert("RGB")
        width, height = img.size
        if height % 2 != 0:
            new_img = Image.new("RGB", (width, height + 1), (0, 0, 0))
            new_img.paste(img, (0, 0))
            img = new_img
            height += 1
        output_lines = []
        for y in range(0, height, 2):
            line = []
            for x in range(width):
                r1, g1, b1 = img.getpixel((x, y))
                r2, g2, b2 = img.getpixel((x, y + 1)) if y + 1 < height else (0, 0, 0)
                line.append(f"\x1b[38;2;{r1};{g1};{b1}m\x1b[48;2;{r2};{g2};{b2}m▀")
            output_lines.append("".join(line))
        output = "\n".join(output_lines) + "\x1b[0m"
        return output

    @staticmethod
    def get_terminal_size() -> tuple[int, int]:
        try:
            columns, rows = os.get_terminal_size()
            return (columns, rows)
        except:
            return (80, 24)

    @staticmethod
    def catimg(
        image_path: str,
        width: Any = None,
        height: Any = None,
        use_half_blocks: bool = True,
        dpi: int = 96,
        bg_color: Any = None,
    ) -> None:
        if not os.path.exists(image_path):
            print(f"Error: File '{image_path}' not found", file=sys.stderr)
            sys.exit(1)
        try:
            term_width, term_height = Pycatimg.get_terminal_size()
            if use_half_blocks:
                term_height *= 2
            render_width = None
            render_height = None
            if width or height:
                if width:
                    render_width = min(width, term_width)
                if height:
                    render_height = min(height, term_height)
            else:
                render_width = term_width
                render_height = term_height
            if image_path.lower().endswith(".svg"):
                img = Pycatimg.load_svg(image_path, render_width, render_height)
            else:
                img = Pycatimg.load_image(image_path)
                img = Pycatimg.resize_image(img, term_width, term_height, width, height)
            if use_half_blocks:
                output = Pycatimg.image_to_ansi_blocks(img)
            else:
                output = Pycatimg.image_to_ansi(img)
            print(output)
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(
            description="Display images in terminal with true color support (including SVG)",
            epilog="Supports: PNG, JPEG, GIF, BMP, TIFF, SVG (requires cairosvg)",
        )
        parser.add_argument("image", help="Path to image file (supports SVG)")
        parser.add_argument("-w", "--width", type=int, help="Maximum width in characters")
        parser.add_argument("-H", "--height", type=int, help="Maximum height in characters")
        parser.add_argument(
            "--no-half-blocks",
            action="store_true",
            help="Disable half-block characters (lower vertical resolution)",
        )
        parser.add_argument("--dpi", type=int, default=96, help="DPI for SVG rendering (default: 96)")
        parser.add_argument(
            "--bg-color", help='Background color for transparent areas (e.g., "black" or "#000000")'
        )
        args = parser.parse_args()
        if args.image.lower().endswith(".svg") and (not SVG_SUPPORT):
            print("Warning: SVG support requires 'cairosvg'. Install with:", file=sys.stderr)
            print("  pip install cairosvg", file=sys.stderr)
            print("\nFor system dependencies (Linux):", file=sys.stderr)
            print("  Ubuntu/Debian: sudo apt-get install libcairo2-dev", file=sys.stderr)
            print("  Fedora: sudo dnf install cairo-devel", file=sys.stderr)
            print("Arch: sudo pacman -S cairo", file=sys.stderr)
            sys.exit(1)
        Pycatimg.catimg(args.image, args.width, args.height, use_half_blocks=not args.no_half_blocks)


if __name__ == "__main__":
    raise SystemExit(Pycatimg.main())
