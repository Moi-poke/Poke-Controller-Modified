#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch2 を起こす。bcon の BEACON_START で保存済みビーコンを再生する。

bcon専用。未保存なら拒否されるため、先に Bcon設定の「取込開始」で
保存しておく。有線中も拒否されるため、W0無線へ切り替えて再起動後に
送り直す。送り口は運搬器の公開口だけを使い、新規 import は足さない
（利用者スクリプトの公開API面を変えない）。
"""

from Commands.PythonCommandBase import PythonCommand


class WakeSwitch2(PythonCommand):
    NAME = "Switch2を起こす"

    def do(self):
        transport = self._bcon_transport()
        if transport is None:
            self.print2("シリアルが開いていません。先に接続してください。")
            self.finish()
            return
        if getattr(transport, "name", "") != "bcon":
            self.print2("bconを選んで接続してください。")
            self.finish()
            return
        sender = getattr(transport, "send_beacon", None)
        if not callable(sender):
            self.print2("bconの運搬器にBEACON口がありません。")
            self.finish()
            return

        self.print2("wake を再生します。")
        self.print2("Joy-Con の電源は OFF にしておいてください。")
        try:
            status = sender(timeout=2.0)
        except Exception:
            status = None
        if status is None:
            self.print2("応答がありません。接続とファームを確かめてください。")
            self.finish()
            return
        try:
            errcode = int(status.get("errcode", 0)) & 0xFF
            # STATUS flags の bit5=有線。SSOTは Switch-bcon の spec。
            wired = bool(int(status.get("flags", 0)) & 0x20)
        except (TypeError, ValueError, AttributeError):
            self.print2("応答が読めません。接続を確かめてください。")
            self.finish()
            return
        if errcode == 0x00:
            self.wait(2.0)
            self.print2("送りました。Switch2 が起きなければ保存の取り直しを。")
        elif errcode == 0x11:
            self.print2("保存された wake がありません。")
            self.print2("Bcon設定の「取込開始」で保存してください。")
        elif wired and errcode == 0x10:
            self.print2("有線中のため拒否されました。")
            self.print2("W0無線へ切り替え、再起動後に送り直してください。")
        else:
            self.print2(f"拒否されました(errcode=0x{errcode:02X})。")
        self.finish()

    def _bcon_transport(self):
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
            if not transport.is_open():
                return None
        except Exception:
            return None
        return transport
