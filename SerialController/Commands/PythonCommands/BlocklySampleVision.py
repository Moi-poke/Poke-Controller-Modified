from Commands.Keys import Button
from Commands.PythonCommandBase import ImageProcPythonCommand


class BlocklyCmd(ImageProcPythonCommand):
    NAME = "画像認識例"
    TAGS = ["blockly", "サンプル"]

    def __init__(self, cam, gui=None):
        super().__init__(cam, gui)

    def do(self) -> None:
        self.waitTemplate(
            "controller_check/btn_a.png", timeout=10, threshold=0.7, use_gray=False
        )
        self.press_until(
            "controller_check/btn_a.png",
            Button.A,
            timeout=10,
            threshold=0.7,
            use_gray=False,
        )
        self.wait_count(
            "controller_check/btn_a.png", 2, timeout=10, threshold=0.7, use_gray=False
        )
        if (
            self.countTemplate(
                "controller_check/btn_a.png", threshold=0.7, use_gray=False
            )
            >= 1
        ):
            self.press(Button.B, duration=0.1, wait=0.1)
