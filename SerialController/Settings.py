#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import hashlib
import os
import re
import tkinter as tk
from typing import Any, Dict, Optional

from loguru import logger


class GuiSettings:
    # プロファイル未指定のときのファイル名。従来と同じ場所・同じ名前。
    SETTING_PATH = os.path.join(os.path.dirname(__file__), "settings.ini")

    @staticmethod
    def sanitize_profile(name: Any) -> str:
        """プロファイル名をファイル名に使える形へ正規化する。

        利用者が任意の文字を渡せるため、素通しにすると
        --profile ../../foo のような指定でフォルダ外へ書けてしまう。
        英数字と _ - 以外は _ に潰し、全て落ちる名前（日本語のみ等）は
        ハッシュで代替して一意性を保つ。
        """
        if not name:
            return ""
        cleaned = re.sub(r"[^0-9A-Za-z_-]", "_", str(name)).strip("_")
        cleaned = re.sub(r"_+", "_", cleaned)
        if not cleaned:
            digest = hashlib.md5(str(name).encode("utf-8")).hexdigest()
            cleaned = "p" + digest[:8]
        return cleaned[:32]

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

    # キーコンフィグが扱うセクション。KeyConfig / Keyboard の双方が参照する。
    # ここを直接 configparser で書き換えると他の設定を巻き戻すため、
    # 読み書きは必ず load_key_map() / update_key_map() を通す。
    KEYMAP_SECTIONS: tuple = ("KeyMap-Button", "KeyMap-Direction", "KeyMap-Hat")

    def load_key_map(self, section: str) -> Dict[str, str]:
        """KeyMap セクションを {項目名: 割り当てキー} で返す。"""
        if section not in self.KEYMAP_SECTIONS:
            raise ValueError(f"KeyMap のセクション名ではありません: {section}")
        if not self.setting.has_section(section):
            logger.warning(f"{section} がありません。既定値を返します")
            defaults = self._default_sections().get(section, {})
            return {k: str(v) for k, v in defaults.items()}
        return dict(self.setting[section])

    def load_all_key_maps(self) -> Dict[str, Dict[str, str]]:
        """全 KeyMap セクションをまとめて返す。"""
        return {s: self.load_key_map(s) for s in self.KEYMAP_SECTIONS}

    def update_key_map(self, key_maps: Dict[str, Dict[str, str]]) -> None:
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

    def reset_key_map(self) -> Dict[str, Dict[str, str]]:
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
        """プロファイル名から settings ファイルのパスを作る。"""
        if not profile:
            return cls.SETTING_PATH
        root, ext = os.path.splitext(cls.SETTING_PATH)
        return f"{root}.{profile}{ext}"

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
        """不足項目と不正値を既定値へ補正する。"""
        defaults = self._default_sections()
        changed: list[str] = []
        for section, values in defaults.items():
            if not self.setting.has_section(section):
                self.setting[section] = {k: str(v) for k, v in values.items()}
                changed.append(section)
                continue
            for key, value in values.items():
                if key not in self.setting[section]:
                    self.setting[section][key] = str(value)
                    changed.append(f"{section}.{key}")
        general = self.setting["General Setting"]
        integer_rules = {
            "camera_id": (0, lambda value: value >= 0),
            "com_port": (0, lambda value: value >= 0),
            "baud_rate": (9600, lambda value: value > 0),
            "fps": (45, lambda value: value in (5, 15, 30, 45, 60)),
        }
        for key, (default, valid) in integer_rules.items():
            try:
                value = int(general.get(key, ""))
            except (TypeError, ValueError):
                value = None
            if value is None or not valid(value):
                general[key] = str(default)
                changed.append(f"General Setting.{key}")
        valid_sizes = ("640x360", "1280x720", "1920x1080")
        if general.get("show_size", "") not in valid_sizes:
            general["show_size"] = "640x360"
            changed.append("General Setting.show_size")
        # Transport / Arbitration もここで直す。読む側（Sender）で落ちると
        # 起動直後の接続で例外になり、原因が設定だと分かりにくい。
        # name の存在確認まではしない（利用者定義の Transport が足される
        # ため）。空だけ既定へ戻す。
        if self.setting.has_section("Transport"):
            transport = self.setting["Transport"]
            if not transport.get("name", "").strip():
                transport["name"] = "legacy_text"
                changed.append("Transport.name")
        if self.setting.has_section("Arbitration"):
            arb = self.setting["Arbitration"]
            if arb.get("mode", "").strip().lower() not in ("off", "human", "script"):
                arb["mode"] = "off"
                changed.append("Arbitration.mode")
            try:
                cooldown = float(arb.get("cooldown", ""))
            except (TypeError, ValueError):
                cooldown = None
            if cooldown is None or cooldown < 0:
                arb["cooldown"] = "2.0"
                changed.append("Arbitration.cooldown")
        if changed:
            logger.info(f"設定を既定値で補正しました: {changed}")
            self._write_ini()
        self._migrate_legacy_keymap()

    def _migrate_legacy_keymap(self) -> None:
        """KeyMap に残った旧プレースホルダ（10000 等の数値）を未割当に直す。

        旧版は Hat/Direction の既定値に 10000・20001 といった数値を入れて
        いた。これはキーボードのどのキーとも対応しない見せかけの値で、
        Keyboard 側は解釈できず毎回 WARNING を出す（実害は無いが、起動の
        たび13行流れて本当の警告が埋もれる）。_complete_missing は「無い
        キー」しか補わないため、既に値がある以上そのまま残ってしまう。
        数値だけの値は未割当（空文字）とみなして書き換える。利用者が
        自分で割り当てた値は "a" や "Key.up" の形なので巻き込まない。
        """
        changed = []
        for section in self.KEYMAP_SECTIONS:
            if not self.setting.has_section(section):
                continue
            for key, value in self.setting.items(section):
                stripped = value.strip()
                # "1" のような1文字の数字は実際に押せるキーなので残す。
                # 旧プレースホルダは 10000・20001 のように複数桁だけ。
                if len(stripped) > 1 and stripped.isdigit():
                    self.setting[section][key] = ""
                    changed.append(f"{section}.{key}")
        if changed:
            logger.info(f"旧形式のキー割り当てを未割当に直しました: {changed}")
            self._write_ini()

    @staticmethod
    def _default_sections() -> Dict[str, Dict[str, Any]]:
        """既定値。generate() と _complete_missing() の両方がここを参照する。"""
        return {
            "General Setting": {
                "camera_id": 0,
                "camera_key": "",
                "com_port": 0,
                "com_port_name": "",
                "baud_rate": 9600,
                "fps": 45,
                "show_size": "640x360",
                "is_show_realtime": True,
                "is_show_serial": False,
                "is_use_keyboard": True,
                "is_use_left_stick_mouse": False,
                "is_use_right_stick_mouse": False,
                "is_take_stick_log": False,
            },
            "Window": {
                # 空文字なら OS 任せ（従来どおりの位置に出る）
                "geometry": "",
                "restore_geometry": True,
                # ログ欄の仕切り位置(0.0〜1.0)。上側の占める割合
                "log_sash_ratio": 0.6,
            },
            "Input Log": {
                # プリセット名（simple / detail / compact / csv / command / raw）
                # かテンプレート文字列そのもの。詳細は InputLog.py の冒頭
                "format": "simple",
                "enabled": True,
                "log_stick_change": False,
                # 記録する操作。PRESS,RELEASE,CHANGE をカンマ区切りで。
                # 空なら書式ごとの既定に従う（command は RELEASE のみ）。
                "actions": "",
            },
            "Transport": {
                # 通信方式のプリセット名。Transport.py の登録簿にある名前。
                # 組み込みは legacy_text（従来と同じテキスト行）だけ。
                "name": "legacy_text",
                # 自作の Transport を置くフォルダ（ブックの場所からの
                # 相対でも絶対でもよい）。各 .py は register(register)
                # という関数を持つこと。空なら読み込まない。
                "plugin_dir": "",
            },
            "Arbitration": {
                # 入力調停。off / human / script のいずれか。
                #   off    … 調停しない（本家と同じ挙動。既定）
                #   human  … 人の操作を優先する
                #   script … スクリプトを優先し、実行中の手操作を断る
                "mode": "off",
                # 優先された側が書いてから、反対側を断る秒数。
                "cooldown": "2.0",
            },
            "Pokemon Home": {
                "Season": 1,
                "Single or Double": "シングル",
            },
            "KeyMap-Button": {
                "Button.Y": "y",
                "Button.B": "b",
                "Button.X": "x",
                "Button.A": "a",
                "Button.L": "l",
                "Button.R": "r",
                "Button.ZL": "k",
                "Button.ZR": "e",
                "Button.MINUS": "m",
                "Button.PLUS": "p",
                "Button.LCLICK": "q",
                "Button.RCLICK": "w",
                "Button.HOME": "h",
                "Button.CAPTURE": "c",
            },
            "KeyMap-Direction": {
                # 左スティック。斜めは上下と左右の同時押しで入るため、
                # 既定では斜めに専用キーを割り当てない（空 = 未割当）。
                "Direction.UP": "Key.up",
                "Direction.RIGHT": "Key.right",
                "Direction.DOWN": "Key.down",
                "Direction.LEFT": "Key.left",
                "Direction.UP_RIGHT": "",
                "Direction.DOWN_RIGHT": "",
                "Direction.DOWN_LEFT": "",
                "Direction.UP_LEFT": "",
            },
            "KeyMap-Hat": {
                # 十字キー。既定では割り当てない（左スティックと同じキーに
                # なると重複するため、必要な人がキーコンフィグで設定する）。
                # 旧既定値 10000 などは Keyboard 側が int として解釈し、
                # キーボードのどのキーとも一致しないため機能していなかった。
                "Hat.TOP": "",
                "Hat.TOP_RIGHT": "",
                "Hat.RIGHT": "",
                "Hat.BTM_RIGHT": "",
                "Hat.BTM": "",
                "Hat.BTM_LEFT": "",
                "Hat.LEFT": "",
                "Hat.TOP_LEFT": "",
                "Hat.CENTER": "",
            },
        }

    def generate(self) -> None:
        """既定値で settings.ini を新規作成する。"""
        logger.info("既定の設定ファイルを作成します")
        for section, values in self._default_sections().items():
            self.setting[section] = {k: str(v) for k, v in values.items()}
        self._write_ini()

    def save(self, path: Optional[str] = None) -> None:
        # tkinter の変数はそのまま書けないので値を取り出して詰め直す。
        # KeyMap-* は tk 変数を持たないのでここでは組み直さない。ただし
        # メモリ上の内容は「読み込んだ時点」のもので、その後にキーコンフィグ
        # 画面（別インスタンス）が保存した割り当てを、古い内容で上書きして
        # 戻してしまう。書き出す直前にファイル側を読み直して合わせる。
        self._reload_key_maps()

        self.setting["General Setting"] = {
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
        self.setting["Window"] = {
            "geometry": self.window_geometry.get(),
            "restore_geometry": self.restore_geometry.get(),
            "log_sash_ratio": self.log_sash_ratio.get(),
        }
        self.setting["Input Log"] = {
            "format": self.input_log_format.get(),
            "enabled": self.input_log_enabled.get(),
            "log_stick_change": self.input_log_stick_change.get(),
            "actions": self.input_log_actions.get(),
        }
        self.setting["Transport"] = {
            "name": self.transport_name.get(),
            "plugin_dir": self.transport_plugin_dir.get(),
        }
        self.setting["Arbitration"] = {
            "mode": self.arbitration_mode.get(),
            "cooldown": self.arbitration_cooldown.get(),
        }

        # pokemon home用の設定
        self.setting["Pokemon Home"] = {
            "Season": self.season.get(),
            "Single or Double": self.is_SingleBattle.get(),
        }

        self._write_ini(path)
        logger.debug("設定ファイルを保存しました")

    def _write_ini(self, path: Optional[str] = None) -> None:
        """一時ファイルへ書いてから置き換える（書き込み中の中断で設定を失わないため）。"""
        path = path or self.setting_path
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            self.setting.write(file)
        os.replace(tmp_path, path)  # 同一ボリューム上の原子的な置き換え
        os.chmod(
            path, 0o600
        )  # 設定値は Keyboard 側で解釈されるため本人のみ読み書き可にする
