from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand

count = None


class BlocklyCmd(PythonCommand):
    NAME = "変数フロー例"

    def do(self) -> None:
        count = 0
        for count2 in range(10):
            count = count + 1
            if count % 2 == 0:
                self.press(Button.B, duration=0.1, wait=0.1)
            if count >= 5:
                break
        print(count)
