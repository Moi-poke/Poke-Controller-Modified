#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import PythonCommand, ImageProcPythonCommand
from Commands.Keys import KeyPress, Button, Hat, Direction, Stick
import time


holdtime: float = 0.095;
delaytime: float = 0.3;

class battleSchoolWars(PythonCommand):
    NAME = '学校最強大会（手持ち複数可）'

    def __init__(self):
        super().__init__()

    def do(self):
        while True:
            self.press(Button.A, holdtime, delaytime);
            self.press(Button.A, holdtime, delaytime);
            self.press(Button.A, holdtime, delaytime);  
            self.press(Button.A, holdtime, delaytime);
            self.press(Button.A, holdtime, delaytime);
            self.press(Button.B, holdtime, delaytime);
            self.press(Button.B, holdtime, delaytime);