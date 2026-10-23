#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
from multiprocessing import Pool
import sys
from typing import Any
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

mpl.use("Agg")
_FUNCTION_EXPR: str = ""


class Plotter:
    @staticmethod
    def f(x: np.ndarray) -> Any:
        return eval(
            _FUNCTION_EXPR,
            {"__builtins__": {}},
            {
                "x": x,
                "np": np,
                "sin": np.sin,
                "cos": np.cos,
                "tan": np.tan,
                "arctan": np.arctan,
                "arcsin": np.arcsin,
                "arccos": np.arccos,
                "sqrt": np.sqrt,
                "exp": np.exp,
                "log": np.log,
                "pi": np.pi,
                "e": np.e,
            },
        )

    @staticmethod
    def _eval_expr(expr: str) -> Any:
        return eval(
            expr,
            {"__builtins__": {}},
            {
                "np": np,
                "sin": np.sin,
                "cos": np.cos,
                "tan": np.tan,
                "arctan": np.arctan,
                "arcsin": np.arcsin,
                "arccos": np.arccos,
                "sqrt": np.sqrt,
                "exp": np.exp,
                "log": np.log,
                "pi": np.pi,
                "e": np.e,
            },
        )

    @staticmethod
    def _compute(args: tuple[str, np.ndarray]) -> np.ndarray:
        expr, x = args
        return eval(
            expr,
            {"__builtins__": {}},
            {
                "x": x,
                "np": np,
                "sin": np.sin,
                "cos": np.cos,
                "tan": np.tan,
                "arctan": np.arctan,
                "arcsin": np.arcsin,
                "arccos": np.arccos,
                "sqrt": np.sqrt,
                "exp": np.exp,
                "log": np.log,
                "pi": np.pi,
                "e": np.e,
            },
        )

    @staticmethod
    def _plot(function_expr: str, y: np.ndarray, x: np.ndarray) -> None:
        mask = np.isfinite(y)
        x_clean = x[mask]
        y_clean = y[mask]
        if len(y_clean) > 0:
            y_min, y_max = np.percentile(y_clean, [1, 99])
        else:
            y_min, y_max = (-10.0, 10.0)
        y_range = y_max - y_min if y_max != y_min else 10.0
        y_min -= y_range * 0.1
        y_max += y_range * 0.1
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(x_clean, y_clean, "b-", linewidth=2, label=function_expr)
        ax.axhline(y=0, color="black", linewidth=0.5)
        ax.axvline(x=0, color="black", linewidth=0.5)
        ax.set_xlabel("x")
        ax.set_ylabel("f(x)")
        ax.set_title(f"f(x) = {function_expr}")
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.set_xlim(-10, 10)
        ax.set_ylim(y_min, y_max)
        fig.savefig("plot.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    @staticmethod
    def main() -> None:
        if len(sys.argv) < 2:
            print("Usage: python script.py 'f(x)=expression'")
            print("Examples:")
            print("  python script.py 'f(x)=arctan(x)'")
            print("  python script.py 'f(x)=sin(x)'")
            print("  python script.py 'f(x)=(x+1)/(x-1)'")
            sys.exit(1)
        input_str = sys.argv[1]
        if "=" not in input_str:
            print("Error: Input must be in format 'f(x)=expression'")
            sys.exit(1)
        function_expr = input_str.split("=", 1)[1].strip()
        print(f"Plotting: {input_str}")
        x = np.linspace(-10, 10, 1000)
        try:
            with Pool() as pool:
                y = pool.apply(Plotter._compute, ((function_expr, x),))
        except Exception as e:
            print(f"Error evaluating function: {e}")
            sys.exit(1)
        Plotter._plot(function_expr, y, x)
        print("✅ Plot saved as 'plot.png'")


if __name__ == "__main__":
    Plotter.main()
