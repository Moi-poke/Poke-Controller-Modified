"""BconSetup.py - bcon の初期設定とCONFIG操作をする小窓。

bcon へ開→HELLO→live開始の順で繋ぎ、CAPTURE_START / BEACON_START /
STATUS_REQ / PING / WIRED_MODE / KEY_DELETE / COLOR_SET を送り、
応答フレーム（STATUS / PONG / PLAYER_INFO / errcode）を読む。
S・N は操作系（Controller / Keyboard・live経路）で、RUMBLE送出は
対象外のためここには置かない。色変更（COLOR_SET）は別コマンドに
寄せずここ1か所に置く。
読み書きは作業スレッドで行い、画面の更新だけ after() で戻す。
GUI スレッドで線を待たない。

前提: シリアルが開いていること。live の STATE が流れていても構わないが、
接続ボタンは STATE を止めてから HELLO を送り、HELLO_ACK の前に
STATE を流さない（HELLOなし既定受付に甘えない）。UNSUPPORTED /
DOWNGRADED なら NEUTRAL を保ち版を表示して止める（送り続けハングに
しない）。WIRED_MODE 切替後は再起動が入るため復帰後に再HELLOから
やり直すこと。

WakeSetup.py とは並置で、共通化は動作確認後に抽出する。
凍結のため WakeSetup.py は1行も変えず、ここからも読まない。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from collections.abc import Callable
from typing import Any

from core.transport.base import BCON_STATE
from core.transport.bcon_protocol import (
    T_BEACON_START,
    T_CAPTURE_START,
    T_COLOR_SET,
    T_KEY_DELETE,
)

BCON_WINDOW_TITLE = "Bcon設定"

# 取込秒数の受付域。Pico側は秒1-60、範囲外は ERRCODE 0x10 で拒否する。
# 範囲外は送らず注意だけ出す（送って拒否させる運用にしない）。
CAPTURE_MIN_S = 1
CAPTURE_MAX_S = 60

# COLOR_SET の12Bは RGB×4（本体・本体・左・右）の順。画面もこの順で4欄置く。
COLOR_SLOT_NAMES = ("本体", "本体2", "左", "右")
COLOR_DEFAULTS = ("000000", "000000", "000000", "000000")

# STATUS flags（SSOTは pico-bcon の spec/protocol_v3.md §5.5）。
# bit0 USB mounted・bit1 Switch ready・bit2 timeout-neutral中・
# bit3 WDT recovered・bit4 UART overrun・bit5 wired mode・
# bit6 BT connected・bit7 RUMBLE見た（前回STATUS以降のSwitch振動出力）。
STATUS_FLAG_NAMES = (
    "USB接続中",
    "Switch準備OK",
    "無操作中立",
    "WDTから復帰",
    "UART overrun",
    "有線",
    "BT接続中",
    "RUMBLEあり",
)
STATUS_FLAG_WIRED = 0x20

# CONFIG拒否（0x10+(TYPE&0x0F)）の TYPE 名。0x36 BAUD_SET まで載せる。
_CONFIG_TYPE_NAMES = {
    0x30: "CAPTURE_START",
    0x31: "BEACON_START",
    0x32: "COLOR_SET",
    0x33: "KEY_DELETE",
    0x34: "WIRED_MODE",
    0x35: "STATUS_REQ",
    0x36: "BAUD_SET",
}


def parse_capture_seconds(raw: Any) -> int | None:
    """取込秒数を読む。1-60の外は None（送らず注意を出す側で使う）。"""
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    if CAPTURE_MIN_S <= seconds <= CAPTURE_MAX_S:
        return seconds
    return None


def parse_color_hex(text: Any) -> tuple[int, int, int] | None:
    """RRGGBB（先頭 # 可）を RGB 3つ組へ。読めなければ None（送らない）。"""
    try:
        cleaned = str(text).strip().lstrip("#")
    except (TypeError, ValueError, AttributeError):
        return None
    if len(cleaned) != 6:
        return None
    try:
        value = int(cleaned, 16)
    except ValueError:
        return None
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def build_color_payload(slots: Any) -> bytes:
    """RGB 4つ組から COLOR_SET の12Bを作る。4つ無ければ ValueError。"""
    try:
        items = list(slots)
    except TypeError:
        raise ValueError(f"色は4つ組で入れてください: {slots!r}") from None
    if len(items) != 4:
        raise ValueError(f"色は4つ組で入れてください: {len(items)}つ")
    out = bytearray()
    for item in items:
        try:
            red, green, blue = item
            out.extend([int(red) & 0xFF, int(green) & 0xFF, int(blue) & 0xFF])
        except (TypeError, ValueError):
            raise ValueError(f"色の組が不正です: {item!r}") from None
    return bytes(out)


