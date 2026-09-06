#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""base.py - 通信方式の抽象（差し替え口）。

Sender は姿勢（どのボタンが押されているか）と入力ログを持ち、
実際に何で運ぶかは知らない。運び方を替えたいときは、この抽象を
満たすクラスを1つ書いて Sender へ渡すだけでよい。

なぜ分けるか:
  ・通信をテキストからバイナリへ替える、相手を Leonardo から RP2040 へ
    替える、といった変更が、姿勢や入力ログの話と混ざらない。
  ・逆に、姿勢の直しは運び方に触れずに済む。

add_listener の既定は「繋げなかった」(False):
  入力ログは送信行（文字列）を読んで組み立てる。バイナリで運ぶ実装は
    送信行を作らないので繋げない。黙って何もしないと、1行も出ないのに
    利用者は動いていると思う（静かに壊れる型）。実際に踏んだ例がある
    ので、既定を False にし、呼び出し側が理由を出せるようにしてある。

コメントには理由を書き、外部参照は付けない。
"""

from __future__ import annotations

import abc
import inspect
from collections.abc import Callable
from typing import Any

# Transport が受け取れる入力の形式。代表値はこの 1 箇所にのみ定義する。
# 新しい接続方式が出てきてもここへ定数を足す必要は無い。未知の capability
# 名は register 時に警告を出して受け入れる（Sender は legacy 等価で動く）。
# live worker が要る方式だけが LIVE_WORKER_CAPABILITIES へ名を連ねる。
LEGACY_ROW = "LEGACY_ROW"
PICO_LIVE_STATE = "PICO_LIVE_STATE"
VALID_CAPABILITIES = (LEGACY_ROW, PICO_LIVE_STATE)
# live worker（8ms スロット送出）を必要とする capability の集合。
# Sender._liveCapable が参照する唯一の表。第3の方式で worker が要る場合は
# この集合へ加える（登録時に capability 名を合わせるか、ここへ追加する）。
LIVE_WORKER_CAPABILITIES: set[str] = {PICO_LIVE_STATE}


class Transport(abc.ABC):
    """通信方式の抽象。これを満たせば Sender の下に差し込める。

    実装が必要なのは open、close、is_open、send_row、flush_pending の 5 つで
    ある。入力ログを出力する実装は add_listener で True を返す。
    """

    # プリセット名。設定画面や起動引数から選ぶときの鍵
    name = "base"
    # 既定は従来の 1 行送信。live 対応の Transport のみが上書きする。
    # 将来の方式は独自の capability 名を持てる。未知値は Sender が
    # legacy 等価として扱う（同期 send_row、worker なし）。
    capability = LEGACY_ROW

    def open(
        self,
        portNum: int,
        portName: str = "",
        baudrate: int = 9600,
        **extra: Any,
    ) -> bool:
        """線を開く。開けたら True。

        extra は将来の接続方式のための余白（VID/PID・IP 等）。
        基底と TextSerial 系は無視する。引数を足しても既存の
        呼び出し（portNum, portName, baudrate の3つ）は壊れない。
        """
        _ = extra
        return False

    def close(self) -> None:
        """線を閉じる。"""

    def is_open(self) -> bool:
        """開いているか。"""
        return False

    @abc.abstractmethod
    def send_row(self, row: str, measure_perf: bool = True) -> None:
        """1行ぶんの姿勢を送る。実装が無いと意味を成さないので抽象。"""

    def flush_pending(self) -> None:
        """間引きで保留している分があれば送り切る。"""

    def set_hooks(
        self,
        on_write_begin: Callable[..., None] | None = None,
        on_write_end: Callable[..., None] | None = None,
    ) -> None:
        """実際に書き出す前後で呼ぶ手。Sender の帳簿（計測・直前の行）用。

        listeners とは別に持つ。listeners は「送信行を読みたい人」で、
          繋がらない実装もある。こちらは Sender 自身の記録なので必ず要る。

        手は (row) でも (row, show) でも受け取れる。引数の数をここで
          一度だけ調べ、1つしか取らない手には row だけを渡す。
        なぜそうするか: Sender 側の手は show を受け取る形になったが、
          この抽象は外部の実装も差し込める口である。
          従来どおり (row) だけを取る手を繋いでいる利用者がいた
            場合、渡す数を増やすと TypeError で送信ごと落ちる。
          毎回 try で包むと、手の中で起きた本物の TypeError まで
            握りつぶすので、繋ぐ時点で1度だけ調べる形にした。
        メソッド名・引数名は変えていない。
        """
        self._on_write_begin = self._adapt_hook(on_write_begin)
        self._on_write_end = self._adapt_hook(on_write_end)

    @staticmethod
    def _adapt_hook(
        func: Callable[..., None] | None,
    ) -> Callable[..., None] | None:
        """手が受け取れる引数の数に合わせて包む。"""
        if func is None:
            return None
        try:
            n = len(inspect.signature(func).parameters)
        except (TypeError, ValueError):
            # 調べられない手（組み込み等）は、安全側の1引数として扱う
            n = 1
        if n >= 2:
            return func
        return lambda row, show=True: func(row)

    def add_listener(self, func: Callable[[str], None]) -> bool:
        """送信行を受け取る相手を足す。繋げたら True。

        既定は False。送信行（文字列）を作らない実装があるため。
          呼び出し側はこの値を見て「繋がらなかった」理由を出せる。
        """
        return False

    def remove_listener(self, func: Callable[[str], None]) -> None:
        """聞き手を外す。既定は何もしない（繋がっていないため）。"""

    def get_raw_serial(self) -> Any:
        """応答つき通信（WakeLink）が使う生のシリアル。無ければ None。

        非シリアル方式（HID/BLE/TCP 等）は None を返す。その場合
        WakeLink は例外なく False/None を返し、Sender の Q/R 確認は
        従来経路へフォールバックする。直に .ser を触らずこちらを使う。
        """
        return getattr(self, "ser", None)

    def acquire_write_lock(self) -> Any:
        """書き出しの錠。WakeLink が1行単位の噛み合い用に使う。

        無ければ何もしない文脈管理を返す。直に ._lock を触らずこちらを使う。
        """

        class _NullLock:
            def __enter__(self) -> None:
                return None

            def __exit__(self, *args: Any) -> None:
                return None

        lock = getattr(self, "_lock", None)
        if lock is None:
            return _NullLock()
        return lock
