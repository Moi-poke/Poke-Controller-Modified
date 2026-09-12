from Commands.PythonCommandBase import PythonCommand

color = None


class BlocklyCmd(PythonCommand):
    NAME = "設定入力例"
    TAGS = ["blockly", "Sample"]

    def do(self) -> None:
        color = self.dialogue6widget("色", [["combo", "項目", ["赤", "青"], "赤"]])
        if color is None:
            self.print2("取り消しました。")
            self.finish()
            color = []
        else:
            color = color[0]
        count = self.dialogue6widget(
            "数", [["spin", "個数", list(map(str, range(1, 10 + 1))), "3"]]
        )
        if count is None:
            self.print2("取り消しました。")
            self.finish()
            count = []
        else:
            count = int(count[0])
        confirm = self.dialogue6widget("確認", [["check", "送る", True]])
        if confirm is None:
            self.print2("取り消しました。")
            self.finish()
            confirm = []
        else:
            confirm = bool(confirm[0])
        print(color)
