#!/usr/bin/env python3

from Commands.PythonCommandBase import PythonCommand


class Widget_sample(PythonCommand):
    NAME = "Widget-sample"

    def __init__(self):
        super().__init__()

    def do(self):
        ret = self.dialogue("dialogue(list)", ["Entry#1", "Entry#2"])
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Entry,list)",
            [["Entry", "Entry#3", "初期値A"], ["Entry", "Entry#4", "初期値B"]],
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Checkbox,list)",
            [["Check", "Check#1", False], ["Check", "Check#2", "True"]],
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Combobox,list)",
            [
                ["Combo", "Combo#1", ["aa", "bb", "cc"], "aa"],
                ["Combo", "Combo#2", ["AA", "BB", "CC"], "BB"],
            ],
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Radiobuttom,list)",
            [
                ["Radio", "Radio#1", ["1111", "2222", "3333"], "1111"],
                ["Radio", "Radio#2", ["123", "456", "789"], "789"],
            ],
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Spinbox,list)",
            [
                ["Spin", "Spin#1", ["ABC", "DEF", "GHI"], "ABC"],
                ["Spin", "Spin#2", ["1", "2", "3"], "2"],
            ],
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Scale,list)",
            [["Scale", "Scale#1", 0, 10, 5, 0], ["Scale", "Spin#2", 0, 100, 50, 2]],
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Mixed,list)",
            [
                ["Combo", "Combo#3", ["Aa", "Bb", "Cc"], "Aa"],
                ["Radio", "Radio#3", ["1000", "2000", "3000"], "1000"],
            ],
        )
        print(ret)

        ret = self.dialogue("dialogue(dict)", ["Entry#1", "Entry#2"], need=dict)
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Entry,dict)",
            [["Entry", "Entry#3", "初期値A"], ["Entry", "Entry#4", "初期値B"]],
            need=dict,
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Checkbox,dict)",
            [["Check", "Check#1", False], ["Check", "Check#2", "True"]],
            need=dict,
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Combobox,dict)",
            [
                ["Combo", "Combo#1", ["aa", "bb", "cc"], "aa"],
                ["Combo", "Combo#2", ["AA", "BB", "CC"], "BB"],
            ],
            need=dict,
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Radiobuttom,dict)",
            [
                ["Radio", "Radio#1", ["1111", "2222", "3333"], "1111"],
                ["Radio", "Radio#2", ["123", "456", "789"], "789"],
            ],
            need=dict,
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Spinbox,dict)",
            [
                ["Spin", "Spin#1", ["ABC", "DEF", "GHI"], "ABC"],
                ["Spin", "Spin#2", ["1", "2", "3"], "2"],
            ],
            need=dict,
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Scale,dict)",
            [["Scale", "Scale#1", 0, 10, 5, 0], ["Scale", "Spin#2", 0, 100, 50, 2]],
            need=dict,
        )
        print(ret)

        ret = self.dialogue6widget(
            "dialogue6widget(Mixed,dict)",
            [
                ["Combo", "Combo#3", ["Aa", "Bb", "Cc"], "Aa"],
                ["Radio", "Radio#3", ["1000", "2000", "3000"], "1000"],
            ],
            need=dict,
        )
        print(ret)

        self.finish()
