#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""マクロ録画の取りまとめ（GUI非依存）。

InputLogger が組み立てる InputEvent の列を受け、コマンドへ貼れる
do() 本体の行（self.press / self.wait）へ直す。貼り付け先の書式は
Commands.* の凍結名だけを使う（from Commands.Keys import ...、
from Commands.PythonCommandBase import PythonCommand の世界）。
"""

from __future__ import annotations

from core.InputLog import InputEvent, is_rotation, rotation_text, snap_direction


class MacroRecorder:
    """録画→保持→描画の3手順だけを持つ小さな記録係。"""

    def __init__(self) -> None:
        # 受け取った順に貯める。並べ替えはしない（押した順が命のため）。
        self._events: list[InputEvent] = []
        # 録画中だけ feed を受け付ける。start/stop の外の操作は混ぜない。
        self._recording = False

    def start(self) -> None:
        """録画を始める。貯めていた前回ぶんは捨てる。"""
        self._events = []
        self._recording = True

    def feed(self, event: InputEvent) -> None:
        """イベントを1件貯める。録画中でなければ何もしない。"""
        if not self._recording:
            return
        self._events.append(event)

    def stop(self) -> list[InputEvent]:
        """録画を終える。貯めた列の写しを返す（後から見返す用）。"""
        self._recording = False
        return list(self._events)

    def render(self, name: str | None = None) -> list[str]:
        """貯めた列を do() へ貼れる行にする。決定的（同じ列は同じ行）。

        name は将来の保存名の予約であり、行の内容には影響しない。
        貼り付け先（do()）が決めるものなので、ここでは使わない。
        """
        _ = name
        lines: list[str] = []
        # 直前に離した時刻。次の押し始めとの差が「待ち」になる。
        prev_release: float | None = None
        for event in self._events:
            # 押下時間は離したときだけ確定する。途中（PRESS/CHANGE）は
            # 行にしない（半端な duration を付けないため）。
            if event.action != "RELEASE":
                continue
            if event.kind == "stick" and is_rotation(event):
                # 1周の回しは Direction 1つでは表せない。press 化せず
                # 説明だけ残す（pressRep へのまとめもしない）。
                lines.append(f"    # {rotation_text(event)}")
                prev_release = event.at
                continue
            target = self._press_target(event)
            if target is None:
                # 向きが取れないなど、press にできないものは説明に留める。
                lines.append(
                    f"    # {event.name}（再現できない操作のためpress化しない）"
                )
                prev_release = event.at
                continue
            # 押し始め＝離した時刻−押下時間。間の空きが待ちになる。
            # 連打もまとめず、空きはそのまま self.wait で出す。
            start = event.at - (event.duration if event.duration is not None else 0.0)
            if prev_release is not None:
                gap = round(start - prev_release, 2)
                if gap > 0.0:
                    lines.append(f"    self.wait({gap:.2f})")
            if event.duration is None:
                lines.append(f"    self.press({target})")
            else:
                lines.append(f"    self.press({target}, duration={event.duration:.2f})")
            prev_release = event.at
        return lines

    @staticmethod
    def _press_target(event: InputEvent) -> str | None:
        """press に渡せる名前を返す。作れなければ None。"""
        if event.kind == "stick":
            # どちらのスティックか（LEFT/RIGHT）は向きを持たないので、
            # 倒した向きを8方向へ丸めた Direction 名へ直す（貼れば動く形）。
            label = snap_direction(event.deg)
            if not label:
                return None
            prefix = "R_" if event.name.endswith("RIGHT") else ""
            return f"Direction.{prefix}{label}"
        if event.kind in ("button", "hat"):
            # Button.A / Hat.TOP はそのまま press に渡せる。
            return event.name
        return None
