#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import PythonCommand


# Mash a button A
# A連打
class Mash_A(PythonCommand):
    NAME = "テキスト出力サンプル"

    def __init__(self) -> None:
        super().__init__()

    def do(self) -> None:
        print("この文字列は上に表示されます")
        print("この文字列も上に表示されます", output_target=0)
        print("この文字列は下に表示されます", output_target=1)
        print("この文字列は表示されません", output_target=2)  # 0未満, 2以上は出力しない
