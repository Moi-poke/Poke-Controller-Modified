#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""利用者スクリプトの公開API面の検査。

Commands/PythonCommands/・Commands/McuCommands/ 以下のスクリプトが
凍結された公開面（Commands.* の4経路＋標準ライブラリ＋既知の
サードパーティ）以外を import していないか確かめる。

背景: 利用者スクリプトは10年近い積み上げがあり、メジャー更新でも
動かし続ける契約である。逆に言えば、ここで許可したもの以外へ
手を出すスクリプトは無い前提で、アプリ内部（core.*・Window・
Settings・GuiAssets 等）を自由に再編できる。

使い方: uv run --frozen python tools/check_user_api.py（task userapi）
違反があれば一覧を出して非ゼロ終了する。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    ROOT / "SerialController" / "Commands" / "PythonCommands",
    ROOT / "SerialController" / "Commands" / "McuCommands",
]

# Commands.* のうち利用者スクリプトが使ってよい経路。
# （Keys / PythonCommandBase / McuCommandBase / WakeLink / CommandVision）
ALLOWED_COMMANDS_SUBS = {
    "Keys",
    "PythonCommandBase",
    "McuCommandBase",
    "WakeLink",
    "CommandVision",
}

# 標準ライブラリ以外で許可するトップレベル名（pyproject の依存＋実績）。
THIRD_PARTY = {
    "cv2",
    "numpy",
    "PIL",
    "pandas",
    "scipy",
    "requests",
    "yaml",
    "loguru",
    "icecream",
    "deprecated",
    "pynput",
    "serial",
    "pygubu",
    "matplotlib",
    # 利用者スクリプトの実績（listen_shiny.py が使用）
    "pyaudio",
}


def _top_names(tree: ast.AST) -> list[tuple[str, str]]:
    """(トップレベル名, 詳細) の一覧。相対 import は '<relative>'。"""
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append((a.name.split(".")[0], f"import {a.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                found.append(("<relative>", "relative import"))
            elif node.module:
                top = node.module.split(".")[0]
                detail = f"from {node.module} import ..."
                if top == "Commands":
                    parts = node.module.split(".")
                    sub = parts[1] if len(parts) > 1 else ""
                    found.append((f"Commands.{sub}", detail))
                else:
                    found.append((top, detail))
    return found


def main() -> int:
    allowed_top = set(sys.stdlib_module_names) | THIRD_PARTY | {"Commands"}
    violations: list[str] = []
    checked = 0
    for base in TARGETS:
        if not base.is_dir():
            violations.append(f"{base}: ディレクトリがありません")
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (OSError, SyntaxError) as e:
                violations.append(f"{rel}: 読めません: {e}")
                continue
            checked += 1
            for top, detail in _top_names(tree):
                if top == "<relative>":
                    violations.append(f"{rel}: 相対 import 禁止: {detail}")
                elif top == "Commands":
                    violations.append(f"{rel}: Commands 直下の指定が不正: {detail}")
                elif top.startswith("Commands."):
                    sub = top.split(".", 1)[1]
                    if sub not in ALLOWED_COMMANDS_SUBS:
                        violations.append(f"{rel}: 凍結外の Commands 経路: {detail}")
                elif top not in allowed_top:
                    violations.append(
                        f"{rel}: 許可外の import（公開API面外）: {detail}"
                    )
    if violations:
        print("利用者API面の違反:")
        for v in violations:
            print(f"  {v}")
        return 1
    print(f"利用者API面 OK（{checked} ファイル検査）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
