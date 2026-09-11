from Commands.PythonCommandBase import AudioPythonCommand


class BlocklyCmd(AudioPythonCommand):
    NAME = "音検知例"

    def __init__(self, audio=None):
        super().__init__(audio)

    def do(self) -> None:
        # Audio欄の入力が必要です
        self.waitTone([(3000, 3200), (4150, 4400)], [1000000, 2000000], timeout=30)
        if self.isTonePresent([(3000, 3200)], [1000000]):
            self.print2("検知")
