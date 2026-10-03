#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status_view.py - 実行状態を 1 行の文字列へ決める（純粋ロジック・Tkなし）。

複数台を 1 画面に並べると、どの窓が動いているかを 1 画面目で見分ける必要が
ある。タイトルだけでは追えないので、コンパクトバーとプレビューのバッジに
同じ 1 行を出す。決めるだけなら状態 6 つほどしかないため、ウィジェットへ
触らずここで閉じる。

Tk の変数も描画も触らない（ヘッドレスで検証できるように）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# バッジの背景色。Win32 の COLORREF（0x00BBGGRR）で、
# CaptureArea.setBadge が受け取る形と同じ並び順。
#   running      … 緑（動いている）
#   paused       … 橙（人の操作で止まっている）
#   stopping     … 橙（停止を待っている。待ち時間の残りも出す）
#   disconnected … 赤（繋がっていない）
#   idle         … 灰（止まっているが繋がっている）
BADGE_COLORS: Final[dict[str, int]] = {
    "running": 0x00307A2E,
    "paused": 0x000080C0,
    "stopping": 0x000080C0,
    "disconnected": 0x002020B0,
    "idle": 0x00505050,
}

# 1 行に出すコマンド名の上限。Window.TITLE_COMMAND_MAX と同じ値を複製して
# ある（core は Window を import できないため）。ここを直するときは
# Window.TITLE_COMMAND_MAX も直すこと。タイトルとバッジの文字数が
# 食い違うと、どちらが今の状態か分からなくなる。
_COMMAND_MAX: Final[int] = 20

# 状態ごとの表示。どの状態でも text 側は「1 行で読める長さ」を守る。
_TEXT_IDLE: Final[str] = "■ 停止中"
_TEXT_DISCONNECTED: Final[str] = "⚠ 未接続"
_MARK_RUNNING: Final[str] = "▶"
_MARK_PAUSED: Final[str] = "⏸"
_MARK_STOPPING: Final[str] = "⏳"


@dataclass(frozen=True, slots=True)
class StatusView:
    """状態の表し方。text はそのまま画面に出す（バッジにも載せる）。"""

    kind: str
    text: str


def _clip(name: str) -> str:
    """長い名前を「19 文字 + …」に切る（Window._update_title と同じ規則）。"""
    if len(name) <= _COMMAND_MAX:
        return name
    return name[: _COMMAND_MAX - 1] + "…"


def status_view(
    *,
    profile: str,
    running_command: str,
    paused: bool,
    stop_waited_ms: int,
    device: str,
    opened: bool,
) -> StatusView:
    """実行状態・接続状態・プロファイル名から、1 行の表し方を決める。

    優先順位は「停止待ち > 実行中/一時停止 > 未接続 > 停止中」。停止待ちを
    先頭に出すのは、そのあいだは待つ以外の手が無いから（実行中の表示が
    残ると「まだ動いてるのでは」と誤解される）。

    profile が空でなければ先頭に ``[名前] `` を付ける。並べた窓が見分けられる
    ことが第1目的のため、どの状態でも必ず付ける。
    """
    if stop_waited_ms > 0:
        kind = "stopping"
        text = f"{_MARK_STOPPING}停止待ち {stop_waited_ms // 1000}秒"
    elif running_command:
        kind = "paused" if paused else "running"
        mark = _MARK_PAUSED if paused else _MARK_RUNNING
        text = f"{mark} {_clip(running_command)}"
    elif not device or not opened:
        kind = "disconnected"
        text = _TEXT_DISCONNECTED
    else:
        kind = "idle"
        text = _TEXT_IDLE

    prefix = f"[{profile}] " if profile else ""
    return StatusView(kind=kind, text=prefix + text)


def hex_to_colorref(text: str) -> int:
    """``#RRGGBB`` を Win32 の ``COLORREF``（0x00BBGGRR）へ変換する。

    Tk の並び順（赤・緑・青）と逆なので入れ替える。変換できない値は 0
    （透明＝描かない）。バッジの背景色は 0 が安全なので、
    壊れた設定でも落ちずに「色なし」で済む。
    """
    value = (text or "").strip()
    if len(value) != 7 or not value.startswith("#"):
        return 0
    try:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
    except ValueError:
        return 0
    return (blue << 16) | (green << 8) | red
