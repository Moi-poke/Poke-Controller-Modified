#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


# Mash a button A
# A連打
class Mash_A(PythonCommand):
    NAME = "A連打"

    def __init__(self):
        super().__init__()

    def do(self):
        # 押下は 10ms 以上にする。Pico の mailbox は 8ms 周期で畳むため、
        # 5ms では次の値で上書きされて押下が消える（実測: 4ms で約1/20、
        # 8ms 以上で 20/20 到達）。legacy 経路でも短すぎる押下は読まれない。
        # ZR を押しっぱなしにして A/B/X/Y を回す形にしてある。ZR-hold は
        # _cleanup（keys.end）が離すため、ここで明示的に離さない。
        self.hold(Button.ZR)
        while True:
            self.press(Button.A, wait=0, duration=0.01)
            self.press(Button.B, wait=0, duration=0.01)
            self.press(Button.X, wait=0, duration=0.01)
            self.press(Button.Y, wait=0, duration=0.01)
