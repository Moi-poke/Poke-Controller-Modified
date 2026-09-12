#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.py - settings.ini の書式知識（Tkなし・純粋関数）。

Settings.GuiSettings は Tk 変数の鏡とファイル入出力の手順だけを持ち、
既定値・補正・移行の判断はここへ委ねる。ここは tkinter も loguru も
読まないので、ヘッドレスの検証でそのまま動く。

ini の書式自体は変えない。利用者の既存ファイルがそのまま読めること。
"""

from __future__ import annotations

import configparser
import hashlib
import os
import re
from typing import Any

# キーコンフィグが扱うセクション。KeyConfig / Keyboard の双方が参照する。
KEYMAP_SECTIONS: tuple = ("KeyMap-Button", "KeyMap-Direction", "KeyMap-Hat")


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


def profile_path(setting_path: str, profile: str) -> str:
    """プロファイル名から settings ファイルのパスを作る。"""
    if not profile:
        return setting_path
    root, ext = os.path.splitext(setting_path)
    return f"{root}.{profile}{ext}"


def default_sections() -> dict[str, dict[str, Any]]:
    """既定値。呼び出すたびに新しい辞書を返す（書き換えてよい）。"""
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
            # ライブ入力の最低保持ミリ秒。40ms間隔の連打を滞留なく
            # 通す上限が16ms（8msスロット量子化で実効16ms×2状態=32ms）。
            # 8〜64以外は補正で既定へ戻す。
            "live_min_dwell_ms": 16,
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
        "Audio": {
            # 音声入力の指定。空なら既定の入力を使う。番号（列挙の
            # "番号: 名前" の番号）が確実。同名重複があるため名前より
            # 番号を推奨する。旧来の名前も読める（先頭一致へ解決する）。
            "input_device": "",
            # モニター再生の出力先。書式は入力と同じ。
            "output_device": "",
            # ゲーム音のモニター再生。既定OFF（ハウリング・遅延・
            # 無音環境の混乱を避けるため、使う人だけが入れる）。
            "monitor_enabled": False,
            # モニター音量。0.0〜1.0。範囲外は補正で既定へ戻す。
            "monitor_volume": 0.8,
        },
        "PreviewFilter": {
            # メイン画面の表示専用フィルタ（OBSの色補正に相当）。
            # 認識・保存には影響しない。ON/OFF自体は持たず、起動時は
            # 常にOFF（不意の加工表示を避ける）。補正5項目の既定は
            # core.preview_filter.DEFAULT_CORRECTION と揃えること。
            # ガンマ 0.1〜3.0。
            "gamma": 1.0,
            # コントラスト 0起点±2.0（倍率=1.0+値）。
            "contrast": 0.0,
            # 輝度 -100〜100。
            "brightness": 0,
            # 彩度 0.0〜3.0。
            "saturation": 1.0,
            # 色相シフト -90〜90。
            "hue_shift": 0,
            # 色抽出のHSV範囲（H 0〜179、S・V 0〜255）。
            "lower_h": 0,
            "lower_s": 0,
            "lower_v": 0,
            "upper_h": 179,
            "upper_s": 255,
            "upper_v": 255,
            # 抽出の見せ方。gray_out … 対象外グレー / mask … 白黒。
            "mode": "gray_out",
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


def complete_missing(parser: configparser.ConfigParser) -> list[str]:
    """不足項目と不正値を既定値へ補正する。直した箇所の一覧を返す。

    書き出しは呼び出し側が行う（ここは辞書を直すだけ）。
    """
    defaults = default_sections()
    changed: list[str] = []
    for section, values in defaults.items():
        if not parser.has_section(section):
            parser[section] = {k: str(v) for k, v in values.items()}
            changed.append(section)
            continue
        for key, value in values.items():
            if key not in parser[section]:
                parser[section][key] = str(value)
                changed.append(f"{section}.{key}")
    general = parser["General Setting"]
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
    if parser.has_section("Transport"):
        transport = parser["Transport"]
        if not transport.get("name", "").strip():
            transport["name"] = "legacy_text"
            changed.append("Transport.name")
        # ライブ入力の最低保持ミリ秒。範囲外・非数値は既定へ戻す。
        try:
            dwell = int(transport.get("live_min_dwell_ms", ""))
        except (TypeError, ValueError):
            dwell = None
        if dwell is None or not 8 <= dwell <= 64:
            transport["live_min_dwell_ms"] = "16"
            changed.append("Transport.live_min_dwell_ms")
    if parser.has_section("Arbitration"):
        arb = parser["Arbitration"]
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
    if parser.has_section("Audio"):
        audio = parser["Audio"]
        if audio.get("monitor_enabled", "").strip() not in ("True", "False"):
            audio["monitor_enabled"] = "False"
            changed.append("Audio.monitor_enabled")
        try:
            volume = float(audio.get("monitor_volume", ""))
        except (TypeError, ValueError):
            volume = None
        if volume is None or not 0.0 <= volume <= 1.0:
            audio["monitor_volume"] = "0.8"
            changed.append("Audio.monitor_volume")
    if parser.has_section("PreviewFilter"):
        filt = parser["PreviewFilter"]
        float_rules = {
            "gamma": ("1.0", lambda value: 0.1 <= value <= 3.0),
            "contrast": ("0.0", lambda value: -2.0 <= value <= 2.0),
            "saturation": ("1.0", lambda value: 0.0 <= value <= 3.0),
        }
        for key, (dflt, ok) in float_rules.items():
            try:
                value = float(filt.get(key, ""))
            except (TypeError, ValueError):
                value = None
            if value is None or not ok(value):
                filt[key] = dflt
                changed.append(f"PreviewFilter.{key}")
        int_rules = {
            "brightness": ("0", lambda value: -100 <= value <= 100),
            "hue_shift": ("0", lambda value: -90 <= value <= 90),
            "lower_h": ("0", lambda value: 0 <= value <= 179),
            "lower_s": ("0", lambda value: 0 <= value <= 255),
            "lower_v": ("0", lambda value: 0 <= value <= 255),
            "upper_h": ("179", lambda value: 0 <= value <= 179),
            "upper_s": ("255", lambda value: 0 <= value <= 255),
            "upper_v": ("255", lambda value: 0 <= value <= 255),
        }
        for key, (dflt, ok) in int_rules.items():
            try:
                raw = filt.get(key, "").strip()
                num = int(raw) if raw != "" else None
            except (TypeError, ValueError, AttributeError):
                num = None
            if num is None or not ok(num):
                filt[key] = dflt
                changed.append(f"PreviewFilter.{key}")
        if filt.get("mode", "").strip() not in ("gray_out", "mask"):
            filt["mode"] = "gray_out"
            changed.append("PreviewFilter.mode")
    return changed


def migrate_legacy_keymap(parser: configparser.ConfigParser) -> list[str]:
    """KeyMap に残った旧プレースホルダ（10000 等の数値）を未割当に直す。

    旧版は Hat/Direction の既定値に 10000・20001 といった数値を入れて
    いた。これはキーボードのどのキーとも対応しない見せかけの値で、
    Keyboard 側は解釈できず毎回 WARNING を出す（実害は無いが、起動の
    たび13行流れて本当の警告が埋もれる）。complete_missing は「無い
    キー」しか補わないため、既に値がある以上そのまま残ってしまう。
    数値だけの値は未割当（空文字）とみなして書き換える。利用者が
    自分で割り当てた値は "a" や "Key.up" の形なので巻き込まない。
    直した箇所の一覧を返す。書き出しは呼び出し側が行う。
    """
    changed = []
    for section in KEYMAP_SECTIONS:
        if not parser.has_section(section):
            continue
        for key, value in parser.items(section):
            stripped = value.strip()
            # "1" のような1文字の数字は実際に押せるキーなので残す。
            # 旧プレースホルダは 10000・20001 のように複数桁だけ。
            if len(stripped) > 1 and stripped.isdigit():
                parser[section][key] = ""
                changed.append(f"{section}.{key}")
    return changed


def str_values(values: dict[str, Any]) -> dict[str, str]:
    """ini へ書けるよう値を文字列へ揃える（generate と同じ規則）。"""
    return {k: str(v) for k, v in values.items()}
