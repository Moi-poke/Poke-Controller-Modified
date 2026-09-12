from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


class BlocklyCmd(PythonCommand):
    NAME = "サンプル基本"
    TAGS = ["blockly", "Sample"]

    def do(self) -> None:
        self.press(Button.A, duration=0.1, wait=0.1)
        self.wait(1)
        for count in range(3):
            self.press(Button.B, duration=0.1, wait=0.1)