def config_reject_type(errcode: Any) -> int | None:
    """CONFIG拒否（0x10-0x1F）を拒否元の TYPE へ戻す。違えば None。"""
    try:
        code = int(errcode) & 0xFF
    except (TypeError, ValueError):
        return None
    if 0x10 <= code <= 0x1F:
        return 0x30 | (code & 0x0F)
    return None


def describe_bcon_errcode(errcode: Any) -> str:
    """STATUS errcodeの日本語説明。CONFIG拒否は拒否元の TYPE 名まで解く。"""
    try:
        code = int(errcode) & 0xFF
    except (TypeError, ValueError):
        return f"不明({errcode!r})"
    table = {
        0x00: "正常",
        0x01: "LEN不正",
        0x02: "CRC不一致",
        0x03: "SEQ欠番",
        0x04: "未対応",
        0x05: "UART overrun",
        0x06: "overflow",
    }
    if code in table:
        return str(table[code])
    rejected = config_reject_type(code)
    if rejected is not None:
        name = _CONFIG_TYPE_NAMES.get(rejected, f"0x{rejected:02X}")
        return f"CONFIG拒否（{name}・0x10+(TYPE&0x0F)=0x{code:02X}）"
    return f"不明(0x{code:02X})"


def format_status_flags(flags: Any) -> str:
    """STATUS flagsを日本語の並びへ。D/M表示切替の相当情報はこれで見る。"""
    try:
        value = int(flags) & 0xFF
    except (TypeError, ValueError):
        return f"flags=不明({flags!r})"
    names = [name for bit, name in enumerate(STATUS_FLAG_NAMES) if value & (1 << bit)]
    if not names:
        return f"flags=0x{value:02X}（なし）"
    return f"flags=0x{value:02X}（{'・'.join(names)}）"


def format_status_block(status: Any) -> str:
    """STATUS_REQ の応答を1画面分にまとめる。読めなければ理由だけ出す。"""
    try:
        flags = int(status["flags"]) & 0xFF
        last_seq = int(status["last_seq"]) & 0xFF
        err_crc = int(status["err_crc"]) & 0xFFFF
        err_drop = int(status["err_drop"]) & 0xFFFF
        errcode = int(status["errcode"]) & 0xFF
    except (TypeError, ValueError, KeyError, AttributeError):
        return f"STATUSの読み取りに失敗しました: {status!r}"
    return (
        f"{format_status_flags(flags)} last_seq={last_seq} "
        f"err_crc={err_crc} err_drop={err_drop} "
        f"errcode=0x{errcode:02X}（{describe_bcon_errcode(errcode)}）"
    )


def is_bcon_transport(transport: Any) -> bool:
    """bconの運搬器か。bcon以外には CONFIG を送らないための門。"""
    if transport is None:
        return False
    try:
        if getattr(transport, "name", "") == "bcon":
            return True
        return getattr(transport, "capability", "") == BCON_STATE
    except Exception:
        return False


def send_config_frame(transport: Any, type_: int, payload: bytes) -> bool:
    """CONFIG系を1発で送る。Task 6時点では会話口を使う。

    CAPTURE / BEACON / KEY_DELETE / COLOR_SET に公開の送り口が無いため、
    Transport 内部の会話口（_send_session_frame）を借りる。口が無い・
    失敗は False で返し、落とさない。共通化時（動作確認後）に送り口へ
    寄せるまでの暫定である。
    """
    if transport is None:
        return False
    sender = getattr(transport, "_send_session_frame", None)
    if not callable(sender):
        return False
    try:
        return bool(
            sender(
                int(type_) & 0xFF,
                bytes(payload),
                f"bcon-setup:0x{int(type_) & 0xFF:02X}",
            )
        )
    except Exception:
        return False


