#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import os
from typing import Any

from Commands.Keys import Button, Direction, Hat
from Settings import GuiSettings
from loguru import logger
from pynput.keyboard import Key, Listener


# キーボード入力を受け取る基底クラス
class Keyboard:
    def __init__(self) -> None:
        self.listener: Listener | None = None

    def listen(self) -> None:
        # pynput の Listener は stop() 後に再start できないため、毎回作り直す
        if self.listener is not None and self.listener.running:
            logger.debug("キーボード監視はすでに動作中です")
            return
        self.listener = Listener(on_press=self.on_press, on_release=self.on_release)
        self.listener.start()
        logger.debug("キーボード操作を開始しました")

    def stop(self) -> None:
        if self.listener is None:
            return
        self.listener.stop()
        self.listener = None
        logger.debug("キーボード操作を停止しました")

    def on_press(self, key: Any) -> None:
        logger.debug(f"press: {getattr(key, 'char', key)}")

    def on_release(self, key: Any) -> None:
        logger.debug(f"release: {getattr(key, 'char', key)}")


# This regards a keyboard inputs as Switch controller
class SwitchKeyboardController(Keyboard):
    SETTING_PATH = os.path.join(os.path.dirname(__file__), "settings.ini")

    # settings.ini から読み取ってよい Enum のホワイトリスト。
    # eval を使わずここに載っている名前だけを解決する。
    _ENUM_TABLE: dict[str, type[Any]] = {
        "Button": Button,
        "Direction": Direction,
        "Hat": Hat,
    }

    def __init__(self, keyPress: Any) -> None:
        super().__init__()

        if keyPress is None:
            raise ValueError(
                "keyPress が None です。シリアル接続後に生成してください。"
            )

        self.to_use = Button.A
        self.key = keyPress
        self.holding: list[Any] = []
        self.holdingDir: list[Any] = []
        self.holdingHatDir: list[Any] = []

        self.setting = configparser.ConfigParser()
        self.setting.optionxform = str  # type: ignore[assignment, method-assign]

        logger.debug("キーコンフィグを読み込みます")
        if not os.path.isfile(self.SETTING_PATH):
            # 設定ファイルが無いと items() が NoSectionError になるため先に生成する
            logger.info("settings.ini が無いため既定値で生成します")
            GuiSettings().generate()
        self.setting.read(self.SETTING_PATH, encoding="utf-8")

        self.key_map: dict[Any, Any] = {}
        for section in ("KeyMap-Button", "KeyMap-Direction", "KeyMap-Hat"):
            self.key_map.update(self._load_key_map(section))

        logger.debug("キーコンフィグの読み込みが完了しました")

    def _load_key_map(self, section: str) -> dict[Any, Any]:
        """settings.ini の1セクションを {入力キー: Enum} の辞書に変換する。"""
        if not self.setting.has_section(section):
            logger.warning(f"{section} セクションがありません。読み飛ばします")
            return {}

        key_map: dict[Any, Any] = {}
        for name, value in self.setting.items(section):
            enum_member = self._to_enum(name)
            if enum_member is None:
                continue
            input_key = self._to_input_key(value)
            if input_key is None:
                # 空文字は「未割当」なので正常。それ以外は設定ミスとして残す
                if value.strip():
                    logger.warning(f"{section} の {name} = {value} は解釈できません")
                continue
            key_map[input_key] = enum_member
        return key_map

    def _to_enum(self, name: str) -> Any | None:
        """'Button.A' のような文字列を Enum / 定数へ変換する（eval は使わない）。

        Button と Hat は Enum だが Direction は素のクラスで、UP/LEFT などは
        クラス属性として後付けされている（Keys.py 参照）。そのため Enum 用の
        enum_cls[member] は Direction に使えず TypeError になる。
        3種とも属性参照で引けるので getattr に統一する。
        ただし getattr は __init__ や mro といったメンバー以外まで拾ってしまうので、
        「そのクラスの実体かどうか」を isinstance で確かめてから返す。
        """
        cls_name, _, member = name.partition(".")
        enum_cls = self._ENUM_TABLE.get(cls_name)
        if enum_cls is None or not member:
            logger.warning(f"未知の種別です: {name}")
            return None

        value = getattr(enum_cls, member, None)
        if not isinstance(value, enum_cls):
            logger.warning(f"未知の項目です: {name}")
            return None
        return value

    def _to_input_key(self, value: str) -> Any:
        """設定値を pynput のキー表現へ変換する。

        受け付けるのは2つの形式だけにする。
          "a"      … 1文字の通常キー
          "Key.up" … pynput の特殊キー
        空文字は「未割当」を意味するので、警告を出さずに読み飛ばす
        （Hat のように既定では割り当てない項目があるため）。

        旧実装は数字だけの値を int として受け付けていたが、これは
        settings.ini のプレースホルダ（10000 など）を拾うためのもので、
        キーボードのどのキーとも一致せず機能していなかった。数値は
        キーとして解釈できないので、他の解釈できない値と同じ扱いにする。
        """
        value = (value or "").strip()
        if not value:
            return None  # 未割当
        if len(value) == 1:
            return value  # 'a' などの文字キーはそのまま
        if value.startswith("Key."):
            return getattr(Key, value[4:], None)  # 'Key.up' → Key.up
        return None

    def _resolve_input(self, key: Any) -> tuple:
        """押されたキーから (キー, 対応する Enum の型) を求める。未割当なら (None, None)。"""
        _k = getattr(key, "char", None)
        if _k is None:
            _k = key  # 特殊キーは Key オブジェクトのまま引く
        mapped = self.key_map.get(_k)
        if mapped is None:
            return None, None
        return _k, type(mapped)

    def on_press(self, key: Any) -> None:
        if key is None:
            logger.warning("未知のキーが入力されました")
            return

        _k, key_type = self._resolve_input(key)
        if _k is None:
            return

        if key_type is type(Button.A):
            if _k in self.holding:
                return
            self.key.input(self.key_map[_k])
            self.holding.append(_k)
        elif key_type is type(Direction.LEFT):
            if _k in self.holdingDir:
                return
            self.holdingDir.append(_k)
            self.inputDir(self.holdingDir)
        elif key_type is type(Hat.TOP):
            if _k in self.holding:
                return
            self.key.input(self.key_map[_k])
            self.holding.append(_k)

    def on_release(self, key: Any) -> None:
        if key is None:
            logger.warning("未知のキーが離されました")
            return

        _k, key_type = self._resolve_input(key)
        if _k is None:
            return

        if key_type is type(Button.A):
            if _k in self.holding:
                self.holding.remove(_k)
                self.key.inputEnd(self.key_map[_k])
        elif key_type is type(Direction.LEFT):
            if _k in self.holdingDir:
                self.holdingDir.remove(_k)
                if not self.holdingDir:
                    self.key.inputEnd(self.key_map[_k])
                self.inputDir(self.holdingDir)
        elif key_type is type(Hat.TOP):
            if _k in self.holding:
                self.holding.remove(_k)
                self.key.inputEnd(self.key_map[_k], unset_hat=True)

    def inputDir(self, dirs: list[Any]) -> None:
        if len(dirs) == 0:
            return
        if len(dirs) == 1:
            self.key.input(self.key_map[dirs[0]])
            return

        valid_dirs = dirs[-2:]  # 直近2方向だけを見る
        to_input = []
        if Key.up in valid_dirs:
            if Key.right in valid_dirs:
                to_input.append(Direction.UP_RIGHT)
            elif Key.left in valid_dirs:
                to_input.append(Direction.UP_LEFT)
        elif Key.down in valid_dirs:
            if Key.left in valid_dirs:
                to_input.append(Direction.DOWN_LEFT)
            elif Key.right in valid_dirs:
                to_input.append(Direction.DOWN_RIGHT)

        if not to_input:
            # 上下同時押しなど斜めに解決できない組み合わせは直近の1方向を使う
            self.key.input(self.key_map[valid_dirs[-1]])
            return
        self.key.input(to_input)
