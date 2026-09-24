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

import logging
import queue
import threading
import time
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from collections.abc import Callable
from typing import Any

from LogPane import DropOldestQueue
from core.transport.base import BCON_STATE
from core.transport.bcon import decode_rumble
from core.transport.bcon_baud import (
    BAUD_DEFAULT_INDEX,
    BAUD_DISPLAY_LABELS,
    baud_adopt_message,
    baud_bps_for_index,
    baud_index_for_label,
    baud_index_sendable,
    baud_revert_message,
)
from core.transport.bcon_protocol import (
    BAUD_TABLE,
    DEFAULT_BAUD_INDEX,
    T_BEACON_START,
    T_CAPTURE_START,
    T_COLOR_SET,
    T_KEY_DELETE,
    T_PLAYER_INFO,
    T_RUMBLE,
)

BCON_WINDOW_TITLE = "Bcon設定"

# 計測の間引き幅。debugへの書き出しはこの秒数に1件までにする。
# per-frameのI/Oにしないための枠で、数え自体は毎回行う。
COUNTERS_LOG_INTERVAL_S = 5.0

# RX→UIの洪水対策（LogPaneのflushQueueと同一規律）。
# 未処理の溜めに上限を設け、超えた分は古い方から捨てる。捨てた件数は
# 「... N行省略 ...」の1行で可視化する（黙って捨てない）。
BCON_QUEUE_MAX = 1024
# 1回の_pollで取り出す上限。洪水でも1 tickで抱え込まない。
# LogPaneのFLUSH_MAX_LINESと同値。rumble/player_infoの連打程度
# （～200件）は1 tickで畳み切れ、溢れは次tickへ残る。
BCON_FLUSH_MAX = 512
# ログ欄に残す行数。Textは行数に比例して重くなる。
BCON_MAX_LINES = 1000

_LOGGER = logging.getLogger(__name__)

# 取込秒数の受付域。Pico側は秒1-60、範囲外は ERRCODE 0x10 で拒否する。
# 範囲外は送らず注意だけ出す（送って拒否させる運用にしない）。
CAPTURE_MIN_S = 1
CAPTURE_MAX_S = 60

# COLOR_SET の12Bは RGB×4（本体・本体・左・右）の順。画面もこの順で4欄置く。
COLOR_SLOT_NAMES = ("本体", "本体2", "左", "右")
COLOR_DEFAULTS = ("000000", "000000", "000000", "000000")

# プレイヤーLEDの色（PLAYER_INFOのランプ表示）。点灯=黄・消灯=灰。
PLAYER_LAMP_ON_COLOR = "yellow"
PLAYER_LAMP_OFF_COLOR = "gray"

# STATUS flags（SSOTは Switch-bcon の spec/protocol_v3.md §5.5）。
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

