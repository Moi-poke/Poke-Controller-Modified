#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import os
import tkinter as tk
import json
import time
import hashlib
from typing import Dict, List, Any, Optional, Union, OrderedDict, cast
from loguru import logger
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RecentCommandInfo:
    id: int
    name: str
    last_executed: datetime
    file_path: str
    hash: str = ""  # コマンドスクリプトのハッシュ値

    def to_dict(self) -> dict:
        """JSONシリアライズのためのdict変換メソッド"""
        return {
            "id": self.id,
            "name": self.name,
            "last_executed": self.last_executed.isoformat(),
            "file_path": self.file_path,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RecentCommandInfo":
        """dictからRecentCommandInfoを生成するクラスメソッド"""
        return cls(
            id=data["id"],
            name=data["name"],
            last_executed=datetime.fromisoformat(data["last_executed"]),
            file_path=data["file_path"],
            hash=data.get("hash", ""),  # 後方互換性のため
        )


class GuiSettings:
    SETTING_PATH = os.path.join(os.path.dirname(__file__), "settings.ini")
    USER_DATA_PATH = os.path.join(os.path.dirname(__file__), "user_data.json")

    def __init__(self) -> None:
        self.setting = configparser.ConfigParser()
        self.setting.optionxform = str  # type:ignore

        # ユーザーデータの初期化
        self.user_data: Dict[str, Any] = {
            "recent_commands": {},
            "tab_state": {"main_tab": 0, "command_tab": 0},
        }

        if not os.path.exists(self.SETTING_PATH):
            logger.debug("Setting file does not exists.")
            self.generate()
            self.load()
            logger.debug("Settings file has been generated.")
        else:
            logger.debug("Setting file exists.")
            self.load()
            logger.debug("Settings file has been loaded.")

        self.setup_tk_variables()

        # 旧ファイルから新形式への移行処理
        self._migrate_legacy_data()

    def setup_tk_variables(self) -> None:
        # default
        self.camera_id = tk.IntVar(
            value=self.setting["General Setting"].getint("camera_id")
        )
        self.com_port = tk.IntVar(
            value=self.setting["General Setting"].getint("com_port")
        )
        self.com_port_name = tk.StringVar(
            value=self.setting["General Setting"].get("com_port_name")
        )
        self.baud_rate = tk.IntVar(
            value=self.setting["General Setting"].getint("baud_rate")
        )
        self.fps = tk.StringVar(value=self.setting["General Setting"]["fps"])
        self.show_size = tk.StringVar(
            value=self.setting["General Setting"].get("show_size")
        )
        self.is_show_realtime = tk.BooleanVar(
            value=self.setting["General Setting"].getboolean("is_show_realtime")
        )
        self.is_show_serial = tk.BooleanVar(
            value=self.setting["General Setting"].getboolean("is_show_serial")
        )
        self.is_use_keyboard = tk.BooleanVar(
            value=self.setting["General Setting"].getboolean("is_use_keyboard")
        )
        # Pokemon Home用の設定
        self.season = tk.StringVar(value=self.setting["Pokemon Home"].get("Season"))
        self.is_SingleBattle = tk.StringVar(
            value=self.setting["Pokemon Home"].get("Single or Double")
        )

    def load(self) -> None:
        # INI設定ファイルの読み込み
        if os.path.isfile(self.SETTING_PATH):
            self.setting.read(self.SETTING_PATH, encoding="utf-8")

        # ユーザーデータの読み込み
        if os.path.isfile(self.USER_DATA_PATH):
            try:
                with open(self.USER_DATA_PATH, "r", encoding="utf-8") as f:
                    self.user_data = json.load(f)
                logger.debug("User data loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load user data: {e}")
                self.user_data = {
                    "recent_commands": {},
                    "tab_state": {"main_tab": 0, "command_tab": 0},
                }

    def generate(self) -> None:
        # logger.info('Create Default setting file.')
        # default
        self.setting["General Setting"] = {
            "camera_id": "0",
            "com_port": "0",
            "com_port_name": "",
            "baud_rate": "9600",
            "fps": "45",
            "show_size": "640x360",
            "is_show_realtime": "True",
            "is_show_serial": "False",
            "is_use_keyboard": "True",
        }
        # pokemon home用の設定
        self.setting["Pokemon Home"] = {
            "Season": "1",
            "Single or Double": "シングル",
        }
        # keyconfig
        self.setting["KeyMap-Button"] = {
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
        }
        self.setting["KeyMap-Direction"] = {
            "Direction.UP": "Key.up",
            "Direction.RIGHT": "Key.right",
            "Direction.DOWN": "Key.down",
            "Direction.LEFT": "Key.left",
            "Direction.UP_RIGHT": "20001",
            "Direction.DOWN_RIGHT": "20002",
            "Direction.DOWN_LEFT": "20010",
            "Direction.UP_LEFT": "20011",
        }
        self.setting["KeyMap-Hat"] = {
            "Hat.TOP": "10000",
            "Hat.TOP_RIGHT": "10001",
            "Hat.RIGHT": "10010",
            "Hat.BTM_RIGHT": "10011",
            "Hat.BTM": "10100",
            "Hat.BTM_LEFT": "10101",
            "Hat.LEFT": "10110",
            "Hat.TOP_LEFT": "10111",
            "Hat.CENTER": "11000",
        }
        with open(self.SETTING_PATH, "w", encoding="utf-8") as file:
            self.setting.write(file)
        os.chmod(path=self.SETTING_PATH, mode=0o777)

        # 空のユーザーデータファイルを作成
        with open(self.USER_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"recent_commands": {}, "tab_state": {"main_tab": 0, "command_tab": 0}},
                f,
                indent=2,
                ensure_ascii=False,
            )
        os.chmod(path=self.USER_DATA_PATH, mode=0o777)

    def save(self, path: Optional[str] = None) -> None:
        # INI設定の保存 (すべての値を文字列に変換)
        self.setting["General Setting"] = {
            "camera_id": str(self.camera_id.get()),
            "com_port": str(self.com_port.get()),
            "com_port_name": str(self.com_port_name.get()),
            "baud_rate": str(self.baud_rate.get()),
            "fps": str(self.fps.get()),
            "show_size": str(self.show_size.get()),
            "is_show_realtime": str(self.is_show_realtime.get()),
            "is_show_serial": str(self.is_show_serial.get()),
            "is_use_keyboard": str(self.is_use_keyboard.get()),
        }
        # pokemon home用の設定
        self.setting["Pokemon Home"] = {
            "Season": str(self.season.get()),
            "Single or Double": str(self.is_SingleBattle.get()),
        }

        with open(self.SETTING_PATH, "w", encoding="utf-8") as file:
            self.setting.write(file)
        os.chmod(path=self.SETTING_PATH, mode=0o777)
        logger.debug("Settings file has been saved.")

        # ユーザーデータの保存
        self._save_user_data()

    def _save_user_data(self) -> None:
        """ユーザーデータをJSONファイルに保存"""
        try:
            with open(self.USER_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(self.user_data, f, indent=2, ensure_ascii=False)
            os.chmod(path=self.USER_DATA_PATH, mode=0o777)
            logger.debug("User data saved successfully")
        except Exception as e:
            logger.error(f"Failed to save user data: {e}")

    def _migrate_legacy_data(self) -> None:
        """古いデータ形式から新しい形式に移行"""
        # recent_py_commands.jsonからの移行
        legacy_recent_py_path = os.path.join(
            os.path.dirname(__file__), "recent_py_commands.json"
        )
        if os.path.exists(legacy_recent_py_path):
            try:
                with open(legacy_recent_py_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)

                # 既存のデータをマージ
                for key, item_data in legacy_data.items():
                    self.user_data["recent_commands"][key] = item_data

                logger.info("Migrated data from recent_py_commands.json")
            except Exception as e:
                logger.error(f"Error migrating recent_py_commands.json: {e}")

        # tab_state.jsonからの移行
        legacy_tab_state_path = os.path.join(
            os.path.dirname(__file__), "tab_state.json"
        )
        if os.path.exists(legacy_tab_state_path):
            try:
                with open(legacy_tab_state_path, "r", encoding="utf-8") as f:
                    legacy_tab_state = json.load(f)

                # タブ状態を更新
                self.user_data["tab_state"] = legacy_tab_state

                logger.info("Migrated data from tab_state.json")
            except Exception as e:
                logger.error(f"Error migrating tab_state.json: {e}")

        # 移行後に保存
        self._save_user_data()

    # タブ状態の管理メソッド
    def save_tab_state(self, main_tab: int, command_tab: int) -> None:
        """メインタブとコマンドタブの状態を保存"""
        self.user_data["tab_state"] = {"main_tab": main_tab, "command_tab": command_tab}
        self._save_user_data()

    def get_tab_state(self) -> Dict[str, int]:
        """タブ状態を取得"""
        tab_state = self.user_data.get("tab_state", {"main_tab": 0, "command_tab": 0})
        return cast(Dict[str, int], tab_state)

    # 最近使用したコマンド管理メソッド
    def add_recent_command(self, command_info: RecentCommandInfo) -> None:
        """最近使用したコマンドを追加"""
        recent_commands = self.user_data.setdefault("recent_commands", {})

        # ハッシュをキーとして使用（なければID+名前）
        command_key = (
            command_info.hash
            if command_info.hash
            else f"{command_info.id}_{command_info.name}"
        )
        recent_commands[command_key] = command_info.to_dict()

        # 古いエントリを削除（最大20件）
        self._cleanup_recent_commands(20)
        self._save_user_data()

    def get_recent_commands(self) -> Dict[str, RecentCommandInfo]:
        """最近使用したコマンドのリストを取得"""
        recent_data = self.user_data.get("recent_commands", {})
        result = {}

        for key, data in recent_data.items():
            try:
                result[key] = RecentCommandInfo.from_dict(data)
            except Exception as e:
                logger.error(f"Error parsing recent command data: {e}")

        return result

    def get_sorted_recent_commands(self, limit: int = 20) -> List[RecentCommandInfo]:
        """使用日時でソートされた最近使用したコマンドのリストを取得"""
        commands = list(self.get_recent_commands().values())

        # 日時でソート
        commands.sort(key=lambda x: x.last_executed, reverse=True)

        # 指定数に制限
        return commands[:limit]

    def _cleanup_recent_commands(self, max_entries: int) -> None:
        """古いコマンド履歴をクリーンアップ"""
        recent_commands = self.user_data.get("recent_commands", {})

        if len(recent_commands) <= max_entries:
            return

        # 日付順にソート
        sorted_items = sorted(
            recent_commands.items(),
            key=lambda x: datetime.fromisoformat(x[1]["last_executed"]),
            reverse=True,
        )

        # 最大数を超える古いエントリを削除
        self.user_data["recent_commands"] = dict(sorted_items[:max_entries])

    def clear_recent_commands(self) -> None:
        """コマンド履歴をクリア"""
        self.user_data["recent_commands"] = {}
        self._save_user_data()

    def check_command_hash(self, file_path: str, cmd_hash: str) -> bool:
        """指定したコマンドのハッシュが一致するかチェックする

        Args:
            file_path (str): ファイルパス
            cmd_hash (str): 比較対象のハッシュ値

        Returns:
            bool: ハッシュが一致する場合はTrue、それ以外はFalse
        """
        try:
            # ファイルが存在しない場合はFalse
            if not os.path.exists(file_path):
                return False

            # 現在のファイルのハッシュを計算
            with open(file_path, "rb") as f:
                content = f.read()
                current_hash = hashlib.md5(content).hexdigest()

            # ハッシュを比較
            return bool(current_hash == cmd_hash)

        except Exception as e:
            logger.error(f"Error checking command hash: {e}")
            return False
