from Commands.Keys import Direction, Stick
from Commands.PythonCommandBase import PythonCommand


class BlocklyCmd(PythonCommand):
    NAME = "サンプルスティック"

    def do(self) -> None:
        self.press(Direction(Stick.LEFT, 90, magnification=1), duration=0.5, wait=0.2)
        self.wait(0.5)
        self.press(Direction(Stick.RIGHT, 0, magnification=1), duration=0.5, wait=0.2)
