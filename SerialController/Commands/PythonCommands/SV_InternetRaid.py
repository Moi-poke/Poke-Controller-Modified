#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import ImageProcPythonCommand
from Commands.Keys import Button

class SV_InternetRaid(ImageProcPythonCommand):

    NAME = 'SV無限野良レイド'

    def __init__(self, cam):
        super().__init__(cam)

    def do(self):
        while True:
            self.press(Button.A, 0.1, 0.5)
            self.press(Button.X, 0.1, 0.5)
