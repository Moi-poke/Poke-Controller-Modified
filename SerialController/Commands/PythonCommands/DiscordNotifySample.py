#!/usr/bin/env python3

from typing import Any

from Commands.PythonCommandBase import ImageProcPythonCommand


class DiscordNotifySample(ImageProcPythonCommand):
    NAME = "Discord通知のサンプル"

    def __init__(self, cam: Any):
        super().__init__(cam)

    def do(self) -> None:
        # テキストのみ
        self.discord_text(content="通知テストです")
        self.discord_text(content="通常は1つ目に通知されます")

        # キャプチャ画像も合わせて通知
        self.discord_image(content="可能であれば画面も含めた通知を行います。")

        # 通知先の選択
        self.discord_image(
            content="名前によって通知先を選択できます",
            name="サンプル2",  # 登録時の名前を入力
        )
        self.discord_image(
            content="indexでも選択できます",
            index=1,  # 登録順(0始まり)
        )
