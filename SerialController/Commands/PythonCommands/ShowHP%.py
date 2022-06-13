#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.Keys import Button
from Commands.PythonCommandBase import ImageProcPythonCommand
import tkinter.font as font
import numpy as np
import cv2


class ShowHP(ImageProcPythonCommand):
    NAME = 'Show HP Percentage'

    def __init__(self, camera, preview):
        super().__init__(cam=camera)
        self.double_battle = False
        self.preview = preview
        self.HP = 1
        self.HP_2 = 1
        self.pos_w = 1185
        self.pos_w_2 = 845
        self.pos_h = 23
        self.fontsize = int(20 * (self.preview.show_width / 1280) ** 1)
        self.font = font.Font(self.preview, family="Yu Gothic UI", size=self.fontsize)
        src = cv2.cvtColor(self.camera.readFrame(), cv2.COLOR_BGR2RGB)
        self.double_battle = self.check_double(src)

    def do(self):
        src = cv2.cvtColor(self.camera.readFrame(), cv2.COLOR_BGR2RGB)
        self.HP = self.checkHP(65, 980, src)
        self.preview.create_text(self.pos_w / 1280 * self.preview.show_width,
                                 self.pos_h / 720 * self.preview.show_height,
                                 text=f"{self.HP:.0%}", font=self.font,
                                 tag="hp", anchor="ne", fill="black")
        if self.double_battle:
            self.HP_2 = self.checkHP(65, 638, src)
            self.preview.create_text(self.pos_w_2 / 1280 * self.preview.show_width,
                                     self.pos_h / 720 * self.preview.show_height,
                                     text=f"{self.HP_2:.0%}", font=self.font,
                                     tag="hp2", anchor="ne", fill="black")
        self.wait(2)
        self.preview.delete("hp")
        if self.double_battle:
            self.preview.delete("hp2")

    def checkHP(self, height, width, src):
        hp_area = src[height:height + 4, width:width + 266]
        hp = np.sum(np.where(hp_area[1] > 190)[:][1]) / (hp_area.shape[1])
        return hp

    def check_double(self, src):
        # src = cv2.cvtColor(self.camera.readFrame(), cv2.COLOR_BGR2RGB)
        white_area = src[28:78 + 4, 910:920]
        white = np.sum(np.where(white_area[1] > 220)) / (white_area.shape[1])
        if white > 0.95:
            return True
        else:
            return False