# CONFIG拒否（0x10+(TYPE&0x0F)）の TYPE 名。0x38 EMULATE_MODE まで載せる。
_CONFIG_TYPE_NAMES = {
    0x30: "CAPTURE_START",
    0x31: "BEACON_START",
    0x32: "COLOR_SET",
    0x33: "KEY_DELETE",
    0x34: "WIRED_MODE",
    0x35: "STATUS_REQ",
    0x36: "BAUD_SET",
    0x37: "BOOTSEL",
    0x38: "EMULATE_MODE",
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


def read_bcon_current_bps(transport: Any) -> int | None:
    """表示用に現在baudを読む。取れなければNone（落とさない）。

    transportの _current_baud と同じ ser.baudrate を見るが、UIの
    復帰文言（新旧bps入り）を作るための表示専用である。切替の実体には
    触れない。
    """
    try:
        ser = getattr(transport, "ser", None)
        if ser is None:
            return None
        rate = getattr(ser, "baudrate", None)
        if rate is None:
            return None
        return int(rate)
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


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


# PLAYER_INFO の LED / flags は Switch-bcon の spec/protocol_v3.md に従う。
# LEDは下位4bitをそのまま LED1-4 へ（並べ替え無し）。flagsは
# bit0=IMU・bit1=振動・bit2=保存済みで、予約の上位bitは見ない。
# bit2は取込済みwakeのFlash保存ありの写し（BEACON再生可）。

# PLAYER_INFO flags bit2の保存証拠マスク。
PLAYER_FLAG_CAP_SAVED = 0x04

# 取込期限の余白秒。期限は要求秒数＋この秒数の壁時計で切る。
CAPTURE_DEADLINE_MARGIN_S = 5

# 取込期限の表示文。成功文は単一定数で一字一句固定する。
SAVE_SUCCESS_TEXT = "取込を保存しました。"
SAVE_TIMEOUT_TEXT = "取込期間が終了しましたが保存を確認できませんでした。"


def decode_player_lamp(byte: int) -> tuple[bool, bool, bool, bool]:
    """PLAYER_INFO の LED バイトを下位4bitそのまま4つの真偽へ。

    bit0=LED1, bit1=LED2, bit2=LED3, bit3=LED4。並べ替えはしない。
    窓口（_update_player_lamps）で据え置き/全消灯を決める手前の復号段。
    """
    value = int(byte) & 0x0F
    return (
        bool((value >> 0) & 1),
        bool((value >> 1) & 1),
        bool((value >> 2) & 1),
        bool((value >> 3) & 1),
    )


def decode_player_flags(byte: int) -> tuple[bool, bool]:
    """PLAYER_INFO の flags を (IMU, 振動) へ。予約の上位bitは見ない。"""
    value = int(byte) & 0xFF
    return (bool((value >> 0) & 1), bool((value >> 1) & 1))


def decode_player_save(byte: int) -> bool:
    """PLAYER_INFO の flags bit2を保存済みへ。予約bit3-7は見ない。"""
    value = int(byte) & 0xFF
    return bool(value & PLAYER_FLAG_CAP_SAVED)


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
    """CONFIG系を1発で送る。公開口を使う。

    運搬器の公開口（send_config）があれば使い、無い旧実装だけ
    内部の会話口（_send_session_frame）へ落とす。口が無い・
    失敗は False で返し、落とさない。
    """
    if transport is None:
        return False
    try:
        kind = int(type_) & 0xFF
        body = bytes(payload)
    except (TypeError, ValueError):
        return False
    public = getattr(transport, "send_config", None)
    if callable(public):
        try:
            return bool(public(kind, body))
        except Exception:
            return False
    sender = getattr(transport, "_send_session_frame", None)
    if not callable(sender):
        return False
    try:
        return bool(
            sender(
                kind,
                body,
                f"bcon-setup:0x{kind:02X}",
            )
        )
    except Exception:
        return False


def _pre_status_snapshot(transport: Any) -> dict[str, Any] | None:
    """送出前のSTATUS写しを取る。線に触れず、口が無ければNone。

    旧運搬器・偽物に last_status が無くても落とさない。
    """
    try:
        current = getattr(transport, "last_status", None)
        if not callable(current):
            return None
        snapshot = current()
    except Exception:
        return None
    if not isinstance(snapshot, dict):
        return None
    try:
        return dict(snapshot)
    except (TypeError, ValueError):
        return None


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
        self._queue: queue.Queue = DropOldestQueue(maxsize=BCON_QUEUE_MAX)
        self._queue_dropped_total = 0
        self._busy = False
        self._stop = False
        self._closed = False
        # 常時計測の素朴な数え（H1/H2/H3切り分け用・永置）。
        # int/float/perf_counterだけで、per-frameのI/Oはしない。
        self._poll_ticks = 0
        self._poll_drained_total = 0
        self._poll_last_drained = 0
        self._poll_last_ms = 0.0
        self._poll_max_ms = 0.0
        self._queue_max_qsize = 0
        self._rx_counts: dict[int, int] = {}
        self._rx_total = 0
        self._rx_start_t = 0.0
        self._counters_log_t = 0.0
        # PLAYER_INFO の購読（Task 4）。unsub と張り先を1組で持つ。
        # 張り先を覚えるのは、運搬器が替わったときだけ張り直すため。
        self._rx_unsub: Callable[[], None] | None = None
        self._rx_transport: Any = None
        # 取込期限の保存証拠とafter予約。期限は要求秒数＋余白の壁時計。
        self._capture_saved_seen = False
        self._capture_deadline_id: Any = None

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

        row_em = ttk.Frame(body)
        row_em.pack(fill=tk.X, pady=2)
        ttk.Label(row_em, text="種別").pack(side=tk.LEFT)
        self._btn_e0 = ttk.Button(
            row_em, text="E0 ProCon", command=lambda: self._on_emulate_set(0)
        )
        self._btn_e0.pack(side=tk.LEFT, padx=2)
        self._btn_e1 = ttk.Button(
            row_em, text="E1 JoyL", command=lambda: self._on_emulate_set(1)
        )
        self._btn_e1.pack(side=tk.LEFT, padx=2)
        self._btn_e2 = ttk.Button(
            row_em, text="E2 JoyR", command=lambda: self._on_emulate_set(2)
        )
        self._btn_e2.pack(side=tk.LEFT, padx=2)

        row_bootsel = ttk.Frame(body)
        row_bootsel.pack(fill=tk.X, pady=2)
        self._btn_bootsel = ttk.Button(
            row_bootsel, text="BOOTSEL再起動", command=self._on_bootsel
        )
        self._btn_bootsel.pack(side=tk.LEFT, padx=2)

        row_baud = ttk.Frame(body)
        row_baud.pack(fill=tk.X, pady=2)
        ttk.Label(row_baud, text="baud").pack(side=tk.LEFT)
        self._baud_var = tk.StringVar(value=BAUD_DISPLAY_LABELS[BAUD_DEFAULT_INDEX])
        self._baud_cb = ttk.Combobox(
            row_baud,
            width=8,
            state="readonly",
            textvariable=self._baud_var,
            values=[BAUD_DISPLAY_LABELS[i] for i in sorted(BAUD_TABLE)],
        )
        self._baud_cb.pack(side=tk.LEFT, padx=4)
        self._btn_baud = ttk.Button(row_baud, text="実行", command=self._on_baud)
        self._btn_baud.pack(side=tk.LEFT, padx=4)

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

        row_player = ttk.Frame(body)
        row_player.pack(fill=tk.X, pady=2)
        ttk.Label(row_player, text="プレイヤーLED").pack(side=tk.LEFT)
        # PLAYER_INFO のLEDはbit0=LED1..bit3=LED4。実機は縦4灯で
        # LED1が最下段・LED4が最上段。ここは左からLED1..LED4の横並びで出す。
        self._lamp_canvas = tk.Canvas(
            row_player, width=120, height=30, highlightthickness=0
        )
        self._lamp_canvas.pack(side=tk.LEFT, padx=4)
        self._lamp_items: list[int] = []
        for index in range(4):
            left = 4 + index * 30
            self._lamp_items.append(
                self._lamp_canvas.create_rectangle(
                    left,
                    4,
                    left + 22,
                    26,
                    fill=PLAYER_LAMP_OFF_COLOR,
                    outline="#4D4D4D",
                )
            )
        self._imu_label = ttk.Label(row_player, text="IMU: 未受信")
        self._imu_label.pack(side=tk.LEFT, padx=(8, 0))
        self._vib_label = ttk.Label(row_player, text="振動: 未受信")
        self._vib_label.pack(side=tk.LEFT, padx=4)

        self._log = tk.Text(body, height=16, width=72, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._poll()
        # 既に bcon で繋がっていれば、窓を開いた時点から実況を受ける。
        self._bind_player_info(getattr(self._sender, "transport", None))

    # -- 画面まわり ------------------------------------------------------
    # WakeSetup と同一規律である。作業は別スレッド、画面更新だけ after()、
    # 閉じた後の届けは積まない・残さない。共通化は動作確認後に行う。

    def close(self) -> None:
        """窓を閉じる。二重に呼ばれても安全にする。"""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._stop = True
        # 購読を先に外す。外れた後のフレームは _on_rx_frame の _closed 門で
        # 積まれず、間に合った分も下の捨てで消える。
        self._unbind_player_info()
        # 取込期限の予約を消す。閉じた後の期限表示は出さない。
        self._cancel_capture_deadline()
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
            self._btn_e0,
            self._btn_e1,
            self._btn_e2,
            self._btn_bootsel,
            self._btn_baud,
            self._btn_clear,
            self._btn_keys,
            self._btn_color,
        ):
            btn.configure(state=state)

    def _append(self, text: str) -> None:
        try:
            at_bottom = self._log.yview()[1] >= 0.999
        except Exception:
            at_bottom = True
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text + "\n")
        self._trim_log()
        if at_bottom:
            try:
                self._log.see(tk.END)
            except Exception:
                pass
        self._log.configure(state=tk.DISABLED)

    def _trim_log(self) -> None:
        log = getattr(self, "_log", None)
        if log is None:
            return
        try:
            total = int(log.index("end-1c").split(".")[0])
        except Exception:
            return
        if total > BCON_MAX_LINES:
            try:
                log.delete("1.0", f"end-{BCON_MAX_LINES}l")
            except Exception:
                pass

    def _update_player_lamps(self, lamp: Any, flags: Any = None) -> None:
        """PLAYER_INFO のランプとIMU/振動を画面へ反映する。

        引数は復号前の生値でよい（lamp・flagsの2引数、または2byteの
        payload 1つ）。b"" や1byteの短い payload・予約値は全消灯へ倒し、
        例外は投げない。mainloopの無いヘッドレス検査（__new__＋stub）
        からも同じ口で呼べる。作業スレッドから直接触らず、after() 還流の
        先で呼ぶこと。
        """
        if flags is None and isinstance(lamp, (bytes, bytearray, memoryview)):
            payload = bytes(lamp)
            if len(payload) >= 2:
                lamp, flags = payload[0], payload[1]
            else:
                lamp, flags = None, None
        try:
            lamps = decode_player_lamp(lamp)
            imu, vibration = decode_player_flags(flags)
        except (TypeError, ValueError):
            # 読めない値は全消灯にする（前回の点灯を残すと紛らわしい）。
            lamps = (False, False, False, False)
            imu = vibration = False
        items = getattr(self, "_lamp_items", None)
        canvas = getattr(self, "_lamp_canvas", None)
        if items is not None and canvas is not None:
            try:
                for item, is_on in zip(items, lamps):
                    color = PLAYER_LAMP_ON_COLOR if is_on else PLAYER_LAMP_OFF_COLOR
                    canvas.itemconfigure(item, fill=color)
            except Exception:
                # 閉じた後・stubなど、描けない時は色だけ諦める。
                pass
        for name, label, detected in (
            ("_imu_label", "IMU", imu),
            ("_vib_label", "振動", vibration),
        ):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.configure(text=f"{label}: {'あり' if detected else 'なし'}")
            except Exception:
                continue
        # 保存証拠は表示と別に覚える。BEACONでは立てない。
        self._note_player_save(flags)

    def _note_player_save(self, flags: Any) -> None:
        """PLAYER_INFO flags bit2を見たら保存済みを覚える。例外は出さない。"""
        try:
            if decode_player_save(int(flags) & 0xFF):
                self._capture_saved_seen = True
        except (TypeError, ValueError):
            pass

    def _arm_capture_deadline(self, seconds: int) -> None:
        """取込期限を張る。要求秒数＋余白の壁時計で1発だけ鳴らす。"""
        try:
            wanted = int(seconds)
        except (TypeError, ValueError):
            return
        if wanted < CAPTURE_MIN_S or wanted > CAPTURE_MAX_S:
            return
        self._cancel_capture_deadline()
        self._capture_saved_seen = False
        try:
            delay_ms = (wanted + CAPTURE_DEADLINE_MARGIN_S) * 1000
            self._capture_deadline_id = self.window.after(
                delay_ms, self._on_capture_deadline
            )
        except Exception:
            self._capture_deadline_id = None

    def _cancel_capture_deadline(self) -> None:
        """取込期限の予約を消す。二重・予約なしでも落とさない。"""
        pending = getattr(self, "_capture_deadline_id", None)
        self._capture_deadline_id = None
        if pending is None:
            return
        try:
            cancel = getattr(self.window, "after_cancel", None)
            if callable(cancel):
                cancel(pending)
        except Exception:
            pass

    def _on_capture_deadline(self) -> None:
        """取込期限が来たら保存の有無を1行出す。BEACONは見ない。

        完了後は取込用の購読だけ外し、残りカスを捨てる。完了文だけ
        残し、待ち行列を静寂へ戻す（T1数えのqsize証跡）。二重でも
        落とさず、次の運搬器へは張り直せる。他の購読者は持たないため
        壊さない。
        """
        self._capture_deadline_id = None
        if getattr(self, "_closed", False):
            # 閉じ競合でも購読だけは外す（持ち込まない）。
            self._teardown_capture()
            return
        try:
            seen = bool(getattr(self, "_capture_saved_seen", False))
        except Exception:
            seen = False
        # 先に外して残りカスを捨て、完了文だけ残す。
        self._teardown_capture()
        try:
            if seen:
                self._queue.put(("log", SAVE_SUCCESS_TEXT))
            else:
                self._queue.put(("log", SAVE_TIMEOUT_TEXT))
        except Exception:
            pass

    def _drain_stale_capture_entries(self) -> int:
        """取込の残りカスだけ捨てる。log/doneは残す。二重安全。

        player_info/rumble/capture_armは取込期限後の古い届けであり、
        次の取込へ持ち込まない。log/doneは画面の履歴のため残す。
        捨てた件数を返す（T1数えのqsize証跡の補助）。
        """
        try:
            q = getattr(self, "_queue", None)
            if q is None:
                return 0
        except Exception:
            return 0
        stale = 0
        keep: list[Any] = []
        try:
            while True:
                item = q.get_nowait()
                try:
                    kind, _ = item
                except (TypeError, ValueError):
                    stale += 1
                    continue
                if kind in ("player_info", "rumble", "capture_arm"):
                    stale += 1
                    continue
                keep.append(item)
        except queue.Empty:
            pass
        except Exception:
            pass
        for item in keep:
            try:
                q.put(item)
            except Exception:
                pass
        return stale

    def _teardown_capture(self) -> None:
        """取込完了時の後片付け。購読解除＋残りカス捨て＋期限取消。

        完了・閉鎖・持替の3口で同じ後始末を通す。二重・未購読でも
        落とさない（_unbind側で無害化）。外すのは自分の購読だけであり、
        他の購読者（live/状態確認の口）は壊さない。外した後は
        _rx_transportも消えるため、次の運搬器へは張り直せる。
        """
        try:
            self._unbind_player_info()
        except Exception:
            pass
        try:
            self._cancel_capture_deadline()
        except Exception:
            pass
        try:
            self._drain_stale_capture_entries()
        except Exception:
            pass

    def _unbind_player_info(self) -> None:
        """PLAYER_INFO の購読を外す。二重・未購読でも落とさない。

        外した後は _rx_transport も消すため、次に繋いだ先へは張り直す。
        例外は投げない（閉じ手順を止めない）。
        """
        unsub = getattr(self, "_rx_unsub", None)
        self._rx_unsub = None
        self._rx_transport = None
        if callable(unsub):
            try:
                unsub()
            except Exception:
                pass

    def _bind_player_info(self, transport: Any) -> None:
        """PLAYER_INFO の購読を張る。bcon 以外・口なしは黙って諦める。

        別スレッド（接続の作業スレッド）からも呼ばれる。重複除けと
        ポンプの起こし直しは subscribe_rx 側の責務である。別の運搬器へ
        替わっていたら古い購読を先に外す。閉じた後は張らない。
        """
        if getattr(self, "_closed", False):
            return
        subscribe = getattr(transport, "subscribe_rx", None)
        if not callable(subscribe) or not is_bcon_transport(transport):
            return
        if getattr(self, "_rx_transport", None) is not transport:
            # 持替では古い購読を先に外し、残りカスと期限も片付ける。
            # 外すのは自分の購読だけであり、他の購読者は壊さない。
            self._teardown_capture()
        try:
            unsub = subscribe(self._on_rx_frame)
        except Exception:
            return
        if getattr(self, "_closed", False):
            # 閉じ手順と競合した場合は張った端から外す（持ち込まない）。
            if callable(unsub):
                try:
                    unsub()
                except Exception:
                    pass
            return
        self._rx_transport = transport
        self._rx_unsub = unsub if callable(unsub) else None

    def _ensure_counters(self) -> None:
        # __new__素体の検査体でも数えが動くよう欠品分だけ足す。
        if getattr(self, "_rx_counts", None) is None:
            self._rx_counts = {}
        for name, fresh in (
            ("_poll_ticks", 0),
            ("_poll_drained_total", 0),
            ("_poll_last_drained", 0),
            ("_poll_last_ms", 0.0),
            ("_poll_max_ms", 0.0),
            ("_queue_max_qsize", 0),
            ("_queue_dropped_total", 0),
            ("_rx_total", 0),
            ("_rx_start_t", 0.0),
            ("_counters_log_t", 0.0),
        ):
            if not hasattr(self, name):
                setattr(self, name, fresh)

    def counters_snapshot(self) -> dict[str, Any]:
        # H1/H2/H3切り分け用の写し。呼ぶだけで線・画面に触れない。
        self._ensure_counters()
        now = time.perf_counter()
        start = float(self._rx_start_t)
        span = now - start if start > 0.0 else 0.0
        by_ftype = dict(self._rx_counts)
        per_sec = (
            {ftype: count / span for ftype, count in by_ftype.items()}
            if span > 0.0
            else dict.fromkeys(by_ftype, 0.0)
        )
        return {
            "poll_ticks": int(self._poll_ticks),
            "poll_drained_total": int(self._poll_drained_total),
            "poll_last_drained": int(self._poll_last_drained),
            "poll_last_ms": float(self._poll_last_ms),
            "poll_max_ms": float(self._poll_max_ms),
            "queue_max_qsize": int(self._queue_max_qsize),
            "queue_dropped_total": int(self._queue_dropped_total),
            "rx_total": int(self._rx_total),
            "rx_by_ftype": by_ftype,
            "rx_per_sec_by_ftype": per_sec,
        }

    def _maybe_log_counters(self) -> None:
        # debugへの書き出しは間引く（既定では出ない・出しても5秒に1件）。
        try:
            now = time.perf_counter()
            if now - float(self._counters_log_t) < COUNTERS_LOG_INTERVAL_S:
                return
            self._counters_log_t = now
            _LOGGER.debug(
                "bcon計測 tick=%d drain=%d/%d poll_ms=%.3f(max %.3f) qmax=%d rx=%s",
                int(self._poll_ticks),
                int(self._poll_last_drained),
                int(self._poll_drained_total),
                float(self._poll_last_ms),
                float(self._poll_max_ms),
                int(self._queue_max_qsize),
                dict(self._rx_counts),
            )
        except Exception:
            pass

    def _on_rx_frame(self, frame: Any) -> None:
        """RXポンプ（別スレッド）から届く1フレーム。PLAYER_INFO/RUMBLEだけ拾う。

        ここはスレッド親和の境界である。widget・after()・Tk変数には
        触れず、スレッド安全な _queue へ積むだけにする。画面反映は
        GUIスレッドの _poll が行う。閉じた後は積まない（1件漏れ防止）。
        RUMBLEは decode_rumble で (L, R) へ直して積む。短い欠片は
        (0, 0) へ倒し、例外は投げない。
        """
        try:
            type_ = int(frame[0]) & 0xFF
        except (TypeError, ValueError, IndexError):
            return
        # ftype別の数えは選別の前に行う（積まないframeも母数に入れる）。
        # worker親和のためqueueと数えだけ触り、widget・after()は触らない。
        self._ensure_counters()
        self._rx_counts[type_] = self._rx_counts.get(type_, 0) + 1
        self._rx_total += 1
        if not self._rx_start_t:
            self._rx_start_t = time.perf_counter()
        if type_ == T_RUMBLE:
            try:
                payload = bytes(frame[1])
            except (TypeError, ValueError, IndexError):
                payload = b""
            # 閉じ門（完了後始末と共有）。外れた後の到着は積まず捨てる。
            # 解除自体は購読外しで止めるが、競合の1件はここで落とす。
            if getattr(self, "_closed", False) or getattr(self, "_stop", False):
                return
            try:
                left, right = decode_rumble(payload)
                entry = (int(left) & 0xFF, int(right) & 0xFF)
            except Exception:
                entry = (0, 0)
            try:
                self._queue.put(("rumble", entry))
            except Exception:
                pass
            return
        if type_ != T_PLAYER_INFO:
            return
        try:
            payload = bytes(frame[1])
        except (TypeError, ValueError, IndexError):
            return
        # 閉じ門（完了後始末と共有）。外れた後の到着は積まず捨てる。
        if getattr(self, "_closed", False) or getattr(self, "_stop", False):
            return
        if len(payload) >= 2:
            # 保存証拠は作業スレッド側でも覚える。BEACON系は見ない。
            self._note_player_save(payload[1])
            self._queue.put(("player_info", (payload[0], payload[1])))
        else:
            # 短い欠片は全消灯へ倒す（古い点灯を残さない）。
            self._queue.put(("player_info", b""))

    def _poll(self) -> None:
        """作業スレッドからの届けを画面へ出す。

        閉じ排水は完了後始末と対になる。捨てた件数はT1数えへ残し、
        待ち行列を静寂へ戻す（qsize→idleの証跡）。
        """
        self._ensure_counters()
        tick_start = time.perf_counter()
        drained = 0
        try:
            peak = self._queue.qsize()
            if peak > self._queue_max_qsize:
                self._queue_max_qsize = peak
        except Exception:
            pass
        if getattr(self, "_stop", False) or getattr(self, "_closed", False):
            # 閉じた後の届けは捨てる。残すと待ち行列に1件漏れる。
            try:
                while True:
                    self._queue.get_nowait()
                    drained += 1
            except queue.Empty:
                pass
            except Exception:
                pass
            self._note_poll_tick(tick_start, drained)
            return
        batch: list[Any] = []
        try:
            while len(batch) < BCON_FLUSH_MAX:
                batch.append(self._queue.get_nowait())
                drained += 1
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            take_dropped = getattr(self._queue, "take_dropped", None)
            dropped = int(take_dropped()) if callable(take_dropped) else 0
        except Exception:
            dropped = 0
        if dropped:
            try:
                self._queue_dropped_total += dropped
            except Exception:
                pass
        lines: list[str] = []
        if dropped:
            lines.append(f"... {dropped}行省略 ...\n")
        has_player = False
        pending_player: Any = None
        has_rumble = False
        pending_rumble: Any = None
        busy_done = False
        pending_arm: int | None = None
        for item in batch:
            try:
                kind, text = item
            except (TypeError, ValueError):
                continue
            if kind == "log":
                lines.append(f"{text}\n")
            elif kind == "done":
                busy_done = True
                if text:
                    lines.append(f"{text}\n")
            elif kind == "player_info":
                pending_player = text
                has_player = True
            elif kind == "rumble":
                pending_rumble = text
                has_rumble = True
            elif kind == "capture_arm":
                try:
                    pending_arm = int(text)
                except (TypeError, ValueError):
                    pending_arm = None
        if has_player:
            # 復号前の (lamp, flags) か、短い欠片の b"" が来る。
            if isinstance(pending_player, tuple) and len(pending_player) == 2:
                self._update_player_lamps(pending_player[0], pending_player[1])
            else:
                self._update_player_lamps(pending_player)
        if has_rumble:
            # _on_rx_frame が積んだ (L, R)。生bytesが来ても読む。
            try:
                if isinstance(pending_rumble, tuple) and len(pending_rumble) == 2:
                    left = int(pending_rumble[0]) & 0xFF
                    right = int(pending_rumble[1]) & 0xFF
                else:
                    raw = (
                        bytes(pending_rumble)
                        if isinstance(pending_rumble, (bytes, bytearray, memoryview))
                        else b""
                    )
                    left, right = decode_rumble(raw)
                    left, right = int(left) & 0xFF, int(right) & 0xFF
            except Exception:
                left, right = 0, 0
            lines.append(f"振動 L={left} R={right}\n")
        if busy_done:
            try:
                self._set_busy(False)
            except Exception:
                pass
        if pending_arm is not None:
            # 取込期限はGUI側で張る。送出直後の壁時計起点に寄せる。
            try:
                self._arm_capture_deadline(pending_arm)
            except Exception:
                pass
        if lines:
            try:
                at_bottom = self._log.yview()[1] >= 0.999
            except Exception:
                at_bottom = True
            try:
                self._log.configure(state=tk.NORMAL)
                self._log.insert(tk.END, "".join(lines))
                self._trim_log()
                if at_bottom:
                    self._log.see(tk.END)
                self._log.configure(state=tk.DISABLED)
            except Exception:
                pass
        self._note_poll_tick(tick_start, drained)
        try:
            self.window.after(120, self._poll)
        except Exception:
            pass

    def _note_poll_tick(self, tick_start: float, drained: int) -> None:
        # 1 tickの吐き出し数と壁msを残す（H2切り分け用・GUI側のみ）。
        try:
            elapsed_ms = (time.perf_counter() - tick_start) * 1000.0
            self._poll_ticks += 1
            self._poll_drained_total += drained
            self._poll_last_drained = drained
            self._poll_last_ms = elapsed_ms
            if elapsed_ms > self._poll_max_ms:
                self._poll_max_ms = elapsed_ms
        except Exception:
            pass
        self._maybe_log_counters()

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
        # bcon ならここで PLAYER_INFO の実況も繋ぐ。接続（HELLO）でも
        # 状態確認でも、操作ボタンの入口はこの1か所を通る。
        self._bind_player_info(transport)
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

    def _config_result(self, transport: Any, what: str, pre_status: Any = None) -> None:
        """CONFIG送出の直後に STATUS を読み、errcode の意味を出す。

        errcode は直近エラーを粘着保持する（正常復帰でも0へ戻らない）。
        pre_status（送出前のSTATUS写し）があれば前後差分で今回の可否を
        判定する。差分が無ければ粘着扱いで受け付け扱いにする。有線中の
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
        # 差分判定用の事前写しを取り出す。形が壊れていたら絶対判定へ倒す。
        pre: dict[str, Any] | None = None
        if isinstance(pre_status, dict):
            try:
                pre = {
                    "errcode": int(pre_status.get("errcode", 0)) & 0xFF,
                    "err_drop": int(pre_status.get("err_drop", 0)),
                    "err_crc": int(pre_status.get("err_crc", 0)),
                    "last_seq": int(pre_status.get("last_seq", 0)),
                    "flags": int(pre_status.get("flags", 0)),
                }
            except (TypeError, ValueError, AttributeError):
                pre = None
        try:
            post_drop = int(status.get("err_drop", 0))
            post_crc = int(status.get("err_crc", 0))
        except (TypeError, ValueError, AttributeError):
            pre = None
        if pre is not None:
            # 今回の送出で輸送計数が動いたら新規の拒否として扱う。
            if post_drop != pre["err_drop"] or post_crc != pre["err_crc"]:
                pass
            # 計数は不動だがerrcodeだけ変わったら今回の層事象として扱う。
            elif errcode != pre["errcode"]:
                pass
            else:
                # errcode同一・計数不動は粘着（過去の記録）のため受け付け扱い。
                self._queue.put(
                    (
                        "log",
                        f"{what}を受け付けました。"
                        f"（注記: 直近errcode 0x{errcode:02X} は過去の記録のため今回の判定から除外しました）"
                        f"{format_status_block(status)}",
                    )
                )
                return
        note = f"{what}は拒否されました（{describe_bcon_errcode(errcode)}）。"
        if wired and errcode in (0x10, 0x11):
            note += "有線中のため拒否されます。先にW0無線へ切り替え、再起動後に再HELLOからやり直してください。"
        if what == "BEACON" and errcode == 0x11:
            note += "前提: W0無線起動・Switch未接続・取込窓内でのHOME長押しが必要です"
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
                (
                    "log",
                    f"CAPTURE_START {seconds}s を送ります。範囲外は送りません。"
                    "前提: W0無線起動・Switch未接続・取込窓内でのHOME長押しが必要です",
                )
            )
            # 送出前の写しを先に取る。線に触れない。口が無ければNone。
            pre = _pre_status_snapshot(transport)
            if not send_config_frame(
                transport, T_CAPTURE_START, bytes([seconds & 0xFF])
            ):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            self._config_result(transport, "取込", pre_status=pre)
            # 取込期限は送出起点の壁時計で切る。表示は期限側で出す。
            try:
                self._queue.put(("capture_arm", seconds))
            except Exception:
                pass

        self._run(job)

    def _on_beacon(self) -> None:
        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            self._queue.put(("log", "BEACON_START を送ります。"))
            # 送出前の写しを先に取る。線に触れない。口が無ければNone。
            pre = _pre_status_snapshot(transport)
            if not send_config_frame(transport, T_BEACON_START, b""):
                self._queue.put(("log", "送信に失敗しました。"))
                return
            self._config_result(transport, "BEACON", pre_status=pre)

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
                # 状態確認はランプ表示も最新へ引き直す（Task 4）。
                self._queue.put(("player_info", info))
            else:
                # 未受信・短い欠片は全消灯へ倒す（古い点灯を残さない）。
                self._queue.put(("log", "PLAYER_INFO: （なし）"))
                self._queue.put(("player_info", b""))
            try:
                rumble_fn = getattr(transport, "last_rumble", None)
                rumble = bytes(rumble_fn()) if callable(rumble_fn) else b""
            except Exception:
                rumble = b""
            if rumble:
                try:
                    left, right = decode_rumble(rumble)
                    left, right = int(left) & 0xFF, int(right) & 0xFF
                except Exception:
                    left, right = 0, 0
                self._queue.put(("log", f"RUMBLE: L={left} R={right}"))
            else:
                self._queue.put(("log", "振動:（なし）"))

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

    def _on_emulate_set(self, role: int) -> None:
        names = ("Pro Controller", "Joy-Con (L)", "Joy-Con (R)")
        label = names[role] if 0 <= int(role or 0) < len(names) else str(role)
        if (
            tkmsg.askquestion("確認", f"E {role}: {label} に切替えます。続けますか?")
            != "yes"
        ):
            return

        def job(role_value: int = int(role)) -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                setter = getattr(transport, "set_emulate_mode", None)
                ok = (
                    bool(setter(role_value, timeout=1.0)) if callable(setter) else False
                )
            except Exception:
                ok = False
            if not ok:
                self._queue.put(("log", "応答がありません。"))
                return
            self._queue.put(
                (
                    "log",
                    f"{names[role_value]}へ切替えました。"
                    "Flash保存・約500ms後自発再起動します。"
                    "再起動中のSTATE送出は止め、復帰後に再HELLOからやり直してください。",
                )
            )

        self._run(job)

    def _on_bootsel(self) -> None:
        if (
            tkmsg.askquestion(
                "確認",
                "BOOTSELへ入ります。再起動後はCOMが外れます。続けますか?",
                parent=self.window,
            )
            != "yes"
        ):
            return

        def job() -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            try:
                request = getattr(transport, "request_bootsel", None)
                ok = bool(request(timeout=1.0)) if callable(request) else False
            except Exception:
                ok = False
            if not ok:
                self._queue.put(
                    (
                        "log",
                        "BOOTSEL要求に応答なしでした。配線・電源を確認してください。",
                    )
                )
                return
            self._queue.put(
                (
                    "log",
                    "BOOTSELへ入りました。約500ms後に再起動します。"
                    "再起動中のSTATE送出は止め、COMが外れたら"
                    "RPI-RP2ドライブ表示・COM再出現まで待ってください。"
                    "復帰後はポートを選び直して再接続し、再HELLOからやり直してください。"
                    "再接続後は取込・BEACONが使えます。",
                )
            )

        self._run(job)

    def _on_baud(self) -> None:
        """baud切替。確認の上で作業スレッドから set_baud_index を呼ぶ。

        切替の実体（BAUD_SET送出・adopt確認・失敗時の旧レート自動復帰）は
        transport層の責務である。UIは送ってよいindexだけ送り、確定文言か
        復帰文言（transportが戻した旨＋baud_hunt案内）を出す。_tx_holdに
        は触らない。
        """
        try:
            label = self._baud_var.get()
        except (tk.TclError, ValueError, TypeError, AttributeError):
            label = None
        index = baud_index_for_label(label)
        if index is None or not baud_index_sendable(index):
            # _runを通さないため使用中にはならない。注意だけ置いて終わる。
            self._queue.put(("log", f"baud指定が不正のため送りません: {label!r}。"))
            return
        new_bps = baud_bps_for_index(index)
        if (
            tkmsg.askquestion(
                "確認",
                f"baudを{label}（{new_bps}bps）へ切替えます。続けますか?",
                parent=self.window,
            )
            != "yes"
        ):
            return

        def job(index_value: int = index, bps_value: int = new_bps) -> None:
            transport = self._require_bcon()
            if transport is None:
                return
            old_bps = read_bcon_current_bps(transport)
            try:
                setter = getattr(transport, "set_baud_index", None)
                ok = (
                    bool(setter(index_value, timeout=2.0))
                    if callable(setter)
                    else False
                )
            except Exception:
                ok = False
            if ok:
                self._queue.put(("log", baud_adopt_message(bps_value)))
                return
            if old_bps is None:
                old_bps = BAUD_TABLE[DEFAULT_BAUD_INDEX]
            self._queue.put(("log", baud_revert_message(bps_value, old_bps)))

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
            # 送出前の写しを先に取る。線に触れない。口が無ければNone。
            pre = _pre_status_snapshot(transport)
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
            self._config_result(transport, "色変更", pre_status=pre)

        self._run(job)
