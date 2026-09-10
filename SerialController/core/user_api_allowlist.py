#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""利用者スクリプトの公開API面の正本（GUI非依存）。

Commands.* のうち利用者スクリプトが使ってよい経路と、標準ライブラリ
以外で許可するトップレベル名を一か所で持つ。正本はこのファイル。
`tools/check_user_api.py`・`core/blockly_validate.py`・
`core/pack_zip.py` はここから読むだけにし、個別に複写しない。

表面ごとの深刻度の違い（仕様）:
  - `tools/check_user_api.py`: 許可外は違反（非ゼロ終了で落とす）。
  - `core/blockly_validate.py`: 許可外・未知は異常（保存不可）。
  - `core/pack_zip.py`: 相対 import・公開API面外の Commands 経路は
    異常（導入不可）、見知らぬトップレベルは注意（導入は通す）。
この違いは意図的で、統一しない。保存時は厳しく、配布時は先方の
環境差を注意で残す。
"""

from __future__ import annotations

# Commands.* のうち利用者スクリプトが使ってよい経路。
# （Keys / PythonCommandBase / McuCommandBase / WakeLink / CommandVision / CommandAudio）
ALLOWED_COMMANDS_SUBS = frozenset(
    {
        "Keys",
        "PythonCommandBase",
        "McuCommandBase",
        "WakeLink",
        "CommandVision",
        "CommandAudio",
    }
)

# 標準ライブラリ以外で許可するトップレベル名（pyproject の依存＋実績）。
THIRD_PARTY = frozenset(
    {
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
        "sounddevice",
    }
)
