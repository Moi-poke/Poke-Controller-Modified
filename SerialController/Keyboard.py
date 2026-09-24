#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import os
from collections.abc import Callable
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

    def __init__(
        self,
        keyPress: Any,
        setting_path: str | None = None,
        is_active: Callable[[], bool] | None = None,
    ) -> None:
        """keyPress のキー割り当てを settings.ini から読む。

        setting_path を渡すとそのファイルを、省略時は従来の既定を
        読む。並列起動（profile 別 ini）では呼び出し側が渡すこと。
        渡さないと既定 ini を見て別 profile の割り当てと食い違う。

        is_active は押下を受け付けるかの判定（通常は窓のフォーカス）。
        省略時は常時受け付ける（従来どおり）。pynput の Listener は
        OS 全体の打鍵を拾うため、窓外の打鍵まで流れて誤爆になる。
        離鍵は常時通す。押下を捨てた分は holding に残らないため、
        離鍵まで塞ぐと押しっぱなしが残る。
        """
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

        path = setting_path or self.SETTING_PATH
        self.setting_path = path
        # 再読込で外れた押下中キーの離鍵用に旧割当を残す。
        # 押下側は現 key_map だけ見るため旧キーで再入はしない。
        self._stale_map: dict[Any, Any] = {}
        logger.debug(f"キーコンフィグを読み込みます: {path}")
        if not os.path.isfile(path):
            if path == self.SETTING_PATH:
                # 既定の場所に無いときだけ生成する（従来どおり）。
                # profile 別の場所に無い場合は生成せず警告に留める。
                # 勝手に作ると profile の ini が二重管理になるため。
                logger.info("settings.ini が無いため既定値で生成します")
                GuiSettings().generate()
            else:
                logger.warning(f"キーコンフィグが無いため割り当ては空です: {path}")
        self.setting.read(path, encoding="utf-8")

        self.key_map: dict[Any, Any] = {}
        for section in ("KeyMap-Button", "KeyMap-Direction", "KeyMap-Hat"):
            self.key_map.update(self._load_key_map(section))

        self.is_active = is_active

        logger.debug("キーコンフィグの読み込みが完了しました")

    def reload_key_map(self) -> bool:
        """設定ファイルを読み直して key_map を作り直す。

        Listener は作り直さない。押下中（holding / holdingDir /
        holdingHatDir）は持ち越す。持ち越したキーが新割当から外れて
        いても離鍵で終わわれるよう旧割当を残し、解放時に捨てる。
        ファイルが無い・読めないときは旧割当のまま False を返す。
        """
        path = self.setting_path
        if not os.path.isfile(path):
            logger.warning(f"キーコンフィグが無いため再読込しません: {path}")
            return False
        fresh = configparser.ConfigParser()
        fresh.optionxform = str  # type: ignore[assignment, method-assign]
        try:
            read_ok = fresh.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeError) as e:
            logger.warning(f"キーコンフィグの再読込に失敗しました: {e}")
            return False
        if not read_ok:
            logger.warning(f"キーコンフィグが読めないため再読込しません: {path}")
            return False

        new_map: dict[Any, Any] = {}
        for section in ("KeyMap-Button", "KeyMap-Direction", "KeyMap-Hat"):
            new_map.update(self._load_key_map(section, fresh))

        old_map = self.key_map
        self.setting = fresh
        self.key_map = new_map
        stale: dict[Any, Any] = {}
        held = list(self.holding) + list(self.holdingDir) + list(self.holdingHatDir)
        for key in held:
            if key not in new_map and key in old_map:
                stale[key] = old_map[key]
        self._stale_map = stale
        logger.info("キー割り当てを再読み込みしました")
        return True

    def _load_key_map(
        self, section: str, setting: configparser.ConfigParser | None = None
    ) -> dict[Any, Any]:
        """settings.ini の1セクションを {入力キー: Enum} の辞書に変換する。"""
        source = setting if setting is not None else self.setting
        if not source.has_section(section):
            logger.warning(f"{section} セクションがありません。読み飛ばします")
            return {}

        key_map: dict[Any, Any] = {}
        for name, value in source.items(section):
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

        受け付けるのは3つの形式だけにする。
          "a"        … 1文字の通常キー
          "Key.up"   … pynput の特殊キー
          "Numpad.1" … テンキー数字（メイン行の "1" と区別する）
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
        if value.startswith("Numpad."):
            # テンキー数字は正規文字列のまま引く（char 照合と混ぜない）。
            digit = value[len("Numpad.") :]
            if len(digit) == 1 and digit.isdigit():
                return value
            return None
        if len(value) == 1:
            return value  # 'a' などの文字キーはそのまま
        if value.startswith("Key."):
            return getattr(Key, value[4:], None)  # 'Key.up' → Key.up
        return None

    def _resolve_input(self, key: Any) -> tuple:
        """押されたキーから (キー, 対応する Enum の型) を求める。未割当なら (None, None)。"""
        # テンキー数字は char より先に見る（char はメイン行と同じ "1" になる）。
        # vk が無い環境（他プラットフォーム等）では従来路へ落ちる。
        vk = getattr(key, "vk", None)
        if isinstance(vk, int) and 96 <= vk <= 105:
            _np = f"Numpad.{vk - 96}"
            mapped_np = self.key_map.get(_np)
            if mapped_np is None:
                return None, None
            return _np, type(mapped_np)
        _k = getattr(key, "char", None)
        if _k is None:
            _k = key  # 特殊キーは Key オブジェクトのまま引く
        mapped = self.key_map.get(_k)
        if mapped is None:
            return None, None
        return _k, type(mapped)

    def _lookup(self, key: Any) -> Any:
        """現割当を優先し、再読込で外れた押下中は旧割当で引く。"""
        mapped = self.key_map.get(key)
        if mapped is None:
            mapped = self._stale_map.get(key)
        return mapped

    def _resolve_for_release(
        self, key: Any
    ) -> tuple[Any | None, Any | None, Any | None]:
        """離鍵用に (キー, Enum の型, Enum) を求める。

        現 key_map を優先する。再読込で外れた押下中キーだけ旧割当で
        補う。押下側はこの補完を見ない。
        """
        _k, key_type = self._resolve_input(key)
        if _k is not None:
            return _k, key_type, self.key_map[_k]
        raw = getattr(key, "char", None)
        if raw is None:
            raw = key
        if raw in self.holding or raw in self.holdingDir:
            mapped = self._stale_map.get(raw)
            if mapped is not None:
                return raw, type(mapped), mapped
        return None, None, None

    def on_press(self, key: Any) -> None:
        if key is None:
            logger.warning("未知のキーが入力されました")
            return

        if self.is_active is not None and not self.is_active():
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

        _k, key_type, mapped = self._resolve_for_release(key)
        if _k is None or mapped is None:
            return

        if key_type is type(Button.A):
            if _k in self.holding:
                self.holding.remove(_k)
                self._stale_map.pop(_k, None)
                self.key.inputEnd(mapped)
        elif key_type is type(Direction.LEFT):
            if _k in self.holdingDir:
                self.holdingDir.remove(_k)
                self._stale_map.pop(_k, None)
                if not self.holdingDir:
                    self.key.inputEnd(mapped)
                self.inputDir(self.holdingDir)
        elif key_type is type(Hat.TOP):
            if _k in self.holding:
                self.holding.remove(_k)
                self._stale_map.pop(_k, None)
                self.key.inputEnd(mapped, unset_hat=True)

    def inputDir(self, dirs: list[Any]) -> None:
        if len(dirs) == 0:
            return
        if len(dirs) == 1:
            mapped = self._lookup(dirs[0])
            if mapped is None:
                return
            self.key.input(mapped)
            return

        # 保持中の各入力キーを key_map 経由で Direction に解決する。
        # 矢印キー決め打ちだと文字キー割り当て（WASD など）で斜めが出ないため。
        resolved = [self._lookup(d) for d in dirs]
        vertical = None  # 直近の上下（UP/DOWN）
        horizontal = None  # 直近の左右（LEFT/RIGHT）
        for mapped in reversed(resolved):
            if vertical is None and (
                mapped is Direction.UP or mapped is Direction.DOWN
            ):
                vertical = mapped
            if horizontal is None and (
                mapped is Direction.LEFT or mapped is Direction.RIGHT
            ):
                horizontal = mapped
            if vertical is not None and horizontal is not None:
                break

        if vertical is not None and horizontal is not None:
            # 縦×横が揃えば斜めに入力する
            if vertical is Direction.UP:
                if horizontal is Direction.RIGHT:
                    self.key.input(Direction.UP_RIGHT)
                else:
                    self.key.input(Direction.UP_LEFT)
            else:
                if horizontal is Direction.LEFT:
                    self.key.input(Direction.DOWN_LEFT)
                else:
                    self.key.input(Direction.DOWN_RIGHT)
            return
        # 上下同時押しなど斜めに解決できない組み合わせは直近の1方向を使う
        fallback = self._lookup(dirs[-1])
        if fallback is None:
            return
        self.key.input(fallback)
