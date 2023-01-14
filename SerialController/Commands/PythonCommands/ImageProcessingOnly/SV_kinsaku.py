#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import ImageProcPythonCommand
from Commands.Keys import Button, Hat, Direction, Stick

class SV_Money(ImageProcPythonCommand):

    NAME = 'SV金策'

    def __init__(self, cam):
        super().__init__(cam)
        self.count = 1
        self.battle = 1
        self.err = 0
        self.lap = 1

    def do(self):
        while True:
            # self.press(Button.A, 0.1, 1.5)
            self.wait(0.5)

            if self.err > 5:
                if self.isContainTemplate("SV/fight.png", 0.8):
                    self.err = 0
                else:
                    self.press(Button.B, 0.1, 1)
                    self.press(Hat.BTM, 0.1, 0.5)
                    self.press(Button.A, 0.1, 1)
                    self.press(Button.A, 0.1, 1)

            else:
                if self.isContainTemplate('SV/change.png', 0.8):
                    self.press(Button.B, 0.1, 1)
                    self.count = self.count + 1

                if self.isContainTemplate('SV/miss.png', 0.8):
                    self.count = self.count + 1
                    self.press(Button.B, 0.1, 0.5)
                    self.press(Button.B, 0.1, 0.5)
                    self.press(Button.B, 0.1, 0.5)
                    self.err = self.err + 1

                if self.isContainTemplate("SV/fight.png", 0.8):
                    self.err = 0
                    self.press(Button.A, 0.1, 0.5)
                    self.wait(0.5)
                    if self.isContainTemplate('SV/shadow_flag.png', 0.8):
                        self.press(Hat.TOP, 0.1, 0.3)

                    if self.isContainTemplate('SV/moon_0.png', 0.8):
                        self.press(Hat.BTM, 0.1, 0.5)
                        self.press(Button.A, 0.1, 1)
                        print(f"{self.lap}週目 {self.battle}戦目 {self.count}匹目 シャドーボール")
                        if self.count > 5:
                            self.count = 1
                            self.battle = self.battle + 1
                            if self.battle > 4:
                                self.lap = self.lap + 1
                                self.battle = 1

                    else:
                        self.press(Button.A, 0.1, 1)
                        print(f"{self.lap}週目 {self.battle}戦目 {self.count}匹目 ムーンフォース")
                        if self.count > 5:
                            self.count = 1
                            self.battle = self.battle + 1
                            if self.battle > 4:
                                self.lap = self.lap + 1
                                self.battle = 1

                else:
                    self.press(Button.A, 0.1, 0.1)
