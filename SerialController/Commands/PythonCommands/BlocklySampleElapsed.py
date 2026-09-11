import time

from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


class BlocklyCmd(PythonCommand):
    NAME = "時間制限例"
    TAGS = ["blockly", "サンプル"]

    def do(self) -> None:
        self._blockly_t0 = time.time()
        for count in range(100):
            self.press(Button.A, duration=0.1, wait=0.1)
            if (time.time() - self._blockly_t0) >= 3:
                self.finish()