def connect_bcon(
    transport: Any,
    log: Callable[[str], None],
    sender: Any = None,
    hello_timeout: float = 3.0,
) -> bool:
    """開→HELLO→live開始の順で繋ぐ。HELLO_ACKの前にSTATEを流さない。

    Task 5 からの引継ぎである。通常の接続（Sender.openSerial）は live
    対応で worker を自動起動するため、HELLOを通す前に S 行→STATE が
    流れる。ここでは STATE を止めてから HELLO を送り、通ってから起こす。
    UNSUPPORTED / DOWNGRADED / 不通は NEUTRAL を保ち（HELLO内で1発保つ）
    版を表示して止める。送り続けハングにしない。止めた live は起こさず
    返す。直したら接続を押し直すか、繋ぎ直すこと。例外は投げない。
    """
    try:
        if not is_bcon_transport(transport):
            log("Transportがbconではありません。bconを選んで接続してください。")
            return False
        try:
            opened = bool(transport.is_open())
        except Exception:
            log("bconの開閉状態が読めません。繋ぎ直してください。")
            return False
        if not opened:
            log("シリアルが開いていません。先にメイン画面で接続してください。")
            return False
        # まず STATE を止める。120Hz loop と Sender 側 worker の双方を
        # 落とす。片方だけだと残った方が送り続ける。
        try:
            stop_loop = getattr(transport, "stop_live_loop", None)
            if callable(stop_loop):
                stop_loop()
        except Exception:
            pass
        if sender is not None:
            try:
                stop_worker = getattr(sender, "stopLiveWorker", None)
                if callable(stop_worker):
                    stop_worker()
            except Exception:
                pass
        try:
            hello_ok = bool(transport.hello(timeout=hello_timeout))
        except Exception:
            log("HELLOで例外が出ました。配線・電源を確認してください。")
            return False
        if not hello_ok:
            log(
                "HELLOが通りませんでした。NEUTRALを保ち止めます。"
                "版・配線・baudを確認してください。"
                "liveは止めたままです。直したら接続を押し直すか、繋ぎ直してください。"
            )
            return False
        log("HELLOが通りました。liveを開始します。")
        try:
            start_loop = getattr(transport, "start_live_loop", None)
            loop_ok = bool(start_loop()) if callable(start_loop) else False
        except Exception:
            loop_ok = False
        if not loop_ok:
            # 120Hz loopが起こせなくても HELLO は通っているため即送で
            # 続ける。送れないよりはましであり、順序（HELLO先）は守れる。
            log("120Hz loopが起こせないため即送で続けます。")
        if sender is not None:
            try:
                start_worker = getattr(sender, "startLiveWorker", None)
                if callable(start_worker):
                    start_worker()
            except Exception:
                pass
        return True
    except Exception:
        try:
            log("接続手順で例外が出ました。繋ぎ直してください。")
        except Exception:
            pass
        return False


