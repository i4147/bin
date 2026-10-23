#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from pathlib import Path
import shutil
import sys
from PIL import Image


class Pychafa:
    @staticmethod
    def render_image(
        image_path: str | Path, max_width: int | None = None, max_height: int | None = None
    ) -> None:
        path = Path(image_path)
        if not path.is_file():
            print(f"Error: File '{image_path}' not found.", file=sys.stderr)
            return
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as e:
            print(f"Error opening image: {e}", file=sys.stderr)
            return
        term_cols, term_rows = shutil.get_terminal_size((80, 24))
        target_width = max_width or term_cols
        target_height = max_height or term_rows * 2
        img_width, img_height = img.size
        aspect_ratio = img_height / img_width
        calc_height = int(target_width * aspect_ratio * 0.5) * 2
        if calc_height > target_height:
            calc_height = target_height & ~1
            calc_width = int(calc_height / 2 / (aspect_ratio * 0.5))
        else:
            calc_width = target_width
            calc_height = max(2, calc_height & ~1)
        img = img.resize((calc_width, calc_height), Image.Resampling.LANCZOS)
        pixels = img.load()
        output: list[str] = []
        for y in range(0, calc_height, 2):
            row_str = []
            for x in range(calc_width):
                r1, g1, b1, a1 = pixels[x, y]
                r2, g2, b2, a2 = pixels[x, y + 1]
                if a1 < 128 and a2 < 128:
                    row_str.append(" ")
                    continue
                fg = f"\x1b[38;2{r1};{g1};{b1}m" if a1 >= 128 else "\x1b[39m"
                bg = f"\x1b[48;2;{r2};{g2};{b2}m" if a2 >= 128 else "\x1b[49m"
                row_str.append(f"{fg}{bg}▀")
            output.append("----" + "".join(row_str) + "\x1b[0m")
        print("\n".join(output))

    @staticmethod
    def run() -> None:
        if len(sys.argv) < 2:
            print("Usage: python3 chafa_clone.py <image_path>")
            sys.exit(1)
        Pychafa.render_image(sys.argv[1])


if __name__ == "__main__":
    Pychafa.run()
