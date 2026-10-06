#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.Keys import Button, Direction
from Commands.PythonCommandBase import PythonCommand


# 高速日時変更 Pico用
# 元: 高速日時変更 ver.0.1.0 (フウ作) を現行picoファーム用に移植
# 変更点: 生シリアル直書き(sendCommand直書き)を press(duration, wait) に置換。
# 対応則は機械的: sendCommand(状態, w) は press(状態, w, 0) に等しい
# （次の行で上書きされるまで保持）。Neutral行は self.wait(w) に置換。
# switch2対応: d:\Download\switch_util\switch_util.py のswitch2系に準拠。
#   ・スクロール部は L/R スティック交互・0.1秒周期（switch1のみ0.04）
#   ・個別pressはスティック倒し（Lstick/Rstick交互）
class QuickDateChangePico(PythonCommand):
    NAME = "高速日時変更 Pico用"

    def __init__(self):
        super().__init__()

    def do(self):
        print("-------------------------------------")
        print("高速日時変更 Pico用")
        print("元: 高速日時変更 ver.0.1.0 Developed by フウ")
        print("-------------------------------------")

        while True:
            self.quickDateChange()
            self.wait(1.0)

    def quickDateChange(self):
        # ゲーム選択画面⇒設定
        self.press(Direction.LEFT, duration=0.04, wait=0.0)
        self.wait(0.3)  # 設定画面に移動できない場合は要調整。
        self.press(Direction.DOWN, duration=0.04, wait=0.0)
        self.press(Direction.LEFT, duration=0.04, wait=0.0)
        self.press(Button.A, duration=0.05, wait=0.75)

        # 設定の一番下まで移動（switch2は0.1秒周期・L/R交互）
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Button.A, duration=0.05, wait=0.15)

        # 日付と時刻を選択（switch2は0.1秒周期・L/R交互）
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(
            Direction.DOWN, duration=0.05, wait=0.27
        )  # カーソルが日付と時刻を選択しない場合は要調整。
        self.press(Direction.R_DOWN, duration=0.05, wait=0.05)
        self.press(Direction.DOWN, duration=0.05, wait=0.05)
        self.press(Button.A, duration=0.04, wait=0.26)
        self.wait(0.20)  # タイムゾーンを変更してしまう場合はwaitを大きくすること。

        # 現在の日付と時刻を選択
        self.press(Direction.DOWN, duration=0.04, wait=0.0)
        self.press(Direction.R_DOWN, duration=0.04, wait=0.0)
        self.press(
            Button.A, duration=0.05, wait=0.15
        )  # 時刻変更でminを変更しない場合はwaitを大きくすること。

        # 時間変更画面（L/R交互）
        self.press(Direction.RIGHT, duration=0.04, wait=0.0)
        self.press(Direction.R_RIGHT, duration=0.04, wait=0.0)
        self.press(Direction.RIGHT, duration=0.04, wait=0.0)
        self.press(Direction.R_RIGHT, duration=0.04, wait=0.0)
        self.press(Direction.DOWN, duration=0.04, wait=0.0)
        self.press(Button.A, duration=0.04, wait=0.0)
        self.press(Direction.R_RIGHT, duration=0.04, wait=0.0)
        self.press(Button.A, duration=0.04, wait=0.0)
        self.wait(0.25)  # HOME画面に戻らない場合は要調整。

        # ホーム画面に戻る
        self.press(Button.HOME, duration=0.06, wait=0.94)
        self.wait(0.10)