class BconSetup:
    """bcon設定の小窓。1つだけ開く前提で使う。"""

    def __init__(self, master: Any, sender: Any) -> None:
        self._sender = sender
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._stop = False
        self._closed = False

        self.window = tk.Toplevel(master)
        self.window.title(BCON_WINDOW_TITLE)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        body = ttk.Frame(self.window, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text=(
                "前提: シリアルが開いていること（開→HELLO→live開始の順を守る）。\n"
                "接続ボタンは STATE を止めて HELLO を送り、通ってから起こす。\n"
                "W1有線・W0無線は約500ms後に再起動する。復帰後は再HELLOから。"
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        row0 = ttk.Frame(body)
        row0.pack(fill=tk.X, pady=2)
        self._btn_connect = ttk.Button(
            row0, text="接続(HELLO→開始)", command=self._on_connect
        )
        self._btn_connect.pack(side=tk.LEFT, padx=2)

        row1 = ttk.Frame(body)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="取込秒数").pack(side=tk.LEFT)
        self._seconds = tk.IntVar(value=15)
        ttk.Spinbox(row1, from_=1, to=60, width=4, textvariable=self._seconds).pack(
            side=tk.LEFT, padx=4
        )
        self._btn_cap = ttk.Button(row1, text="取込開始", command=self._on_capture)
        self._btn_cap.pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(body)
        row2.pack(fill=tk.X, pady=2)
        self._btn_beacon = ttk.Button(row2, text="BEACON再生", command=self._on_beacon)
        self._btn_beacon.pack(side=tk.LEFT, padx=2)
        self._btn_status = ttk.Button(row2, text="状態確認", command=self._on_status)
        self._btn_status.pack(side=tk.LEFT, padx=2)
        self._btn_ping = ttk.Button(row2, text="疎通(PING)", command=self._on_ping)
        self._btn_ping.pack(side=tk.LEFT, padx=2)

        row3 = ttk.Frame(body)
        row3.pack(fill=tk.X, pady=2)
        self._btn_winfo = ttk.Button(row3, text="W表示", command=self._on_wired_show)
        self._btn_winfo.pack(side=tk.LEFT, padx=2)
        self._btn_w0 = ttk.Button(row3, text="W0無線", command=self._on_wired_off)
        self._btn_w0.pack(side=tk.LEFT, padx=2)
        self._btn_w1 = ttk.Button(row3, text="W1有線", command=self._on_wired_on)
        self._btn_w1.pack(side=tk.LEFT, padx=2)

        row4 = ttk.Frame(body)
        row4.pack(fill=tk.X, pady=2)
        self._btn_clear = ttk.Button(row4, text="X破棄", command=self._on_clear)
        self._btn_clear.pack(side=tk.LEFT, padx=2)
        self._btn_keys = ttk.Button(row4, text="K鍵削除", command=self._on_delete_keys)
        self._btn_keys.pack(side=tk.LEFT, padx=2)

        row5 = ttk.Frame(body)
        row5.pack(fill=tk.X, pady=2)
        ttk.Label(row5, text="色(RRGGBB)").pack(side=tk.LEFT)
        self._colors: list[Any] = []
        for index, (slot, default) in enumerate(zip(COLOR_SLOT_NAMES, COLOR_DEFAULTS)):
            ttk.Label(row5, text=slot).pack(
                side=tk.LEFT, padx=(6 if index == 0 else 2, 0)
            )
            var = tk.StringVar(value=default)
            entry = ttk.Entry(row5, width=8, textvariable=var)
            entry.pack(side=tk.LEFT, padx=2)
            self._colors.append(var)
        self._btn_color = ttk.Button(row5, text="色変更", command=self._on_color)
        self._btn_color.pack(side=tk.LEFT, padx=4)

        self._log = tk.Text(body, height=16, width=72, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._poll()

    # -- 画面まわり ------------------------------------------------------
    # WakeSetup と同一規律である。作業は別スレッド、画面更新だけ after()、
    # 閉じた後の届けは積まない・残さない。共通化は動作確認後に行う。

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
            self._btn_connect,
            self._btn_cap,
            self._btn_beacon,
            self._btn_status,
            self._btn_ping,
            self._btn_winfo,
            self._btn_w0,
            self._btn_w1,
            self._btn_clear,
            self._btn_keys,
            self._btn_color,
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

    def _require_bcon(self) -> Any | None:
        """bconの運搬器だけ返す。違う方式には CONFIG を送らない。"""
        transport = self._transport()
        if transport is None:
            return None
        if not is_bcon_transport(transport):
            self._queue.put(
                ("log", "Transportがbconではありません。bconを選んで接続してください。")
            )
            return None
        return transport

    def _config_result(self, transport: Any, what: str) -> None:
        """CONFIG送出の直後に STATUS を読み、errcode の意味を出す。

        errcode は直近エラーを保持し正常復帰で0へ戻る。送った直後の
        STATUS を読むことで今回の送出の可否を見る。有線中の
        CAPTURE / BEACON は 0x10 / 0x11 で拒否されるため、有線なら
        W0無線＋再起動の案内を添える。
        """
        try:
            request = getattr(transport, "request_status", None)
            status = request(timeout=1.0) if callable(request) else None
        except Exception:
            status = None
        if status is None:
            self._queue.put(("log", f"{what}を送りました。STATUS応答がありません。"))
            return
        try:
            errcode = int(status.get("errcode", 0)) & 0xFF
            wired = bool(int(status.get("flags", 0)) & STATUS_FLAG_WIRED)
        except (TypeError, ValueError, AttributeError):
            self._queue.put(
                ("log", f"{what}を送りました。{format_status_block(status)}")
            )
            return
        if errcode == 0x00:
            self._queue.put(
                ("log", f"{what}を受け付けました。{format_status_block(status)}")
            )
            return
        note = f"{what}は拒否されました（{describe_bcon_errcode(errcode)}）。"
        if wired and errcode in (0x10, 0x11):
            note += "有線中のため拒否されます。先にW0無線へ切り替え、再起動後に再HELLOからやり直してください。"
        note += format_status_block(status)
        self._queue.put(("log", note))

    # -- 操作 ------------------------------------------------------------

    def _on_connect(self) -> None:
        def job() -> None:
            transport = self._transport()
            if transport is None:
                return
            connect_bcon(
                transport,
                lambda text: self._queue.put(("log", text)),
                self._sender,
            )

        self._run(job)

    def _on_capture(self) -> None:
        try:
            raw = self._seconds.get()
        except (tk.TclError, ValueError, TypeError):
            raw = None
        seconds = parse_capture_seconds(raw)
        if seconds is None:
            # _runを通さないため使用中にはならない。注意だけ置いて終わる。
            self._queue.put(
                (
                    "log",
                    f"取込秒数は{CAPTURE_MIN_S}-{CAPTURE_MAX_S}秒で入れてください。送りません。",
                )
            )
            return

        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            self._queue.put(
                ("log", f"CAPTURE_START {seconds}s を送ります。範囲外は送りません。")
            )
            if not send_config_frame(
                transport, T_CAPTURE_START, bytes([seconds & 0xFF])
            ):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            self._config_result(transport, "取込")

        self._run(job)

    def _on_beacon(self) -> None:
        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            self._queue.put(("log", "BEACON_START を送ります。"))
            if not send_config_frame(transport, T_BEACON_START, b""):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            try:
                request = getattr(transport, "request_status", None)
                status = request(timeout=1.0) if callable(request) else None
            except Exception:
                status = None
            if status is None:
                self._queue.put(("log", "BEACONを送りました。STATUS応答がありません。"))
                return
            try:
                errcode = int(status.get("errcode", 0)) & 0xFF
                wired = bool(int(status.get("flags", 0)) & STATUS_FLAG_WIRED)
            except (TypeError, ValueError, AttributeError):
                self._queue.put(
                    ("log", f"BEACONを送りました。{format_status_block(status)}")
                )
                return
            if errcode == 0x00:
                self._queue.put(
                    ("log", f"BEACON再生しました。{format_status_block(status)}")
                )
            elif errcode == 0x11:
                note = (
                    "BEACONは拒否されました（未保存のため 0x11）。先に取込が必要です。"
                )
                if wired:
                    note += "有線中のため拒否されます。先にW0無線へ切り替え、再起動後に再HELLOからやり直してください。"
                note += format_status_block(status)
                self._queue.put(("log", note))
            else:
                self._queue.put(
                    (
                        "log",
                        f"BEACONは拒否されました（{describe_bcon_errcode(errcode)}）。{format_status_block(status)}",
                    )
                )

        self._run(job)

    def _on_status(self) -> None:
        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                request = getattr(transport, "request_status", None)
                status = request(timeout=1.0) if callable(request) else None
            except Exception:
                status = None
            if status is None:
                self._queue.put(("log", "応答がありません。"))
                return
            self._queue.put(("log", format_status_block(status)))
            try:
                player = getattr(transport, "last_player_info", None)
                info = bytes(player()) if callable(player) else b""
            except Exception:
                info = b""
            if info:
                self._queue.put(("log", f"PLAYER_INFO: {info.hex(' ')}"))
            else:
                self._queue.put(("log", "PLAYER_INFO: （なし）"))

        self._run(job)

    def _on_ping(self) -> None:
        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                ping = getattr(transport, "ping", None)
                rtt = ping(timeout=1.0) if callable(ping) else None
            except Exception:
                rtt = None
            if rtt is None:
                self._queue.put(("log", "応答がありません。"))
                return
            try:
                millis = float(rtt) * 1000.0
            except (TypeError, ValueError):
                self._queue.put(("log", "PONG: 疎通OK（往復の読み取りに失敗）"))
                return
            self._queue.put(
                ("log", f"PONG: 疎通OK（往復 {millis:.1f}ms・SEQエコー一致）")
            )

        self._run(job)

    def _on_wired_show(self) -> None:
        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                request = getattr(transport, "request_status", None)
                status = request(timeout=1.0) if callable(request) else None
            except Exception:
                status = None
            if status is None:
                self._queue.put(("log", "応答がありません。"))
                return
            try:
                wired = bool(int(status.get("flags", 0)) & STATUS_FLAG_WIRED)
            except (TypeError, ValueError, AttributeError):
                self._queue.put(("log", format_status_block(status)))
                return
            mode = "有線" if wired else "無線"
            self._queue.put(("log", f"現在は{mode}です。{format_status_block(status)}"))

        self._run(job)

    def _on_wired_off(self) -> None:
        if tkmsg.askquestion("確認", "W 0: 無線に戻します。続けますか?") != "yes":
            return

        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                setter = getattr(transport, "set_wired_mode", None)
                ok = bool(setter(False, timeout=1.0)) if callable(setter) else False
            except Exception:
                ok = False
            if not ok:
                self._queue.put(("log", "応答がありません。"))
                return
            self._queue.put(
                (
                    "log",
                    "無線へ切り替えました。Flash保存・約500ms後自発再起動します。"
                    "再起動中のSTATE送出は止め、復帰後に再HELLOからやり直してください。"
                    "復帰後は取込・BEACONが使えます。",
                )
            )

        self._run(job)

    def _on_wired_on(self) -> None:
        if (
            tkmsg.askquestion(
                "確認", "W 1: 有線(USB直結・BT停止) に切り替えます。続けますか?"
            )
            != "yes"
        ):
            return

        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                setter = getattr(transport, "set_wired_mode", None)
                ok = bool(setter(True, timeout=1.0)) if callable(setter) else False
            except Exception:
                ok = False
            if not ok:
                self._queue.put(("log", "応答がありません。"))
                return
            self._queue.put(
                (
                    "log",
                    "有線へ切り替えました。Flash保存・約500ms後自発再起動します。"
                    "再起動中のSTATE送出は止め、復帰後に再HELLOからやり直してください。"
                    "有線起動では無線一式を上げません。有線中の取込・BEACON要求は"
                    "0x10 / 0x11 で拒否されます。撮り直し・再生はW0無線＋再起動が必要です。",
                )
            )

        self._run(job)

    def _on_clear(self) -> None:
        if tkmsg.askquestion("確認", "BEACON相当とClassic鍵を破棄しますか?") != "yes":
            return

        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            # X破棄は BEACON無効化相当である。bcon に破棄専用フレームは無く、
            # 最も近い KEY_DELETE（Classic鍵全削除）で賄う。K鍵削除と同じ線を
            # 送るが、案内は破棄側に寄せる。
            if not send_config_frame(transport, T_KEY_DELETE, b""):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            self._queue.put(
                (
                    "log",
                    "破棄しました（BEACON無効化相当・Classic鍵全削除）。"
                    "Switch側の登録解除も必要です。",
                )
            )

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
            transport = self._require_bcon()
            if transport is None:
                return
            if not send_config_frame(transport, T_KEY_DELETE, b""):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            self._queue.put(
                (
                    "log",
                    "Classic鍵を全削除しました。"
                    "Switch側の「コントローラーとの接続をきる」（登録解除）も行ってください。",
                )
            )

        self._run(job)

    def _on_color(self) -> None:
        try:
            raws = [var.get() for var in self._colors]
        except (tk.TclError, ValueError, TypeError, AttributeError):
            raws = []
        slots: list[tuple[int, int, int] | None] = [
            parse_color_hex(raw) for raw in raws
        ]
        if len(slots) != 4 or any(slot is None for slot in slots):
            # _runを通さないため使用中にはならない。注意だけ置いて終わる。
            self._queue.put(
                (
                    "log",
                    "色はRRGGBBの6桁で4つ入れてください（本体・本体2・左・右）。送りません。",
                )
            )
            return

        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                payload = build_color_payload(slots)
            except ValueError as e:
                self._queue.put(("log", f"色の組が不正のため送りません: {e}"))
                return
            self._queue.put(("log", f"COLOR_SET {payload.hex(' ')} を送ります。"))
            if not send_config_frame(transport, T_COLOR_SET, payload):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            self._queue.put(
                (
                    "log",
                    "色変更を送りました。有線中は再列挙して読み直してください。"
                    "Switchの色キャッシュに注意（再接続後に反映されます）。"
                    "色変更はここ1か所に置いています。",
                )
            )
            self._config_result(transport, "色変更")

        self._run(job)
