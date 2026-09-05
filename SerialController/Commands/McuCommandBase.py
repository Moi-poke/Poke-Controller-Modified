#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from Commands import CommandBase
from loguru import logger


# MCU command
class McuCommand(CommandBase.Command):
    """MCU 側に実装されたコマンドを開始／終了させるだけの薄いラッパ。"""

    def __init__(self, sync_name: str):
        """
        Args:
            sync_name (str): MCU 側のコマンド名。Sender.writeRow() は
                受け取った文字列に改行を付けて送るだけで、名前が実在するか
                どうかは検証しない。MCU 側の定義と一致させること。
        """
        super().__init__()
        self.sync_name = sync_name
        self.postProcess: Callable[[], None] | None = None

    def start(self, ser, postProcess: Callable[[], None] | None = None):
        # ポートが開いていないと writeRow は AttributeError を握り潰すため、
        # 送れていないのに isRunning = True になり「動いているつもり」になる。
        if ser is None or not ser.isOpened():
            logger.error(f"COM port is not open. cannot start: {self.sync_name}")
            return False

        ser.writeRow(self.sync_name)
        self.isRunning = True
        self.postProcess = postProcess
        return True

    def end(self, ser):
        if ser is None or not ser.isOpened():
            # 送信できないと MCU 側は動き続ける。GUI 表示だけ止まる状態に
            # なるため、原因が追えるようログを残す。
            logger.error(
                f"COM port is not open. MCU may keep running: {self.sync_name}"
            )
        else:
            ser.writeRow("end")

        self.isRunning = False

        # 呼び出したあと None に戻さないと、再 start 時に前回のコールバックが残る
        postProcess, self.postProcess = self.postProcess, None
        if postProcess is not None:
            postProcess()
