#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GuiAssets.py - Poke-Controller Modified の GUI 部品.

CaptureArea    : カメラ映像を描画する Frame。マウスでのスティック操作も担う。
_PhotoImageSurface : 非 Windows 用の描画面（Canvas + PhotoImage）。
ControllerGUI  : Switch コントローラを模した簡易操作ウィンドウ。
MouseStick     : マウス操作をコマンドとして扱うための最小の PythonCommand。
MyScrolledText : flush を持つ ScrolledText（標準出力のリダイレクト先用）。
"""

from __future__ import annotations

import datetime
import math
import os
import time
import tkinter as tk
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from tkinter.scrolledtext import ScrolledText
from typing import Any, Final

import WindowUtils
import cv2
import numpy as np

# 模擬コントローラがボタンの押しっぱなしを扱うため、
#   ボタンと十字キーの種類を直接使う（UnitCommand を経由しない）。
from Commands.PythonCommandBase import PythonCommand
from core.Camera import CAPTURE_SIZE
from core.coordinates import CoordinateMapper, fit_rect
from core.gdi_surface import GdiSurface
from core.preview_phase import PhaseLock
from core.preview_renderer import (
    TK_COLORREF,
    BadgeState,
    ImgRectState,
    OverlayState,
    PreviewRenderer,
    RectState,
    RenderResult,
    StickState,
)
from loguru import logger
from ui import preview_clock

isTakeLog = False
# True にするとスティック操作の軌跡を log/ に CSV で書き出す。

nowtime = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# 相対パスだとカレントディレクトリ次第で読めなくなるため、
# このファイルの場所を基準に解決する。
DISABLED_IMAGE_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "Images", "disabled.png"
    )
)

STICK_LOG_INTERVAL = 0.05  # スティック値を記録・送信する最小間隔(秒)
STICK_SEND_INTERVAL = 0.05  # スティック値をシリアルへ送る最小間隔(秒)
STICK_SEND_MAG_STEP = 0.25  # これ以上倒し量が変われば間隔を無視して送る
# Motion イベントの処理上限。ゲーミングマウス等で毎秒1000件超の Motion が
# 来ると、1件ごとの送信・再描画で GUI スレッドが飽和し「応答なし」になる。
# live 送出が 8ms 周期のため、それより細かく処理しても届く値は変わらない。
# 先頭は必ず通し、離す操作は別イベントなので取りこぼさない。
MOTION_MIN_INTERVAL = 0.008
IDLE_INTERVAL_MS = 200  # 映像を表示しないあいだの描画ループ周期(ms)
LOG_DIR = "log"

# 1:1 の大きさ判断で捨てられたフレームの計数名。設計 10.F が名指しで
# 決めている綴りなので、証拠側で別の綴りにしない。
DISCARD_COUNTER = "frames_discarded_dimension_mismatch"
# compose() の detail がこれなら 1:1 判断に落ちた。present() の no_frame は
# 同じフレームの結果なので、この 1 つだけ数える。
_DIMENSION_MISMATCH = "dimension_mismatch"

# live_sender が isOpened を再照会するまでの猶予(秒)。
# マウス Motion は 8ms 間引き後も約125Hzで届き、そのたび isOpened を
# 呼ぶと switch-bcon-proc では同期 RPC（最大8秒級）が Tk スレッドで
# 直列に積まれ、GUI が数十秒固まる。TTL 内は前回の判定を使い回し、
# 嵐を O(1) 回の照会に畳む。タイマーは足さず、呼ばれたときにだけ
# 期限を見る（lazy）。後方互換のため値は monkeypatch で変えられる。
SER_OPENED_TTL_S = 0.5

# 時刻源。テストでは monkeypatch で差し替える（実機では単調時刻）。
_ser_opened_clock: Callable[[], float] = time.monotonic

# 送り先ごとの opened 判定。id(ser) -> (ser 本体, 開いているか, 時刻)。
# 本体も添えるのは id 使い回し（別物が同じ id を取る）の取り違え防止。
_SER_OPENED_CACHE: dict[int, tuple[Any, bool, float]] = {}
_INVALID_PREVIEW_FPS_MESSAGE: Final[str] = "FPS must be one of 5, 15, 30, 45, 60"


def _validated_preview_fps(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(_INVALID_PREVIEW_FPS_MESSAGE)
    try:
        fps = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(_INVALID_PREVIEW_FPS_MESSAGE) from exc
    if fps not in WindowUtils.FPS_VALUES:
        raise ValueError(_INVALID_PREVIEW_FPS_MESSAGE)
    return fps


class CaptureAreaProxy:
    """ワーカーから安全に呼べる CaptureArea の代理。

    tkinter はスレッドセーフでない。コマンドのワーカースレッドから
    Canvas（create_rectangle / coords / itemconfig / after）を直接
    呼ぶと、描画の競合や終了時の TclError になる。描画系だけは
    GUI スレッドへ after(0) で渡し、戻り値は待たない（非同期）。

    描画以外はそのまま委譲する（従来と同じ。新規に Tk を触る呼び出し
    を足すときは、ここへ marshaled 版を足すこと）。実物が要る場合
    （tk.Toplevel の親など）は .widget で取り出す。あくまで非常口で
    あり、取り出した先の操作は呼び出し側の責任になる。
    """

    def __init__(self, root: Any, target: Any) -> None:
        self._proxy_root = root
        self._proxy_target = target

    @property
    def widget(self) -> Any:
        """裏の実物。Tk を直接触る用途の非常口（呼び出し側の責任）。"""
        return self._proxy_target

    def _marshal(self, name: str, *args: Any, **kwargs: Any) -> None:
        target = self._proxy_target
        root = self._proxy_root
        if target is None or root is None:
            return
        try:
            root.after(0, lambda: self._call_guarded(target, name, args, kwargs))
        except Exception:
            # 終了後など after 自体が積めないときは捨てる。枠表示は
            # 無くても操作に影響しない。
            pass

    @staticmethod
    def _call_guarded(target: Any, name: str, args: Any, kwargs: Any) -> None:
        try:
            getattr(target, name)(*args, **kwargs)
        except Exception:
            logger.debug(f"CaptureAreaProxy.{name} failed: {traceback.format_exc()}")

    def ImgRect(self, *args: Any, **kwargs: Any) -> None:
        """枠の描画を GUI スレッドへ回す（非同期・戻り値なし）。"""
        self._marshal("ImgRect", *args, **kwargs)

    def deleteImageRect(self, *args: Any, **kwargs: Any) -> None:
        """枠の消去を GUI スレッドへ回す（非同期・戻り値なし）。"""
        self._marshal("deleteImageRect", *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("_proxy_", "widget")):
            raise AttributeError(name)
        return getattr(self.__dict__["_proxy_target"], name)


def live_sender(ser: Any, *, _force_refresh: bool = False) -> Any:
    """送り先。未接続なら None を返す。

    操作画面（ControllerGUI）と映像画面（CaptureArea）の両方から使う。
    未接続のまま操作すると、None への呼び出しで AttributeError になる。
    開いているかを見てから触る。isOpened を持たない古い送り先は、
    従来どおり触る。

    isOpened の判定は SER_OPENED_TTL_S のあいだ使い回す。Motion の嵐
    （約125Hz）で毎回 RPC を呼ぶと Tk が固まるため。押し始めと離す
    ときは _force_refresh=True で必ず再照会すること。特に離す操作は
    真偽を間違えると中立が届かず倒したままになる。
    isOpened が投げたら閉じているものとして扱い、Tk へは逃がさない。
    """

    if ser is None:
        return None
    opened = getattr(ser, "isOpened", None)
    if not callable(opened):
        return ser
    # 照会の前後で時刻を2回読むと、差し替え時計の進み方で TTL がずれる。
    # 照会前の1回だけ読む（数msのずれは TTL 0.5s に埋もれる）。
    now = _ser_opened_clock()
    if not _force_refresh:
        hit = _SER_OPENED_CACHE.get(id(ser))
        if hit is not None and hit[0] is ser and now - hit[2] < SER_OPENED_TTL_S:
            return ser if hit[1] else None
    try:
        is_open = bool(opened())
    except Exception:
        is_open = False
    _SER_OPENED_CACHE[id(ser)] = (ser, is_open, now)
    return ser if is_open else None


class _StickRecorder:
    """スティック操作の軌跡を CSV に書き出す（isTakeLog が True のときだけ使う）."""

    def __init__(self, side: str) -> None:
        self.path = os.path.join(LOG_DIR, f"{nowtime}_{side}Stick.log")
        self.dq: deque = deque()
        self.calc_time: float | None = None

    def start(self) -> None:
        """押し始め。前回からの経過を1件だけ残してバッファを空にする。"""
        now = time.perf_counter()
        if self.calc_time is not None:
            self.dq.clear()
            self.dq.append([0, 0, now - self.calc_time])
        else:
            self.dq.clear()
        self.calc_time = now

    def add(self, angle: float, mag: float) -> bool:
        """一定時間経っていれば記録する。記録したら True を返す。"""
        now = time.perf_counter()
        if self.calc_time is None:
            self.calc_time = now
            return False
        if now - self.calc_time <= STICK_LOG_INTERVAL:
            return False
        self.dq.append([angle, mag, now - self.calc_time])
        self.calc_time = now
        return True

    def flush(self, angle: float | None, mag: float | None) -> None:
        """離した時点の値を足してファイルへ書き出す。

        押しただけで動かさずに離すと angle / mag が None のまま来る。
        そのまま書くと CSV に 'None,None,0.12' という行が混ざり、
        読み込む側が数値として解釈できずに落ちる。中立(0,0)として
        記録する（実際に倒していないので意味も合う）。
        """
        if self.calc_time is not None:
            elapsed = time.perf_counter() - self.calc_time
            self.dq.append([angle or 0.0, mag or 0.0, elapsed])
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                for row in self.dq:
                    print(",".join(map(str, row)), file=f)
        except OSError as e:
            logger.warning(f"stick log write failed: {e}")
        self.dq.clear()


class MouseStick(PythonCommand):
    """マウス操作をコマンド経由で扱うための最小実装."""

    NAME = "MOUSEスティック"

    def __init__(self) -> None:
        super().__init__()
        # マウス操作は人の手による操作として扱うため、送り主を
        # 「マウス」と名乗る。スティックの座標も同じ名乗りで送っているので、
        # ボタン側もここで揃えておく。
        self.input_source = "mouse"

    def do(self) -> None:
        pass

    def stick(self, buttons: Any, duration: float = 0.1, wait: float = 0.1) -> None:
        """指定時間だけ入力を保持する。"""
        self.keys.input(buttons, ifPrint=False)
        self.wait(duration)
        self.wait(wait)

    def stickEnd(self, buttons: Any) -> None:
        """入力を解除する。"""
        self.keys.inputEnd(buttons)


def _create_preview_surface(host: Any, renderer: str = "auto") -> PreviewRenderer:
    """指定の描画方式で面を作る。既定は Windows なら GDI 子窓、それ以外は Canvas 実装。

    mac/Linux には GDI が無いので、同じプロトコルを PhotoImage 実装で
    満たす。プラットフォーム要件であって一般化ではない（設計 8節）。

    ``renderer`` は WindowUtils.RENDERER_VALUES の名前。表に無い値は推測で
    解釈せず、警告してこのプラットフォームの既定へ戻す（利用者が設定を
    書き間違えただけなので、起動を落とさない）。
    """
    if renderer in WindowUtils.RENDERER_VALUES:
        name = renderer
    else:
        logger.warning("描画方式 '{}' は候補に無いため 'auto' として扱います", renderer)
        name = "auto"
    if name == "auto":
        name = "gdi" if os.name == "nt" else "photo"
    if name == "gdi" and os.name != "nt":
        # GDI が無い環境で構築を試みない。CtypesGdiApi の OSError 捕捉は
        # 実行時の最終防衛として残すが、通常経路ではここで寄せる。
        logger.warning("この環境に GDI は無いため photo を使います")
        name = "photo"

    surface: PreviewRenderer | None = None
    if name == "gdi":
        try:
            surface = GdiSurface()
        except OSError as error:
            # 名前で指定しても、この環境には GDI が無いことがある
            # （CtypesGdiApi が OSError を上げる）。ここで落とすと
            # mac/Linux の起動が壊れるので、必ず代替へ落とす。
            logger.warning("GDI の面を作れないため Canvas 実装に落とします: {}", error)
    if surface is None:
        from ui.photo_surface import PhotoImageSurface

        surface = PhotoImageSurface(host)

    logger.info("プレビュー面を作成 surface={}", type(surface).__name__)
    return surface


class CaptureArea(tk.Frame):
    """カメラ映像を表示し、マウスでスティック操作を行うウィジェット.

    映像は GDI の子 HWND が持ち、スティック・範囲枠・認識枠は同じ面の
    renderer が描く。Tk の Canvas 項目は廃止した（設計 0節・8節）。子窓は
    親の絵より常に上に合成されて Canvas を透かせず、``coords()`` は移動
    させる項目の古い箱を背景色で塗り直すので映像を消してしまう。
    """

    def __init__(
        self,
        camera: Any,
        fps: Any,
        is_show: Any,
        ser: Any,
        master: Any = None,
        show_width: int = 640,
        show_height: int = 360,
        take_stick_log: bool | None = None,
        renderer: str = "auto",
    ) -> None:
        super().__init__(
            master,
            borderwidth=0,
            highlightthickness=0,
            cursor="tcross",
            width=show_width,
            height=show_height,
        )
        self.master: Any = master
        self.camera = camera
        self.ser = ser
        # Misc.keys() と衝突するため keys という属性は置かない。
        # 旧版にあった self.keys = None は誰も読まない死に代入であり、
        # tkinter の keys() を隠してしまうため削除した。
        self.is_show_var = is_show

        self.radius = 60  # 描画するスティック円の半径（表示座標）
        # 上記をキャプチャ座標に直した半径。押下ごとにビューポートから求め直す
        # ので、これは初期値にすぎない。オーバーレイはキャプチャ座標で持つため、
        # 変換前の半径を渡すと画面の縮尺ぶんだけ小さい円になる。
        self._stick_radius = self.radius
        self.show_width = int(show_width)
        self.show_height = int(show_height)
        self.show_size = (self.show_width, self.show_height)
        # 実際に受け皿へ渡した表示面（ビューポート）。Tk が要求サイズどおりに
        # 割り当てるとは限らないので、座標変換の基準はこちらが正であり、
        # show_size は「要求した大きさ」であって「描いている大きさ」ではない。
        self._viewport: tuple[int, int] = self.show_size

        self.lx_init, self.ly_init = 0, 0
        self.rx_init, self.ry_init = 0, 0
        self.min_x, self.min_y = 0, 0
        self.max_x, self.max_y = 0, 0
        self.ss = None

        self._langle: float | None = None
        self._lmag: float | None = None
        self._rangle: float | None = None
        self._rmag: float | None = None
        self._select_after_id: str | None = None

        # 呼び出し側（Window）が settings.ini の値を渡す。未指定なら
        # 従来どおりモジュール定数 isTakeLog を見る。
        take_log = isTakeLog if take_stick_log is None else bool(take_stick_log)
        self._lrec = _StickRecorder("L") if take_log else None
        self._rrec = _StickRecorder("R") if take_log else None

        self._configured_fps = _validated_preview_fps(fps)
        self.next_frames = 1000.0 / self._configured_fps
        self._preview_clock: preview_clock.PreviewClock | None = None
        # 取込と表示の位相が重なって取りこぼすのを防ぐ（core/preview_phase.py）
        self._phase_lock = PhaseLock(round(1e9 / self._configured_fps))
        self._realign_after_id: str | None = None
        self._camera_observation: dict[str, int] = {
            "camera_read_count": 0,
            "camera_frame_present_count": 0,
            "camera_none_count": 0,
            "camera_read_error_count": 0,
            "camera_unique_sequence_count": 0,
            "camera_duplicate_sequence_count": 0,
            "camera_sequence_regression_count": 0,
            "camera_last_sequence": 0,
            "post_teardown_camera_read_count": 0,
            DISCARD_COUNTER: 0,
        }
        # 同じ detail のログは 1 度だけ出す。60fps で毎フレーム warn すると
        # ログが溶けて何も読めなくなる。継続した回数は上の計数が持つ。
        self._logged_render_failures: set[str] = set()
        self._preview_stop_requested = False

        self.bind("<Control-ButtonPress-1>", self.mouseCtrlLeftPress)
        self.bind("<Control-ButtonRelease-1>", self.mouseCtrlLeftRelease)
        self.bind("<Control-Shift-ButtonPress-1>", self.StartRangeSS)
        self.bind("<Control-Shift-Button1-Motion>", self.MotionRangeSS)
        self.bind("<Control-Shift-ButtonRelease-1>", self.ReleaseRangeSS)

        # 描画ループの制御用。stopCapture() で clock を確実に止める
        self._capturing = False
        # 表示実測（getStats で読むたびに区切り直す）
        self._stat_shown = 0
        self._stat_began_at: float | None = None
        # 描画本体の所要(ms)の指数移動平均と区間最大。平均は粛々と、
        # 最大はスタッター（瞬間的な落ち込み）の犯人探しに使う。
        self._stat_draw_ms = 0.0
        self._stat_draw_max_ms = 0.0

        # スティック送信の間引き用。最後に送った時刻
        self._last_sent = 0.0
        self._last_sent_mag = 0.0
        # Motion 処理の最終時刻（片側ごと）。洪水時はここで間引く
        self._motion_last: dict = {}

        # 画像認識の枠は1組だけ使って使い回す
        self._rect_after_id: str | None = None
        # 直近に描いた世代。同じ seq の再合成・再提示を省く。
        self._last_frame_seq: int | None = None
        # 停止画像で描いた最後の世代。カメラが止まると seq が動かないので、
        # 生フレームとは別の箱で重複判定する（_drawFrame 側）。
        self._last_disabled_seq: int | None = None

        # 描画の作業バッファ（毎フレームの確保を避ける）
        self._filter_enabled = False
        self._filter_lower = [0, 0, 0]
        self._filter_upper = [179, 255, 255]
        self._filter_mode = "gray_out"
        # 色補正（ガンマ等）の辞書と有効旗。None は中立（恒等変換）。
        self._correction: dict | None = None
        self._correction_active = False

        # オーバーレイは Tk の項目ではなくデータ。renderer が読むだけ。
        self._stick_left = StickState()
        self._stick_right = StickState()
        self._guide = RectState()
        self._img_rect = ImgRectState()
        # 左上の状態バッジ。他の成分と同じ不変値で、setBadge が差し替える
        # だけ。初期値は非表示なので、状態を出さない面が黒い箱を描かない。
        self._badge = BadgeState()
        self._allocBuffers()

        # 映像面。子窓は Frame の HWND に作る（設計 6節）。Windows は
        # 親を動かすときに子を動かすが大きさは変えないので、<Configure> が
        # 無いと最初の geo 指定以降がそのままになる。
        self._surface: PreviewRenderer = _create_preview_surface(self, renderer)
        self._surface.attach(int(self.winfo_id()), self.show_size)
        from ui.photo_surface import SelfTestResult

        # 「まだやっていない」。``None`` は既に「self_test を持たない面」の
        # 意味で使うので、未判定とは別の値にしておく。
        self.surface_selftest = SelfTestResult("pending", None, (0, 0, 0, 0))
        self._selftest_after_id: str | None = None
        self._schedule_selftest()
        self.bind("<Configure>", self._onConfigure)
        self.bind("<Destroy>", self._cancelSelftest, add="+")

    _SELFTEST_POLL_MS: Final[int] = 50
    _SELFTEST_TIMEOUT_S: Final[float] = 2.0

    def _schedule_selftest(self) -> None:
        """自己検査を遅延実行する。

        ``wait_visibility()`` はイベントループを内側で回し続けるため、
        withdraw されたウィンドウや表示のないセッションで永久に止まる。
        代わりに ``after()`` でポーリングし、上限時間を過ぎたら
        ``not_mapped`` として記録する。
        """
        self._selftest_deadline = time.monotonic() + self._SELFTEST_TIMEOUT_S
        self._poll_selftest()

    def _poll_selftest(self) -> None:
        """自己検査を、判定が出るか期限が来るまで繰り返す。

        host の ``winfo_viewable()`` を見るのは足りない。pack による子のマップ
        は idle の処理で行われるので、host が viewable になった直後は Canvas が
        まだマップされていないことがある。その1 回を ``not_mapped`` で確定させ
        るのは、表示できている面を未マップと誤判定することになる。面自身の答え
        を見て、``not_mapped`` のうちは打ち切らずに待つ。

        ``not_run`` も判定ではない。面が構築時に持つ初期値であり、子窓や DC が
        まだ使えなかった1 回の試行がそのまま残るだけ。判定が出た ``self_test()``
        まで待ち、打ち切りは期限だけが担う。
        """
        if not hasattr(self, "_surface"):
            return
        result = self._run_selftest()
        if getattr(result, "outcome", None) not in ("not_mapped", "not_run"):
            return
        if time.monotonic() > self._selftest_deadline:
            return
        self._selftest_after_id = self.after(
            self._SELFTEST_POLL_MS, self._poll_selftest
        )

    def _run_selftest(self) -> Any:
        probe: Any = self._surface
        self.surface_selftest = (
            probe.self_test() if hasattr(probe, "self_test") else None
        )
        logger.info(
            "プレビュー面の自己検査 outcome={}",
            getattr(self.surface_selftest, "outcome", "not_supported"),
        )
        return self.surface_selftest

    def _refreshSelftestMirror(self) -> None:
        """面の自己検査結果をミラーへ反映する。

        ``_poll_selftest`` は最初の確定判定で打ち切るが、面は resize のたびに
        ``self_test()`` をやり直す。ミラーが打ち切り時点の値のまま面の答と
        ずれないよう、受け皿の resize 後に走らせて塞ぐ。判定が変わったとき
        だけ記録する（``<Configure>`` は嵐になりうる）。
        """
        probe: Any = self._surface
        if not hasattr(probe, "self_test"):
            return
        result = probe.self_test()
        if result == getattr(self, "surface_selftest", None):
            return
        self.surface_selftest = result
        logger.info(
            "プレビュー面の自己検査 outcome={}（リサイズ後に更新）",
            getattr(result, "outcome", "not_supported"),
        )

    def _cancelSelftest(self, event: Any) -> None:
        """予約済みの after を外す。破棄後に残ると background error になる。

        ``hasattr(self, "_surface")`` は破棄されたあとも True のままなので、
        それでは打ち切りにならない。after の ID を持っておき、破棄の時点で外す。
        """
        destroyed = getattr(event, "widget", None)
        # Tkinter は破棄中の widget を解決できなければパス文字列で渡す
        # （``Misc.__str__`` が返す ``_w`` と同じ形）。
        if destroyed is not self and destroyed != getattr(self, "_w", None):
            # 子（Canvas など）の破棄ではポーリングは続ける。停止は期限が
            # 来たときだけで、面の答では打ち切らない。
            return
        if self._selftest_after_id is None:
            return
        try:
            self.after_cancel(self._selftest_after_id)
        except tk.TclError:
            # 既に消化済み、または widget が既に消えている。
            pass
        self._selftest_after_id = None

    @property
    def overlay(self) -> OverlayState:
        """この瞬間のオーバーレイ。不変値なので描画が途中状態を見ない。"""
        return OverlayState(
            left_stick=self._stick_left,
            right_stick=self._stick_right,
            guide=self._guide,
            img_rect=self._img_rect,
            badge=self._badge_state(),
        )

    def _badge_state(self) -> BadgeState:
        """バッジの状態。__init__ が _badge を入れないインスタンスには既定を 1 個返す。

        ``__new__`` だけで組み立てる検査用のインスタンスのための経路で、
        実機では __init__ が必ず _badge を入れるのでここには来ない。

        **既定を読み出すたびに新しいインスタンスを返してはならない。**
        _drawFrame の「オーバーレイが変わったか」は各成分の同一性（``is``）で
        判定するので、毎回新しい BadgeState だと「ずっと変化した」と読まれ、
        静止したウィンドウが毎フレーム recompose される（そして recompose を
        持たない面では AttributeError になる）。一度作ったらインスタンスに
        残す。
        """
        badge = getattr(self, "_badge", None)
        if badge is None:
            badge = self._badge = BadgeState()
        return badge

    @property
    def surface(self) -> PreviewRenderer:
        """描画面。終了処理から release() されるための公開口。"""
        return self._surface

    # ------------------------------------------------------------------
    # 映像描画
    # ------------------------------------------------------------------
    def _loadDisabledImage(self) -> np.ndarray:
        """カメラ停止中に出す画像（BGR）。読めなければ黒画像で代用する。

        大きさは CAPTURE_SIZE（キャプチャ解像度）に固定する。受け皿は 1:1 の
        フレームしか合成しないので、表示サイズで作ると dimension_mismatch で
        弾かれ、「カメラが止まった」絵が一度も画面に出ない。
        """
        width, height = CAPTURE_SIZE
        # imread のスタブは ndarray 固定だが、実機では読めないと None が返る。
        img: Any = cv2.imread(DISABLED_IMAGE_PATH, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning(f"disabled image not found: {DISABLED_IMAGE_PATH}")
            return np.zeros((height, width, 3), np.uint8)
        return cv2.resize(img, CAPTURE_SIZE, interpolation=cv2.INTER_AREA)

    def _onConfigure(self, event: Any) -> None:
        """Frame の大きさの変化を受け皿と座標変換の基準へ伝える（設計 8節）。

        受け皿は渡された表示面へ映像を縦横比のまま収めて中央に置くので、
        表示サイズを下限にはしない。下限を設けると受け皿は Frame より大きい
        子窓に描くことになり、絵が枠からはみ出すうえクリック位置までずれる。
        """
        self._viewport = (int(event.width), int(event.height))
        self._surface.resize(self._viewport)
        # さっき描いた絵は古い配置の絵なので、同じ世代番号でも次の tick で
        # 描き直させる。カメラが止まっていると世代番号は動かないので、
        # ここを落とさないとリサイズ後だけ配置が古いまま止まる。
        self._last_frame_seq = None
        self._last_disabled_seq = None
        self._refreshSelftestMirror()

    def startCapture(self) -> None:
        """描画ループを開始する。"""
        if self._capturing:
            return
        self._capturing = True
        self._preview_stop_requested = False
        phase_lock = getattr(self, "_phase_lock", None)
        if phase_lock is not None:
            phase_lock.reset()
        self._preview_clock = preview_clock.PreviewClock(
            self.winfo_toplevel(),
            self._dispatch_tick,
            self._configured_fps,
            IDLE_INTERVAL_MS,
        )
        self._preview_clock.start()

    def stopCapture(self) -> preview_clock.StopResult:
        """描画ループを止め、clock の停止結果を返す。"""
        self._capturing = False
        self._preview_stop_requested = True
        for name in ("_rect_after_id", "_select_after_id", "_realign_after_id"):
            after_id = getattr(self, name, None)
            if after_id is not None:
                setattr(self, name, None)
                try:
                    self.after_cancel(after_id)
                except (tk.TclError, ValueError):
                    continue
        if self._preview_clock is None:
            return preview_clock.StopResult.STOPPED
        result = self._preview_clock.stop()
        if result is preview_clock.StopResult.STOPPED:
            self._preview_clock = None
        return result

    def capture(self) -> None:
        """旧入口から1 tick だけ同期実行する。"""
        self._dispatch_tick()

    def _dispatch_tick(self) -> preview_clock.DispatchResult:
        """1フレームを同期処理し、次回の予約は行わない。"""
        if not self._capturing or self._preview_stop_requested:
            return preview_clock.DispatchResult(schedule="idle")
        started = time.perf_counter()
        showing = True
        try:
            showing = bool(self.is_show_var.get())
            if showing:
                frame, seq = self._readLatest()
                if self._drawFrame(frame, seq):
                    # 表示実測 fps はカメラフレームの提示数を測るもの。
                    # オーバーレイだけの再描画（recompose）が True を返しても
                    # tick 周波数で飽和するので、seq が変わったとき（または seq
                    # なしの従来経路）だけ数える。
                    if seq is None or seq != getattr(self, "_stat_counted_seq", None):
                        self._stat_shown += 1
                        self._stat_counted_seq = seq
                    if self._stat_began_at is None:
                        self._stat_began_at = started
                draw_ms = (time.perf_counter() - started) * 1000.0
                if self._stat_draw_ms <= 0.0:
                    self._stat_draw_ms = draw_ms
                else:
                    self._stat_draw_ms = self._stat_draw_ms * 0.9 + draw_ms * 0.1
                if draw_ms > self._stat_draw_max_ms:
                    self._stat_draw_max_ms = draw_ms
                if frame is not None and seq is not None:
                    self._trackPhase(seq)
            return preview_clock.DispatchResult(
                schedule="active" if showing else "idle"
            )
        except Exception as exc:
            logger.error(f"capture failed: {exc}")
            return preview_clock.DispatchResult(
                schedule="active" if showing else "idle"
            )

    def _trackPhase(self, seq: int) -> None:
        """この tick の観測を位相合わせへ渡し、要れば据え直しを予約する。

        公開時刻は readFrameWithTiming からしか取れない。_readLatest とは別の
        読み取りなので、その間に次のフレームが届いて seq がずれたら、この
        tick では判断しない。公開時刻を持たないカメラ（共有メモリ版）では
        何もしない。描画時間の計測には含めない。
        """
        phase_lock = getattr(self, "_phase_lock", None)
        reader = getattr(self.camera, "readFrameWithTiming", None)
        if phase_lock is None or not callable(reader):
            return
        try:
            _frame, timed_seq, _t_capture_ns, t_ready_ns = reader()
            if int(timed_seq) != seq:
                return
            delay_ns = phase_lock.observe(time.perf_counter_ns(), seq, int(t_ready_ns))
        except Exception as exc:
            logger.debug(f"位相合わせの観測を取れませんでした: {exc}")
            return
        if delay_ns is None or getattr(self, "_realign_after_id", None) is not None:
            return
        delay_ms = max(1, math.ceil(delay_ns / 1_000_000))
        self._realign_after_id = self.after(delay_ms, self._realignPreview)

    def _realignPreview(self) -> None:
        """予約した瞬間に 1 回描き、表示クロックをそこから刻み直す。"""
        self._realign_after_id = None
        clock = self._preview_clock
        if clock is None or not self._capturing or self._preview_stop_requested:
            return
        logger.debug("表示 tick の位相をカメラに合わせ直しました")
        clock.realign()

    def _allocBuffers(self) -> None:
        """描画の作業バッファと停止中画像を確保する（キャプチャ解像度で固定）。

        表示サイズではなく CAPTURE_SIZE で作る。``_convert`` は補正・抽出の結果を
        このバッファへ np.copyto する対象がキャプチャ解像度のフレームなので、
        表示サイズのバッファだと表示寸法が 1280x720 と違う場合に毎回例外になる。
        大きさが変わらないので setShowsize から再実行しなくてよい。
        """
        width, height = CAPTURE_SIZE
        self._filter_buf = np.empty((height, width, 3), np.uint8)
        self._correct_buf = np.empty((height, width, 3), np.uint8)
        self._disabled = self._loadDisabledImage()

    def setPreviewFilter(
        self,
        enabled: bool,
        lower: list[int],
        upper: list[int],
        mode: str,
        correction: dict | None = None,
    ) -> None:
        """表示専用フィルタ。認識・保存には影響しない。変更時はseq dedupを無効化する。

        correction は色補正5項目の辞書（None は中立）。補正は
        HSV抽出より先にかかる（OBSのフィルタチェーンと同列）。
        """
        from core import preview_filter as _pf

        lo, hi = _pf.validate_hsv(lower, upper)
        if str(mode) not in ("gray_out", "mask"):
            raise ValueError("modeは gray_out / mask で指定してください")
        self._filter_enabled = bool(enabled)
        self._filter_lower, self._filter_upper, self._filter_mode = lo, hi, str(mode)
        if correction is None:
            self._correction = None
            self._correction_active = False
        else:
            self._correction = _pf.validate_correction(correction)
            self._correction_active = not _pf.is_correction_neutral(self._correction)
        self._last_frame_seq = None

    def clearPreviewFilter(self) -> None:
        self._filter_enabled = False
        self._last_frame_seq = None

    def _readLatest(self) -> tuple[Any, int | None]:
        """最新フレームと世代番号を1回だけ読み、観測値を更新する。"""
        if self._preview_stop_requested:
            self._camera_observation["post_teardown_camera_read_count"] += 1
            return None, None

        self._camera_observation["camera_read_count"] += 1
        frame: Any = None
        sequence: int | None = None
        primary_error = False
        fallback_error = False
        read_seq = getattr(self.camera, "readFrameWithSeq", None)
        if callable(read_seq):
            try:
                frame, sequence = read_seq()
                sequence = int(sequence)
            except Exception:
                primary_error = True

        if not callable(read_seq) or primary_error:
            try:
                frame = self.camera.readFrame()
            except Exception:
                fallback_error = True

        if primary_error or fallback_error:
            self._camera_observation["camera_read_error_count"] += 1

        if fallback_error:
            return None, None
        if sequence is None:
            sequence_getter = getattr(self.camera, "frame_seq", None)
            if callable(sequence_getter):
                try:
                    sequence = int(sequence_getter())
                except Exception:
                    sequence = None
        if frame is None:
            self._camera_observation["camera_none_count"] += 1
        else:
            self._camera_observation["camera_frame_present_count"] += 1

        if sequence is not None:
            last_sequence = int(self._camera_observation["camera_last_sequence"])
            if sequence == last_sequence:
                self._camera_observation["camera_duplicate_sequence_count"] += 1
            elif sequence < last_sequence:
                self._camera_observation["camera_sequence_regression_count"] += 1
            else:
                self._camera_observation["camera_unique_sequence_count"] += 1
            self._camera_observation["camera_last_sequence"] = max(
                last_sequence, sequence
            )
        return frame, sequence

    def _preview_evidence_snapshot(
        self,
    ) -> dict[str, int | str | bool | dict[str, int | str | bool]]:
        clock = self._preview_clock
        return {
            **self._camera_observation,
            "clock_health": dict(clock.health_snapshot()) if clock is not None else {},
        }

    def _drawFrame(self, frame: Any, seq: int | None = None) -> bool:
        """BGR フレームを描画面へ合成してから提示する。

        返り値は 1 枚が実際に画面へ出たかどうか。描こうとした数では
        なく出た数を数えるので、合成も提示も落ちた tick は 0 になる。

        同じ (seq, overlay) では合成・提示を省く。5〜10fps の機器では描画 tick より
        frame が変わらないことが多く、無駄な変換が数倍に膨らむ。
        clear() で seq が進むため旧絵を使い回さない。frame が同じでも
        overlay が変わったとき（スティックの押下・解放など）は映像の複製から
        オーバーレイだけ描き直す。カメラバッファは再読しない。

        フィルタも補正も有効でなければ _convert を経由せず、受け取った
        frame をそのまま renderer へ渡す。毎フレームの確保がゼロになる
        経路で、設計の前提そのものでもある。
        """
        last_seq = getattr(self, "_last_frame_seq", None)
        same_frame = seq is not None and seq == last_seq

        if frame is None:
            # 停止画像はオーバーレイを描画しないので、seq のみで重複排除する。
            # ただし生フレームと同じ seq が来ても、画面上はまだ停止画像では
            # ない。停止画像専用の seq 記録で重複を判定し、カメラ停止の検知を
            # 逃がさない（stopCapture 後は tick 自体が回らないので無限再描画に
            # はならない）。
            last_disabled_seq = getattr(self, "_last_disabled_seq", None)
            if seq is not None and seq == last_disabled_seq:
                return False
            shown = self._showDisabled()
            if shown:
                if seq is not None:
                    self._last_disabled_seq = seq
                # カメラ停止の告警は tick 毎のログになるので、生フレームから
                # 停止画像へ移った瞬間に 1 回だけ出す。
                if not getattr(self, "_last_shown_disabled", False):
                    self._last_shown_disabled = True
                    logger.warning(
                        "カメラから frame を取得できないため停止画像を表示する seq={}",
                        seq,
                    )
            return shown

        overlay = self.overlay  # 1 回だけ読む（property は毎回新規 dataclass）
        # overlay は不変 dataclass の組み立てなので、押下→解放で値が元に
        # 戻る経路では == では変更を検知できない。差し替えられた各
        # コンポーネントの同一性で「変わったか」を判定する。
        last_overlay = getattr(self, "_last_overlay", None)
        same_overlay = (
            last_overlay is not None
            and overlay.left_stick is last_overlay.left_stick
            and overlay.right_stick is last_overlay.right_stick
            and overlay.guide is last_overlay.guide
            and overlay.img_rect is last_overlay.img_rect
            and overlay.badge is last_overlay.badge
        )
        if same_frame and same_overlay:
            return False
        if same_frame:
            # 新しいフレームは無いがオーバーレイだけ変わった → 映像の複製から
            # オーバーレイだけ描き直す（カメラバッファは再読しない）。
            composed = self._surface.recompose(overlay)
        else:
            composed = self._surface.compose(self._prepared(frame), overlay)
        # 合成が落ちても提示は呼ぶ。1 tick 1 回の提示という現状を保ち、
        # どちらの reason で落ちたかを両方観測できるようにする。
        presented = self._surface.present()
        shown = self._note_render(composed, presented)
        # 提示が成功を返しても合成が落ちていれば画出していない。出たときだけ
        # 进入済みにする。
        if shown:
            self._last_overlay = overlay
            self._last_shown_disabled = False
            if seq is not None:
                self._last_frame_seq = seq
        return shown

    def _prepared(self, frame: Any) -> Any:
        """表示する BGR フレーム。処理が要らなければ入力そのもの。"""
        if not self._filter_enabled and not self._correction_active:
            return frame
        return self._convert(frame)

    def _convert(self, frame: Any) -> np.ndarray:
        """補正と抽出を順に適用した BGR を作業バッファへ作る。

        縮小も BGR→RGB 変換もしない。縮小はカメラ側へ移り、色順は DIB が
        BGR をそのまま取るので不要になった。補正→抽出の順は従来どおり。
        """
        from core import preview_filter as _pf

        work = frame
        if self._correction_active:
            np.copyto(self._correct_buf, _pf.apply_correction(work, self._correction))
            work = self._correct_buf
        if self._filter_enabled:
            np.copyto(
                self._filter_buf,
                _pf.apply_filter(
                    work,
                    self._filter_lower,
                    self._filter_upper,
                    self._filter_mode,
                ),
            )
            work = self._filter_buf
        return work

    def _showDisabled(self) -> bool:
        """停止中の画像を描く。1 枚出せたかを返す。"""
        composed = self._surface.compose(self._disabled, OverlayState())
        presented = self._surface.present()
        return self._note_render(composed, presented)

    def _note_render(
        self, composed: RenderResult | None, presented: RenderResult | None
    ) -> bool:
        """合成・提示の落ちを数え、同じ reason のログは 1 度だけ出し、成否を返す。

        RenderResult を返さない合成・提示は成否の証拠が無いので成功とみなす。
        """
        # __new__ だけで組んだインスタンスは __init__ を経由しないので getattr で開く。
        logged: set[str] | None = getattr(self, "_logged_render_failures", None)
        if logged is None:
            logged = self._logged_render_failures = set()
        for stage, result in (("compose", composed), ("present", presented)):
            if result is None or result.ok:
                continue
            if stage == "compose" and result.detail == _DIMENSION_MISMATCH:
                self._camera_observation[DISCARD_COUNTER] = (
                    self._camera_observation.get(DISCARD_COUNTER, 0) + 1
                )
            if result.detail in logged:
                continue
            logged.add(result.detail)
            logger.warning(
                "プレビュー描画に失敗 stage={} detail={} {}={}",
                stage,
                result.detail,
                DISCARD_COUNTER,
                self._camera_observation.get(DISCARD_COUNTER, 0),
            )
        return all(result is None or result.ok for result in (composed, presented))

    def getStats(self) -> dict[str, float]:
        """表示実測を返す。呼ぶたびに区切り直す（期間fps方式）。

        戻り値は {"fps": 実際に描いた枚数/秒}。Camera.getStats と
        並べると「機器が遅いか描画が遅いか」が分かる。
        """
        now = time.perf_counter()
        shown, began_at = self._stat_shown, self._stat_began_at
        draw_ms, draw_max_ms = self._stat_draw_ms, self._stat_draw_max_ms
        self._stat_shown = 0
        self._stat_began_at = None
        self._stat_draw_max_ms = 0.0
        if began_at is None or shown <= 0:
            return {"fps": 0.0, "draw_ms": round(draw_ms, 1), "draw_max_ms": 0.0}
        elapsed = now - began_at
        if elapsed < 1e-6:
            return {"fps": 0.0, "draw_ms": round(draw_ms, 1), "draw_max_ms": 0.0}
        return {
            "fps": round(shown / elapsed, 1),
            "draw_ms": round(draw_ms, 1),
            "draw_max_ms": round(draw_max_ms, 1),
        }

    def setFps(self, fps: Any) -> None:
        """描画 FPS を変更し、clock がある場合は再アンカーする。"""
        fps_value = _validated_preview_fps(fps)
        if fps_value == self._configured_fps:
            return
        self._configured_fps = fps_value
        self.next_frames = 1000.0 / fps_value
        phase_lock = getattr(self, "_phase_lock", None)
        if phase_lock is not None:
            phase_lock.set_period(round(1e9 / fps_value))
        if self._preview_clock is not None:
            self._preview_clock.set_fps(fps_value)
        logger.info(f"FPS set to {fps_value} (interval {self.next_frames:.1f} ms)")

    def setShowsize(self, show_height: int, show_width: int) -> None:
        """要求する表示サイズを変更する。受け皿と座標変換の基準も追従させる
        （子窓そのものは作り直さない）。

        ``<Configure>`` が来れば受け皿への resize はそちらが担当するが、幾何
        マネージャが要求サイズどおりに割り当てないことがあるため、ここでも
        同じ値を入れておく。作業バッファはキャプチャ解像度で固定なので、
        作り直さない。
        """
        self.show_width = int(show_width)
        self.show_height = int(show_height)
        self.show_size = (self.show_width, self.show_height)
        self.config(width=self.show_width, height=self.show_height)
        self._viewport = (self.show_width, self.show_height)
        self._surface.resize(self._viewport)
        self._refreshSelftestMirror()
        logger.info(f"Show size set to {self.show_width} x {self.show_height}")

    def saveCapture(self) -> None:
        """現在のフレームを画像として保存する。"""
        self.camera.saveCapture()

    # ------------------------------------------------------------------
    # 座標変換
    # ------------------------------------------------------------------
    def _mapper(self, frame: Any = None) -> CoordinateMapper:
        """キャプチャ座標と表示座標の変換層を組み立てる。

        ``frame`` は、これから添字で切り出すフレームそのもの。渡された
        ときは必ずその形から capture_size を取る。カメラに要求した大きさを
        引き算すると、要求と実際が食い違うフレームを掴んだときに範囲内に
        収まったまま別の画素を返してしまい、どこにも例外が出ない。

        ``frame`` が無いのは、その呼び出し側がフレームを一切扱わない
        ときだけ（枠を描くだけの経路）。配列に手を伸ばさないので、
        切り出す先が無いと分かるこの場合は camera の申告した名目サイズで
        よい。未接続カメラが 0 を申告してきても、その結果は読み込みも
        保存も起きない座標に落ちるだけ。

        表示側の大きさは ``self._viewport`` へ ``fit_rect`` を通したものを使う。
        受け皿は映像を枠に収めて余白付きで中央に置くので、変換側も同じ配置を
        知らないと余白のぶんだけクリック位置がずれる。映像そのものの矩形
        （余白は含めない）が display_size、余白の位置が display_origin になる。
        描画側と同じ計算を 1 箇所で共有するのが目的。
        """
        capture_size = (
            self.camera.capture_size
            if frame is None
            else (int(frame.shape[1]), int(frame.shape[0]))
        )
        origin_x, origin_y, width, height = fit_rect(capture_size, self._viewport)
        return CoordinateMapper(
            capture_size=capture_size,
            display_size=(width, height),
            display_origin=(origin_x, origin_y),
        )

    def _eventToCapture(self, event: Any) -> tuple[int, int]:
        """マウスイベントの座標をキャプチャ座標へ直す。

        すべての入力がここを入口にして、以後はキャプチャ座標だけで扱う。
        倍率と余白はビューポートから毎回求める（リサイズ直後でもずれない）。
        変換を 1 箇所に閉じ込めて、入力ごとに別の計算を持たないようにする。

        クランプはしない。ここを通る値はフレームを切り出さない（四隅や
        中心だけ）ので、余白のクリックを映像の端に丸めるのは
        ``mouseCtrlLeftPress`` の責務。
        """
        return self._mapper().to_capture(event.x, event.y)

    # ------------------------------------------------------------------
    # 範囲スクリーンショット (Ctrl+Shift+ドラッグ)
    # ------------------------------------------------------------------
    def StartRangeSS(self, event: Any) -> None:
        """範囲選択を開始する。選択枠は最初からキャプチャ座標で持つ。"""
        # 選択中フレームを保持するのでコピーを受け取る
        self.ss = self.camera.readFrame(copy=True)
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.UnbindRightClick()

        # 選択するのは 1 つの枠で、切り出すのも描画するのも同じフレーム上の
        # 座標なので、入口で一度変換して以降は持ち回すだけにする。
        self.min_x, self.min_y = self._eventToCapture(event)
        self._guide = RectState(
            x0=self.min_x,
            y0=self.min_y,
            x1=self.min_x + 1,
            y1=self.min_y + 1,
            visible=True,
        )

        logger.info(
            "Mouse down: Show ({}, {}) / Capture ({}, {})".format(
                event.x,
                event.y,
                self.min_x,
                self.min_y,
            )
        )

        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

    def MotionRangeSS(self, event: Any) -> None:
        """ドラッグ中の選択枠を追従させる。"""
        # クランプの基準は表示サイズではなくキャプチャ側の大きさにする。
        # Motion はウィジェット箱の外まで届くので、表示サイズで縛ると余白や
        # 原点のぶんだけ位置がずれた座標になる。選択中フレームの形から取る
        # ので、カメラの要求サイズとの食い違いの影響も受けない。
        mapper = self._mapper(self.ss)
        capture_x, capture_y = mapper.to_capture(event.x, event.y)
        capture_width, capture_height = mapper.capture_size
        self.max_x = min(capture_width, max(0, capture_x))
        self.max_y = min(capture_height, max(0, capture_y))
        # 端は包含端で持ち、renderer 側で GDI の +1 補正をする。
        self._guide = replace(self._guide, x1=self.max_x + 1, y1=self.max_y + 1)

    def ReleaseRangeSS(self, event: Any) -> None:
        """選択範囲を切り出して保存する。min/max はすでにキャプチャ座標。"""
        logger.info(
            "Mouse up: Show ({}, {}) / Capture ({}, {})".format(
                event.x,
                event.y,
                self.max_x,
                self.max_y,
            )
        )
        if self.min_x > self.max_x:
            self.min_x, self.max_x = self.max_x, self.min_x
        if self.min_y > self.max_y:
            self.min_y, self.max_y = self.max_y, self.min_y

        # ここで二度と変換しない。StartRangeSS / MotionRangeSS が
        # キャプチャ座標で持ち回しているので、二度目の変換は縮尺を
        # 二重に掛けることになる（余白のある表示では 2 回分ずれる）。
        crop_x0, crop_y0 = self.min_x, self.min_y
        crop_x1, crop_y1 = self.max_x, self.max_y
        # loguru はファイル（と stderr）へしか出ず、ログ欄（sys.stdout 経由）には
        # 出さない。saveCapture の戻り値を捨てると、押した本人には「何も起きな
        # かった」と「保存された」の区別がつかないので、成否を 1 行だけ出す。
        saved = self.camera.saveCapture(
            crop=1,
            crop_ax=[crop_x0, crop_y0, crop_x1, crop_y1],
        )
        if saved:
            saved_to = getattr(self.camera, "capture_dir", "Captures")
            print(
                f"範囲キャプチャを保存しました: ({crop_x0}, {crop_y0})-"
                f"({crop_x1}, {crop_y1}) → {saved_to}"
            )
        else:
            print("範囲キャプチャに失敗しました（詳細はログファイル）")

        # after には呼び出し可能オブジェクトを渡す（直接呼ぶと即時実行になる）
        # 予約 ID は控えておく。終了時に残っていると破棄途中の Frame を
        # 触るため、stopCapture で取り消す。
        try:
            if self._select_after_id is not None:
                self.after_cancel(self._select_after_id)
        except Exception:
            pass
        try:
            self._select_after_id = self.after(250, self._clearGuide)
        except Exception:
            self._select_after_id = None

        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

    def _clearGuide(self) -> None:
        """範囲選択枠を消す。"""
        self._guide = RectState()

    # ------------------------------------------------------------------
    # 色取得 (Ctrl+左クリック)
    # ------------------------------------------------------------------
    def mouseCtrlLeftPress(self, event: Any) -> None:
        """クリック位置の座標と色を表示する。"""
        frame = self.camera.readFrame()
        if frame is None:
            logger.warning("no frame available")
            return
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()

        # 1画素の色を見るためだけに 1280x720 全体を BGR→RGB, RGB→HSV と
        # 2回変換すると約5.5MB を走査することになり、クリックのたびに
        # 数十ms 止まる。先に座標を求め、その 1x1 だけを変換する。
        # 変換層は切り出すフレームそのものから組むので、倍率（表示面 ÷
        # フレーム幅）と上限（フレーム幅 − 1）が別の大きさに由来する
        # ことが起こらない。
        px, py = self._mapper(frame).to_capture(event.x, event.y, clamp=True)
        pixel = frame[py : py + 1, px : px + 1]
        rgb = cv2.cvtColor(pixel, cv2.COLOR_BGR2RGB)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        r, g, b = rgb[0, 0]
        h, s, v = hsv[0, 0]
        logger.info(
            "Mouse down: Show ({}, {}) / Capture ({}, {})".format(
                event.x, event.y, px, py
            )
        )
        logger.info(f"Color [R: {r}, G: {g}, B: {b}] / HSV [H: {h}, S: {s}, V: {v}]")

    def mouseCtrlLeftRelease(self, event: Any) -> None:
        """左スティック操作のバインドを戻す。"""
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()

    # ------------------------------------------------------------------
    # スティック操作の共通処理
    # ------------------------------------------------------------------
    def _angleMag(self, event: Any, x_init: int, y_init: int) -> tuple[float, float]:
        """中心からの角度(度)と 0〜1 に丸めた倒し量を返す。

        ``x_init``/``y_init`` は押下位置のキャプチャ座標、``event`` も
        キャプチャ座標へ直してから差を取る。半径もキャプチャ座標に直した
        ``_stick_radius`` で割るので、「画面上で 60px 動かした」操作が
        そのまま最大倒しになる。片方だけ変換すると、画面を縮小していると
        きの操作だけ最大まで倒らなくなる。

        スカラー1個の計算に numpy を使うと ufunc のディスパッチが乗り
        math の5〜10倍遅い。ここはマウス Motion のたびに呼ばれるので
        math を使う（numpy は画像処理側だけで使う）。
        """
        cursor_x, cursor_y = self._eventToCapture(event)
        dx = cursor_x - x_init
        dy = y_init - cursor_y
        angle = math.degrees(math.atan2(dy, dx))
        mag = math.hypot(dx, dy) / self._stick_radius
        return angle, min(max(mag, 0.0), 1.0)

    def _sendStick(self, side: str, angle: float, mag: float) -> None:
        """片側のスティック値を送る。

        生の送信行を組み立てるのをやめ、送信側が持つ現在の姿勢へ
          差分だけを申告する形に変えた。

        なぜ変えるか:
          旧実装は行全体を組み立てていた。この行は先頭のボタンが固定値で、
          もう片方のスティックも中立で埋めていた。つまり自分が知らない項目まで
          自分の値で上書きしていた。結果、
            ・スクリプトが押していたボタンが外れる
            ・左を倒したまま右を倒せない
          という2つの不具合が出ていた。どちらも行全体を送ることが原因。

        差分の申告なら、触っていない項目（ボタン・十字キー・
          もう片方のスティック）は送信側の現在値のまま保たれる。
          これで上の2件が仕組み上起きなくなる。

        古い版への退避:
          送信側が古い版（差分の申告を受けられない）の場合は、従来どおり
          生の行を送る。移行の途中でも動き続けるようにするため。
           どの段階でも必ず動く形を保つ。
        """
        ser = live_sender(self.ser)
        if ser is None:
            return
        x, y = self._stickXY(angle, mag)

        # 送り主付きの操作口を通す。
        #   setStick / sendPosture は「誰の操作か」を送信側へ伝えるので、
        #   入力の優先付けを有効にしたとき、マウス操作を人の手入力として
        #   優先できる。applyStick を直接呼ぶと優先付けを素通りしていた。
        #   なお setStick 自体は常時受け付ける（連続値に所有権は無い）。
        #   旧来の送信経路の sendPosture は優先付けに従い、棄却時は送らない。
        #   常時送信経路では applyStick が最新の値置き場へ直行しているため、
        #   値は届く（sendPosture は常時送信経路では何もしない）。
        set_stick = getattr(ser, "setStick", None)
        send_posture = getattr(ser, "sendPosture", None)
        if callable(set_stick) and callable(send_posture):
            if set_stick(side, x, y, source="mouse"):
                send_posture(source="mouse")
            return

        # 退避1・退避2: いずれも古い送信側用であり、現行の送信側では
        # 通らない。優先付けが無い版なので迂回のしようが無く、挙動は従来どおり。
        # 特に退避2の生の行（ボタン固定）は、現行では絶対に送らないこと。
        # ボタン落ちと片側スティックの上書きが再発するため。退避が必要な相手にだけ
        # 残してある。
        # 退避1: 姿勢は持つが送り主付きの操作口が無い旧版向け
        apply_stick = getattr(ser, "applyStick", None)
        build_row = getattr(ser, "_buildRow", None)
        if callable(apply_stick) and callable(build_row):
            apply_stick(side, x, y)
            ser.writeRow(build_row(), is_show=False)
            return

        # 退避2: 旧 Sender。従来どおり生の行で送る（挙動は変わらない）
        xy = self._stickHex(angle, mag)
        row = f"3 8 {xy} 80 80" if side == "L" else f"3 8 80 80 {xy}"
        ser.writeRow(row, is_show=False)

    def _stickXY(self, angle: float, mag: float) -> tuple[int, int]:
        """角度と倒し量を 0〜255 の座標へ直す。

        _stickHex と同じ計算をして、16進へ直す前の整数を返す。
          _stickHex は hex() で "0x80" の形にするが、姿勢へ渡すのは
          数値なので、共通部分だけをここへ出した。
        丸め方（int()）まで _stickHex と揃えること。round() に
          変えると1ずれる座標が出て、旧実装と送信行が一致しなくなる。
        """
        rad = math.radians(angle)
        x = int(128 + mag * 127.5 * math.cos(rad))
        y = int(128 - mag * 127.5 * math.sin(rad))
        return x, y

    def _stickHex(self, angle: float, mag: float) -> str:
        """角度と倒し量を、シリアルに流す x y の16進表記に変換する。

        旧送信側へ退避したときだけ使う。_stickXY と同じ値を返すこと。
        丸めがずれると送信行が変わるため、変えるときは両方を揃える。
        """
        x, y = self._stickXY(angle, mag)
        return f"{hex(x)} {hex(y)}"

    def _sendNeutralStick(self, side: str) -> None:
        """片側のスティックだけを中立へ戻して送る。

        旧実装は両方のスティックを中立に戻し、ボタンも初期値にする行を
          送っていた。これは左を離しただけなのに右まで戻し、
          押しているボタンも消していた。
        離した側だけを中立にすれば、もう片方は倒したまま残る。

        中立行はスティックだけの変化なので Sender の間引きに
          引っかかりうる。離した状態が届かないと倒したままになるため、
           呼び出し側で flushPending して必ず送り切る（従来どおり）。
        """
        # 離す操作は TTL を待たず必ず再照会する。古い「開」を信じて
        # 送ると閉じた回線へ投げ、古い「閉」を信じると中立が届かず
        # 倒したまま残る。離す頻度は低いので RPC 1回のコストは問題ない。
        ser = live_sender(self.ser, _force_refresh=True)
        if ser is None:
            return
        # 離す操作も送り主付きの操作口を通す（送り主はマウス）。
        set_stick = getattr(ser, "setStick", None)
        send_posture = getattr(ser, "sendPosture", None)
        if callable(set_stick) and callable(send_posture):
            if set_stick(side, 128, 128, source="mouse"):
                send_posture(source="mouse")
            return

        apply_stick = getattr(ser, "applyStick", None)
        build_row = getattr(ser, "_buildRow", None)
        if callable(apply_stick) and callable(build_row):
            apply_stick(side, 128, 128)
            ser.writeRow(build_row(), is_show=False)
            return
        ser.writeRow("3 8 80 80 80 80", is_show=False)

    def _armStick(self, side: str, x: int, y: int) -> None:
        """押下。外周円の中心は操作点に留め、ノブも同じ位置に置く。

        ``x``/``y`` はキャプチャ座標、``radius`` はその座標系に直した
        ``_stick_radius`` を使う。オーバーレイは全面キャプチャ座標で描かれる
        ので、ここを表示座標のままだと縮尺ぶんだけ円が小さく／ノブがずれる。
        """
        state = StickState(
            active=True,
            center_x=x,
            center_y=y,
            radius=self._stick_radius,
            knob_x=x,
            knob_y=y,
        )
        if side == "L":
            self._stick_left = state
        else:
            self._stick_right = state

    def _dragStick(
        self, side: str, event: Any, x_init: int, y_init: int, angle: float, mag: float
    ) -> None:
        """ノブを移動する。振り切っているときは外周円の外へ貼り付ける。

        外周円の中心は動かさない。押した位置がそのまま円の中心で、
        何回ドラッグしても動かない（設計 5節）。

        振り切りの位置は押下位置から d = radius + radius//11 の円周上。
        画面の y は下向きなので、sin だけ符号を反転する。GDI は整数しか
        取れないので round する（半径は最大 0.5px ずれるが、
        端数分は表現できない）。``x_init``/``y_init`` と半径はどちらも
        キャプチャ座標系なので、この計算は表示縮尺に依存しない。
        """
        if mag >= 1:
            d = self._stick_radius + self._stick_radius // 11
            rad = math.radians(angle)
            knob_x = round(x_init + d * math.cos(rad))
            knob_y = round(y_init - d * math.sin(rad))
        else:
            knob_x, knob_y = self._eventToCapture(event)
        if side == "L":
            current = self._stick_left
        else:
            current = self._stick_right
        state = replace(current, knob_x=knob_x, knob_y=knob_y)
        if side == "L":
            self._stick_left = state
        else:
            self._stick_right = state

    def _releaseStick(self, side: str) -> None:
        """離した。外周円とノブを消したのと同じ状態へ戻す。"""
        if side == "L":
            self._stick_left = StickState()
        else:
            self._stick_right = StickState()

    def _pressing(
        self,
        event: Any,
        side: str,
        x_init: int,
        y_init: int,
        prev_angle: float | None,
        prev_mag: float | None,
        rec: _StickRecorder | None,
    ) -> tuple[float | None, float | None]:
        """ドラッグ中の共通処理。今回の角度と倒し量を返す。

        送信の間引きは記録(rec)の有無と切り離す。旧実装は条件が逆で、
        ログ無効（既定 isTakeLog=False）のときに間引き無しで毎回送って
        いた。マウスの Motion は毎秒100回以上届くのに 9600bps では1行
        約19ms かかるため、通常運用でシリアルが詰まり操作が数百ms遅れて
        効く状態になっていた。
        """
        now = time.perf_counter()
        last = self._motion_last.get(side)
        if last is not None and now - last < MOTION_MIN_INTERVAL:
            return prev_angle, prev_mag
        self._motion_last[side] = now
        angle, mag = self._angleMag(event, x_init, y_init)

        if self._shouldSend(mag, prev_angle, prev_mag):
            self._sendStick(side, angle, mag)
        if rec is not None and prev_angle is not None and prev_mag is not None:
            rec.add(angle, mag)

        self._dragStick(side, event, x_init, y_init, angle, mag)
        return angle, mag

    def _shouldSend(
        self,
        mag: float,
        prev_angle: float | None,
        prev_mag: float | None,
    ) -> bool:
        """送ってよいかを判定する。間引きつつ、大きな変化は取りこぼさない。

        比較の相手は「直前のフレーム」ではなく「最後に実際に送った値」に
        する。直前フレームと比べると、中立ちょうどで手が微動したときに
        毎フレーム「中立へ戻った」と判定され、間引きが効かなくなる。

        一定間隔で間引くだけだと、振り切った瞬間や中立へ戻した瞬間が
        最大 STICK_SEND_INTERVAL ぶん遅れて効く。そこで値が大きく動いた
        ときだけ間隔を無視して即送る。

        この間引きは 1 行あたり約 19 ms を要する 9600 bps の legacy 経路
        のための措置である。live 経路は容量 1 の mailbox が最新値に畳むため、
        申告を間引いても中間座標が失われるだけで利点がない。実測では 20 Hz
        に制限されていた。したがって live 経路では毎フレーム申告する。
        """
        now = time.perf_counter()
        if self._liveStickPath():
            self._markSent(now, mag)
            return True
        if prev_angle is None or prev_mag is None:
            self._markSent(now, mag)
            return True

        last = self._last_sent_mag
        # 大きく動いた場合、振り切った場合、中立に戻した場合は即時送出する
        if abs(mag - last) >= STICK_SEND_MAG_STEP:
            self._markSent(now, mag)
            return True
        if mag >= 1.0 and last < 1.0:
            self._markSent(now, mag)
            return True
        if mag <= 0.0 and last > 0.0:
            self._markSent(now, mag)
            return True

        if now - self._last_sent < STICK_SEND_INTERVAL:
            return False
        self._markSent(now, mag)
        return True

    def _liveStickPath(self) -> bool:
        """現在の送信先が live 経路（間引きが不要な経路）かを返す。"""
        judge = getattr(self.ser, "_liveCapable", None)
        if not callable(judge):
            return False
        try:
            return bool(judge())
        except Exception:
            return False

    def _markSent(self, now: float, mag: float) -> None:
        """送出した時刻と倒し量を記録する。次回の判定に使用する。"""
        self._last_sent = now
        self._last_sent_mag = mag

    # ------------------------------------------------------------------
    # 左スティック (左ドラッグ)
    # ------------------------------------------------------------------
    def mouseLeftPress(self, event: Any, ser: Any = None) -> None:
        """左スティックの操作を開始する。"""
        # 押し始めに真値を1回だけ掴み、直後の Motion の嵐は TTL 内の
        # 使い回しで捌く。押す頻度は低いので RPC 1回は問題ない。
        live_sender(self.ser, _force_refresh=True)
        if self.master.is_use_right_stick_mouse.get():
            self.UnbindRightClick()
        self.config(cursor="dot")
        # 押下位置はキャプチャ座標で持ち続ける。Motion 側も同じ座標系で
        # 差を取るので、ここが表示座標のままだと誤差が縮尺ぶんだけ残る。
        # 表示上の半径もキャプチャ座標へ直し直して、この 1 回のドラッグのあいだ固定する。
        self.lx_init, self.ly_init = self._eventToCapture(event)
        self._stick_radius = self._mapper().length_to_capture(self.radius)
        self._armStick("L", self.lx_init, self.ly_init)
        self._langle = None
        self._lmag = None
        if self._lrec is not None:
            self._lrec.start()

    def mouseLeftPressing(self, event: Any, ser: Any = None, angle: float = 0) -> None:
        """左スティックを倒す。"""
        self._langle, self._lmag = self._pressing(
            event,
            "L",
            self.lx_init,
            self.ly_init,
            self._langle,
            self._lmag,
            self._lrec,
        )

    def mouseLeftRelease(self, ser: Any = None) -> None:
        """左スティックを離して中立へ戻す。"""
        self.config(cursor="tcross")
        self._sendNeutralStick("L")
        # 中立行は「スティックだけの変化」なので Sender の間引きに
        # 引っかかりうる。離した状態が届かないと倒したままになるため、
        # ここは必ず送り切る。
        flush = getattr(self.ser, "flushPending", None)
        if callable(flush):
            flush()
        self._releaseStick("L")
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()
        if self._lrec is not None:
            self._lrec.flush(self._langle, self._lmag)

    # ------------------------------------------------------------------
    # 右スティック (右ドラッグ)
    # ------------------------------------------------------------------
    def mouseRightPress(self, event: Any, ser: Any = None) -> None:
        """右スティックの操作を開始する。"""
        # 左と同じく押し始めに真値を掴み、嵐は TTL で捌く。
        live_sender(self.ser, _force_refresh=True)
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()
        self.config(cursor="dot")
        # 左と同じく、押下位置も半径もキャプチャ座標へ移してから固定する。
        self.rx_init, self.ry_init = self._eventToCapture(event)
        self._stick_radius = self._mapper().length_to_capture(self.radius)
        self._armStick("R", self.rx_init, self.ry_init)
        self._rangle = None
        self._rmag = None
        if self._rrec is not None:
            self._rrec.start()

    def mouseRightPressing(self, event: Any, ser: Any = None, angle: float = 0) -> None:
        """右スティックを倒す。"""
        self._rangle, self._rmag = self._pressing(
            event,
            "R",
            self.rx_init,
            self.ry_init,
            self._rangle,
            self._rmag,
            self._rrec,
        )

    def mouseRightRelease(self, ser: Any = None) -> None:
        """右スティックを離して中立へ戻す。"""
        self.config(cursor="tcross")
        self._sendNeutralStick("R")
        # 中立行は「スティックだけの変化」なので Sender の間引きに
        # 引っかかりうる。離した状態が届かないと倒したままになるため、
        # ここは必ず送り切る。
        flush = getattr(self.ser, "flushPending", None)
        if callable(flush):
            flush()
        self._releaseStick("R")
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self._rrec is not None:
            self._rrec.flush(self._rangle, self._rmag)

    # ------------------------------------------------------------------
    # 画像認識の枠表示
    # ------------------------------------------------------------------
    def ImgRect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        outline: str,
        tag: str = "",
        ms: int = 2000,
    ) -> None:
        """キャプチャ座標で指定された矩形をそのまま描く。

        tag は後方互換のため受け取るが、枠は1組だけを使い回すので
        参照しない。消去の予約も after_cancel で前回分を取り消してから
        入れ直すため、予約が積み上がることはない。

        外面だけ capture 座標を 1.0 拡がっている（従来の 4.5px 白い枠）。
        内面は補正なし。1つにまとめると白い枠が黙って消える。

        受け皿はキャプチャ解像度のバックバッファへ描くので、ここでの
        座標変換は不要になった。表示座標へ直すと、表示される枠が検出された
        画素の 2 倍（または 1/2 倍）の位置にずれてしまう。
        """
        # 認識矩形は float で届く。オーバーレイは整数しか持てないため、
        # 外面は 1px 拡げた値まで含めてここで画素番号に落とす。
        self._img_rect = ImgRectState(
            outer=RectState(
                x0=round(x1 - 1.0),
                y0=round(y1 - 1.0),
                x1=round(x2 + 1.0),
                y1=round(y2 + 1.0),
            ),
            inner=RectState(x0=round(x1), y0=round(y1), x1=round(x2), y1=round(y2)),
            visible=True,
            # 未知の名前は白に落とす。Tk なら unknown color name で例外に
            # なっていたが、この経路は CaptureAreaProxy が debug で握り
            # 潰すので、枠が出ない보다白で描くほうが取りこぼしが無い。
            color=TK_COLORREF.get(outline, TK_COLORREF["white"]),
        )
        if self._rect_after_id is not None:
            self.after_cancel(self._rect_after_id)
        self._rect_after_id = self.after(ms, self.deleteImageRect)

    def deleteImageRect(self, tag: str = "") -> None:
        """ImgRect で描いた枠を隠す（状態は残すので同じ枠を使い回せる）。"""
        self._rect_after_id = None
        self._img_rect = replace(self._img_rect, visible=False)

    # ------------------------------------------------------------------
    # 左上の状態バッジ
    # ------------------------------------------------------------------
    def setBadge(
        self,
        text: str,
        background: int,
        foreground: int = 0x00FFFFFF,
    ) -> None:
        """プレビュー左上に描く状態バッジを差し替える。

        「何をいつ入れるか」は呼び出し側の責務で、このメソッドは渡された文字列と
        色を renderer へ渡すだけ。色は Win32 ``COLORREF`` (0x00BBGGRR) で、
        Tk の色名ではない。

        **内容が同じなら何もしない。** ``_drawFrame`` はオーバーレイの変化を
        各成分の同一性 (``is``) で判定するので、同じ内容の BadgeState を作り
        直すと「変化した」と読まれ、静止している窓が毎フレーム recompose される。
        状態ポーラが同じ状態を返し続けるのは正常なので、ここで新しい
        インスタンスを作ってはならない。
        """
        current = self._badge_state()
        if (
            current.text == text
            and current.background == background
            and current.foreground == foreground
        ):
            return
        self._badge = BadgeState(
            text=text,
            background=background,
            foreground=foreground,
            # 空文字は「何も言わない」。描かせないので visible は False に
            # なる。文字だけ持った空のバッジは、測定結果が 0 で箱だけが
            # 描かれる壊れた表示になる。
            visible=bool(text),
        )

    def clearBadge(self) -> None:
        """バッジを消す。既に非表示なら何もしない（非表示も一種の状態）。"""
        if not self._badge_state().visible:
            return
        self._badge = BadgeState()

    # ------------------------------------------------------------------
    # バインド管理
    # ------------------------------------------------------------------
    def ApplyLStickMouse(self) -> None:
        """設定に合わせて左スティック操作の有効・無効を切り替える。"""
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        else:
            self.UnbindLeftClick()

    def ApplyRStickMouse(self) -> None:
        """設定に合わせて右スティック操作の有効・無効を切り替える。"""
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()
        else:
            self.UnbindRightClick()

    def BindLeftClick(self) -> None:
        """左ドラッグを左スティックに割り当てる。"""
        self.bind("<ButtonPress-1>", lambda ev: self.mouseLeftPress(ev, self.ser))
        self.bind("<Button1-Motion>", lambda ev: self.mouseLeftPressing(ev, self.ser))
        self.bind("<ButtonRelease-1>", lambda ev: self.mouseLeftRelease(self.ser))
        logger.debug("Bind left click")
        self._sync_surface_binds()

    def BindRightClick(self) -> None:
        """右ドラッグを右スティックに割り当てる。"""
        self.bind("<ButtonPress-3>", lambda ev: self.mouseRightPress(ev, self.ser))
        self.bind("<Button3-Motion>", lambda ev: self.mouseRightPressing(ev, self.ser))
        self.bind("<ButtonRelease-3>", lambda ev: self.mouseRightRelease(self.ser))
        logger.debug("Bind right click")
        self._sync_surface_binds()

    def UnbindLeftClick(self) -> None:
        """左ドラッグの割り当てを外す。"""
        for seq in ("<ButtonPress-1>", "<Button1-Motion>", "<ButtonRelease-1>"):
            self.unbind(seq)
        logger.debug("Unbind left click")
        self._sync_surface_binds()

    def UnbindRightClick(self) -> None:
        """右ドラッグの割り当てを外す。"""
        for seq in ("<ButtonPress-3>", "<Button3-Motion>", "<ButtonRelease-3>"):
            self.unbind(seq)
        logger.debug("Unbind right click")
        self._sync_surface_binds()

    def _sync_surface_binds(self) -> None:
        """束縛の増減を描画面へ伝える。

        photo 面は Frame を覆う Canvas がポインタ事象を受け止めるので、Frame の
        束縛を Canvas から転送している。転送表は attach 時点の束縛から作られ、
        スティック操作は attach の後に Bind*/Unbind* で増減するため、その都度
        追従させないとプレビュー上のドラッグが Frame へ届かない。GDI 面は子窓が
        入力を取らず Frame へ直接届くので sync_host_binds を持たず、何もしない。
        """
        sync = getattr(getattr(self, "_surface", None), "sync_host_binds", None)
        if callable(sync):
            sync()


# GUI of switch controller simulator
# To avoid the error says 'ScrolledText' object has no attribute 'flush'
class MyScrolledText(ScrolledText):
    """標準出力のリダイレクト先に使うため flush を持たせた ScrolledText."""

    def flush(self) -> None:
        pass
