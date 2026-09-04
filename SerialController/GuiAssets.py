#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GuiAssets.py - Poke-Controller Modified の GUI 部品.

CaptureArea    : カメラ映像を描画する Canvas。マウスでのスティック操作も担う。
ControllerGUI  : Switch コントローラを模した簡易操作ウィンドウ。
MouseStick     : マウス操作をコマンドとして扱うための最小の PythonCommand。
MyScrolledText : flush を持つ ScrolledText（標準出力のリダイレクト先用）。
"""

from __future__ import annotations

import datetime
import math
import os
import re
import time
import tkinter as tk
from collections import deque
from tkinter.scrolledtext import ScrolledText
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageTk
from loguru import logger

from Commands import UnitCommand
from Commands.PythonCommandBase import PythonCommand
# 2026/08/25 段 V-c: GUI の模擬コントローラが押しっぱなしを扱うため、
#   Button / Hat の列挙を直に使う（UnitCommand を経由しなくなった）。
from Commands.Keys import Button, Hat


isTakeLog = False
# True にするとスティック操作の軌跡を log/ に CSV で書き出す。

nowtime = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# 相対パスだとカレントディレクトリ次第で読めなくなるため、
# このファイルの場所を基準に解決する。
DISABLED_IMAGE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "Images", "disabled.png")
)

STICK_LOG_INTERVAL = 0.05  # スティック値を記録・送信する最小間隔(秒)
STICK_SEND_INTERVAL = 0.05   # スティック値をシリアルへ送る最小間隔(秒)
STICK_SEND_MAG_STEP = 0.25   # これ以上倒し量が変われば間隔を無視して送る
# Motion イベントの処理上限。ゲーミングマウス等で毎秒1000件超の Motion が
# 来ると、1件ごとの送信・再描画で GUI スレッドが飽和し「応答なし」になる。
# live 送出が 8ms 周期のため、それより細かく処理しても届く値は変わらない。
# 先頭は必ず通し、離す操作は別イベントなので取りこぼさない。
MOTION_MIN_INTERVAL = 0.008
IDLE_INTERVAL_MS = 200       # 映像を表示しないあいだの描画ループ周期(ms)
LOG_DIR = "log"


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
        # 2026/08/25 段 V-b: マウス操作は人の手によるもの。
        #   入力調停で「人の手入力」として優先されるよう名札を付ける。
        #   _sendStick 経由の座標は source="mouse" で送っている（段 V-a）ので、
        #     ボタン側もここで揃えておく。
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


class CaptureArea(tk.Canvas):
    """カメラ映像を表示し、マウスでスティック操作を行う Canvas.

    描画は PhotoImage を1枚だけ作って paste で中身を差し替える。
    毎フレーム PhotoImage を作り直すと GC で周期的にカクつくため。
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
    ) -> None:
        super().__init__(
            master, borderwidth=0, cursor="tcross", width=show_width, height=show_height
        )
        self.master = master
        self.camera = camera
        self.ser = ser
        self.keys = None
        self.is_show_var = is_show

        self.radius = 60  # 描画するスティック円の半径
        self.show_width = int(show_width)
        self.show_height = int(show_height)
        self.show_size = (self.show_width, self.show_height)

        self.lx_init, self.ly_init = 0, 0
        self.rx_init, self.ry_init = 0, 0
        self.min_x, self.min_y = 0, 0
        self.max_x, self.max_y = 0, 0
        self.ss = None

        self._langle: float | None = None
        self._lmag: float | None = None
        self._rangle: float | None = None
        self._rmag: float | None = None

        # 呼び出し側（Window）が settings.ini の値を渡す。未指定なら
        # 従来どおりモジュール定数 isTakeLog を見る。
        take_log = isTakeLog if take_stick_log is None else bool(take_stick_log)
        self._lrec = _StickRecorder("L") if take_log else None
        self._rrec = _StickRecorder("R") if take_log else None

        self.setFps(fps)

        self.bind("<Control-ButtonPress-1>", self.mouseCtrlLeftPress)
        self.bind("<Control-ButtonRelease-1>", self.mouseCtrlLeftRelease)
        self.bind("<Control-Shift-ButtonPress-1>", self.StartRangeSS)
        self.bind("<Control-Shift-Button1-Motion>", self.MotionRangeSS)
        self.bind("<Control-Shift-ButtonRelease-1>", self.ReleaseRangeSS)

        # 描画ループの制御用。stopCapture() で after を確実に止める
        self._capturing = False
        self._after_id: str | None = None

        # スティック送信の間引き用。最後に送った時刻
        self._last_sent = 0.0
        self._last_sent_mag = 0.0
        # Motion 処理の最終時刻（片側ごと）。洪水時はここで間引く
        self._motion_last: dict = {}

        # 画像認識の枠は1組だけ作って使い回す
        self._rect_created = False
        self._rect_after_id: str | None = None

        # 描画の作業バッファ（毎フレームの確保を避ける）
        self._allocBuffers()

        # 映像用の PhotoImage は1枚だけ作って使い回す
        self._photo = ImageTk.PhotoImage(Image.new("RGB", self.show_size))
        self.disabled_tk = self._loadDisabledImage()
        self.im = self.disabled_tk
        self.im_ = self.create_image(0, 0, image=self.disabled_tk, anchor=tk.NW)

    # ------------------------------------------------------------------
    # 映像描画
    # ------------------------------------------------------------------
    def _loadDisabledImage(self) -> ImageTk.PhotoImage:
        """カメラ停止中に出す画像。読めなければ黒画像で代用する。"""
        img = cv2.imread(DISABLED_IMAGE_PATH, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning(f"disabled image not found: {DISABLED_IMAGE_PATH}")
            pil = Image.new("L", self.show_size)
        else:
            pil = Image.fromarray(
                cv2.resize(img, self.show_size, interpolation=cv2.INTER_AREA)
            )
        return ImageTk.PhotoImage(pil)

    def startCapture(self) -> None:
        """描画ループを開始する。"""
        self._capturing = True
        self.capture()

    def stopCapture(self) -> None:
        """描画ループを止める。camera.destroy() より先に必ず呼ぶ。

        止めずに破棄すると、解放済みのカメラ/共有メモリへ readFrame() が
        走ってクラッシュする。
        """
        self._capturing = False
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def capture(self) -> None:
        """1フレーム描画し、次回を予約する。例外が出ても止まらないようにする。

        次回の予約は「処理が終わってから interval 待つ」のではなく、
        「このフレームの開始時刻から interval 経過した時点」を狙う。
        前者だと実処理時間の分だけ毎フレーム遅れが積み上がり、
        30fps 指定でも 30fps に届かず、実機からどんどん遅延していく。
        """
        if not self._capturing:
            return

        started = time.perf_counter()
        showing = True
        try:
            showing = bool(self.is_show_var.get())
            if showing:
                self._drawFrame(self.camera.readFrame())
        except Exception as e:
            logger.error(f"capture failed: {e}")
        finally:
            if self._capturing:
                self._after_id = self.after(
                    self._next_delay(started, showing), self.capture
                )

    def _next_delay(self, started: float, showing: bool = True) -> int:
        """フレーム開始時刻を基準に、次フレームまでの待ち時間(ms)を求める。

        「映像を表示しない」あいだは描く物が無いので 1/fps では回さず、
        IDLE_INTERVAL_MS まで間隔を空ける（60fps 指定なら毎秒60回だった
        空回りが5回になる）。表示へ戻せば次の1回で通常周期に復帰する。

        遅れているときに下限1ms で詰めると、遅いフレームほど高頻度に
        capture() が積まれてイベントループが飽和し、後追いで悪化する。
        interval を超えた分は「1フレーム捨てて次の周期へ合わせる」形
        （残り = interval - 経過 % interval）にする。映像は間引かれるが
        GUI の応答は保たれ、平均周期も維持される。
        """
        if not showing:
            return IDLE_INTERVAL_MS

        interval = self.next_frames
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms < interval:
            return max(1, int(round(interval - elapsed_ms)))
        return max(1, int(round(interval - (elapsed_ms % interval))))

    def _allocBuffers(self) -> None:
        """描画の作業バッファを確保する（表示サイズ変更時に作り直す）。"""
        h, w = self.show_height, self.show_width
        self._resize_buf = np.empty((h, w, 3), np.uint8)
        self._rgb_buf = np.empty((h, w, 3), np.uint8)

    def _drawFrame(self, frame: Any) -> None:
        """BGR フレームを Canvas へ反映する。

        resize / cvtColor は dst を指定して確保済みバッファへ書く。
        毎フレーム新しい配列を作ると 640x360 で約1.4MB/フレーム
        （30fps なら 42MB/s）になり、GC が周期的に走ってカクつく。
        PhotoImage を1枚使い回している対策と理由は同じ。
        """
        if frame is None:
            self._showDisabled()
            return
        # 先に縮小してから色変換すると変換対象が減って軽い
        cv2.resize(
            frame,
            self.show_size,
            dst=self._resize_buf,
            interpolation=cv2.INTER_AREA,
        )
        cv2.cvtColor(self._resize_buf, cv2.COLOR_BGR2RGB, dst=self._rgb_buf)
        # frombuffer は fromarray と違い配列を複製しない
        self._photo.paste(
            Image.frombuffer("RGB", self.show_size, self._rgb_buf, "raw", "RGB", 0, 1)
        )
        if self.im is not self._photo:
            self.im = self._photo
            self.itemconfig(self.im_, image=self._photo)

    def _showDisabled(self) -> None:
        """停止中の画像に切り替える（既に表示中なら何もしない）。"""
        if self.im is not self.disabled_tk:
            self.im = self.disabled_tk
            self.itemconfig(self.im_, image=self.disabled_tk)

    def setFps(self, fps: Any) -> None:
        """描画間隔を設定する。

        interval は float (ms) で保持する。int(1000/fps) で切り捨てると
        30fps→33ms(=30.3fps) のように端数が失われ、そのぶん実機とずれる。
        実際の待ち時間は _next_delay() が経過時間を差し引いて毎回丸める。

        なお Windows のタイマー分解能は約 15.6ms のため、after(16) 相当の
        60fps 指定では1フレームごとの揺れは避けられない。フレーム開始基準に
        することで「揺れても平均周期は保たれる」状態にしている。
        """
        fps_value = max(1, int(fps))
        self.next_frames = 1000.0 / fps_value
        logger.info(f"FPS set to {fps_value} (interval {self.next_frames:.1f} ms)")

    def setShowsize(self, show_height: int, show_width: int) -> None:
        """表示サイズを変更する。PhotoImage も作り直す。"""
        self.show_width = int(show_width)
        self.show_height = int(show_height)
        self.show_size = (self.show_width, self.show_height)
        self.config(width=self.show_width, height=self.show_height)

        self._allocBuffers()
        self._photo = ImageTk.PhotoImage(Image.new("RGB", self.show_size))
        self.disabled_tk = self._loadDisabledImage()
        self.im = self.disabled_tk
        self.itemconfig(self.im_, image=self.disabled_tk)
        logger.info(f"Show size set to {self.show_width} x {self.show_height}")

    def saveCapture(self) -> None:
        """現在のフレームを画像として保存する。"""
        self.camera.saveCapture()

    # ------------------------------------------------------------------
    # 座標変換
    # ------------------------------------------------------------------
    def _captureRatio(self) -> tuple[float, float]:
        """表示座標 → キャプチャ座標 の倍率を返す。

        カメラ未接続時は capture_size が 0 になることがあり、
        そのまま割るとゼロ除算で落ちる。倍率1（等倍）で返して
        座標変換だけは成立させ、描画や保存の呼び出しを止めない。
        """
        return self._ratio(self.camera.capture_size, self.show_size)

    def _showRatio(self) -> tuple[float, float]:
        """キャプチャ座標 → 表示座標 の倍率を返す（_captureRatio の逆）。"""
        return self._ratio(self.show_size, self.camera.capture_size)

    @staticmethod
    def _ratio(numer: Any, denom: Any) -> tuple[float, float]:
        """要素ごとに割り算する。0 で割りそうなときは 1.0 を返す。"""
        def one(a: float, b: float) -> float:
            return float(a) / float(b) if b else 1.0

        return one(numer[0], denom[0]), one(numer[1], denom[1])

    # ------------------------------------------------------------------
    # 範囲スクリーンショット (Ctrl+Shift+ドラッグ)
    # ------------------------------------------------------------------
    def StartRangeSS(self, event: Any) -> None:
        """範囲選択を開始する。"""
        # 選択中フレームを保持するのでコピーを受け取る
        self.ss = self.camera.readFrame(copy=True)
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.UnbindRightClick()

        self.min_x, self.min_y = event.x, event.y
        self.delete("SelectArea")
        self.create_rectangle(
            self.min_x,
            self.min_y,
            self.min_x + 1,
            self.min_y + 1,
            outline="red",
            tag="SelectArea",
        )

        ratio_x, ratio_y = self._captureRatio()
        logger.info(
            "Mouse down: Show ({}, {}) / Capture ({}, {})".format(
                self.min_x,
                self.min_y,
                int(self.min_x * ratio_x),
                int(self.min_y * ratio_y),
            )
        )

        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

    def MotionRangeSS(self, event: Any) -> None:
        """ドラッグ中の選択枠を追従させる。"""
        self.max_x = min(self.show_width, max(0, event.x))
        self.max_y = min(self.show_height, max(0, event.y))
        self.coords(
            "SelectArea", self.min_x, self.min_y, self.max_x + 1, self.max_y + 1
        )

    def ReleaseRangeSS(self, event: Any) -> None:
        """選択範囲を切り出して保存する。"""
        ratio_x, ratio_y = self._captureRatio()
        logger.info(
            "Mouse up: Show ({}, {}) / Capture ({}, {})".format(
                self.max_x,
                self.max_y,
                int(self.max_x * ratio_x),
                int(self.max_y * ratio_y),
            )
        )
        if self.min_x > self.max_x:
            self.min_x, self.max_x = self.max_x, self.min_x
        if self.min_y > self.max_y:
            self.min_y, self.max_y = self.max_y, self.min_y

        self.camera.saveCapture(
            crop=1,
            crop_ax=[
                int(self.min_x * ratio_x),
                int(self.min_y * ratio_y),
                int(self.max_x * ratio_x),
                int(self.max_y * ratio_y),
            ],
        )

        # after には呼び出し可能オブジェクトを渡す（直接呼ぶと即時実行になる）
        self.after(250, lambda: self.delete("SelectArea"))

        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

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
        ratio_x, ratio_y = self._captureRatio()
        px = min(max(int(event.x * ratio_x), 0), frame.shape[1] - 1)
        py = min(max(int(event.y * ratio_y), 0), frame.shape[0] - 1)
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

        スカラー1個の計算に numpy を使うと ufunc のディスパッチが乗り
        math の5〜10倍遅い。ここはマウス Motion のたびに呼ばれるので
        math を使う（numpy は画像処理側だけで使う）。
        """
        dx = event.x - x_init
        dy = y_init - event.y
        angle = math.degrees(math.atan2(dy, dx))
        mag = math.hypot(dx, dy) / self.radius
        return angle, min(max(mag, 0.0), 1.0)

    def _sendStick(self, side: str, angle: float, mag: float) -> None:
        """片側のスティック値を送る。

        2026/08/25 段 II（PORTBACK 5節）: 生の行の組み立てをやめ、
          Sender が持つ姿勢（applyStick）へ申告する形へ変えた。

        なぜ変えるか（SER-06 / SER-07 / ARC-01）:
          旧実装は f"3 8 {xy} 80 80" と行を丸ごと組み立てていた。この行は
          先頭が btn=3 で固定され、もう片方のスティックも 80（中立）で
          埋めている。つまり「自分が知らない項目まで自分の値で上書き」
          していた。結果、
            ・スクリプトが押していたボタンが落ちる（SER-06）
            ・左を倒したまま右を倒せない（SER-07）
          という2つの症状が出ていた。どちらも「全体を送る」ことが原因。

        applyStick は差分の申告なので、触っていない項目（btn / hat /
          もう片方のスティック）は Sender が持つ現在値のまま保たれる。
          これで上の2件が構造的に起きなくなる。

        旧経路への退避:
          Sender が古い版（applyStick を持たない）の場合は、従来どおり
          生の行を送る。段 II の途中でも動き続けるようにするため。
          STRUCTURE 9-5 の「各段で必ず動く形を保つ」に従う。
        """
        x, y = self._stickXY(angle, mag)

        # 段 V-a: 調停の口（source 付き API）を通す。
        #   setStick / sendPosture は「誰の操作か」を Sender へ伝えるので、
        #   入力調停を有効にしたとき、マウス操作を人の手入力として
        #   優先できる。applyStick を直接呼ぶと調停を素通りしていた。
        set_stick = getattr(self.ser, "setStick", None)
        send_posture = getattr(self.ser, "sendPosture", None)
        if callable(set_stick) and callable(send_posture):
            if set_stick(side, x, y, source="mouse"):
                send_posture(source="mouse")
            return

        # 退避1: 段 IV より前の Sender（姿勢はあるが調停の口が無い）
        apply_stick = getattr(self.ser, "applyStick", None)
        build_row = getattr(self.ser, "_buildRow", None)
        if callable(apply_stick) and callable(build_row):
            apply_stick(side, x, y)
            self.ser.writeRow(build_row(), is_show=False)
            return

        # 退避2: 旧 Sender。従来どおり生の行で送る（挙動は変わらない）
        xy = self._stickHex(angle, mag)
        row = f"3 8 {xy} 80 80" if side == "L" else f"3 8 80 80 {xy}"
        self.ser.writeRow(row, is_show=False)

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

        旧 Sender へ退避したときだけ使う。_stickXY と同じ値を返す
          ことを検査で見張る（verify_stage2 相当）。
        """
        x, y = self._stickXY(angle, mag)
        return f"{hex(x)} {hex(y)}"

    def _sendNeutralStick(self, side: str) -> None:
        """片側のスティックだけを中立へ戻して送る。

        2026/08/25 段 II: 旧実装は "3 8 80 80 80 80" を送っていた。
          これは「両方のスティックを中立にし、btn も 3 にする」行で、
          左を離しただけなのに右まで戻し、押しているボタンも消していた。
        離した側だけを中立にすれば、もう片方は倒したまま残る。

        中立行はスティックだけの変化なので Sender の間引きに
          引っかかりうる。離した状態が届かないと倒したままになるため、
          呼び出し側で flushPending して必ず送り切る（従来どおり）。
        """
        # 段 V-a: 離す操作も調停の口を通す（source="mouse"）。
        set_stick = getattr(self.ser, "setStick", None)
        send_posture = getattr(self.ser, "sendPosture", None)
        if callable(set_stick) and callable(send_posture):
            if set_stick(side, 128, 128, source="mouse"):
                send_posture(source="mouse")
            return

        apply_stick = getattr(self.ser, "applyStick", None)
        build_row = getattr(self.ser, "_buildRow", None)
        if callable(apply_stick) and callable(build_row):
            apply_stick(side, 128, 128)
            self.ser.writeRow(build_row(), is_show=False)
            return
        self.ser.writeRow("3 8 80 80 80 80", is_show=False)


    def _drawStick(self, x: int, y: int, color: str, tag: str) -> None:
        """スティックの外周円とノブを描く。"""
        r = self.radius
        k = r // 10
        self.create_oval(x - r, y - r, x + r, y + r, outline=color, tag=tag)
        self.create_oval(x - k, y - k, x + k, y + k, fill=color, tag=tag + "2")

    def _moveKnob(
        self, event: Any, x_init: int, y_init: int, angle: float, mag: float, tag: str
    ) -> None:
        """ノブを移動する。振り切っているときは円周上に貼り付ける。"""
        k = self.radius // 10
        if mag >= 1:
            d = self.radius + self.radius // 11
            rad = math.radians(angle)
            cx = x_init + d * math.cos(rad)
            cy = y_init - d * math.sin(rad)
        else:
            cx, cy = event.x, event.y
        self.coords(tag, cx - k, cy - k, cx + k, cy + k)

    def _pressing(
        self,
        event: Any,
        side: str,
        x_init: int,
        y_init: int,
        prev_angle: float | None,
        prev_mag: float | None,
        rec: _StickRecorder | None,
        tag: str,
    ) -> tuple[float, float]:
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

        self._moveKnob(event, x_init, y_init, angle, mag, tag)
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
        if self.master.is_use_right_stick_mouse.get():
            self.UnbindRightClick()
        self.config(cursor="dot")
        self.lx_init, self.ly_init = event.x, event.y
        self._drawStick(self.lx_init, self.ly_init, "cyan", "lcircle")
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
            "lcircle2",
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
        self.delete("lcircle")
        self.delete("lcircle2")
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()
        if self._lrec is not None:
            self._lrec.flush(self._langle, self._lmag)

    # ------------------------------------------------------------------
    # 右スティック (右ドラッグ)
    # ------------------------------------------------------------------
    def mouseRightPress(self, event: Any, ser: Any = None) -> None:
        """右スティックの操作を開始する。"""
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()
        self.config(cursor="dot")
        self.rx_init, self.ry_init = event.x, event.y
        self._drawStick(self.rx_init, self.ry_init, "red", "rcircle")
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
            "rcircle2",
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
        self.delete("rcircle")
        self.delete("rcircle2")
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self._rrec is not None:
            self._rrec.flush(self._rangle, self._rmag)

    # ------------------------------------------------------------------
    # 画像認識の枠表示
    # ------------------------------------------------------------------
    # 画像認識の枠は固定タグ1組だけを作り、以後は位置と色を更新する。
    # 呼び出しのたびにユニークな tag で作ると、判定をループで回した分
    # だけキャンバスアイテムが積み上がり Canvas の再描画が線形に重くなる。
    RECT_TAG_OUTER = "ImgRectOuter"
    RECT_TAG_INNER = "ImgRectInner"

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
        """キャプチャ座標で指定された矩形を表示座標に直して描く。

        tag は後方互換のため受け取るが、枠は固定タグ1組を使い回すので
        参照しない。消去の予約も after_cancel で前回分を取り消してから
        入れ直すため、予約が積み上がることはない。
        """
        ratio_x, ratio_y = self._showRatio()
        outer = (
            (x1 - 1.0) * ratio_x,
            (y1 - 1.0) * ratio_y,
            (x2 + 1.0) * ratio_x,
            (y2 + 1.0) * ratio_y,
        )
        inner = (x1 * ratio_x, y1 * ratio_y, x2 * ratio_x, y2 * ratio_y)

        if not self._rect_created:
            self.create_rectangle(
                *outer, width=4.5, outline="white", tag=self.RECT_TAG_OUTER
            )
            self.create_rectangle(
                *inner, width=2.5, outline=outline, tag=self.RECT_TAG_INNER
            )
            self._rect_created = True
        else:
            self.coords(self.RECT_TAG_OUTER, *outer)
            self.coords(self.RECT_TAG_INNER, *inner)
            self.itemconfig(self.RECT_TAG_OUTER, state="normal")
            self.itemconfig(self.RECT_TAG_INNER, state="normal", outline=outline)

        if self._rect_after_id is not None:
            self.after_cancel(self._rect_after_id)
        self._rect_after_id = self.after(ms, self.deleteImageRect)

    def deleteImageRect(self, tag: str = "") -> None:
        """ImgRect で描いた枠を隠す（アイテムは消さずに使い回す）。"""
        self._rect_after_id = None
        if self._rect_created:
            self.itemconfig(self.RECT_TAG_OUTER, state="hidden")
            self.itemconfig(self.RECT_TAG_INNER, state="hidden")

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

    def BindRightClick(self) -> None:
        """右ドラッグを右スティックに割り当てる。"""
        self.bind("<ButtonPress-3>", lambda ev: self.mouseRightPress(ev, self.ser))
        self.bind("<Button3-Motion>", lambda ev: self.mouseRightPressing(ev, self.ser))
        self.bind("<ButtonRelease-3>", lambda ev: self.mouseRightRelease(self.ser))
        logger.debug("Bind right click")

    def UnbindLeftClick(self) -> None:
        """左ドラッグの割り当てを外す。"""
        for seq in ("<ButtonPress-1>", "<Button1-Motion>", "<ButtonRelease-1>"):
            self.unbind(seq)
        logger.debug("Unbind left click")

    def UnbindRightClick(self) -> None:
        """右ドラッグの割り当てを外す。"""
        for seq in ("<ButtonPress-3>", "<Button3-Motion>", "<ButtonRelease-3>"):
            self.unbind(seq)
        logger.debug("Unbind right click")


# GUI of switch controller simulator
class ControllerGUI:
    """Switch コントローラを模した操作ウィンドウ."""

    JOYCON_L_COLOR = "#95f1ff"
    JOYCON_R_COLOR = "#ff6b6b"
    BUTTON_BG = "#343434"
    BUTTON_FG = "#fff"

    # (表示名, UnitCommand の属性名, row, column)
    ABXY = (
        ("A", "A", 1, 2),
        ("B", "B", 2, 1),
        ("X", "X", 0, 1),
        ("Y", "Y", 1, 0),
    )
    HAT = (
        ("UP", "UP", 0, 1),
        ("", "UP_RIGHT", 0, 2),
        ("RIGHT", "RIGHT", 1, 2),
        ("", "DOWN_RIGHT", 2, 2),
        ("DOWN", "DOWN", 2, 1),
        ("", "DOWN_LEFT", 2, 0),
        ("LEFT", "LEFT", 1, 0),
        ("", "UP_LEFT", 0, 0),
    )

    # 2026/08/25 段 V-c: 十字キーの押しっぱなしと同時押しの合成表。
    #   Hat は「値」であってビット列ではない（MCU-13 / 段 I と同じ注意）。
    #     ボタンのように OR で足せないため、押している方向の集合から
    #     どの値になるかを引く表を持つ。
    HAT_DIRS = ("UP", "RIGHT", "DOWN", "LEFT")
    HAT_COMBO = {
        (): "CENTER",
        ("UP",): "TOP",
        ("RIGHT",): "RIGHT",
        ("DOWN",): "BTM",
        ("LEFT",): "LEFT",
        ("RIGHT", "UP"): "TOP_RIGHT",
        ("DOWN", "RIGHT"): "BTM_RIGHT",
        ("DOWN", "LEFT"): "BTM_LEFT",
        ("LEFT", "UP"): "TOP_LEFT",
    }

    # 十字キーのボタン名（HAT の2番目の要素）から、押している向きへの対応。
    #   斜めのボタン（UP_RIGHT など）は2方向を同時に押したものとして扱う。
    HAT_NAME2DIRS = {
        "UP": ("UP",),
        "UP_RIGHT": ("UP", "RIGHT"),
        "RIGHT": ("RIGHT",),
        "DOWN_RIGHT": ("DOWN", "RIGHT"),
        "DOWN": ("DOWN",),
        "DOWN_LEFT": ("DOWN", "LEFT"),
        "LEFT": ("LEFT",),
        "UP_LEFT": ("UP", "LEFT"),
    }

    BUTTON_ACTIVE_BG = "#8a8a8a"   # 押している間の色
    # (表示名, UnitCommand の属性名, width, x, y)
    JOYCON_L = (
        ("L", "L", 20, 30, 30),
        ("ZL", "ZL", 20, 30, 0),
        ("LCLICK", "LCLICK", 7, 120, 120),
        ("MINUS", "MINUS", 5, 220, 70),
        ("CAP", "CAPTURE", 5, 200, 270),
    )
    JOYCON_R = (
        ("R", "R", 20, 120, 30),
        ("ZR", "ZR", 20, 120, 0),
        ("RCLICK", "RCLICK", 7, 120, 205),
        ("PLUS", "PLUS", 5, 35, 70),
        ("HOME", "HOME", 5, 50, 270),
    )

    def __init__(self, root: Any, ser: Any) -> None:
        self.ser = ser
        # 2026/08/25 段 V-c: 押しっぱなしに対応するための保持。
        #   従来は tk.Button の command= を使っていた。command は
        #     「離したとき」に1回だけ呼ばれるため、押しっぱなしを
        #     表現できない（ARC-06 の「入口の非対称」）。
        #   押下と解放を別々に受け取り、Sender の姿勢へ差分申告する。
        #     触っていない項目は保たれるので、他のボタンを消さない。
        self._held_btn: dict = {}   # 表示名 -> Keys.Button
        self._held_hat: set = set() # 押している向き（"UP" など）
        self._buttons: dict = {}    # 表示名 -> tk.Button（見た目の反映用）

        self.window = tk.Toplevel(root)
        self.window.title("Switch Controller Simulator")
        # 親ウィンドウの位置から少しずらして出す。geometry の座標は
        # マルチモニタで左/上の画面へ動かすと負になり '600x300-10+20'
        # の形になる。'+' で split すると要素が足りず IndexError で
        # 落ちるため、符号込みで正規表現から取り出す。
        pos = re.search(r"([+-]\d+)([+-]\d+)$", root.geometry())
        root_x = int(pos.group(1)) if pos else 0
        root_y = int(pos.group(2)) if pos else 0
        self.window.geometry("%dx%d%+d%+d" % (600, 300, 250 + root_x, 125 + root_y))
        self.window.resizable(False, False)

        joycon_L_frame = tk.Frame(
            self.window, width=300, height=300, relief="flat", bg=self.JOYCON_L_COLOR
        )
        joycon_R_frame = tk.Frame(
            self.window, width=300, height=300, relief="flat", bg=self.JOYCON_R_COLOR
        )
        hat_frame = tk.Frame(joycon_L_frame, relief="flat", bg=self.JOYCON_L_COLOR)
        abxy_frame = tk.Frame(joycon_R_frame, relief="flat", bg=self.JOYCON_R_COLOR)

        for text, name, row, column in self.ABXY:
            self._makeButton(abxy_frame, text, name).grid(row=row, column=column)
        abxy_frame.place(relx=0.2, rely=0.3)

        for text, name, row, column in self.HAT:
            self._makeButton(hat_frame, text, name).grid(row=row, column=column)
        hat_frame.place(relx=0.2, rely=0.6)

        for text, name, width, x, y in self.JOYCON_L:
            self._makeButton(joycon_L_frame, text, name, width=width).place(x=x, y=y)
        for text, name, width, x, y in self.JOYCON_R:
            self._makeButton(joycon_R_frame, text, name, width=width).place(x=x, y=y)

        joycon_L_frame.grid(row=0, column=0)
        joycon_R_frame.grid(row=0, column=1)

        # button style settings
        for button in abxy_frame.winfo_children():
            self.applyButtonSetting(button)
        for button in hat_frame.winfo_children():
            self.applyButtonSetting(button)
        for frame in (joycon_L_frame, joycon_R_frame):
            for button in frame.winfo_children():
                if isinstance(button, tk.Button):
                    self.applyButtonColor(button)

        logger.debug("Create GUI controller")

    def _makeButton(self, parent: Any, text: str, name: str,
                    **kwargs: Any) -> tk.Button:
        """押している間だけ入力を保持するボタンを作る。

        2026/08/25 段 V-c: command= をやめ、押下と解放を別々に受ける。
          ・command は「離したとき」に1回だけ呼ばれるので押しっぱなしを
            表現できない。<ButtonPress-1> / <ButtonRelease-1> なら
            押した瞬間と離した瞬間の両方を取れる。
          ・従来は UnitCommand が毎回 KeyPress を作り、input → sleep(0.1)
            → inputEnd を同期実行していた。sleep が GUI スレッド（mainloop）
            を止めるため、押すたびに画面が固まっていた。
          押しっぱなしにすると待つ必要そのものが無くなるので、sleep も
            スレッドも要らない。送信は押した瞬間と離した瞬間の2回だけ。

        Leave（押したまま枠外へ出る）でも必ず解放する。これが無いと
          ボタンの上でマウスを離さなかったときに押しっぱなしが残る。
        """
        button = tk.Button(parent, text=text, **kwargs)
        button.bind("<ButtonPress-1>", lambda ev, n=name: self._onPress(n))
        button.bind("<ButtonRelease-1>", lambda ev, n=name: self._onRelease(n))
        button.bind("<Leave>", lambda ev, n=name: self._onRelease(n))
        self._buttons[name] = button
        return button

    # ------------------------------------------------------------------
    # 段 V-c: 押下・解放の受け口
    # ------------------------------------------------------------------
    # どれも Sender の差分申告 API（段 IV）を通す。source="gui" を付ける
    #   ので、入力調停（段 V-a）では人の手入力として扱われる。
    # 申告してから sendPosture で1行にまとめて送る。項目ごとに送ると
    #   行数が増え、SERIAL_OPT の遅延予算に響く。

    def _onPress(self, name: str) -> None:
        """ボタンまたは十字キーを押した。"""
        if name in self.HAT_NAME2DIRS:
            self._pressHat(name)
        else:
            self._pressButton(name)

    def _onRelease(self, name: str) -> None:
        """ボタンまたは十字キーを離した。"""
        if name in self.HAT_NAME2DIRS:
            self._releaseHat(name)
        else:
            self._releaseButton(name)

    def _pressButton(self, name: str) -> None:
        """押していないときだけ申告する（二重押下を無視）。

        同じボタンの ButtonPress が続けて来ても送信は1回で済む。
          イベントの取りこぼしや連打に強くするための保持。
        """
        if name in self._held_btn:
            return
        btn = getattr(Button, name, None)
        if btn is None:
            return
        self._held_btn[name] = btn
        self._setActive(name, True)
        if self.ser.pressButtons([int(btn)], source="gui"):
            self.ser.sendPosture(source="gui")

    def _releaseButton(self, name: str) -> None:
        """押していたものだけ解放する（二重解放を無視）。"""
        btn = self._held_btn.pop(name, None)
        if btn is None:
            return
        self._setActive(name, False)
        if self.ser.releaseButtons([int(btn)], source="gui"):
            self.ser.sendPosture(source="gui")

    def _pressHat(self, name: str) -> None:
        """十字キーを押す。斜めのボタンは2方向を同時に押したものとして扱う。"""
        dirs = self.HAT_NAME2DIRS.get(name, ())
        if self._held_hat.issuperset(dirs):
            return
        self._held_hat.update(dirs)
        self._setActive(name, True)
        self._applyHat()

    def _releaseHat(self, name: str) -> None:
        """十字キーを離す。"""
        dirs = self.HAT_NAME2DIRS.get(name, ())
        if not self._held_hat.intersection(dirs):
            return
        self._held_hat.difference_update(dirs)
        self._setActive(name, False)
        self._applyHat()

    def _hatValue(self) -> Any:
        """押している向きの集合から Hat の値を決める。

        Hat は「値」でありビット列ではない（MCU-13 / 段 I と同じ注意）。
          ボタンのように OR で足せないため、組み合わせを表から引く。
        上下同時・左右同時のように打ち消し合う組み合わせや、3つ以上の
          同時押しは表に無い。その場合は中立へ倒す（実機の十字キーでも
          相反する方向は同時に入らない）。
        """
        keys = tuple(sorted(d for d in self._held_hat if d in self.HAT_DIRS))
        name = self.HAT_COMBO.get(keys, "CENTER")
        return getattr(Hat, name)

    def _applyHat(self) -> None:
        """現在の向きを申告して送る。

        2026/08/25 段 V-d: 押している間は holdHat で「押しっぱなし」と
          して申告し、離すときは releaseHat で取り下げる。
          中立を「値(CENTER)」で送ると、他の系統が押しっぱなしにして
            いる十字キーまで中立へ戻してしまう（B1 の跨ぎ問題）。
          取り下げなら、他が押していればその向きへ戻るだけで済む。
        退避: 古い Sender（holdHat を持たない）なら従来どおり値で送る。
        """
        hold = getattr(self.ser, "holdHat", None)
        release = getattr(self.ser, "releaseHat", None)
        if callable(hold) and callable(release):
            ok = (hold(int(self._hatValue()), source="gui")
                  if self._held_hat else release(source="gui"))
        else:
            ok = self.ser.setHat(int(self._hatValue()), source="gui")
        if ok:
            self.ser.sendPosture(source="gui")

    def _setActive(self, name: str, on: bool) -> None:
        """押している間だけ色を変える（押しっぱなしが目で分かるように）。"""
        button = self._buttons.get(name)
        if button is None:
            return
        try:
            button["bg"] = self.BUTTON_ACTIVE_BG if on else self.BUTTON_BG
        except tk.TclError:
            pass

    def releaseAllHeld(self) -> None:
        """押しているものをすべて離す。

        窓を閉じるときや切断時に必ず呼ぶ。押しっぱなしのまま閉じると、
          解放が届かず Switch 側でボタンが押されたままになる。
        """
        for name in list(self._held_btn):
            self._releaseButton(name)
        for name in list(self.HAT_NAME2DIRS):
            self._setActive(name, False)
        if self._held_hat:
            self._held_hat.clear()
            self._applyHat()

    def bind(self, event: str, func: Any) -> None:
        self.window.bind(event, func)

    def protocol(self, event: str, func: Any) -> None:
        self.window.protocol(event, func)

    def focus_force(self) -> None:
        self.window.focus_force()

    def destroy(self) -> None:
        # 段 V-c: 押しっぱなしのまま閉じると解放が届かず、
        #   Switch 側でボタンが押されたままになる。必ず離してから閉じる。
        self.releaseAllHeld()
        flush = getattr(self.ser, "flushPending", None)
        if callable(flush):
            flush()
        self.window.destroy()
        logger.debug("GUI controller destroyed")


# To avoid the error says 'ScrolledText' object has no attribute 'flush'
class MyScrolledText(ScrolledText):
    """標準出力のリダイレクト先に使うため flush を持たせた ScrolledText."""

    def flush(self) -> None:
        pass
