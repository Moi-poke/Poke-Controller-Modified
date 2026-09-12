from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


class BlocklyCmd(PythonCommand):
    NAME = "保持連打例"
    TAGS = ["blockly", "Sample"]

    def do(self) -> None:
        self.hold(Button.A, wait=0.5)
        self.wait(1)
        self.holdEnd(Button.A)
        self.pressRep(Button.B, 3, duration=0.1, interval=0.1, wait=0.2)
        self.finish()
