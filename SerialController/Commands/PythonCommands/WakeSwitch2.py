#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch2 を起こす。保存済みの wake ビーコンを B で再生する。

保存が無ければ起こせない。その場合は GUI の「Switch2 Wake設定」か
Pico へ直に C を送って取込んでおく。C 行の書式は Registrar 側ではなく
wakecon の取込（Switch2 をスリープ→Joy-Con の HOME）が要る。
"""

from Commands.PythonCommandBase import PythonCommand
from Commands.WakeLink import query, send_line


class WakeSwitch2(PythonCommand):
    NAME = "Switch2を起こす"

    def do(self):
        transport = self._wake_transport()
        if transport is None:
            self.print2("シリアルが開いていません。先に接続してください。")
            self.finish()
            return

        # 保存の有無だけ確かめる。無ければ B を送っても何も起きない。
        found = query(transport, "?", ("st ", "saved "), timeout=2.0)
        saved = any(line.startswith("saved ") and "none" not in line
                    for line in found)
        if not saved:
            self.print2("保存された wake がありません。")
            self.print2("GUI の「Switch2 Wake設定」で C 取込を済ませてください。")
            self.finish()
            return

        self.print2("wake を再生します（約1.5秒）。")
        self.print2("Joy-Con の電源は OFF にしておいてください。")
        if not send_line(transport, "B"):
            self.print2("送信に失敗しました。接続を確かめてください。")
            self.finish()
            return
        self.wait(2.5)
        self.print2("送りました。Switch2 が起きなければ保存の取り直しを。")
        self.finish()

    def _wake_transport(self):
        """Sender が持つ Transport。無ければ None。"""
        keys = getattr(self, "keys", None)
        sender = getattr(keys, "ser", None)
        transport = getattr(sender, "transport", None)
        if transport is None:
            return None
        ser = getattr(transport, "ser", None)
        if ser is None:
            return None
        try:
            if not ser.isOpen():
                return None
        except Exception:
            return None
        return transport
