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
        self.hold(Button.ZR)
        while True:
            self.press(Button.A, wait=0, duration=0.005)
            self.press(Button.B, wait=0, duration=0.005)
            self.press(Button.X, wait=0, duration=0.005)
            self.press(Button.Y, wait=0, duration=0.005)
