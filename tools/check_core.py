#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ の境界検査。GUI依存の混入を防ぐ。

規則:
  - core/** は tkinter / pygubu を import してはならない
  - core/** は SerialController 直下・Commands 以下のアプリ層を
    import してはならない（core.* 同士・標準ライブラリ・
    サードパーティは可）

使い方: uv run --frozen python tools/check_core.py（task bounds）
違反があれば一覧を出して非ゼロ終了する。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "SerialController" / "core"

# 持ち込み禁止のトップレベル名（core 内からの参照）
BANNED_TOP = {"tkinter", "pygubu"}
# アプリ層（core から参照してはならない同居パッケージ）
BANNED_APP = {
    "Commands",
    "Settings",
    "GuiAssets",
    "Window",
    "Menubar",
    "KeyConfig",
    "TagEditor",
    "WakeSetup",
    "InputLogConfig",
    "LogPane",
    "CommandPalette",
    "CommandTags",
    "CommandStats",
    "WindowUtils",
    "WindowGeometry",
    "Camera",
    "Keyboard",
    "PokeConLogger",
    "LineNotify",
    "DiscordNotify",
    "Utility",
    "CommandLoader",
    "launcher",
}


def _module_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                names.append("<relative>")
            elif node.module:
                names.append(node.module.split(".")[0])
    return names


def main() -> int:
    if not CORE.is_dir():
        print("core/ がありません")
        return 1
    violations: list[str] = []
    files = sorted(CORE.rglob("*.py"))
    if not files:
        print("core/ に .py がありません")
        return 1
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as e:
            violations.append(f"{path}: 読めません: {e}")
            continue
        rel = path.relative_to(ROOT).as_posix()
        for name in _module_names(tree):
            if name in BANNED_TOP:
                violations.append(f"{rel}: GUI 依存の import 禁止: {name}")
            elif name in BANNED_APP:
                violations.append(f"{rel}: アプリ層の import 禁止: {name}")
            elif name == "<relative>":
                violations.append(
                    f"{rel}: 相対 import 禁止（core.* の絶対 import を使う）"
                )
    if violations:
        print("境界違反:")
        for v in violations:
            print(f"  {v}")
        return 1
    print(f"境界 OK（{len(files)} ファイル検査）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
