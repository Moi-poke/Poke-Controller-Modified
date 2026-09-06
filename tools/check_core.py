#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/ と services/ と ui/ の境界検査。層の向きを守る。

規則:
  - core/** は tkinter / pygubu を import してはならない
  - core/** は SerialController 直下・Commands 以下のアプリ層を
    import してはならない（core.* 同士・標準ライブラリ・
    サードパーティは可）
  - services/** は tkinter / pygubu を import してはならない
    （タイマーは呼び出し側の schedule に任せ、描画は合図で返す）
  - services/** は画面部品（Window・GuiAssets・ダイアログ類）を
    import してはならない（core.*・Commands・Keyboard・Settings・
    標準ライブラリ・サードパーティは可）
  - ui/** は Window 本体を import してはならない（循環になる）。
    tkinter・core・services の利用は可

使い方: uv run --frozen python tools/check_core.py（task bounds）
違反があれば一覧を出して非ゼロ終了する。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "SerialController" / "core"
SERVICES = ROOT / "SerialController" / "services"
UI = ROOT / "SerialController" / "ui"

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

# services から参照してはならない画面部品。手順の所有に専念させ、
# 表示（tk 変数・ウィジェット・ダイアログ）への依存を断つため。
# core・Commands・Keyboard・Settings は手順に要るので許可する。
BANNED_UI = {
    "Window",
    "GuiAssets",
    "Menubar",
    "KeyConfig",
    "TagEditor",
    "WakeSetup",
    "InputLogConfig",
    "CommandPalette",
    "LogPane",
    "WindowUtils",
    "WindowGeometry",
    "Camera",
    "CommandLoader",
    "CommandStats",
    "CommandTags",
    "InputLog",
    "PokeConLogger",
    "Utility",
    "launcher",
    "video_capture_wrapper",
    "DiscordNotify",
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


def _check_dir(
    base: Path, label: str, banned_top: set[str], banned_app: set[str]
) -> tuple[list[str], int]:
    """1フォルダを検査する。(違反一覧, 検査件数) を返す。"""
    violations: list[str] = []
    if not base.is_dir():
        return ([f"{label}/ がありません"], 0)
    files = sorted(base.rglob("*.py"))
    if not files:
        return ([f"{label}/ に .py がありません"], 0)
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as e:
            violations.append(f"{path}: 読めません: {e}")
            continue
        rel = path.relative_to(ROOT).as_posix()
        for name in _module_names(tree):
            if name in banned_top:
                violations.append(f"{rel}: GUI 依存の import 禁止: {name}")
            elif name in banned_app:
                violations.append(f"{rel}: アプリ層の import 禁止: {name}")
            elif name == "<relative>":
                violations.append(f"{rel}: 相対 import 禁止（絶対 import を使う）")
    return (violations, len(files))


def main() -> int:
    core_violations, core_count = _check_dir(CORE, "core", BANNED_TOP, BANNED_APP)
    svc_violations, svc_count = _check_dir(SERVICES, "services", BANNED_TOP, BANNED_UI)
    ui_violations, ui_count = _check_dir(UI, "ui", set(), {"Window"})
    violations = core_violations + svc_violations + ui_violations
    if violations:
        print("境界違反:")
        for v in violations:
            print(f"  {v}")
        return 1
    print(
        f"境界 OK（core {core_count} / services {svc_count} / ui {ui_count} "
        "ファイル検査）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
