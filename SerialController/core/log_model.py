"""log_model.py - ログ欄に流す1行の型と、その水準の推定.

tkinter を持たない純粋な層。描画（ui/log_view.py）とキュー（LogPane.py）
の間で受け渡す「1行」をここで決める。

水準（level）を持たせる理由：従来のログ欄は print の文字列をそのまま
流していたため、例外の traceback も進捗の print も同じ見た目で、
エラーが起きても埋もれて気づけなかった。Prism Launcher のログ画面や
ブラウザ開発ツールのコンソールと同じく、行ごとに水準を持たせて
色・記号・絞り込み・集約（下の欄）の手がかりにする。

print からは水準が来ないため、LineClassifier が本文から推定する。
推定は「明示的な目印がある行だけ」に留め、普通の文は info のままにする
（誤ってエラー扱いにすると、色が警報として信用されなくなるため）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

#: 水準。並びは重さの順（絞り込みメニューの並びにも使う）。
LEVELS: tuple[str, ...] = (
    "debug",
    "system",
    "info",
    "success",
    "result",
    "warning",
    "error",
    "input",
)

#: 絞り込みの単位。細かい水準をそのまま並べると選びにくいので、
#: 利用者から見た種類（出力・結果・警告・エラー）に束ねる。
FILTER_GROUPS: dict[str, tuple[str, ...]] = {
    "info": ("debug", "system", "info", "success", "input"),
    "result": ("result",),
    "warning": ("warning",),
    "error": ("error",),
}

FILTER_LABELS: dict[str, str] = {
    "info": "通常の出力",
    "result": "結果（print2）",
    "warning": "警告",
    "error": "エラー",
}

#: 行頭の記号。色だけで区別しない（色覚の差・白黒の印刷でも分かる）。
LEVEL_MARKS: dict[str, str] = {
    "error": "✖ ",
    "warning": "⚠ ",
    "success": "✔ ",
    "result": "▶ ",
}

#: 下の欄へ自動で集める水準。
PROBLEM_LEVELS: frozenset[str] = frozenset({"error", "warning"})


@dataclass(frozen=True, slots=True)
class LogEntry:
    """ログ欄の1行。text は末尾の改行を含まない。

    pin は「下の欄（結果とエラー）にも残す」指定。print2 の出力や、
    traceback の最後の行（例外名と内容）に立てる。
    """

    text: str
    level: str = "info"
    ts: float = field(default_factory=time.time)
    pin: bool = False


def format_time(ts: float) -> str:
    """時刻欄の文字列。ミリ秒まで出す（入力ログとの突き合わせ用）。"""
    lt = time.localtime(ts)
    ms = int((ts - int(ts)) * 1000)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.{ms:03d}"


def split_lines(text: str) -> list[str]:
    """キューから来た文字列を行に割る。末尾の改行1つは区切りとして捨てる。

    "a\\nb\\n" → ["a", "b"]、"a" → ["a"]、"\\n" → [""]（空行も1行）。
    """
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


_ERROR_RE = re.compile(
    r"^\s*(\[(ERROR|FATAL|CRITICAL)[\]/: ]|(ERROR|FATAL|CRITICAL)[:：]|(エラー|失敗)[:：])"
)
_WARN_RE = re.compile(r"^\s*(\[(WARN|WARNING)[\]/: ]|(WARN|WARNING)[:：]|警告[:：])")
_SYSTEM_RE = re.compile(r"^-- .+ --$")
_SUCCESS_TEXT = "-- finished successfully. --"
_TB_HEAD = "Traceback (most recent call last):"
#: 連鎖した例外の継ぎ目。traceback の途中に非インデントで現れる。
_TB_CHAIN = (
    "During handling of the above exception",
    "The above exception was the direct cause",
)
#: PythonCommandBase が例外時に出す見出し。エラーだが、本体は直後の
#: traceback なので下の欄へは集めない（同じ失敗が2行並ぶのを避ける）。
_EXC_LEAD = "例外が発生しました"


class LineClassifier:
    """print の1行から水準を推定する。traceback の塊を追うため状態を持つ。

    traceback は「Traceback (most recent call last):」で始まり、
    インデントされた File 行が続き、最後に非インデントの
    「ValueError: ...」で終わる。塊全体をエラーにし、最後の行だけを
    下の欄へ集める（何が起きたかが1行で分かる行だから）。
    """

    def __init__(self) -> None:
        self._in_tb = False

    def classify(self, text: str) -> tuple[str, bool]:
        """(level, pin) を返す。"""
        stripped = text.strip()
        if self._in_tb:
            if not stripped or text[:1] in (" ", "\t"):
                return "error", False
            if stripped.startswith(_TB_CHAIN) or stripped == _TB_HEAD:
                return "error", False
            # 非インデント行＝例外名と内容。ここで塊が終わる。
            self._in_tb = False
            return "error", True
        if stripped == _TB_HEAD:
            self._in_tb = True
            return "error", False
        if stripped.startswith(_EXC_LEAD):
            return "error", False
        if _ERROR_RE.match(text):
            return "error", True
        if _WARN_RE.match(text):
            return "warning", True
        if stripped == _SUCCESS_TEXT:
            return "success", False
        if _SYSTEM_RE.match(stripped):
            return "system", False
        return "info", False

    def entries(self, raw: str, ts: float | None = None) -> list[LogEntry]:
        """キューの文字列1件を LogEntry の列にする。"""
        now = time.time() if ts is None else ts
        out: list[LogEntry] = []
        for line in split_lines(raw):
            level, pin = self.classify(line)
            out.append(LogEntry(line, level, now, pin))
        return out


def entries_for(
    raw: str, level: str, pin: str = "none", ts: float | None = None
) -> list[LogEntry]:
    """水準が決まっている文字列（print2・入力ログ・logger）を行に割る。

    pin は "none" / "all" / "first"。print2 は全行が結果なので "all"。
    logger のエラーは traceback 付きの複数行で来るため "first" にして、
    下の欄では1件として見せる（全文が下の欄を埋めると一覧性が落ちる）。
    """
    now = time.time() if ts is None else ts
    lines = split_lines(raw)
    return [
        LogEntry(line, level, now, pin == "all" or (pin == "first" and i == 0))
        for i, line in enumerate(lines)
    ]


def visible_levels(groups: dict[str, bool]) -> set[str]:
    """絞り込みの ON/OFF から、表示する水準の集合を作る。"""
    shown: set[str] = set()
    for group, levels in FILTER_GROUPS.items():
        if groups.get(group, True):
            shown.update(levels)
    return shown
