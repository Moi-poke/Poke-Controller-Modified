import random

from Commands.PythonCommandBase import PythonCommand


class BlocklyCmd(PythonCommand):
    NAME = "出力例"

    def do(self) -> None:
        print("結果:" + str(random.randint(1, 6)))
        self.print2("おわり")
