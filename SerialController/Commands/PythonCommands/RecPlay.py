#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.Keys import Direction, Stick
from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand
from tkinter import filedialog


# Mash a button A
# A連打
class PlayRec(PythonCommand):
    NAME = '記録したログを再生'

    def __init__(self):
        super().__init__()

    # press button at duration times(s)
    def stick(self, buttons, duration=0.015, wait=0.1):
        self.keys.input(buttons, ifPrint=False)
        # time.sleep では Stop を見ないため、停止要求があっても待ち切る
        # まで止まらない。停止対応の待ちへ変える（挙動は同じ）。
        self.wait(duration)

    # press button at duration times(s)
    def stickEnd(self, buttons):
        self.keys.inputEnd(buttons)
        self.checkIfAlive()

    def LStick(self, angle, r=1.0, duration=0.015):
        # 生の行を直接送っていた旧実装（Leonardo 書式）を、姿勢の申告へ
        # 変えた。生の行は legacy 書式（btn を2ビットシフト・可変長）の
        # ため Pico 経路では ERR になる。Direction へ直せば、どちらの線
        # でも同じ倒しになる（y の反転は申告側が行う）。
        # 座標は約束が同じで、丸めが ±1 ずれることがある（int と ceil /
        # floor の差）。スティックの分解能では無視できる。
        self.keys.input([Direction(Stick.LEFT, angle, r)], ifPrint=False)
        self.wait(duration)

    def do(self):
        file = filedialog.askopenfile(initialdir='~/')
        self.log = file.name
        print(self.log)
        with open(self.log) as f:
            l_strip = [list(map(float, s.strip().split(","))) for s in f.readlines()]
            # print(l_strip[:20])
        for i in l_strip:
            self.LStick(i[0], i[1], duration=i[2] * 1.0)
            # self.wait(i[2]*0.90)

        self.stickEnd(Direction(Stick.LEFT, 0, 0, showName=f'Angle={l_strip[0][0]},r={l_strip[0][1]}'))
