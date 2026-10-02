#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_panel.py - Bcon設定タブの出し入れ。

PokeControllerApp に混ぜて使う（多重継承）。Bcon設定の中身は BconSetup が
持ち、ここでは「Transport が bcon 系のときだけ『シリアル』の隣へタブを出し、
外れたら中身ごと片付ける」という Notebook 側の手順だけを受け持つ。

出し入れの基準は選択中の通信方式（transport_name）であって、線が開いて
いるかではない。bcon を選んだ時点で設定を見られるようにし、接続前に
色や有線/無線を準備できるようにするため。
"""

from __future__ import annotations

import tkinter.ttk as ttk
from typing import Any

from BconSetup import BconSetup
from loguru import logger
from services.serial_service import SerialService

BCON_TAB_TEXT = "Bcon"


class BconPanelMixin:
    """Bconタブ Mixin。単体では使わない。"""

    setting_nb: Any
    tab_serial: Any
    tab_bcon: Any
    serial: SerialService
    transport_name: Any
    _bcon_setup: BconSetup | None

    def _build_bcon_tab(self) -> None:
        """タブ枠だけ作る。Notebook へは足さない（bcon 選択時に足す）。"""
        self.tab_bcon = ttk.Frame(self.setting_nb)
        self._bcon_setup = None

    def _bcon_tab_shown(self) -> bool:
        return str(self.tab_bcon) in self.setting_nb.tabs()

    def _refresh_bcon_tab(self) -> None:
        """選択中の通信方式に合わせてタブを出し入れする。何度呼んでもよい。

        通信方式の候補を組んだ後と、画面から選び直した後に呼ぶ。
        例外は外へ出さない（タブが出ないだけで本体の操作は止めない）。
        """
        try:
            wanted = self.serial.uses_bcon(self.transport_name.get())
            if wanted:
                self._show_bcon_tab()
            else:
                self._hide_bcon_tab()
        except Exception:
            logger.exception("Bconタブの更新に失敗しました")

    def _show_bcon_tab(self) -> None:
        if self._bcon_setup is not None and self._bcon_tab_shown():
            return
        # 「シリアル」の直後。位置は都度引く（他のタブの増減に依らない）
        position = list(self.setting_nb.tabs()).index(str(self.tab_serial)) + 1
        try:
            # 作る前に Notebook へ足す。未表示の枠へ組むと寸法が定まらない
            self.setting_nb.insert(position, self.tab_bcon, text=BCON_TAB_TEXT)
            self._bcon_setup = BconSetup(
                self.tab_bcon,
                self.serial.sender,
                embedded=True,
                # 送り先は serial サービスが作り直しうる。握り込まず引き直す
                sender_provider=lambda: self.serial.sender,
            )
        except Exception:
            logger.exception("Bconタブを作れませんでした")
            self._close_bcon_tab()
            # 組みかけの部品を残さない（次の試行が同じ枠へ積み増さないように）
            for child in getattr(self.tab_bcon, "winfo_children", lambda: [])():
                try:
                    child.destroy()
                except Exception:
                    pass
            try:
                self.setting_nb.forget(self.tab_bcon)
            except Exception:
                pass

    def _hide_bcon_tab(self) -> None:
        self._close_bcon_tab()
        if self._bcon_tab_shown():
            self.setting_nb.forget(self.tab_bcon)

    def _close_bcon_tab(self) -> None:
        """中身を閉じる。購読・期限予約・poll を残さない。二重に呼んでよい。"""
        # 組み立て前の殻（テスト・起動途中の終了）でも終了処理を落とさない
        setup = getattr(self, "_bcon_setup", None)
        self._bcon_setup = None
        if setup is None:
            return
        try:
            setup.close()
        except Exception:
            logger.exception("Bconタブの後始末に失敗しました")

    def select_bcon_tab(self) -> bool:
        """タブを前面にする。出ていなければ何もせず偽（メニューの案内用）。"""
        if not self._bcon_tab_shown():
            return False
        try:
            self.setting_nb.select(self.tab_bcon)
        except Exception:
            logger.exception("Bconタブを前面にできませんでした")
            return False
        return True
