#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import ImageProcPythonCommand
from Commands.Keys import Button, Hat, Direction, Stick
import os
import datetime

class SV_Money(ImageProcPythonCommand):

    NAME = 'SV競り'

    def __init__(self, cam):
        super().__init__(cam)
        self.buy_flag = False
        self.count = 1
        # self.items = os.listdir("./Template/SV/Auction/items")

    def do(self):
        while True:
            buy_item = os.listdir("./Template/SV/Auction/Item")
            if self.isContainTemplate("SV/Auction/title_A.png", 0.8):
                self.wait(0.5)
                self.press(Button.A,0.1,0.5)
            if self.isContainTemplate("SV/Auction/mark.png", 0.8):
                self.wait(1)
                for i in buy_item:
                    if self.isContainTemplate(f"SV/Auction/Item/{i}", 0.8):
                        self.press(Button.A,0.1,2)
                        while not self.isContainTemplate("SV/Auction/b.png", 0.8):
                            self.press(Button.X,0.1,0.1)
                            self.press(Button.A,0.1,1)
                        self.press(Button.R,0.1,1)
                        self.press(Button.A,0.1,1)
                        self.press(Button.A,0.1,1)
                        while not self.isContainTemplate("SV/Auction/mark.png", 0.8):
                            self.press(Button.B,0.1,1)
                        self.buy_flag = True
                        self.time()
                        break
                    if i == buy_item[-1]:
                        if self.count > 31 or self.count == 1:
                            while not self.isContainTemplate("SV/Auction/b.png", 0.8):
                                self.press(Button.X,0.1,0.1)
                                self.press(Button.A,0.1,1)
                            self.press(Button.R,0.1,1)
                            self.press(Button.A,0.1,1)
                            self.press(Button.A,0.1,1)
                            while not self.isContainTemplate("SV/Auction/mark.png", 0.8):
                                self.press(Button.B,0.1,1)
                        self.buy_flag = False
                        # for j in range(len(self.items)):
                        #     if self.isContainTemplate(f"SV/Auction/items/{self.items[j]}",0.8):
                        #         break
                        #     elif self.items[j] == self.items[-1]:
                        #         now = datetime.datetime.now()
                        #         self.camera.saveCapture(f'./temp/{now.month}-{now.day}-{now.hour}-{now.minute}-{now.second}')
                        self.time()
            self.wait(1)

    def time(self):
        self.press(Button.HOME, 0.1, 0.5)#HOME
        self.press(Button.X,0.05,0.5)
        self.press(Button.A,0.05,0.5)
        while not self.isContainTemplate("SV/Auction/start.png", 0.8):
            self.wait(0.5)
        self.wait(1)
        if self.count > 31:
            self.count = 1
        if not self.buy_flag and self.count != 1:
            self.press(Button.A,0.05,1.5)
            self.press(Button.A,0.05,1.5)
        else:
            self.count = self.count +1
            self.press(Hat.BTM, 0.1, 0.5)
            self.press(Direction.RIGHT,duration=0.8,wait=0.5)
            self.press(Direction.LEFT,0.05,0.3)
            self.press(Button.A,0.05,0.5)#設定
            self.wait(0.1)
            self.press(Direction.DOWN,duration=1.5,wait=0.3)
            self.press(Button.A,0.05,0.5)#本体
            while not self.isContainTemplate("SV/Auction/time.png", 0.8):
                self.press(Hat.BTM, 0.1, 0.1)
            self.press(Button.A,0.05,0.5)#時間
            self.wait(0.1)
            self.press(Direction.DOWN,duration=0.5,wait=0.3)
            self.wait(0.3)
            self.press(Button.A,0.05,0.5)
            self.press(Button.A,0.05,0.1)#時間変更
            self.press(Button.A,0.05,0.1)
            self.press(Hat.TOP, 0.05, 0.1)
            self.press(Button.A,0.05,0.1)
            self.press(Button.A,0.05,0.1)
            self.press(Button.A,0.05,0.1)
            self.press(Button.A,0.05,0.5)
            self.press(Button.HOME, 0.1, 1)
            self.press(Button.A,0.05,1.5)
            self.press(Button.A,0.05,1.5)

