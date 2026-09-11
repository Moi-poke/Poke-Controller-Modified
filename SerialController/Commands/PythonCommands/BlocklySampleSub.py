from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand

msg = None


class BlocklyCmd(PythonCommand):
    NAME = "サブルーチン例"

    def do(self) -> None:
        # 戻り値を表示する
        print(self.aisatsu())
        self.aisatsu()
        self.say_twice("やっほー")
        self.print2("おわり")

    def aisatsu(self):
        self.press(Button.A, duration=0.1, wait=0.1)
        return "おはよう"

    def say_twice(self, msg) -> None:
        print(msg)
        print(msg)
