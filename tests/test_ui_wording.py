"""メイン画面の文言の決まり（Tk を作らない、源の静的検査）。

日本語化された国内向けアプリ（OBS Studio の ja-JP 訳など）に合わせ、動詞・
一般名詞は日本語、略語・固有名・規格名は英字のまま残す。ボタンや見出しに
英語だけの文言が戻ってこないよう、ここで止める。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# メイン画面を組み立てるファイル。ダイアログ類は個別に見直すので含めない。
_PANELS = (
    "SerialController/ui/camera_panel.py",
    "SerialController/ui/serial_panel.py",
    "SerialController/ui/command_panel.py",
    "SerialController/ui/audio_panel.py",
    "SerialController/ui/log_panel.py",
    "SerialController/ui/layout_panel.py",
)

# 英字のまま残す語（略語・固有名・規格名）。日本語を含まない文言は、
# 英字の語がすべてここに載っているときだけ許す。
_LATIN_OK = {"COM", "LED", "Python", "MCU", "FPS", "Bcon", "HID", "USB"}

_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_WORD = re.compile(r"[A-Za-z]+")


def _inside_fstring(root: ast.AST, target: ast.AST) -> bool:
    """target が root 配下のどれかの f-string の部品か。"""
    return any(
        target in node.values
        for node in ast.walk(root)
        if isinstance(node, ast.JoinedStr)
    )


def _labels(path: str) -> list[tuple[int, str]]:
    """``text=`` に渡された文字列定数と、ボタンの ``["text"] =`` の代入先の値。"""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "text":
            values = [node.value]
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.slice, ast.Constant)
            and t.slice.value == "text"
            for t in node.targets
        ):
            values = [node.value]
        else:
            continue
        for value in values:
            for part in ast.walk(value):
                if isinstance(part, ast.JoinedStr):
                    # f-string は定数部分をつないで 1 つの文言として見る。
                    text = "".join(
                        c.value
                        for c in part.values
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    )
                    found.append((part.lineno, text))
                elif (
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and not _inside_fstring(value, part)
                ):
                    found.append((part.lineno, part.value))
    return found


@pytest.mark.parametrize("path", _PANELS)
def test_main_window_labels_are_japanese_except_known_latin_terms(path: str) -> None:
    # Then: 日本語を含むか、英字の語が全部「英字のまま残す語」であること。
    bad = [
        (line, text)
        for line, text in _labels(path)
        if text.strip()
        and not _JAPANESE.search(text)
        and not set(_WORD.findall(text)) <= _LATIN_OK
    ]
    assert not bad, f"{path}: 英語だけの文言 {bad}"
