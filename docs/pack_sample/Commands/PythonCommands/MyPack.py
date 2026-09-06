#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


class MyPack(PythonCommand):
    NAME = "配布サンプル"

    def __init__(self) -> None:
        super().__init__()

    def do(self) -> None:
        self.press(Button.A)
