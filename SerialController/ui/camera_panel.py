#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""camera_panel.py - カメラ枠の組み立てとカメラ操作を受け持つMixin.

PokeControllerApp に混ぜて使う（多重継承）。状態は self 越しに
触るため、触る属性は下に宣言しておく（mypy のため。値は Window 側が持つ）。
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
from typing import Any

import WindowUtils
from GuiAssets import CaptureArea
from core.Camera import Camera
from core.display_mode import (
    DEFAULT_SHOW_MODE,
    DEFAULT_SHOW_SIZE,
    SHOW_MODES,
    SHOW_SIZES,
    layout_plan,
)
from loguru import logger
from ui.combo_fit import fit_combo_width

# _on_camera_opened へ渡すまでの再試行上限。mainloop は構築直後に始まるため
# 通常は 1 回で通る。待つのは裏スレッドであり GUI は止めない。
_OPENED_HANDOFF_TIMEOUT_S = 10.0
_OPENED_HANDOFF_RETRY_S = 0.05
# openCamera() が錠を待つ上限。裏の CameraOpener が握ったままなのは、
# 開けない機器への cv2.VideoCapture() が driver の中で止まっている場合。
# 無限に待つと GUI スレッドが駐車して起動時フリーズに見えるため上限を置く。
# 実測の正規 open は約 3 秒なので余裕を持たせている。
_CAMERA_OPEN_LOCK_TIMEOUT_S = 10.0


