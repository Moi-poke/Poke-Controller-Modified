#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tkinter

from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


class dialogue_sample(PythonCommand):
    NAME = 'SAMPLE'

    def __init__(self):
        super().__init__()

    def do(self):
        ret = self.dialogue("I will return LIST",
                            ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
                            need=list)
        print(ret)
        ret = self.dialogue("辞書を返すよ",
                            ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
                            need=dict)
        print(ret)
        raise Exception
