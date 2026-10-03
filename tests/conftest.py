"""pytest の土台。SerialController を import できるようにする。

アプリ本体は SerialController/ を sys.path に置いて動く前提なので、
テストも同じ置き方にする（パッケージ改名は Phase 2 以降の話）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

import tkinter as tk
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def tk_root() -> Iterator[tk.Tk]:
    """実 Tk を使うテストで共有する根。

    Tk を作り直すと Windows では時々 tcl_findLibrary で作れなくなり、テストが
    黙って skip される。1 プロセス 1 個に留め、窓はテストごとの Toplevel にする。
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # 表示の無い環境では Tk 自体が作れない
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()
