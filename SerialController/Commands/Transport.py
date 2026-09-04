#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transport.py - 送信の下回り（線そのもの）を受け持つ層。

Sender から「線を開く・閉じる・1行を書き出す」処理をここへ移している。
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

  送信の中身は変えない。ここにある処理は Sender の
    writeRow / _is_coalescable / _write / openSerial / closeSerial から
    そのまま移したもので、間引きの条件も例外の扱いも同じ。

コメントには理由を書き、外部参照は付けない。
"""
from __future__ import annotations

import abc
import inspect
import os
import platform
import threading
import time
import traceback
from logging import getLogger, DEBUG, NullHandler
from typing import Any, Callable, Dict, List, Optional, Tuple

import serial

# 送信の最小間隔(秒)。この間隔より短く呼ばれた分は最新値へまとめて1回にする。
# 9600bps 時代の固定値 0.02 をそのまま使うと、通信速度を上げても間引きが
# 頭を押さえてしまう。38400 では1行(22バイト)の伝送が約5.7ms なのに対し、
# 0.02 は50行/秒＝20msに1本しか通さない。速度を3倍にした効果が出ない。
#
# かといって短くしすぎると、今度は送信が伝送速度を追い越す。この線には
# フロー制御が無く、相手の空き状況を見ずに送るため、溢れた分は取りこぼす。
# そこで下限を「1行の伝送時間の2倍」とし、上限は従来の 0.02 に据え置く。
MIN_SEND_INTERVAL = 0.02          # 上限（従来値）。接続前や算出不能時はこれ
SEND_ROW_BYTES = 22               # "0x0003 8 80 80 80 80" + CRLF の最大長
BITS_PER_BYTE = 10                # 8N1（スタート1＋データ8＋ストップ1）
SEND_INTERVAL_MARGIN = 2.0        # 伝送時間の何倍を下限にするか
# write が無期限にブロックし、GUI ごと固まるのを防ぐ
READ_TIMEOUT = 0.5
WRITE_TIMEOUT = 0.5


# Transport が受け取れる入力の形式。許可値はこの 1 箇所にのみ定義する。
LEGACY_ROW = "LEGACY_ROW"
PICO_LIVE_STATE = "PICO_LIVE_STATE"
VALID_CAPABILITIES = (LEGACY_ROW, PICO_LIVE_STATE)


class Transport(abc.ABC):
    """通信方式の抽象。これを満たせば Sender の下に差し込める。

    実装が必要なのは open、close、is_open、send_row、flush_pending の 5 つで
    ある。入力ログを出力する実装は add_listener で True を返す。
    """

    # プリセット名。設定画面や起動引数から選ぶときの鍵
    name = "base"
    # 既定は従来の 1 行送信。live 対応の Transport のみが上書きする。
    capability = LEGACY_ROW

    def open(self, portNum: int, portName: str = '',
             baudrate: int = 9600) -> bool:
        """線を開く。開けたら True。"""
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

    def set_hooks(self, on_write_begin: Optional[Callable[..., None]] = None,
                  on_write_end: Optional[Callable[..., None]] = None) -> None:
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
    def _adapt_hook(func: Optional[Callable[..., None]]):
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


class TextSerialTransport(Transport):
    """P1 legacy_text - 現行と同じテキスト行を pyserial で送る実装。

    本家のマイコン（Leonardo）へそのまま繋がる。既定として
      これだけを用意し、挙動を変えない。
    """

    name = "legacy_text"

    def __init__(self, logger: Any = None) -> None:
        self.ser = None
        self._logger = logger if logger is not None else getLogger(__name__)
        if logger is None:
            self._logger.addHandler(NullHandler())
            self._logger.setLevel(DEBUG)
            self._logger.propagate = True
        # 送信の入口は4経路あり、うち3つは別のスレッドから来る（GUI /
        # コマンド / キーボード）。守らないと間引き判定が誤る・保留行が
        # 失われる。RLock にするのは close が flush_pending を呼び、
        # 同じスレッドで錠を取り直すため（Lock だとそこで自分自身を待つ）。
        self._lock = threading.RLock()
        self._last_write = 0.0
        self._pending = None
        self._before = None
        self._send_interval = MIN_SEND_INTERVAL
        self._on_write_begin = None
        self._on_write_end = None
        self.listeners: List[Callable[[str], None]] = []
        self._listener_ng = set()   # 一度落ちた聞き手。同じ苦情を繰り返さない

    # -- 聞き手の付け外し ---------------------------------------------------

    def add_listener(self, func: Callable[[str], None]) -> bool:
        """送信行を受け取る相手を足す。入力ログはここへ繋ぐ。

        重ねて足さない。同じ相手を2回足すと同じ行が2回届く。
        """
        with self._lock:
            if func not in self.listeners:
                self.listeners.append(func)
        return True

    def remove_listener(self, func: Callable[[str], None]) -> None:
        """聞き手を外す。外したら苦情の記録も消す（付け直せる）。"""
        with self._lock:
            if func in self.listeners:
                self.listeners.remove(func)
            self._listener_ng.discard(id(func))

    # -- 線の開け閉め -------------------------------------------------------

    @staticmethod
    def calc_send_interval(baudrate: int) -> float:
        """通信速度から送信の最小間隔を決める。

        速い線ほど間引きを緩めてよいが、いくらでも短くしてよいわけでは
        ない。この線には相手の空き状況を見る仕組みが無いので、1行を送り
        終える前に次を積むと送信バッファが詰まり、操作が遅れて効くように
        なる。そこで1行の伝送時間に余裕を掛けたものを下限とし、従来値
        0.02 を上限に置く。

        9600 では算出値が約 0.046 秒となり上限側の 0.02 が採られるため、
        従来と同じ挙動になる。38400 では約 0.0115 秒となり間引きが緩む。
        """
        try:
            baudrate = int(baudrate)
        except (TypeError, ValueError):
            return MIN_SEND_INTERVAL
        if baudrate <= 0:
            return MIN_SEND_INTERVAL
        row_sec = SEND_ROW_BYTES * BITS_PER_BYTE / baudrate
        return min(MIN_SEND_INTERVAL, row_sec * SEND_INTERVAL_MARGIN)

    @property
    def send_interval(self) -> float:
        """いま使っている間引き幅（検算・表示用）。"""
        return self._send_interval

    def open(self, portNum: int, portName: str = '',
             baudrate: int = 9600) -> bool:
        # baudrate は StringVar 由来の str が渡ることがあるため int に正規化
        baudrate = int(baudrate)
        # 間引き幅は速度で決まる。ポートを開く前に決めておけば、
        # 開けなかった場合も次の接続まで前回の値が残らない。
        self._send_interval = self.calc_send_interval(baudrate)

        try:
            if portName is None or portName == '':
                if os.name == 'nt':
                    print('connecting to ' + "COM" + str(portNum) + "(" + str(baudrate) + ")")
                    self._logger.info('connecting to ' + "COM" + str(portNum) + "(" + str(baudrate) + ")")
                    self.ser = serial.Serial(
                        "COM" + str(portNum), baudrate,
                        timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT,
                    )
                    return True
                elif os.name == 'posix':
                    if platform.system() == 'Darwin':
                        print('connecting to ' + "/dev/tty.usbserial-" + str(portNum) + "(" + str(baudrate) + ")")
                        self._logger.info('connecting to ' + "/dev/tty.usbserial-" + str(portNum) + "(" + str(baudrate) + ")")
                        self.ser = serial.Serial(
                            "/dev/tty.usbserial-" + str(portNum), baudrate,
                            timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT,
                        )
                        return True
                    else:
                        print('connecting to ' + "/dev/ttyUSB" + str(portNum) + "(" + str(baudrate) + ")")
                        self._logger.info('connecting to ' + "/dev/ttyUSB" + str(portNum) + "(" + str(baudrate) + ")")
                        self.ser = serial.Serial(
                            "/dev/ttyUSB" + str(portNum), baudrate,
                            timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT,
                        )
                        return True
                else:
                    print('Not supported OS')
                    self._logger.warning('Not supported OS')
                    return False
            else:
                print('connecting to ' + portName)
                self._logger.info('connecting to ' + portName)
                self.ser = serial.Serial(
                    portName, baudrate,
                    timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT,
                )
                return True
        except IOError as e:
            print('COM Port: can\'t be established')
            self._logger.error(f"COM Port: can't be established: {e}")
            return False

    def close(self) -> None:
        # 切断の一連を1つの区切りとして守る。閉じている最中に別スレッドが
        # send_row を呼ぶと、閉じたポートへ書き込むことになる。
        with self._lock:
            # 間引きで保留したままの行があれば先に送る（中途半端な状態で
            # 切断すると、その姿勢のまま残ってしまう）
            self.flush_pending()
            if self.ser is None:
                return
            self.ser.close()

    def is_open(self) -> bool:
        """回線が開いているか。新しい pySerial の is_open 属性を優先する。"""
        if self.ser is None:
            return False
        prop = getattr(self.ser, "is_open", None)
        if isinstance(prop, bool):
            return prop
        legacy = getattr(self.ser, "isOpen", None)
        if callable(legacy):
            try:
                return bool(legacy())
            except Exception:
                return False
        return False

    # -- 送信 ---------------------------------------------------------------

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        """テキスト1行をシリアルへ送る。

        9600bps では1文字あたり約1ms、'3 8 80 80 80 80\\r\\n' の18文字で
        約19ms かかる。スティック操作のように高頻度で呼ばれると送信
        バッファに積み上がり、操作が数百ms遅れて効く「効きが悪い」
        状態になる。そこで最小送信間隔を設け、間隔内に来た行は送らずに
        保留し、後から来た行で上書きする (coalescing)。スティックは
        「最後の値」だけ届けば十分なので、途中の行を捨ててもよい。

        ただし取りこぼしてはいけない行がある。ボタンの押下・解放は
        捨てると押しっぱなしになるため、ボタン/Hat が変化した行は
        間隔を無視して必ず送る（判定は _coalescable が行う）。
        """
        with self._lock:
            now = time.perf_counter()
            if self._coalescable(row) and now - self._last_write < self._send_interval:
                # まだ送らない。保留しておき、次の送信機会か flush で出す
                self._pending = row
                return

            # 保留中の行があれば先に送る。送信行は「変化したスティックだけ」を
            # 含む可変長形式なので、捨てると倒した姿勢がどこにも届かなくなる。
            pending, self._pending = self._pending, None
            if pending is not None:
                self._write(pending, measure_perf=False)

            self._write(row, measure_perf)

    def _coalescable(self, row: str) -> bool:
        """間引いてよい行か（＝スティックだけが変わった行か）を判定する。

        送信行の形式は "btn hat [lx ly] [rx ry]"。先頭2トークン
        （ボタンのビット列と Hat）が前回と同じなら、変化したのは
        スティックだけなので途中を捨ててよい。
        """
        prev = self._before
        if prev is None:
            return False
        try:
            return row.split(' ')[:2] == prev.split(' ')[:2]
        except AttributeError:
            return False

    def flush_pending(self) -> None:
        """間引きで保留した行があれば送る。

        「最後に少しだけ倒した」状態が送られずに残ると、操作が中途半端
        なまま止まる。GUI が離した時や切断時など、区切りで呼ぶ。
        """
        with self._lock:
            row, self._pending = self._pending, None
            if row is not None:
                self._write(row, measure_perf=True)

    def _notify(self, row: str) -> None:
        """聞き手へ配る。聞き手が落ちても送信は止めない。

        ログのために Switch の操作が止まってはいけない。
        ただし黙って消さない。同じ相手の苦情は1度だけ出す。
        """
        for func in list(self.listeners):
            try:
                func(row)
            except Exception as e:
                key = id(func)
                if key not in self._listener_ng:
                    self._listener_ng.add(key)
                    print("  注記: 送信行の受け取りで例外が出ました"
                          + "（以後は黙ります）: " + repr(e))

    def _write(self, row: str, measure_perf: bool = True) -> None:
        """実際にシリアルへ書き出す。

        フックの呼び出しから measure_perf の条件を外し、在れば必ず呼ぶ形にした。
        理由: 間引きで保留された行（send_row が measure_perf=False で
          送る分）こそ最も遅れるのに、そこだけ計測されていなかった。
          このまま応答遅延を測ると、遅い行が統計から丸ごと抜けて
            「速い」という誤った結果が出る。
        measure_perf 引数は残す。外部（Sender.writeRow /
          writeRow_wo_perf_counter）が渡しており、消すと壊れる。
          意味は「画面へ表示してよいか」へ寄せ、フックへ渡す。
          何を測るかと、何を見せるかは別の話なので分ける。
        """
        ok = False
        try:
            if self._on_write_begin is not None:
                self._on_write_begin(row, measure_perf)

            # 送る文字列は ASCII 固定なので utf-8 経由より安く作れる
            self.ser.write(row.encode('ascii') + b'\r\n')
            self._last_write = time.perf_counter()
            ok = True

            if self._on_write_end is not None:
                self._on_write_end(row, measure_perf)
        except serial.SerialTimeoutException as e:
            # write_timeout を付けたことで、相手が受け取らない状態でも
            # 無期限に固まらず、ここへ落ちてくる
            print('Serial write timeout')
            self._logger.error(f"Serial write timeout: {e}")
        except serial.serialutil.SerialException as e:
            print(e)
            self._logger.error(f"Error : {e}")
        except AttributeError as e:
            print('Using a port that is not open.')
            self._logger.error(f"Maybe Using a port that is not open.: {e}")
        finally:
            # 前回(_before)は送れたときだけ進める。送れなかった行を
            # 前回にすると、次に来たスティック行が「変わっていない」と
            # 誤判定され、間引きで捨てられる。送れていないのに捨てるのが
            # 最悪なので、失敗時は基準を動かさない。
            if ok:
                self._before = row

        # 送った行だけを配る。間引きで送らなかった行は Switch にも届いて
        # いないので、記録に残すとログと実機の挙動がずれる。送信の成否は
        # 問わない（送れなかった操作が記録から消えると切り分けができない）。
        self._notify(row)


class PicoUartTransport(TextSerialTransport):
    """Pico 用。フルステートの S 行をシリアルへ送出する。

    繋ぎ方は2通りあり、この層から見ると どちらも同じ「COM ポート」である:

      ①有線（pico_main.c）: PC → USBシリアル変換器 → Pico の UART → Switch
          Pico の USB は Switch が占有するため、PC とは繋げない。
          そこで FT232 などの変換器を挟む。115200bps。

      ②無線（bt_probe.c）: PC → Pico の USB(CDC) → Bluetooth → Switch
          無線化で Pico の USB が空いたので、PC と直結できる。
          変換器が要らず、線が1本になる。

    どちらも送る中身は同じ S 行なので、この実装を分ける必要は無い。
      違うのは「どの COM 番号か」だけで、それは利用者が選ぶ。
    """

    name = "pico_uart"
    capability = PICO_LIVE_STATE

    def add_listener(self, func: Callable[[str], None]) -> bool:
        # 125 Hz の送信行と keepalive を入力ログに流さない。
        return False

    def remove_listener(self, func: Callable[[str], None]) -> None:
        pass

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        # mailbox で最新状態に畳んでいるため、ここでは間引かない。
        with self._lock:
            self._write(row, measure_perf)


# =====================================================================
# プリセットの登録簿
# =====================================================================
# なぜ登録簿を置くか:
#   「運び方を差し替えられる」形だけでは、差し替えるのに呼び出し側が
#   実装クラスを import して自分で組み立てる必要があった。
#   利用者から見ると「選ぶ」ことができない。設定画面や起動引数から
#     指定できるようにするには、名前と作り方の対応表が要る。
#
# ここが持つのは「名前 → 作り方」だけ。実装そのものは持たない。
#   利用者が自分で書いた Transport も register_transport で足せる
#     （利用者定義）。本体側は中身を知らない。

# 既定のプリセット名。設定が無い・読めない・知らない名前のときはここへ戻す
DEFAULT_TRANSPORT = "legacy_text"

# 名前 → {"factory": 呼ぶと Transport を返すもの, "description": 説明,
#         "capability": 許可済み能力, "builtin": 最初から入っているか}
_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_transport(name: str, factory: Callable[..., Transport],
                       description: str = "",
                       builtin: bool = False,
                       replace: bool = False) -> bool:
    """プリセットを登録する。登録できた場合は True を返す。

    name は設定画面や --transport で指定する鍵。factory は「呼ぶと
    Transport を返すもの」で、クラスそのものでよい。

    同じ名前が既にある場合、既定では上書きせず False を返す。利用者が自作を
    追加したつもりで本体側の実装を置き換えてしまう事故を防ぐためである。
    意図的に置き換える場合のみ replace=True を指定する。
    """
    key = str(name).strip()
    if not key:
        print("  注記: プリセット名が空です。登録しませんでした。")
        return False
    if not callable(factory):
        print(f"  注記: プリセット '{key}' の作り方が呼び出せません。")
        return False
    if key in _REGISTRY and not replace:
        print(f"  注記: プリセット '{key}' は既にあります"
              "（置き換えるなら replace=True）。")
        return False
    capability = getattr(factory, "capability", LEGACY_ROW)
    if capability not in VALID_CAPABILITIES:
        print(f"  注記: プリセット '{key}' の capability '{capability}' は"
              "許可されていません。登録しませんでした。")
        return False

    _REGISTRY[key] = {
        "factory": factory,
        "description": str(description),
        "capability": capability,
        "builtin": bool(builtin),
    }
    return True


def unregister_transport(name: str) -> bool:
    """プリセットを外す。組み込みは外せない（戻せなくなるため）。"""
    info = _REGISTRY.get(str(name))
    if info is None:
        return False
    if info.get("builtin"):
        print(f"  注記: '{name}' は組み込みなので外せません。")
        return False
    del _REGISTRY[str(name)]
    return True


def list_transports() -> List[str]:
    """登録されているプリセット名の一覧（設定画面の候補に使う）。"""
    return list(_REGISTRY.keys())


def describe_transports() -> List[Tuple[str, str]]:
    """(名前, 説明) の一覧。画面へ出すときの並びはこの順。"""
    return [(k, v.get("description", "")) for k, v in _REGISTRY.items()]


def get_transport_info(name: str) -> Optional[Dict[str, Any]]:
    """1件ぶんの登録内容。無ければ None。"""
    info = _REGISTRY.get(str(name))
    return dict(info) if info is not None else None


def resolve_transport_name(name: Optional[str]) -> str:
    """指定された名前を、実際に使える名前へ直す。

    知らない名前は黙って既定へ落とさない。理由を出してから落とす。
      設定ファイルの綴りを間違えたまま「なぜか従来どおり動く」と、
      指定が効いていないことに気づけない（静かに壊れる型）。
    """
    key = str(name).strip() if name is not None else ""
    if not key:
        return DEFAULT_TRANSPORT
    if key in _REGISTRY:
        return key
    print(f"通信方式 '{key}' は登録されていません。"
          f"既定の '{DEFAULT_TRANSPORT}' を使います。")
    print("  使えるもの: " + ", ".join(list_transports()))
    return DEFAULT_TRANSPORT


def create_transport(name: Optional[str] = None,
                     logger: Any = None) -> Transport:
    """名前から運び方を1つ作る。設定画面・起動引数はここを通る。

    作れなかった場合も None を返さず、既定の実装を返す。ここで
      None が返ると呼び出し側が全部 None 検査を書く羽目になり、
      しかも「線が無い」状態はどこかで必ず落ちる。
    """
    key = resolve_transport_name(name)
    info = _REGISTRY.get(key)
    if info is None:
        # 既定すら登録されていない（想定外）。最後の砦として直に作る
        return TextSerialTransport(logger=logger)
    try:
        try:
            return info["factory"](logger=logger)
        except TypeError:
            # logger を受け取らない作り方もある（利用者定義など）
            return info["factory"]()
    except Exception:
        print(f"通信方式 '{key}' を用意できませんでした。"
              f"既定の '{DEFAULT_TRANSPORT}' へ戻します。")
        print(traceback.format_exc())
        if key != DEFAULT_TRANSPORT:
            return create_transport(DEFAULT_TRANSPORT, logger=logger)
        return TextSerialTransport(logger=logger)


def load_transport_plugins(dir_path: str) -> List[str]:
    """フォルダの .py を読み、利用者定義のプリセットを取り込む。

    各ファイルはモジュール直下に register(register_transport) を
      持つこと。呼ばれた側はその関数で自分の Transport を登録する。
      本体は中身を知らないまま、名前だけで選べるようになる。

    1本が壊れていても他は読む。読めなかったものは理由を出す。
      黙って飛ばすと「置いたのに出てこない」理由が分からない。
    """
    added: List[str] = []
    if not dir_path or not os.path.isdir(dir_path):
        return added
    import importlib.util
    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        full = os.path.join(dir_path, filename)
        try:
            spec = importlib.util.spec_from_file_location(
                "transport_plugin_" + filename[:-3], full)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            hook = getattr(module, "register", None)
            if not callable(hook):
                print(f"  注記: {filename} に register() がありません。")
                continue
            before = set(_REGISTRY.keys())
            hook(register_transport)
            added.extend(sorted(set(_REGISTRY.keys()) - before))
        except Exception:
            print(f"  注記: {filename} を読み込めませんでした。")
            print(traceback.format_exc())
    return added


# 組み込みプリセット。
register_transport(
    TextSerialTransport.name, TextSerialTransport,
    description="従来と同じテキスト行を pyserial で送る（本家 Leonardo 用）",
    builtin=True,
)

register_transport(
    PicoUartTransport.name, PicoUartTransport,
    description="Picoへfull-state S行を送る（有線=USBシリアル変換器 / 無線=PicoのUSB直結）",
    builtin=True,
)
