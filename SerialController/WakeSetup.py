"""WakeSetup.py - Switch2 wake の初期設定をする小窓。

wakecon へ C（取込）/ L（一覧）/ B（再生）を送り、応答を読む。
読み書きは作業スレッドで行い、画面の更新だけ after() で戻す。
GUI スレッドで線を待たない。

前提: wakecon が繋がっていること。live の S 行が流れていても構わない。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.ttk as ttk
from typing import Any

from Commands.WakeLink import drain, query, read_lines, send_line


class WakeSetup:
    """Switch2 Wake設定の小窓。1つだけ開く前提で使う。"""

    def __init__(self, master: Any, sender: Any) -> None:
        self._sender = sender
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._stop = False
        self._closed = False

        self.window = tk.Toplevel(master)
        self.window.title("Switch2 Wake設定")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(self.window, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text=(
                "1. Switch2 をスリープ → 取込開始 → Joy-Con の HOME\n"
                "2. 一覧で flag=81 を確認（保存は自動）\n"
                "3. Joy-Con の電源を OFF → 起こす"
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        row1 = ttk.Frame(body)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="取込秒数").pack(side=tk.LEFT)
        self._seconds = tk.IntVar(value=15)
        ttk.Spinbox(row1, from_=5, to=60, width=4, textvariable=self._seconds).pack(
            side=tk.LEFT, padx=4
        )
        self._btn_cap = ttk.Button(row1, text="取込開始", command=self._on_capture)
        self._btn_cap.pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(body)
        row2.pack(fill=tk.X, pady=2)
        self._btn_list = ttk.Button(row2, text="一覧", command=self._on_list)
        self._btn_list.pack(side=tk.LEFT, padx=2)
        self._btn_status = ttk.Button(row2, text="状態確認", command=self._on_status)
        self._btn_status.pack(side=tk.LEFT, padx=2)
        self._btn_wake = ttk.Button(row2, text="起こす", command=self._on_wake)
        self._btn_wake.pack(side=tk.LEFT, padx=2)

        self._log = tk.Text(body, height=14, width=72, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._poll()

    # -- 画面まわり ------------------------------------------------------

    def close(self) -> None:
        """窓を閉じる。二重に呼ばれても安全にする。"""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._stop = True
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (self._btn_cap, self._btn_list, self._btn_status, self._btn_wake):
            btn.configure(state=state)

    def _append(self, text: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _poll(self) -> None:
        """作業スレッドからの届けを画面へ出す。"""
        if self._stop:
            return
        try:
            while True:
                kind, text = self._queue.get_nowait()
                if kind == "log":
                    self._append(text)
                elif kind == "done":
                    self._set_busy(False)
                    if text:
                        self._append(text)
        except queue.Empty:
            pass
        self.window.after(120, self._poll)

    def _run(self, func) -> None:
        """作業を別スレッドで走らせる。二重起動しない。"""
        if self._busy:
            return
        self._set_busy(True)
        thread = threading.Thread(target=self._guarded, args=(func,), daemon=True)
        thread.start()

    def _guarded(self, func) -> None:
        try:
            func()
        except Exception as e:  # noqa: BLE001 - 画面へ出して終わる
            self._queue.put(("log", f"error: {e!r}"))
        finally:
            self._queue.put(("done", ""))

    def _transport(self) -> Any | None:
        sender = self._sender
        transport = getattr(sender, "transport", None)
        if transport is None:
            self._queue.put(("log", "シリアルが開いていません。先に接続してください。"))
            return None
        return transport

    # -- 操作 ------------------------------------------------------------

    def _on_capture(self) -> None:
        try:
            seconds = int(self._seconds.get())
        except (tk.TclError, ValueError, TypeError):
            seconds = 15
        seconds = min(max(seconds, 5), 60)

        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            self._queue.put(
                (
                    "log",
                    f"CAP-START {seconds}s."
                    " Switch2 をスリープさせ、Joy-Con の HOME を。",
                )
            )
            drain(transport)
            if not send_line(transport, f"C {seconds}"):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            for text in read_lines(transport, float(seconds) + 4.0):
                if text.startswith("CAP"):
                    self._queue.put(("log", text))
            self._queue.put(("log", "取込おわり。一覧で flag=81 を確かめてください。"))

        self._run(job)

    def _on_list(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            for text in query(transport, "L", ("list ", "saved "), timeout=3.0):
                self._queue.put(("log", text))

        self._run(job)

    def _on_status(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "?", ("st ", "saved ", "color "), timeout=3.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)

    def _on_wake(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            self._queue.put(("log", "wake 再生（約1.5秒）。Joy-Con は OFF で。"))
            if not send_line(transport, "B"):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            for text in read_lines(transport, 4.0):
                if text.startswith("BCN"):
                    self._queue.put(("log", text))

        self._run(job)