class CameraLabelframe(ttk.Labelframe):
    """カメラ枠。マウスでのスティック操作フラグを持つ。

    GuiAssets の CaptureArea が master 経由でこの2つを読む。
    動的に足すと型に見えなくなるため、型として宣言する。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.is_use_left_stick_mouse = tk.BooleanVar()
        self.is_use_right_stick_mouse = tk.BooleanVar()


def _load_icon(master: Any, path: str) -> tk.PhotoImage:
    """アイコン画像を読む。中身は Python で読み、Tk には data で渡す。

    file= で渡すと Tk が自分でファイルを開く。テストの出力取り込み
    （pytest の fd 差し替え）と重なると、まれに空の TclError で失敗した。
    """
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read())
    return tk.PhotoImage(master=master, data=data)


def _camera_title(label: str) -> str:
    """状態表示に出すカメラ名。末尾の識別子（' [fbad85]'）は省く。"""
    return re.sub(r"\s*\[[^\]]*\]$", "", label).strip()


class CameraPanelMixin:
    """カメラパネルMixin。単体では使わない。"""

    frame_1: Any
    root: Any
    settings: Any
    os_name: str
    camera: Camera | None
    camera_dic: dict[int, str] | None
    camera_keys: dict[int, str]
    camera_key: Any
    _camera_labels: list[str]
    preview: Any
    serial: Any
    camera_lf: Any
    camera_id_label: Any
    camera_entry: Any
    camera_id: Any
    reloadButton: Any
    is_show_realtime: Any
    cb_show_realtime: Any
    capture_f: Any
    camera_toolbar: Any
    camera_notice: Any
    camera_notice_label: Any
    camera_notice_button: Any
    tab_camera: Any
    tab_audio: Any
    setting_nb: Any
    captureButton: Any
    open_folder_img: Any
    OpencaptureButton: Any
    fps: Any
    show_size: Any
    show_mode: Any
    filt_enabled: Any
    filt_check: Any
    filt_setting_button: Any
    renderer: Any
    _filt_params: dict[str, Any]
    _camera_open_lock: Any
    _camera_open_seq: int
    camera_name_l: Any
    camera_name_fromDLL: Any
    Camera_Name: Any
    Command_nb: Any
    layout_mode: Any
    applyProfileColor: Any
    _on_setting_changed: Any
    _apply_content_minsize: Any
    _apply_layout: Any

    def _build_camera_frame(self) -> None:
        self.camera_lf = CameraLabelframe(self.frame_1)

        # プレビューの上は、見ながら何度も使う操作だけにする（プレビュー表示の
        # 入/切・キャプチャ・保存先）。カメラの選択と表示フィルタは滅多に
        # 触らないのでカメラタブ（_build_camera_tab）へ置く。1 行に全部並べると
        # 窓が狭いときにカメラ名が潰れて読めなかった。
        self.camera_toolbar = ttk.Frame(self.camera_lf)
        self.camera_toolbar.grid(
            column=0, columnspan=9, row=0, padx="5", pady=(0, 2), sticky="ew"
        )

        # プレビュー描画の入/切（切ると描画の負荷が無くなる。取り込みは続く）。
        self.is_show_realtime = tk.BooleanVar()
        self.cb_show_realtime = ttk.Checkbutton(self.camera_toolbar)
        self.cb_show_realtime.config(
            text="プレビュー表示",
            variable=self.is_show_realtime,
            command=self._on_setting_changed,
        )
        self.cb_show_realtime.pack(side="left")

        # カメラを開けないときだけ出す知らせと、選び直す場所への近道。
        # ツールバーの中に置くので、コンパクト表示でツールバーごと隠れる。
        self.camera_notice = ttk.Frame(self.camera_toolbar)
        self.camera_notice_label = ttk.Label(
            self.camera_notice, text="", foreground="#b3261e"
        )
        self.camera_notice_label.pack(side="left")
        self.camera_notice_button = ttk.Button(
            self.camera_notice, text="カメラタブを開く", command=self.selectCameraTab
        )
        self.camera_notice_button.pack(side="left", padx=(6, 0))

        # -- キャプチャ操作（右端）
        self.capture_f = ttk.Frame(self.camera_toolbar)
        self.captureButton = ttk.Button(self.capture_f)
        self.captureButton.config(text="キャプチャ", command=self.saveCapture)
        self.captureButton.pack(side="left")

        self.open_folder_img = _load_icon(
            self.camera_lf, WindowUtils.OPEN_DIR_ICON_PATH
        )
        self.OpencaptureButton = ttk.Button(self.capture_f)
        self.OpencaptureButton.config(
            image=self.open_folder_img, command=self.OpenCaptureDir
        )
        self.OpencaptureButton.pack(side="left")
        self.capture_f.pack(side="right")

        # 表示専用フィルタ（パラメータはiniへ保存、ON/OFFはセッションのみ）。
        # 起動時は常にOFF（不意の加工表示を避ける）。
        # 組立時点では settings がまだ無い（Window が _build_ui の後で
        # loadSettings する）ため、ここでは中立値で置く。設定ファイルの
        # 値は _apply_settings_to_widgets 経由の _applyFilterSettings で
        # 流し込む（他の tk 変数と同じ手順）。
        self._filt_params = {
            "gamma": 1.0,
            "contrast": 0.0,
            "brightness": 0,
            "saturation": 1.0,
            "hue_shift": 0,
            "lower": [0, 0, 0],
            "upper": [179, 255, 255],
            "mode": "gray_out",
        }
        self.filt_enabled = tk.BooleanVar(value=False)

        # カメラの選択の値。選ぶ部品はカメラタブに置く（_build_camera_tab）。
        self.camera_name_fromDLL = tk.StringVar()
        self.camera_id = tk.IntVar()

        # FPS と描画方式の値。選ぶ画面は表示設定ダイアログ、反映は
        # applyDisplaySettings 経由の applyFps / applyRenderer。
        self.fps = tk.StringVar()
        self.renderer = tk.StringVar()

        # プレビューの表示モードと固定サイズ。選ぶ画面は Menubar の
        # 表示設定ダイアログ（カメラ欄にはコンボを置かない）。ここには
        # 値だけ持ち、決めるのはダイアログ、反映は applyDisplaySettings。
        self.show_mode = tk.StringVar()
        self.show_size = tk.StringVar()

        self.camera_lf.config(height=200, text="カメラ", width=200)
        # 置き場所は仕切り（ui/pane_layout.py）が決める。ここでは grid しない。

    def _build_camera_tab(self) -> None:
        """カメラタブ。カメラの選択と表示フィルタを置く。

        取り込み機器の設定（カメラ・オーディオ）を隣に並べるため、
        オーディオタブの前へ入れる。setting_nb ができた後に呼ぶ。
        """
        self.tab_camera = ttk.Frame(self.setting_nb)
        self.setting_nb.insert(self.tab_audio, self.tab_camera, text="カメラ")
        lf = ttk.Labelframe(self.tab_camera, text="カメラ")
        lf.pack(fill="both", expand=True, padx=5, pady=5)
        lf.columnconfigure(1, weight=1)

        # 表示名は「番号: 機器名 [識別子]」（WindowUtils.cameraLabel）。番号が
        # 入るので、名前を出せる環境では Camera ID 欄を別に出さない。
        self.camera_name_l = ttk.Label(lf, text="カメラ: ")
        self.camera_name_l.grid(column=0, padx="5", pady="2", row=0, sticky="w")
        self.Camera_Name = ttk.Combobox(lf, width=16)
        self.Camera_Name.config(state="readonly", textvariable=self.camera_name_fromDLL)
        self.Camera_Name.grid(column=1, padx="5", pady="2", row=0, sticky="ew")
        self.Camera_Name.bind("<<ComboboxSelected>>", self.set_cameraid, add="")

        self.reloadButton = ttk.Button(lf)
        self.reloadButton.config(text="再読み込み", command=self.reloadCameraFromTab)
        self.reloadButton.grid(column=2, padx="5", pady="2", row=0, sticky="w")

        # Camera ID。カメラ名を取れない環境（Linux 等）でだけ使う。
        # 出し入れは _setup_camera_name が決める。
        self.camera_id_label = ttk.Label(lf, text="カメラ ID: ")
        self.camera_id_label.grid(column=0, padx="5", pady="2", row=1, sticky="w")
        self.camera_entry = ttk.Entry(lf)
        self.camera_entry.config(state="normal", textvariable=self.camera_id, width=6)
        self.camera_entry.grid(column=1, padx="5", pady="2", row=1, sticky="w")

        ttk.Label(lf, text="表示フィルタ: ").grid(
            column=0, padx="5", pady="2", row=2, sticky="w"
        )
        filt_row = ttk.Frame(lf)
        filt_row.grid(column=1, columnspan=2, padx="5", pady="2", row=2, sticky="w")
        self.filt_check = ttk.Checkbutton(filt_row)
        self.filt_check.config(
            text="使う",
            variable=self.filt_enabled,
            command=self.applyPreviewFilter,
        )
        self.filt_check.pack(side="left")
        self.filt_setting_button = ttk.Button(filt_row)
        self.filt_setting_button.config(text="調整...", command=self.openFilterDialog)
        self.filt_setting_button.pack(side="left", padx=(8, 0))

    def selectCameraTab(self) -> None:
        """カメラタブを開く（カメラを開けないときの近道）。"""
        try:
            self.setting_nb.select(self.tab_camera)
        except (tk.TclError, AttributeError):
            pass

    def reloadCameraFromTab(self) -> None:
        """カメラタブの再読み込み。開けたかどうかを知らせへ反映する。"""
        self.openCamera()

    def _show_camera_state(
        self, ok: bool, cam_id: int, *, disabled: bool = False
    ) -> None:
        """カメラの状態をプレビューの上の知らせと状態表示へ出す。

        開けないときはプレビューが止まった絵のままになり、理由が分からない。
        プレビューの上に理由と、選び直す場所（カメラタブ）への近道を出す。
        """
        notice = getattr(self, "camera_notice", None)
        status = getattr(self, "status_camera", None)
        if ok or disabled:
            if notice is not None:
                notice.pack_forget()
            if disabled:
                text = "カメラ: 無効"
            else:
                # 組み立て前（部品の無い呼び出し）でも落とさない。
                label_var = getattr(self, "camera_name_fromDLL", None)
                name = _camera_title(label_var.get()) if label_var is not None else ""
                text = f"カメラ: {name or f'ID {cam_id}'}"
        else:
            if notice is not None:
                self.camera_notice_label.config(
                    text=f"⚠ カメラ（ID {cam_id}）を開けません"
                )
                notice.pack(side="left", padx=(12, 0))
            text = "カメラ: 開けません"
        if status is not None:
            status.set(text)

    def _setup_camera_name(self) -> None:
        """OS ごとにカメラ名コンボボックスの扱いを切り替える。

        旧コードは if / elif が両方 != "Linux" で、elif が到達不能だった。
        """
        if self.os_name == "Linux":
            self.camera_name_fromDLL.set(
                "Linux environment. So that cannot show Camera name."
            )
            self.Camera_Name.config(state="disable")
            # 旧コードはここで __init__ ごと return していたためカメラもシリアルも
            # 初期化されず起動不能だった。カメラ ID を手入力すれば使えるので続行する。
            # ただし手入力を適用する口が無く、打っても反映されなかった。
            # Enter とフォーカス移動で適用する。打鍵ごとに適用すると、
            # 「12」と打ちたいのに「1」の時点で開きに行ってしまう。
            self._bindCameraEntry()
            return

        if self.os_name not in ("Windows", "Darwin"):
            self.camera_name_fromDLL.set(
                "Unknown environment. Cannot show Camera name."
            )
            self.Camera_Name.config(state="disable")
            self._bindCameraEntry()
            return

        try:
            self.locateCameraCmbbox()
            # 一覧の表示名に番号が入っているので、ID 欄は二重表示になる。
            self.camera_id_label.grid_remove()
            self.camera_entry.grid_remove()
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            message = (
                "An error occurred when displaying the camera name in the Win/Mac "
                "environment."
            )
            self.camera_name_fromDLL.set(message)
            logger.warning(message)
            self.Camera_Name.config(state="disable")

    def _bindCameraEntry(self) -> None:
        """Camera ID を手入力で適用できるようにする。

        カメラ名の一覧を出せない環境（Linux / 未知の OS）では、ID を
        直接打つ以外に選ぶ手段が無い。Entry は state="normal" のままで
        打てるが、適用する契機がどこにも無かったため打っても何も
        起きなかった。

        適用の契機は Enter とフォーカス移動にする。打鍵ごとに開きに
        行くと、「12」と打ちたいのに「1」の時点で別のカメラを掴む。
        """
        self.camera_entry.bind("<Return>", self._onCameraEntryApplied, add="+")
        self.camera_entry.bind("<FocusOut>", self._onCameraEntryApplied, add="+")

    def _onCameraEntryApplied(self, *event: Any) -> None:
        """入力された Camera ID を実際に開いて設定へ残す。

        空や数値以外なら何もしない。打ちかけでフォーカスが外れた
        だけの場合に、0 番のカメラを開きに行かないようにするため。
        """
        cam_id = self._cameraIdOrNone()
        if cam_id is None:
            return
        if self.openCamera():
            self._on_setting_changed()

    def _current_fps(self) -> int:
        try:
            fps = int(self.fps.get())
        except (TypeError, ValueError):
            fps = 45
        if fps not in WindowUtils.FPS_VALUES:
            fps = 45
        self.fps.set(str(fps))
        return fps

    def _current_renderer(self) -> str:
        """描画方式を候補表の値で返す。表に無ければ既定（auto）へ戻す。"""
        renderer = self.renderer.get()
        if renderer not in WindowUtils.RENDERER_VALUES:
            renderer = "auto"
        self.renderer.set(renderer)
        return renderer

    def _cameraIdOrNone(self) -> int | None:
        """Camera ID を通常の int で返す。空や不正なら None を返す。

        IntVar は中身が空文字や非数値のとき get() が TclError を投げる。
        Entry を空にした状態で設定を変えると、そこから先の処理が
        まとめて落ちる。呼ぶ側が毎回 try で囲むのではなく、取り出す
        場所を1つにしてそこで守る。
        """
        try:
            return int(self.camera_id.get())
        except (tk.TclError, ValueError):
            return None

    def _start_camera(self) -> None:
        """カメラ枠を作り、デバイス開きは裏で始める（起動を止めない）。

        開く前の readFrame は None を返し、プレビューは無効絵のまま
        待つ。開けたら描画ループが自動で拾う。失敗は GUI スレッドへ
        戻して知らせる（ワーカーから widget は触らない）。
        """
        self.camera = Camera(self._current_fps())
        try:
            cam_id = self.camera_id.get()
        except (tk.TclError, ValueError):
            return
        if self.camera_dic is not None and self.camera_dic.get(cam_id) == "Disable":
            self.camera.destroy()
            print("カメラを無効にしました")
            logger.info("Camera is disabled")
            self._show_camera_state(False, cam_id, disabled=True)
            return
        self._camera_open_seq = int(getattr(self, "_camera_open_seq", 0)) + 1
        thread = threading.Thread(
            target=self._open_camera_bg,
            args=(cam_id, self._camera_open_seq),
            name="CameraOpener",
            daemon=True,
        )
        thread.start()

    def _open_camera_bg(self, cam_id: int, seq: int) -> None:
        """裏で開く。結果は GUI スレッドへ戻す。"""
        camera = self.camera
        if camera is None:
            return
        try:
            with self._camera_open_lock:
                ok = camera.openCamera(cam_id)
        except Exception as e:
            logger.warning(f"カメラを開く裏処理で例外: {e}")
            ok = False
        self._hand_off_open_result(camera, cam_id, seq, ok)

    def _hand_off_open_result(
        self, camera: Any, cam_id: int, seq: int, ok: bool
    ) -> None:
        """開結果を GUI スレッドへ渡す。mainloop 開始前なら始まるまで待つ。

        ``after`` は mainloop 開始前に呼ぶと ``RuntimeError`` を投げる
        （``TclError`` ではない）。ここで落とすと結果受けが走らず、失敗時の
        通知と陳腐カメラの破棄が抜ける。待つのは裏スレッドであり、起動自体は
        止めない。試行中に終了すれば開いた物だけ閉じて抜ける。
        """
        deadline = time.monotonic() + _OPENED_HANDOFF_TIMEOUT_S
        while True:
            try:
                self.root.after(0, self._on_camera_opened, cam_id, seq, camera, ok)
                return
            except tk.TclError:
                # 終了済み。開いてしまった物は閉じる（保持リーク防止）。
                try:
                    camera.destroy()
                except Exception:
                    pass
                return
            except RuntimeError:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "カメラ open 結果の受け渡しを断念: mainloop が "
                        f"{_OPENED_HANDOFF_TIMEOUT_S} 秒待っても始まらない"
                    )
                    try:
                        camera.destroy()
                    except Exception:
                        pass
                    return
                time.sleep(_OPENED_HANDOFF_RETRY_S)

    def _on_camera_opened(self, cam_id: int, seq: int, camera: Any, ok: bool) -> None:
        """裏開きの結果受け（GUI スレッド）。古ければ捨てる。"""
        if getattr(self, "_closing", False):
            try:
                camera.destroy()
            except Exception:
                pass
            return
        if (
            seq != int(getattr(self, "_camera_open_seq", 0))
            or camera is not self.camera
        ):
            # 取り直し済み。別物のまま開いていたら閉じる。
            # 同一物は取り直し側が所有しているため触らない。
            if camera is not self.camera and ok:
                try:
                    camera.destroy()
                except Exception:
                    pass
            return
        try:
            current = self.camera_id.get()
        except (tk.TclError, ValueError):
            return
        if current != cam_id:
            return
        self._show_camera_state(ok, cam_id)
        if not ok:
            message = f"Camera ID {cam_id} cannot open."
            print(message)
            logger.error(message)

    def _build_preview(self) -> None:
        # プレビューの要求サイズは core.display_mode に任せる。show_size を
        # 直接解釈しない（fit では使わない値になるため）。レイアウトが
        # コンパクト / プレビューのときはそちらの要求が勝つ（layout_plan）。
        layout = self._current_preview_layout()
        width, height = layout.request_size
        self.preview = CaptureArea(
            self.camera,
            self._current_fps(),
            self.is_show_realtime,
            self.serial.sender,
            self.camera_lf,
            width,
            height,
            self.settings.is_take_stick_log.get(),
            renderer=self.settings.renderer.get(),
        )
        self.preview.config(cursor="crosshair")
        self.preview.grid(
            column=0, columnspan=9, row=2, padx="5", pady="5", sticky=tk.NSEW
        )

        # 復元したチェック状態を実際のマウス操作へ反映する。設定を読んだ
        # だけではバインドされないため、起動直後は「チェックが入っている
        # のにマウスで動かせない」状態になっていた。
        self.preview.ApplyLStickMouse()
        self.preview.ApplyRStickMouse()

        # 作った直後に配置を確定する（モードが fit ならここから伸びる）。
        self._apply_preview_layout()

    def _acquire_open_lock(self) -> bool:
        """カメラ open の錠を有限待ちで取る。取れなければ False。

        裏の CameraOpener が握ったまま返さないのは、開けない機器への
        cv2.VideoCapture() が driver の中で止まっている場合。この meth は
        GUI スレッドから呼ばれるため無限待ちは駐車＝フリーズに見える。
        取れなくても裏は続行し、終われば after 経由の結果は seq 世代で
        捨てられるので、ここで断るのは安全。
        """
        if self._camera_open_lock.acquire(timeout=_CAMERA_OPEN_LOCK_TIMEOUT_S):
            return True
        message = (
            "カメラを開く処理が使用中です。別の open が終わるまで待ってから "
            "Reload をもう一度押してください"
        )
        print(message)
        logger.warning(message)
        return False

    def openCamera(self) -> bool:
        """選択中のカメラへ切り替え、成功時だけ True を返す。

        表（Reload 等）からの同期実行。裏の初回開きと錠で直列化し、
        番号を進めて裏の古い結果を捨てる。
        """
        if self.camera is None:
            return False
        try:
            cam_id = self.camera_id.get()
        except (tk.TclError, ValueError):
            return False
        if self.camera_dic is not None and self.camera_dic.get(cam_id) == "Disable":
            if not self._acquire_open_lock():
                return False
            try:
                self.camera.destroy()
            finally:
                self._camera_open_lock.release()
            print("カメラを無効にしました")
            logger.info("Camera is disabled")
            self._show_camera_state(False, cam_id, disabled=True)
            return True
        self._camera_open_seq = int(getattr(self, "_camera_open_seq", 0)) + 1
        if not self._acquire_open_lock():
            return False
        try:
            opened = self.camera.openCamera(cam_id)
        finally:
            self._camera_open_lock.release()
        self._show_camera_state(bool(opened), cam_id)
        if opened:
            return True
        message = f"Camera ID {cam_id} cannot open."
        print(message)
        logger.error(message)
        return False

    def assignCamera(self, event: Any = None) -> None:
        """入力途中のIDで表示名だけ追随させる。切替と保存は行わない。"""
        try:
            cam_id = int(self.camera_entry.get().strip())
        except (TypeError, ValueError, tk.TclError):
            return
        if self.camera_dic is not None and cam_id in self.camera_dic:
            self.Camera_Name.current(cam_id)

    def applyCameraId(self, event: Any = None) -> str:
        """Return/FocusOutでIDを検証し、切替成功時だけ保存する。"""
        previous = self.settings.camera_id.get()
        try:
            cam_id = int(self.camera_entry.get().strip())
        except (TypeError, ValueError, tk.TclError):
            print("Camera IDが不正です。元の値へ戻します")
            self.camera_id.set(previous)
            self.assignCamera()
            return "break"
        if cam_id < 0 or (
            self.camera_dic is not None and cam_id not in self.camera_dic
        ):
            print("Camera IDが範囲外です。元の値へ戻します")
            self.camera_id.set(previous)
            self.assignCamera()
            return "break"
        self.camera_id.set(cam_id)
        self.camera_key.set(self.camera_keys.get(cam_id, ""))
        self.assignCamera()
        if self.openCamera():
            self._on_setting_changed()
            return "break"
        self.camera_id.set(previous)
        self.camera_key.set(self.camera_keys.get(previous, ""))
        self.assignCamera()
        self.openCamera()
        return "break"

    def locateCameraCmbbox(self) -> None:
        """接続されているカメラを列挙してコンボボックスへ入れる。

        同型のキャプチャボードを複数挿すと Name が完全に一致するため、
        名前だけでは選んだ台が分からない。表示は必ず先頭に番号を付け、
        Windows では DevicePath から作った短い識別子も添える。
        """
        devices: list[tuple[str, str]] = []  # (名前, 識別子)

        if self.os_name == "Windows":
            import clr

            # カレントディレクトリ基準の相対指定だと、起動場所が違うだけで
            # 見つけられない。このファイルの場所から解決する。
            dll_path = os.path.normpath(
                os.path.join(
                    WindowUtils.APP_DIR, "..", "DirectShowLib", "DirectShowLib-2005"
                )
            )
            clr.AddReference(dll_path)
            # clr で読み込む .NET アセンブリであり、スタブには見えない。
            from DirectShowLib import (  # type: ignore[attr-defined]
                DsDevice,
                FilterCategory,
            )

            captureDevices = DsDevice.GetDevicesOfCat(FilterCategory.VideoInputDevice)
            for device in captureDevices:
                # DevicePath は USB のポートごとに異なる。差し直さない限り不変
                path = getattr(device, "DevicePath", "") or ""
                devices.append((device.Name, WindowUtils.deviceKey(path)))

        elif self.os_name == "Darwin":
            cmd = (
                'system_profiler SPCameraDataType | grep "^    [^ ]" '
                '| sed "s/    //" | sed "s/://" '
            )
            res = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
            ret = res.stdout.decode("utf-8")
            # macOS からは固有の識別子が取れないので番号だけで区別する
            devices = [(line, "") for line in ret.split("\n") if line]

        else:
            return

        self.camera_dic = {i: name for i, (name, _) in enumerate(devices)}
        self.camera_keys = {i: key for i, (_, key) in enumerate(devices)}
        disable_id = len(devices)
        self.camera_dic[disable_id] = "Disable"
        self.camera_keys[disable_id] = ""

        # 表示名は必ず一意にする。番号が入るので同名でも取り違えない
        self._camera_labels = [
            WindowUtils.cameraLabel(i, name, self.camera_keys[i])
            for i, name in self.camera_dic.items()
        ]
        self.Camera_Name["values"] = self._camera_labels
        fit_combo_width(self.Camera_Name, min_chars=16, max_chars=32)
        logger.debug(f"Camera list: {self._camera_labels}")

        dev_num = len(devices)
        if dev_num == 0:
            print("No camera devices can be found.")
            logger.warning("No camera devices can be found.")

        # 同じ物理ボードを掴み直せるよう、まず識別子で照合する。
        # 認識順は起動のたびに入れ替わることがあるため番号だけでは足りない。
        saved_key = self.camera_key.get()
        matched = None
        if saved_key:
            for cam_id, key in self.camera_keys.items():
                if key and key == saved_key:
                    matched = cam_id
                    break
        if matched is None:
            matched = self.camera_id.get()
        if not 0 <= matched <= disable_id:
            print("Inappropriate camera ID! -> set to 0")
            logger.warning("Inappropriate camera ID! -> set to 0")
            matched = 0

        self.camera_id.set(matched)
        self.camera_key.set(self.camera_keys.get(matched, ""))
        self.camera_entry.bind("<KeyRelease>", self.assignCamera)
        self.camera_entry.bind("<Return>", self.applyCameraId)
        self.camera_entry.bind("<FocusOut>", self.applyCameraId)
        self.Camera_Name.current(matched)

    def set_cameraid(self, event: Any = None) -> None:
        """カメラ名の選択を検証し、成功した場合だけ設定へ保存する。"""
        if self.camera_dic is None:
            return
        index = self.Camera_Name.current()
        if index < 0 or index not in self.camera_dic:
            message = "カメラの選択が不正です。現在のカメラを使い続けます"
            print(message)
            logger.warning(message)
            return
        previous = self.settings.camera_id.get()
        self.camera_id.set(index)
        self.camera_key.set(self.camera_keys.get(index, ""))
        if self.openCamera():
            self._on_setting_changed()
            return
        self.camera_id.set(previous)
        self.camera_key.set(self.camera_keys.get(previous, ""))
        self.assignCamera()
        self.openCamera()

    def saveCapture(self) -> None:
        """画面の1枚を保存し、結果をログ欄へ知らせる。

        押した本人に成否が伝わらないと「効いていない」と区別が付かない。
        loguru は stderr へ書くのでログ欄には出ない。ここは print を使う
        （sys.stdout が LogPane 経由でログ欄へ流れている）。
        """
        if self.camera is None or not self.camera.isOpened():
            print("キャプチャできません: カメラが開いていません")
            return
        if self.camera.saveCapture():
            saved_to = getattr(self.camera, "capture_dir", "Captures")
            print(f"キャプチャを保存しました: {saved_to}")
        else:
            print("キャプチャに失敗しました（詳細はターミナルのログ）")

    # ------------------------------------------------------------------
    # 各種設定の変更
    # ------------------------------------------------------------------

    def applyFps(self, event: Any = None) -> None:
        fps = self._current_fps()
        print(f"changed FPS to: {fps} [fps]")
        if self.preview is not None:
            self.preview.setFps(fps)
        if self.camera is not None:
            self.camera.setFps(fps)
        self._on_setting_changed()

    def applyRenderer(self, event: Any = None) -> None:
        """描画方式の選択を保存し、次回起動から効くことをログ欄へ知らせる。

        面は起動時に1回だけ作られる（差し替える口が無い）ので、反映は
        再起動に任せる。選択のたびにダイアログは出さない：tkmsg は同期で、
        開いた瞬間 GUI スレッドが止まる。sys.stdout は LogPane が拾って
        いるので、案内は print だけで足りる。
        """
        renderer = self._current_renderer()
        self.settings.renderer.set(renderer)
        print(f"描画方式を {renderer} に変更しました（再起動後に反映されます）")
        self._on_setting_changed()

    def applyBaudRate(self, event: Any = None) -> None:
        """Baud Rate の選択は受け取らない（意図的に何もしない）。

        画面の Combobox は state="disabled" にしてあり、そもそも選び
        直せない。ここが空なのは実装漏れではなく、その状態に合わせて
        いる。

        Switch の自動化では 9600 から変える理由が無い。一方 GameCube
        の自動化では別の速度を使うが、その利用者は Switch 側の数百分の
        一で、かつ自分でスクリプトを直せる人に限られる。画面から触れる
        ようにすると、多数派である Switch の利用者が誤って変更し、
        「繋がらない」という問い合わせだけが増える。

        速度を変える必要がある場合は settings.ini の baud_rate を直接
        書き換える。Settings は候補で縛らず value > 0 だけを検査するの
        で、任意の値がそのまま通る。マイコン側の SERIAL_BAUD と同じ値
        にすること。片方だけ変えると文字が化けて一切通信できない。
        """
        pass

    def applyDisplaySettings(
        self,
        mode: str,
        size: str,
        color: str | None = None,
        fps: str | None = None,
        renderer: str | None = None,
    ) -> None:
        """表示モードと固定サイズを検証してプレビューへ反映し、保存する。

        確認ダイアログは出さない。拡大縮小は即時で、選び直せば元へ戻るため
        「取り消す」手順が要らない。tkmsg は同期なので、選ぶたびに出すと
        GUI スレッドがそこで止まる（描画まで止まる）。

        Menubar の表示設定ダイアログから呼ばれる。値の補正はここで一度だけ
        行う。color / fps / renderer を省いた呼び出しではそれらを変えない。
        FPS と描画方式は値が変わったときだけ従来の applyFps / applyRenderer
        を通す（変わらないのに「再起動後に反映」と案内しない）。
        """
        if fps is not None and str(fps) != str(self.fps.get()):
            self.fps.set(str(fps))
            self.applyFps()
        if renderer is not None and renderer != self.renderer.get():
            self.renderer.set(renderer)
            self.applyRenderer()
        self.show_mode.set(mode if mode in SHOW_MODES else DEFAULT_SHOW_MODE)
        self.show_size.set(size if size in SHOW_SIZES else DEFAULT_SHOW_SIZE)
        if color is not None:
            # 色だけ先に反映する。applyProfileColor の中で帯と色チップが
            # 更新され、そのまま保存まで済みます（保存の入口は 1 つに保つ）。
            self.applyProfileColor(color)
        # fit / 固定で仕切りの取り分も変わるので、配置ごと組み直す。
        self._apply_layout()
        self._apply_content_minsize()
        self._on_setting_changed()

    def _current_preview_layout(self) -> Any:
        """今のレイアウト・表示モードで、プレビューへ要求する配置を返す。

        判断は core.display_mode.layout_plan（1 箇所）。ここが show_mode を
        解釈し直すと、コンパクト時の要求と食い違う。
        """
        return layout_plan(
            self.layout_mode.get(), self.show_mode.get(), self.show_size.get()
        ).preview

    def _apply_preview_layout(self) -> None:
        """プレビューの要求サイズと伸び方を、現在のレイアウトに合わせて設定する。

        伸びない要求は要求サイズのまま上寄せ。伸ばす要求は枠いっぱいまで
        伸ばし、16:9 を保ったまま中央寄せさせる。カメラ枠の外側（仕切りの中で
        どれだけ取り分を持つか）は layout_panel._apply_layout が決める。
        """
        preview = self.preview
        if preview is None:
            return
        layout = self._current_preview_layout()
        width, height = layout.request_size
        preview.setShowsize(height, width)
        if layout.stretch:
            preview.grid_configure(sticky="nsew")
            self.camera_lf.rowconfigure(2, weight=1)
            self.camera_lf.columnconfigure(1, weight=1)
        else:
            preview.grid_configure(sticky="n")
            self.camera_lf.rowconfigure(2, weight=0)
            self.camera_lf.columnconfigure(1, weight=0)

    def _applyFilterSettings(self) -> None:
        """設定ファイルの表示フィルタ値をパネル側の辞書へ流し込む。

        Window._apply_settings_to_widgets から呼ぶ（他の tk 変数と
        同じ手順）。不正値は GuiSettings 生成時の complete_missing で
        既定へ戻っている前提だが、読む側でも型だけ整える。
        """
        self._filt_params = {
            "gamma": float(self.settings.filt_gamma.get()),
            "contrast": float(self.settings.filt_contrast.get()),
            "brightness": int(self.settings.filt_brightness.get()),
            "saturation": float(self.settings.filt_saturation.get()),
            "hue_shift": int(self.settings.filt_hue_shift.get()),
            "lower": [
                int(self.settings.filt_lower_h.get()),
                int(self.settings.filt_lower_s.get()),
                int(self.settings.filt_lower_v.get()),
            ],
            "upper": [
                int(self.settings.filt_upper_h.get()),
                int(self.settings.filt_upper_s.get()),
                int(self.settings.filt_upper_v.get()),
            ],
            "mode": str(self.settings.filt_mode.get()),
        }

    def applyPreviewFilter(self) -> None:
        """表示専用フィルタのON/OFFをプレビューへ反映する。"""
        if self.preview is None:
            return
        if bool(self.filt_enabled.get()):
            params = self._filt_params
            self.preview.setPreviewFilter(
                True,
                list(params["lower"]),
                list(params["upper"]),
                str(params["mode"]),
                {
                    "gamma": float(params["gamma"]),
                    "contrast": float(params["contrast"]),
                    "brightness": int(params["brightness"]),
                    "saturation": float(params["saturation"]),
                    "hue_shift": int(params["hue_shift"]),
                },
            )
        else:
            self.preview.clearPreviewFilter()

    def openFilterDialog(self) -> None:
        """表示フィルタ設定ダイアログを開く。閉じても設定は保持される。

        調整中はプレビューへ即時反映するが、iniへの保存はしない。
        保存はこの窓を閉じたときとアプリ終了時だけにする（操作のたびに
        書くと高頻度すぎるため）。終了時は Window.exit 経由の
        _save_settings がパネル側の辞書を書き出す。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("表示フィルタ設定")
        params = self._filt_params
        # スライダーの初期値はパネル側の辞書（＝設定ファイルの内容）。
        # 刻みは項目で決める（値の型では決めない。丸めでfloat化するため）。
        corr_specs = (
            ("gamma", "ガンマ", 0.1, 3.0, 0.01),
            ("contrast", "コントラスト", -2.0, 2.0, 0.01),
            ("brightness", "輝度", -100, 100, 1),
            ("saturation", "彩度", 0.0, 3.0, 0.01),
            ("hue_shift", "色相シフト", -90, 90, 1),
        )
        hsv_specs = (
            ("lower_H", "下限 H", 0, 179, 1, params["lower"][0]),
            ("lower_S", "下限 S", 0, 255, 1, params["lower"][1]),
            ("lower_V", "下限 V", 0, 255, 1, params["lower"][2]),
            ("upper_H", "上限 H", 0, 179, 1, params["upper"][0]),
            ("upper_S", "上限 S", 0, 255, 1, params["upper"][1]),
            ("upper_V", "上限 V", 0, 255, 1, params["upper"][2]),
        )
        scales: dict[str, tk.Scale] = {}

        def _add_section(title: str, row: int) -> int:
            ttk.Label(dlg, text=title, font=("", 10, "bold")).grid(
                column=0, row=row, columnspan=2, padx=5, pady=(8, 0), sticky="w"
            )
            return row + 1

        def _add_scale(
            key: str,
            label: str,
            lo: float,
            hi: float,
            step: float,
            init: float,
            row: int,
        ) -> int:
            ttk.Label(dlg, text=label).grid(column=0, row=row, padx=5, sticky="ew")
            sc = tk.Scale(
                dlg,
                from_=lo,
                to=hi,
                resolution=step,
                orient=tk.HORIZONTAL,
                length=200,
            )
            sc.set(init)
            sc.grid(column=1, row=row, padx=5, sticky="ew")
            scales[key] = sc
            return row + 1

        row = _add_section("色補正（抽出より先にかかる）", 0)
        for key, label, lo, hi, step in corr_specs:
            row = _add_scale(key, label, lo, hi, step, float(params[key]), row)
        row = _add_section("色抽出", row)
        for key, label, lo, hi, step, init in hsv_specs:
            row = _add_scale(key, label, lo, hi, step, init, row)

        mode_var = tk.StringVar(value=str(params["mode"]))

        def _on_change(*_args: Any) -> None:
            params["gamma"] = round(float(scales["gamma"].get()), 2)
            params["contrast"] = round(float(scales["contrast"].get()), 2)
            params["brightness"] = int(scales["brightness"].get())
            params["saturation"] = round(float(scales["saturation"].get()), 2)
            params["hue_shift"] = int(scales["hue_shift"].get())
            params["lower"] = [
                int(scales["lower_H"].get()),
                int(scales["lower_S"].get()),
                int(scales["lower_V"].get()),
            ]
            params["upper"] = [
                int(scales["upper_H"].get()),
                int(scales["upper_S"].get()),
                int(scales["upper_V"].get()),
            ]
            params["mode"] = mode_var.get()
            if bool(self.filt_enabled.get()):
                self.applyPreviewFilter()

        for sc in scales.values():
            sc.config(command=lambda _v: _on_change())

        ttk.Radiobutton(
            dlg,
            text="対象外をグレー化",
            variable=mode_var,
            value="gray_out",
            command=_on_change,
        ).grid(column=0, columnspan=2, row=row, sticky="w")
        row += 1
        ttk.Radiobutton(
            dlg,
            text="マスク表示",
            variable=mode_var,
            value="mask",
            command=_on_change,
        ).grid(column=0, columnspan=2, row=row, sticky="w")
        row += 1

        def _reset_defaults() -> None:
            """全11項目＋モードを既定に戻す（ON/OFFは変えない）。"""
            from core import preview_filter as _pf

            for key, value in _pf.DEFAULT_CORRECTION.items():
                scales[key].set(value)
            scales["lower_H"].set(0)
            scales["lower_S"].set(0)
            scales["lower_V"].set(0)
            scales["upper_H"].set(179)
            scales["upper_S"].set(255)
            scales["upper_V"].set(255)
            mode_var.set("gray_out")
            _on_change()

        ttk.Button(dlg, text="既定に戻す", command=_reset_defaults).grid(
            column=0, columnspan=2, row=row, pady=8
        )

        def _on_close() -> None:
            """窓を閉じるときだけ保存する。保存の失敗で閉じるのを止めない。"""
            try:
                # _save_settings がパネル側の辞書を書き出す入口。
                self._on_setting_changed()
            finally:
                dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _on_close)

    def OpenCaptureDir(self) -> None:
        WindowUtils.openDirectory(
            os.path.join(WindowUtils.APP_DIR, "Captures"), self.os_name
        )

    def OpenCommandDir(self) -> None:
        if self.Command_nb.index("current") == 0:  # type: ignore
            directory = os.path.join(WindowUtils.APP_DIR, "Commands", "PythonCommands")
        else:
            directory = os.path.join(WindowUtils.APP_DIR, "Commands", "McuCommands")
        WindowUtils.openDirectory(directory, self.os_name)
