#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""camera_panel.py - カメラ枠の組み立てとカメラ操作を受け持つMixin.

PokeControllerApp に混ぜて使う（多重継承）。状態は self 越しに
触るため、触る属性は下に宣言しておく（mypy のため。値は Window 側が持つ）。
"""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from typing import Any

import WindowUtils
from GuiAssets import CaptureArea
from core.Camera import Camera
from loguru import logger


class CameraLabelframe(ttk.Labelframe):
    """カメラ枠。マウスでのスティック操作フラグを持つ。

    GuiAssets の CaptureArea が master 経由でこの2つを読む。
    動的に足すと型に見えなくなるため、型として宣言する。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.is_use_left_stick_mouse = tk.BooleanVar()
        self.is_use_right_stick_mouse = tk.BooleanVar()


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
    separator_1: Any
    is_show_realtime: Any
    cb_show_realtime: Any
    separator_2: Any
    capture_f: Any
    captureButton: Any
    open_folder_img: Any
    OpencaptureButton: Any
    camera_f2: Any
    fps_label: Any
    fps: Any
    fps_cb: Any
    separator_3: Any
    show_size_label: Any
    show_size: Any
    show_size_cb: Any
    separator_4: Any
    filt_enabled: Any
    filt_check: Any
    filt_setting_button: Any
    _filt_params: dict[str, Any]
    camera_name_l: Any
    camera_name_fromDLL: Any
    Camera_Name: Any
    show_size_tmp: Any
    Command_nb: Any
    _on_setting_changed: Any

    def _build_camera_frame(self) -> None:
        self.camera_lf = CameraLabelframe(self.frame_1)

        self.camera_id_label = ttk.Label(self.camera_lf)
        self.camera_id_label.config(anchor="center", text="Camera ID:")
        self.camera_id_label.grid(padx="5", sticky="ew")

        self.camera_entry = ttk.Entry(self.camera_lf)
        self.camera_id = tk.IntVar()
        self.camera_entry.config(state="normal", textvariable=self.camera_id)
        self.camera_entry.grid(column=1, padx="5", row=0, sticky="ew")
        self.camera_entry.columnconfigure("1", uniform="0")

        self.reloadButton = ttk.Button(self.camera_lf)
        self.reloadButton.config(text="Reload Camera", command=self.openCamera)
        self.reloadButton.grid(column=2, padx="5", row=0, sticky="ew")

        self.separator_1 = ttk.Separator(self.camera_lf)
        self.separator_1.config(orient="vertical")
        self.separator_1.grid(column=3, row=0, sticky="ns")

        self.is_show_realtime = tk.BooleanVar()
        self.cb_show_realtime = ttk.Checkbutton(self.camera_lf)
        self.cb_show_realtime.config(
            text="Show Realtime",
            variable=self.is_show_realtime,
            command=self._on_setting_changed,
        )
        self.cb_show_realtime.grid(column=4, row=0)

        self.separator_2 = ttk.Separator(self.camera_lf)
        self.separator_2.config(orient="vertical")
        self.separator_2.grid(column=5, row=0, sticky="ns")

        # -- キャプチャ操作
        self.capture_f = ttk.Frame(self.camera_lf)
        self.captureButton = ttk.Button(self.capture_f)
        self.captureButton.config(text="Capture", command=self.saveCapture)
        self.captureButton.grid(column=0, row=0)

        self.open_folder_img = tk.PhotoImage(file=WindowUtils.OPEN_DIR_ICON_PATH)
        self.OpencaptureButton = ttk.Button(self.capture_f)
        self.OpencaptureButton.config(
            image=self.open_folder_img, command=self.OpenCaptureDir
        )
        self.OpencaptureButton.grid(column=1, row=0)
        self.capture_f.grid(column=6, row=0, sticky="ns")

        # -- FPS / 表示サイズ
        self.camera_f2 = ttk.Frame(self.camera_lf)
        self.fps_label = ttk.Label(self.camera_f2)
        self.fps_label.config(text="FPS:")
        self.fps_label.grid(padx="5", sticky="ew")

        self.fps = tk.StringVar()
        self.fps_cb = ttk.Combobox(self.camera_f2)
        # values の int 群は実行時に文字列化される。注釈だけの問題のため無視する。
        self.fps_cb.config(  # type: ignore[call-overload]
            justify="right",
            state="readonly",
            textvariable=self.fps,
            values=WindowUtils.FPS_VALUES,
            width=5,
        )
        self.fps_cb.grid(column=1, padx="10", row=0, sticky="ew")
        self.fps_cb.bind("<<ComboboxSelected>>", self.applyFps, add="")

        self.separator_3 = ttk.Separator(self.camera_f2)
        self.separator_3.config(orient="vertical")
        self.separator_3.grid(column=2, row=0, sticky="ns")

        self.show_size_label = ttk.Label(self.camera_f2)
        self.show_size_label.config(text="Show Size:")
        self.show_size_label.grid(column=3, padx="5", row=0, sticky="ew")

        self.show_size = tk.StringVar()
        self.show_size_cb = ttk.Combobox(self.camera_f2)
        self.show_size_cb.config(
            textvariable=self.show_size,
            state="readonly",
            values=WindowUtils.SHOW_SIZE_VALUES,
        )
        self.show_size_cb.grid(column=4, padx="10", row=0, sticky="ew")
        self.show_size_cb.bind("<<ComboboxSelected>>", self.applyWindowSize, add="")

        self.separator_4 = ttk.Separator(self.camera_f2)
        self.separator_4.config(orient="vertical")
        self.separator_4.grid(column=5, row=0, sticky="ns")

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
        self.filt_check = ttk.Checkbutton(self.camera_f2)
        self.filt_check.config(
            text="表示フィルタ",
            variable=self.filt_enabled,
            command=self.applyPreviewFilter,
        )
        self.filt_check.grid(column=6, row=0)

        self.filt_setting_button = ttk.Button(self.camera_f2)
        self.filt_setting_button.config(text="調整...", command=self.openFilterDialog)
        self.filt_setting_button.grid(column=7, row=0)
        self.camera_f2.grid(column=0, columnspan=8, row=3, sticky="nsew")

        # -- カメラ名
        self.camera_name_l = ttk.Label(self.camera_lf)
        self.camera_name_l.config(anchor="center", text="Camera Name: ")
        self.camera_name_l.grid(column=0, padx="5", row=1, sticky="ew")

        self.camera_name_fromDLL = tk.StringVar()
        self.Camera_Name = ttk.Combobox(self.camera_lf)
        self.Camera_Name.config(state="readonly", textvariable=self.camera_name_fromDLL)
        self.Camera_Name.grid(column=1, columnspan=6, padx="5", row=1, sticky="ew")
        self.Camera_Name.bind("<<ComboboxSelected>>", self.set_cameraid, add="")

        self.camera_lf.config(height=200, text="Camera", width=200)
        self.camera_lf.grid(columnspan=3, padx="5", sticky="ew")

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
            self.camera_entry.config(state="disable")
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
        self.camera = Camera(self._current_fps())
        self.openCamera()

    def _build_preview(self) -> None:
        width, height = map(int, self.show_size.get().split("x"))
        self.preview = CaptureArea(
            self.camera,
            self._current_fps(),
            self.is_show_realtime,
            self.serial.sender,
            self.camera_lf,
            width,
            height,
            self.settings.is_take_stick_log.get(),
        )
        self.preview.config(cursor="crosshair")
        self.preview.grid(
            column=0, columnspan=7, row=2, padx="5", pady="5", sticky=tk.NSEW
        )

        # 復元したチェック状態を実際のマウス操作へ反映する。設定を読んだ
        # だけではバインドされないため、起動直後は「チェックが入っている
        # のにマウスで動かせない」状態になっていた。
        self.preview.ApplyLStickMouse()
        self.preview.ApplyRStickMouse()

    def openCamera(self) -> bool:
        """選択中のカメラへ切り替え、成功時だけ True を返す。"""
        if self.camera is None:
            return False
        try:
            cam_id = self.camera_id.get()
        except (tk.TclError, ValueError):
            return False
        if self.camera_dic is not None and self.camera_dic.get(cam_id) == "Disable":
            with self._camera_open_lock:
                self.camera.destroy()
            print("カメラを無効にしました")
            logger.info("Camera is disabled")
            return True
        self._camera_open_seq = int(getattr(self, "_camera_open_seq", 0)) + 1
        with self._camera_open_lock:
            opened = self.camera.openCamera(cam_id)
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

    def applyWindowSize(self, event: Any = None) -> None:
        """表示サイズを変更する。キャンセルされたら元に戻す。"""
        if self.preview is None:
            return
        current_index = self.show_size_cb["values"].index(self.show_size_cb.get())
        if self.show_size_tmp == current_index:
            return

        width, height = map(int, self.show_size.get().split("x"))
        self.preview.setShowsize(height, width)

        if tkmsg.askokcancel("確認", "この画面サイズに変更しますか？"):
            self.show_size_tmp = current_index
            self._on_setting_changed()
        else:
            self.show_size_cb.current(self.show_size_tmp)
            width_bef, height_bef = map(int, self.show_size.get().split("x"))
            self.preview.setShowsize(height_bef, width_bef)

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
