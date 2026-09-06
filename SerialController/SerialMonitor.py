"""SerialMonitor.py - 送受信の時系列モニタ窓。

通常時に Pico→PC 方向 (Rx) を見る手段が無かった。`WD`・`ERR`・
`mon ...` といった非同期の行は、読む瞬間に当たらなければ受信
バッファに残り、次の `drain` で捨てられていた。

読みスレッドは Transport のポンプ1本だけにし、ここでは購読する
だけにする。2か所で read すると応答の横取りが起きる (WakeLink の
応答待ちと食い合う) ため、読み口は増やさない。Tx は既存の
`add_listener` 系、Rx は `subscribe_rx` で受け、到着順に混在表示する。

スレッド規律は WakeSetup と同じ。購読の呼び出しは別スレッドから
来るので、時刻付け・キュー積み・ファイル記録だけ行い、描画は
`after()` ポンプ (GUI スレッド) が行う。widget には触らない。

量の対策:
  ・窓内キューは有界 (`DropOldestQueue`)。溢れたら古い方を捨て、
    件数だけ知らせる。live の Tx は最大125Hz相当で来る。
  ・D/M の大量行は既定フィルタで畳む (件数は状態欄へ)。
    畳んだ行も含め全行をファイル `logger` へ残すので、窓を閉じても
    記録は `log/` に残る。
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
import tkinter.ttk as ttk
from typing import Any

from LogPane import DropOldestQueue
from loguru import logger

FILTER_NORMAL = "通常のみ"
FILTER_ALL = "全部"
FILTER_MON = "mon のみ"
FILTER_ALERT = "エラー・状態のみ"
FILTERS = (FILTER_NORMAL, FILTER_ALL, FILTER_MON, FILTER_ALERT)

# 窓内キューの上限。live Tx の速さでも数秒分は保つ。
QUEUE_MAX = 2000
# 1回の描画で取り出す上限。超えた分は次回へ回す (捨てない)。
FLUSH_MAX = 512


def _now() -> str:
    """時刻 stamp (HH:MM:SS.mmm)。購読スレッドから呼べる純粋関数。"""
    now = time.time()
    frac = int((now - int(now)) * 1000)
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{frac:03d}"


def _visible(kind: str, text: str, mode: str) -> bool:
    """表示フィルタ。kind は "TX" か "RX"。"""
    _ = kind
    if mode == FILTER_ALL:
        return True
    if mode == FILTER_MON:
        return text.startswith("mon ")
    noisy = text.startswith("mon ") or "hci " in text
    if mode == FILTER_NORMAL:
        return not noisy
    # FILTER_ALERT: 異常と状態だけを残す。
    return (
        text.startswith("WD")
        or "ERR" in text
        or text.startswith("st ")
        or text.startswith("usb ")
    )


class SerialMonitor:
    """送受信モニタの小窓。1つだけ開く前提で使う。"""

    def __init__(self, master: Any, sender: Any) -> None:
        self._sender = sender
        self._queue: queue.Queue = DropOldestQueue(maxsize=QUEUE_MAX)
        self._stop = False
        self._closed = False
        self._paused = False
        self._bound: Any = None
        self._tx_unsub: Any = None
        self._rx_unsub: Any = None
        self._shown = 0
        self._hidden = 0
        self._dropped = 0
        self._noted_missing = False

        self.window = tk.Toplevel(master)
        self.window.title("シリアルモニタ")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(self.window, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(body)
        top.pack(fill=tk.X, pady=(0, 4))
        self._btn_pause = ttk.Button(top, text="停止", command=self._on_pause)
        self._btn_pause.pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text="表示").pack(side=tk.LEFT, padx=(8, 2))
        self._filter = tk.StringVar(value=FILTER_NORMAL)
        self._filter_cb = ttk.Combobox(top, textvariable=self._filter, width=14)
        self._filter_cb.config(state="readonly", values=list(FILTERS))
        self._filter_cb.pack(side=tk.LEFT, padx=2)
        self._btn_clear = ttk.Button(top, text="クリア", command=self._on_clear)
        self._btn_clear.pack(side=tk.LEFT, padx=2)
        self._btn_copy = ttk.Button(top, text="コピー", command=self._on_copy)
        self._btn_copy.pack(side=tk.LEFT, padx=2)
        self._status = tk.StringVar(value="")
        ttk.Label(top, textvariable=self._status).pack(side=tk.LEFT, padx=8)

        self._log = tk.Text(body, height=20, width=90, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=True)

        self._ensure_subscribed()
        self._poll()

    # -- 画面まわり ------------------------------------------------------

    def close(self) -> None:
        """窓を閉じる。二重に呼ばれても安全にする。"""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._stop = True
        self._unsubscribe()
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass

    def _on_pause(self) -> None:
        """表示の一時停止・再開。記録 (ファイル) は止めない。"""
        self._paused = not self._paused
        self._btn_pause.configure(text="再開" if self._paused else "停止")

    def _on_clear(self) -> None:
        """表示と計数を捨てる。ファイルの記録は残る。"""
        self._shown = 0
        self._hidden = 0
        self._dropped = 0
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        if isinstance(self._queue, DropOldestQueue):
            self._queue.take_dropped()
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)
        self._update_status()

    def _on_copy(self) -> None:
        """見えている内容をクリップボードへ写す。"""
        try:
            text = self._log.get("1.0", "end-1c")
        except tk.TclError:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
        except tk.TclError:
            pass

    def _update_status(self) -> None:
        self._status.set(
            f"表示 {self._shown}行 / フィルタ省略 {self._hidden}行"
            f" / キュー破棄 {self._dropped}行"
            + (" (一時停止中)" if self._paused else "")
        )

    def _poll(self) -> None:
        """購読からの届けを画面へ出す (GUI スレッド)。"""
        if self._stop:
            return
        self._ensure_subscribed()
        if self._bound is None and not self._noted_missing:
            self._noted_missing = True
            self._queue.put(
                (_now(), "RX", "(注記: シリアルが開いていません。先に接続してください)")
            )
        mode = self._filter.get()
        if mode not in FILTERS:
            mode = FILTER_NORMAL
        taken: list[tuple[str, str, str]] = []
        while len(taken) < FLUSH_MAX:
            try:
                taken.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if isinstance(self._queue, DropOldestQueue):
            self._dropped += self._queue.take_dropped()
        if self._paused:
            # 止めている間も捨てる。記録はファイル側に残っている。
            self._dropped += len(taken)
        elif taken:
            lines: list[str] = []
            for stamp, kind, text in taken:
                if _visible(kind, text, mode):
                    mark = "TX>" if kind == "TX" else "RX<"
                    lines.append(f"[{stamp}] {mark} {text}\n")
                    self._shown += 1
                else:
                    self._hidden += 1
            if lines:
                self._log.configure(state=tk.NORMAL)
                self._log.insert(tk.END, "".join(lines))
                # 行数に上限を設ける。Text は行数に比例して重くなる。
                total = int(self._log.index("end-1c").split(".")[0])
                if total > 5000:
                    self._log.delete("1.0", f"end-{5000}l")
                self._log.see(tk.END)
                self._log.configure(state=tk.DISABLED)
        self._update_status()
        self.window.after(120, self._poll)

    # -- 購読 ------------------------------------------------------------

    def _transport(self) -> Any | None:
        """いまの Transport。差し替えに追従するため都度参照する。"""
        sender = self._sender
        if sender is None:
            return None
        return getattr(sender, "transport", None)

    def _ensure_subscribed(self) -> None:
        """購読の結び直し。通信方式の差し替えに追従する。

        開いていなくても購読だけは張る。ポンプは `open` で起動する
        ので、繋いだ瞬間から行が届く。購読先が変わったときだけ結び直す。
        """
        transport = self._transport()
        if transport is self._bound:
            return
        self._unsubscribe()
        self._bound = transport
        if transport is None:
            return
        rx_sub = getattr(transport, "subscribe_rx", None)
        if callable(rx_sub):
            try:
                self._rx_unsub = rx_sub(self._on_rx)
            except Exception:
                self._rx_unsub = None
        tx_add = getattr(transport, "add_listener", None)
        linked = False
        if callable(tx_add):
            try:
                linked = bool(tx_add(self._on_tx))
            except Exception:
                linked = False
        if linked:
            self._tx_unsub = self._on_tx
        else:
            # 送信行を作らない方式。Rx だけ見せる旨を1行出す。
            self._rx_note_only()

    def _rx_note_only(self) -> None:
        self._queue.put((_now(), "RX", "(注記: この方式では送信行を出せません)"))

    def _unsubscribe(self) -> None:
        bound = self._bound
        if bound is None:
            self._tx_unsub = None
            self._rx_unsub = None
            return
        if self._tx_unsub is not None:
            remove = getattr(bound, "remove_listener", None)
            if callable(remove):
                try:
                    remove(self._tx_unsub)
                except Exception:
                    pass
            self._tx_unsub = None
        if self._rx_unsub is not None:
            try:
                self._rx_unsub()
            except Exception:
                pass
            self._rx_unsub = None
        self._bound = None

    # -- 購読の受け口 (別スレッドから来る。widget に触らない) -------------

    def _on_tx(self, row: str) -> None:
        """送信行の受け口。Tx は最大125Hz相当で来る。"""
        logger.debug(f"MON TX> {row}")
        self._queue.put((_now(), "TX", str(row)))

    def _on_rx(self, text: str) -> None:
        """受信行の受け口。畳む行も含めファイルへ残す。"""
        logger.info(f"MON RX< {text}")
        self._queue.put((_now(), "RX", str(text)))
