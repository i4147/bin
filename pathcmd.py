#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import os


class Pathcmd:
    @staticmethod
    def run() -> None:
        p_env = os.environ.get("PATH")
        pdirs = p_env.split(":")
        for p in pdirs:
            print(p)


if __name__ == "__main__":
    Pathcmd.run()
