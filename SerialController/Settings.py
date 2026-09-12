#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import os
import tkinter as tk
from typing import Any

import config as config
from loguru import logger


class GuiSettings:
    # プロファイル未指定のときのファイル名。従来と同じ場所・同じ名前。
    SETTING_PATH = os.path.join(os.path.dirname(__file__), "settings.ini")

    @staticmethod
    def sanitize_profile(name: Any) -> str:
        """プロファイル名をファイル名に使える形へ正規化する。実体は config。

        後方互換のため名前だけ残す。新規のコードは config 側から読むこと。
        """
        return config.sanitize_profile(name)

    def __init__(self, profile: str = "") -> None:
        """profile を渡すと settings.<profile>.ini を読み書きする。

        同じPCで複数の Poke-Controller を並列起動するとき、既定の
        settings.ini を共有すると互いの設定を上書きし合うため分ける。
        未指定なら従来どおり settings.ini を使う。
        """
        self.profile = self.sanitize_profile(profile)
        self.setting_path = self._path_for(self.profile)

        self.setting: configparser.ConfigParser = configparser.ConfigParser()
        self.setting.optionxform = str  # type: ignore[assignment]

        if not os.path.exists(self.setting_path):
            logger.debug(
                f"設定ファイルが無いため既定値で生成します: {self.setting_path}"
            )
            self.generate()
        self.load()
        self._complete_missing()

        general = self.setting["General Setting"]
        self.camera_id = tk.IntVar(value=general.getint("camera_id"))
        # 同型キャプチャボードを見分けるための識別子（Windows のみ取得できる）
        self.camera_key = tk.StringVar(value=general.get("camera_key", fallback=""))
        self.com_port = tk.IntVar(value=general.getint("com_port"))
        self.com_port_name = tk.StringVar(value=general.get("com_port_name"))
        self.baud_rate = tk.IntVar(value=general.getint("baud_rate"))
        self.fps = tk.IntVar(value=general.getint("fps"))
        self.show_size = tk.StringVar(value=general.get("show_size"))
        self.is_show_realtime = tk.BooleanVar(
            value=general.getboolean("is_show_realtime")
        )
        self.is_show_serial = tk.BooleanVar(value=general.getboolean("is_show_serial"))
        self.is_use_keyboard = tk.BooleanVar(
            value=general.getboolean("is_use_keyboard")
        )
        # マウスでのスティック操作。GUI のチェックボックスと1対1で対応する
        self.is_use_left_stick_mouse = tk.BooleanVar(
            value=general.getboolean("is_use_left_stick_mouse")
        )
        self.is_use_right_stick_mouse = tk.BooleanVar(
            value=general.getboolean("is_use_right_stick_mouse")
        )
        # スティック操作の軌跡を CSV へ書き出すか（旧 GuiAssets.isTakeLog）
        self.is_take_stick_log = tk.BooleanVar(
            value=general.getboolean("is_take_stick_log")
        )

        # ウィンドウの位置とサイズ。並列起動時に台ごとの配置を覚えておく
        window = self.setting["Window"]
        self.window_geometry = tk.StringVar(value=window.get("geometry"))
        self.restore_geometry = tk.BooleanVar(
            value=window.getboolean("restore_geometry")
        )
        # ログ欄の仕切り位置。画素ではなく割合で持つ（ウィンドウの
        # 大きさが変わっても同じ見た目の比率を保つため）
        self.log_sash_ratio = tk.DoubleVar(value=window.getfloat("log_sash_ratio"))

        # 入力ログ。従来は Sender.py の定数を書き換える必要があった
        input_log = self.setting["Input Log"]
        self.input_log_format = tk.StringVar(value=input_log.get("format"))
        self.input_log_enabled = tk.BooleanVar(value=input_log.getboolean("enabled"))
        self.input_log_stick_change = tk.BooleanVar(
            value=input_log.getboolean("log_stick_change")
        )
        # 記録する操作の絞り込み。空なら書式ごとの既定に従う
        self.input_log_actions = tk.StringVar(
            value=input_log.get("actions", fallback="")
        )

        # 通信方式（Transport）のプリセット。
        # 名前だけを持つ。実装の対応表は Transport.py の登録簿にある。
        # 知らない名前でも黙って直さない。ここは設定ファイルの内容を
        # そのまま持ち、既定へ落とす判断は使う側が理由つきで行う。
        transport = self.setting["Transport"]
        self.transport_name = tk.StringVar(
            value=transport.get("name", fallback="legacy_text")
        )
        # 利用者が自作の Transport を置くフォルダ。空なら読み込まない
        self.transport_plugin_dir = tk.StringVar(
            value=transport.get("plugin_dir", fallback="")
        )
        # ライブ入力の最低保持ミリ秒（ini＋既定のみ。画面欄は作らない）
        self.live_min_dwell_ms = tk.IntVar(
            value=transport.getint("live_min_dwell_ms", fallback=24)
        )

        # 入力調停（誰の操作を優先するか）。
        # 既定は off で本家と同じ挙動。script を選ぶと実行中の
        # 手操作を断り、一時停止すれば操作できるようになる。
        # 知らない名前でも黙って直さない。ここは設定ファイルの内容を
        # そのまま持ち、既定へ落とす判断は使う側が理由つきで行う。
        arbitration = self.setting["Arbitration"]
        self.arbitration_mode = tk.StringVar(
            value=arbitration.get("mode", fallback="off")
        )
        # 横取りが続く秒数。文字列で持ち、使う側で数へ直す
        self.arbitration_cooldown = tk.StringVar(
            value=arbitration.get("cooldown", fallback="2.0")
        )

        # Pokemon Home用の設定
        home = self.setting["Pokemon Home"]
        self.season = tk.StringVar(value=home.get("Season"))
        self.is_SingleBattle = tk.StringVar(value=home.get("Single or Double"))

        # 音声（キャプチャボード音声の取込・モニター再生）。
        # デバイス名は起動時に列挙して選ぶ。ここはファイルの内容を
        # そのまま持つ（Transport と同じく、使う側が理由つきで落とす）。
        audio = self.setting["Audio"]
        self.audio_input = tk.StringVar(value=audio.get("input_device", fallback=""))
        self.audio_output = tk.StringVar(value=audio.get("output_device", fallback=""))
        self.audio_monitor_enabled = tk.BooleanVar(
            value=audio.getboolean("monitor_enabled", fallback=False)
        )
        self.audio_monitor_volume = tk.DoubleVar(
            value=audio.getfloat("monitor_volume", fallback=0.8)
        )

        # メイン画面の表示専用フィルタ（色補正＋色抽出）。
        # パラメータだけを保存し、ON/OFFは持たない（起動時は常にOFF）。
        # 不正値は complete_missing 側で既定へ戻っている前提だが、
        # 読む側でも fallback を付けて二重に守る。
        filt = self.setting["PreviewFilter"]
        self.filt_gamma = tk.DoubleVar(value=filt.getfloat("gamma", fallback=1.0))
        self.filt_contrast = tk.DoubleVar(value=filt.getfloat("contrast", fallback=0.0))
        self.filt_brightness = tk.IntVar(value=filt.getint("brightness", fallback=0))
        self.filt_saturation = tk.DoubleVar(
            value=filt.getfloat("saturation", fallback=1.0)
        )
        self.filt_hue_shift = tk.IntVar(value=filt.getint("hue_shift", fallback=0))
        self.filt_lower_h = tk.IntVar(value=filt.getint("lower_h", fallback=0))
        self.filt_lower_s = tk.IntVar(value=filt.getint("lower_s", fallback=0))
        self.filt_lower_v = tk.IntVar(value=filt.getint("lower_v", fallback=0))
        self.filt_upper_h = tk.IntVar(value=filt.getint("upper_h", fallback=179))
        self.filt_upper_s = tk.IntVar(value=filt.getint("upper_s", fallback=255))
        self.filt_upper_v = tk.IntVar(value=filt.getint("upper_v", fallback=255))
        self.filt_mode = tk.StringVar(value=filt.get("mode", fallback="gray_out"))

    # キーコンフィグが扱うセクション。KeyConfig / Keyboard の双方が参照する。
    # 実体は config が持つ。ここでは同じ名前で読めるようにしておく。
    # ここを直接 configparser で書き換えると他の設定を巻き戻すため、
    # 読み書きは必ず load_key_map() / update_key_map() を通す。
    KEYMAP_SECTIONS: tuple = config.KEYMAP_SECTIONS

    def load_key_map(self, section: str) -> dict[str, str]:
        """KeyMap セクションを {項目名: 割り当てキー} で返す。"""
        if section not in self.KEYMAP_SECTIONS:
            raise ValueError(f"KeyMap のセクション名ではありません: {section}")
        if not self.setting.has_section(section):
            logger.warning(f"{section} がありません。既定値を返します")
            defaults = self._default_sections().get(section, {})
            return {k: str(v) for k, v in defaults.items()}
        return dict(self.setting[section])

    def load_all_key_maps(self) -> dict[str, dict[str, str]]:
        """全 KeyMap セクションをまとめて返す。"""
        return {s: self.load_key_map(s) for s in self.KEYMAP_SECTIONS}

    def update_key_map(self, key_maps: dict[str, dict[str, str]]) -> None:
        """キー割り当てだけを差し替えて保存する。

        キーコンフィグ画面は本体とは別の GuiSettings インスタンスなので、
        こちらが持つ General / Window などは「画面を開いた時点」の内容で
        古い。save() を通すと、その古い値で本体側の変更を巻き戻す。
        逆に本体が save() すると、こちらが保存した割り当てが消える
        （「キーコンフィグが毎回リセットされる」の正体）。

        そこで、ここではファイルを読み直したうえで KeyMap-* だけを
        差し替えて書き出す。触っていない設定には一切手を付けない。
        """
        latest = configparser.ConfigParser()
        latest.optionxform = str  # type: ignore[assignment]
        if os.path.isfile(self.setting_path):
            latest.read(self.setting_path, encoding="utf-8")

        for section, values in key_maps.items():
            if section not in self.KEYMAP_SECTIONS:
                raise ValueError(f"KeyMap のセクション名ではありません: {section}")
            if not latest.has_section(section):
                latest.add_section(section)
            for name, value in values.items():
                latest[section][name] = str(value)

        saved, self.setting = self.setting, latest
        try:
            self._write_ini()
        finally:
            self.setting = saved
        # 自分のメモリ上にも反映しておく（画面を開き直さずに済む）
        self._reload_key_maps()
        logger.debug(f"キー割り当てを保存しました: {list(key_maps)}")

    def reset_key_map(self) -> dict[str, dict[str, str]]:
        """キー割り当てを既定値へ戻して保存し、その内容を返す。"""
        defaults = {
            s: {k: str(v) for k, v in self._default_sections()[s].items()}
            for s in self.KEYMAP_SECTIONS
        }
        self.update_key_map(defaults)
        return defaults

    def _reload_key_maps(self) -> None:
        """KeyMap-* だけをファイルから読み直す。

        キーコンフィグ画面は別の GuiSettings インスタンスで保存するため、
        こちらのメモリ上の KeyMap は古いままになる。その状態で save() を
        呼ぶと、保存したばかりの割り当てを起動時の内容へ巻き戻してしまう
        （「キーコンフィグが毎回リセットされる」の正体）。
        """
        if not os.path.isfile(self.setting_path):
            return
        latest = configparser.ConfigParser()
        latest.optionxform = str  # type: ignore[assignment]
        if not latest.read(self.setting_path, encoding="utf-8"):
            return
        for section in self.KEYMAP_SECTIONS:
            if not latest.has_section(section):
                continue
            if not self.setting.has_section(section):
                self.setting.add_section(section)
            for name, value in latest.items(section):
                self.setting[section][name] = value

    @classmethod
    def _path_for(cls, profile: str) -> str:
        """プロファイル名から settings ファイルのパスを作る。実体は config。"""
        return config.profile_path(cls.SETTING_PATH, profile)

    def load(self) -> None:
        """設定ファイルを読み込む。壊れていれば既定値補完へ渡す。"""
        if not os.path.isfile(self.setting_path):
            logger.warning(f"設定ファイルがありません: {self.setting_path}")
            return
        try:
            loaded = self.setting.read(self.setting_path, encoding="utf-8")
        except (configparser.Error, OSError) as exc:
            logger.error(f"設定ファイルを読めないため既定値を使います: {exc}")
            self.setting.clear()
            return
        if not loaded:
            logger.error(f"設定ファイルを読み込めませんでした: {self.setting_path}")

    def _complete_missing(self) -> None:
        """不足項目と不正値を既定値へ補正する。判断は config が持つ。"""
        changed = config.complete_missing(self.setting)
        if changed:
            logger.info(f"設定を既定値で補正しました: {changed}")
            self._write_ini()
        self._migrate_legacy_keymap()

    def _migrate_legacy_keymap(self) -> None:
        """KeyMap の旧プレースホルダを未割当に直す。判断は config が持つ。"""
        changed = config.migrate_legacy_keymap(self.setting)
        if changed:
            logger.info(f"旧形式のキー割り当てを未割当に直しました: {changed}")
            self._write_ini()

    @staticmethod
    def _default_sections() -> dict[str, dict[str, Any]]:
        """既定値。実体は config が持つ（呼び出すたびに新しい辞書）。

        後方互換のため名前だけ残す。新規のコードは config 側から読むこと。
        """
        return config.default_sections()

    def generate(self) -> None:
        """既定値で settings.ini を新規作成する。"""
        logger.info("既定の設定ファイルを作成します")
        for section, values in self._default_sections().items():
            self.setting[section] = {k: str(v) for k, v in values.items()}
        self._write_ini()

    @staticmethod
    def _str_values(values: dict[str, Any]) -> dict[str, str]:
        """ini へ書けるよう値を文字列へ揃える。実体は config が持つ。"""
        return config.str_values(values)

    def save(self, path: str | None = None) -> None:
        # tkinter の変数はそのまま書けないので値を取り出して詰め直す。
        # KeyMap-* は tk 変数を持たないのでここでは組み直さない。ただし
        # メモリ上の内容は「読み込んだ時点」のもので、その後にキーコンフィグ
        # 画面（別インスタンス）が保存した割り当てを、古い内容で上書きして
        # 戻してしまう。書き出す直前にファイル側を読み直して合わせる。
        self._reload_key_maps()

        self.setting["General Setting"] = self._str_values(
            {
                "camera_id": self.camera_id.get(),
                "camera_key": self.camera_key.get(),
                "com_port": self.com_port.get(),
                "com_port_name": self.com_port_name.get(),
                "baud_rate": self.baud_rate.get(),
                "fps": self.fps.get(),
                "show_size": self.show_size.get(),
                "is_show_realtime": self.is_show_realtime.get(),
                "is_show_serial": self.is_show_serial.get(),
                "is_use_keyboard": self.is_use_keyboard.get(),
                "is_use_left_stick_mouse": self.is_use_left_stick_mouse.get(),
                "is_use_right_stick_mouse": self.is_use_right_stick_mouse.get(),
                "is_take_stick_log": self.is_take_stick_log.get(),
            }
        )
        self.setting["Window"] = self._str_values(
            {
                "geometry": self.window_geometry.get(),
                "restore_geometry": self.restore_geometry.get(),
                "log_sash_ratio": self.log_sash_ratio.get(),
            }
        )
        self.setting["Input Log"] = self._str_values(
            {
                "format": self.input_log_format.get(),
                "enabled": self.input_log_enabled.get(),
                "log_stick_change": self.input_log_stick_change.get(),
                "actions": self.input_log_actions.get(),
            }
        )
        self.setting["Transport"] = {
            "name": self.transport_name.get(),
            "plugin_dir": self.transport_plugin_dir.get(),
            "live_min_dwell_ms": str(self.live_min_dwell_ms.get()),
        }
        self.setting["Arbitration"] = {
            "mode": self.arbitration_mode.get(),
            "cooldown": self.arbitration_cooldown.get(),
        }
        self.setting["Audio"] = self._str_values(
            {
                "input_device": self.audio_input.get(),
                "output_device": self.audio_output.get(),
                "monitor_enabled": self.audio_monitor_enabled.get(),
                "monitor_volume": self.audio_monitor_volume.get(),
            }
        )
        self.setting["PreviewFilter"] = self._str_values(
            {
                "gamma": self.filt_gamma.get(),
                "contrast": self.filt_contrast.get(),
                "brightness": self.filt_brightness.get(),
                "saturation": self.filt_saturation.get(),
                "hue_shift": self.filt_hue_shift.get(),
                "lower_h": self.filt_lower_h.get(),
                "lower_s": self.filt_lower_s.get(),
                "lower_v": self.filt_lower_v.get(),
                "upper_h": self.filt_upper_h.get(),
                "upper_s": self.filt_upper_s.get(),
                "upper_v": self.filt_upper_v.get(),
                "mode": self.filt_mode.get(),
            }
        )

        # pokemon home用の設定
        self.setting["Pokemon Home"] = {
            "Season": self.season.get(),
            "Single or Double": self.is_SingleBattle.get(),
        }

        self._write_ini(path)
        logger.debug("設定ファイルを保存しました")

    def _write_ini(self, path: str | None = None) -> None:
        """一時ファイルへ書いてから置き換える（書き込み中の中断で設定を失わないため）。"""
        path = path or self.setting_path
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            self.setting.write(file)
        os.replace(tmp_path, path)  # 同一ボリューム上の原子的な置き換え
        # 0o600 は posix のみ有効。Windows では何もしない。
        # 失敗しても設定自体は書けているので落とさずログだけ残す。
        # （mac/linux でも落ちないようにするため）
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError as e:
                logger.debug(f"chmod を省略: {e}")
