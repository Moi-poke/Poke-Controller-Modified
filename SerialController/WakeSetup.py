"""WakeSetup.py - Switch2 wake の初期設定とwakecon操作をする小窓。

wakecon へ C（取込）/ L（一覧）/ B（再生）/ P（疎通）/ ?（状態）/
W（有線無線の表示切替）/ D・M（表示切替）/ X（破棄）/ K（鍵削除）を送り、
応答を読む。S・N は操作系（Controller/Keyboard）で、O（色）は
コマンド一覧の「プロコンの色を変える」で扱うためここには置かない。
読み書きは作業スレッドで行い、画面の更新だけ after() で戻す。
GUI スレッドで線を待たない。

前提: wakecon が繋がっていること。live の S 行が流れていても構わない。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.messagebox as tkmsg
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
        self._btn_ping = ttk.Button(row2, text="疎通(P)", command=self._on_ping)
        self._btn_ping.pack(side=tk.LEFT, padx=2)

        row3 = ttk.Frame(body)
        row3.pack(fill=tk.X, pady=2)
        self._btn_winfo = ttk.Button(row3, text="W表示", command=self._on_wireless_info)
        self._btn_winfo.pack(side=tk.LEFT, padx=2)
        self._btn_w0 = ttk.Button(row3, text="W0無線", command=self._on_wireless_off)
        self._btn_w0.pack(side=tk.LEFT, padx=2)
        self._btn_w1 = ttk.Button(row3, text="W1有線", command=self._on_wireless_on)
        self._btn_w1.pack(side=tk.LEFT, padx=2)
        self._btn_hci = ttk.Button(row3, text="D表示切替", command=self._on_display_hci)
        self._btn_hci.pack(side=tk.LEFT, padx=2)
        self._btn_mon = ttk.Button(
            row3, text="M表示切替", command=self._on_display_monitor
        )
        self._btn_mon.pack(side=tk.LEFT, padx=2)

        row4 = ttk.Frame(body)
        row4.pack(fill=tk.X, pady=2)
        self._btn_clear = ttk.Button(row4, text="X破棄", command=self._on_clear)
        self._btn_clear.pack(side=tk.LEFT, padx=2)
        self._btn_keys = ttk.Button(row4, text="K鍵削除", command=self._on_delete_keys)
        self._btn_keys.pack(side=tk.LEFT, padx=2)
        ttk.Label(
            row4, text="色変更はコマンド一覧の「プロコンの色を変える」から (O行)"
        ).pack(side=tk.LEFT, padx=8)

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
        # 溜まった届けは捨てる。_pollは止まるため残すと1件漏れる。
        # 使用中も戻す。残したまま閉じると次に開いた窓が塞がったままになる。
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        except Exception:
            pass
        self._busy = False
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (
            self._btn_cap,
            self._btn_list,
            self._btn_status,
            self._btn_wake,
            self._btn_ping,
            self._btn_winfo,
            self._btn_w0,
            self._btn_w1,
            self._btn_hci,
            self._btn_mon,
            self._btn_clear,
            self._btn_keys,
        ):
            btn.configure(state=state)

    def _append(self, text: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _poll(self) -> None:
        """作業スレッドからの届けを画面へ出す。"""
        if getattr(self, "_stop", False) or getattr(self, "_closed", False):
            # 閉じた後の届けは捨てる。残すと待ち行列に1件漏れる。
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
            except Exception:
                pass
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
        try:
            self.window.after(120, self._poll)
        except Exception:
            pass

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
            # 閉じた後の届けは捨てる。溜めても誰も取り出さない。
            if not getattr(self, "_closed", False):
                try:
                    self._queue.put(("log", f"error: {e!r}"))
                except Exception:
                    pass
        finally:
            # 閉じた後の完了は積まない。積むと1件漏れて使用中が戻らない。
            if not getattr(self, "_closed", False):
                try:
                    self._queue.put(("done", ""))
                except Exception:
                    pass

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
            found = query(
                transport, "?", ("st ", "saved ", "color ", "usb "), timeout=3.0
            )
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

    def _on_ping(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "P", ("PONG",), timeout=2.0)
            self._queue.put(("log", "PONG: 疎通OK" if found else "応答がありません。"))

        self._run(job)

    def _on_wireless_info(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "W", ("usb ",), timeout=2.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)

    def _on_wireless_off(self) -> None:
        if tkmsg.askquestion("確認", "W 0: 無線に戻します。続けますか?") != "yes":
            return

        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "W 0", ("usb ",), timeout=3.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)

    def _on_wireless_on(self) -> None:
        if (
            tkmsg.askquestion(
                "確認", "W 1: 有線(USB直結・BT停止) に切り替えます。続けますか?"
            )
            != "yes"
        ):
            return

        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "W 1", ("usb ",), timeout=3.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)

    def _on_display_hci(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "D", ("hci verbose ",), timeout=2.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)

    def _on_display_monitor(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "M", ("monitor ",), timeout=2.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)

    def _on_clear(self) -> None:
        if tkmsg.askquestion("確認", "取込一覧と保存を破棄しますか?") != "yes":
            return

        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            # 成功時は無応答。実行中のみ "X ERR BUSY" が返る。
            found = query(transport, "X", ("X ERR",), timeout=2.0)
            if found:
                for text in found:
                    self._queue.put(("log", text))
            else:
                self._queue.put(("log", "破棄しました (応答なしは正常)。"))

        self._run(job)

    def _on_delete_keys(self) -> None:
        if (
            tkmsg.askquestion(
                "確認",
                "Pico側リンク鍵を全削除します(Switch側の登録解除も必要)。続けますか?",
            )
            != "yes"
        ):
            return

        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            found = query(transport, "K", ("link keys deleted",), timeout=3.0)
            if not found:
                self._queue.put(("log", "応答がありません。"))
            for text in found:
                self._queue.put(("log", text))

        self._run(job)
