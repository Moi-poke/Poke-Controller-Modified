from Commands.Keys import Button
from Commands.PythonCommandBase import ImageProcAudioPythonCommand


class BlocklyCmd(ImageProcAudioPythonCommand):
    NAME = "画像音声例"
    TAGS = ["blockly", "サンプル"]

    def __init__(self, cam, gui=None, audio=None):
        super().__init__(cam, gui, audio)

    def do(self) -> None:
        self.waitTemplate(
            "controller_check/btn_a.png", timeout=10, threshold=0.7, use_gray=False
        )
        self.waitTone([(200, 8000)], [100], timeout=10)
        self.press(Button.A, duration=0.1, wait=0.1)
        print("完了")
