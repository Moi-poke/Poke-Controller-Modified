"""settings.ini が壊れていても既定値で起動できることを確かめる（Tk 不使用）。"""

import configparser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import Settings
import config
import pytest

BOOL_KEYS = [
    ("General Setting", "is_show_realtime"),
    ("General Setting", "is_show_serial"),
    ("General Setting", "is_use_keyboard"),
    ("General Setting", "is_use_gamepad"),
    ("General Setting", "is_use_left_stick_mouse"),
    ("General Setting", "is_use_right_stick_mouse"),
    ("General Setting", "is_take_stick_log"),
    ("Window", "restore_geometry"),
    ("Window", "show_tabs"),
    ("Window", "show_log"),
    ("Input Log", "enabled"),
    ("Input Log", "log_stick_change"),
    ("Log View", "show_time"),
    ("Log View", "wrap"),
    ("Log View", "group_similar"),
    ("Log View", "collect_problems"),
    ("Audio", "monitor_enabled"),
]


def make_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment]
    return parser


def make_settings(path: Path) -> Settings.GuiSettings:
    """__init__（Tk 変数）を通さず、load() が使う状態だけを持つ設定を作る。"""
    settings = Settings.GuiSettings.__new__(Settings.GuiSettings)
    settings.setting_path = str(path)
    settings.setting = make_parser()
    return settings


def test_load_invalid_utf8_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.ini"
    # 途中に不正なバイト列を置き、ファイル全体が UTF-8 として読めない状況にする。
    path.write_bytes(
        b"[General Setting]\n"
        b"is_show_realtime = false\n"
        b"com_port_name = \xff\xfe\n"
        b"[Window]\n"
        b"log_sash_ratio = 0.2\n"
    )
    settings = make_settings(path)

    settings.load()  # UnicodeDecodeError を漏らさない
    settings._complete_missing()

    general = settings.setting["General Setting"]
    assert general.getboolean("is_show_realtime") is True  # 既定へ戻る
    assert settings.setting["Window"]["log_sash_ratio"] == "0.6"
    # 補正後のファイルは有効な UTF-8 として読み直せる。
    reloaded = make_parser()
    reloaded.read(path, encoding="utf-8")
    assert reloaded.getboolean("General Setting", "is_show_realtime") is True
    assert reloaded["Window"]["log_sash_ratio"] == "0.6"


@pytest.mark.parametrize(("section", "key"), BOOL_KEYS)
def test_invalid_bool_falls_back_to_default(section: str, key: str) -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser[section][key] = "maybe"

    changed = config.complete_missing(parser)

    expected = config.default_sections()[section][key]
    assert parser[section].getboolean(key) is expected
    assert f"{section}.{key}" in changed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "True"),
        ("YES", "True"),
        ("true", "True"),
        ("On", "True"),
        ("0", "False"),
        ("NO", "False"),
        ("false", "False"),
        ("off", "False"),
    ],
)
def test_bool_spellings_are_normalized(raw: str, expected: str) -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["General Setting"]["is_show_realtime"] = raw

    config.complete_missing(parser)

    assert parser["General Setting"]["is_show_realtime"] == expected
    assert parser["General Setting"].getboolean("is_show_realtime") is (
        expected == "True"
    )
    # 正規形へ直した後は再度の補正が発生しない。
    assert config.complete_missing(parser) == []


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE"])
def test_audio_monitor_enabled_accepts_boolean_spellings(raw: str) -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["Audio"]["monitor_enabled"] = raw

    changed = config.complete_missing(parser)

    assert parser["Audio"]["monitor_enabled"] == "True"
    assert parser["Audio"].getboolean("monitor_enabled") is True
    assert "Audio.monitor_enabled" in changed


def test_audio_monitor_enabled_invalid_falls_back_to_false() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["Audio"]["monitor_enabled"] = "maybe"

    changed = config.complete_missing(parser)

    assert parser["Audio"]["monitor_enabled"] == "False"
    assert parser["Audio"].getboolean("monitor_enabled") is False
    assert "Audio.monitor_enabled" in changed


@pytest.mark.parametrize("raw", ["abc", "", "1.5", "-0.1", "nan"])
def test_invalid_log_sash_ratio_falls_back(raw: str) -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["Window"]["log_sash_ratio"] = raw

    changed = config.complete_missing(parser)

    assert parser["Window"]["log_sash_ratio"] == "0.6"
    assert "Window.log_sash_ratio" in changed


def test_valid_values_are_preserved() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["General Setting"]["is_show_realtime"] = "no"
    parser["Window"]["log_sash_ratio"] = "0.42"

    config.complete_missing(parser)

    assert parser["General Setting"].getboolean("is_show_realtime") is False
    assert parser["Window"]["log_sash_ratio"] == "0.42"


class _FakeVar:
    """Tk 変数の代役。GuiSettings を Tk 無しで組み立てるため get/set だけ持つ。"""

    def __init__(self, value: Any = None, **_kwargs: Any) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value


@pytest.fixture
def fake_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    """GuiSettings.__init__ が作る Tk 変数を、Tk を使わない代役へ差し替える。"""
    monkeypatch.setattr(
        Settings,
        "tk",
        SimpleNamespace(
            StringVar=_FakeVar,
            BooleanVar=_FakeVar,
            IntVar=_FakeVar,
            DoubleVar=_FakeVar,
        ),
    )


def use_settings_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """GuiSettings が読み書きする ini を一時ファイルへ向ける。"""
    monkeypatch.setattr(Settings.GuiSettings, "SETTING_PATH", str(path))


def test_settings_round_trip_preserves_percent_in_values(
    tmp_path: Path, fake_tk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_settings_path(monkeypatch, tmp_path / "settings.ini")
    settings = Settings.GuiSettings()
    settings.input_log_format.set("{time:%H:%M}")
    settings.com_port_name.set("COM 100%")

    settings.save()

    reloaded = Settings.GuiSettings()
    assert reloaded.input_log_format.get() == "{time:%H:%M}"
    assert reloaded.com_port_name.get() == "COM 100%"


def test_load_accepts_raw_percent_in_ini(
    tmp_path: Path, fake_tk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.ini"
    path.write_text(
        "[General Setting]\n"
        "com_port_name = COM 100%\n"
        "[Input Log]\n"
        "format = {time:%H:%M}\n",
        encoding="utf-8",
    )
    use_settings_path(monkeypatch, path)

    settings = Settings.GuiSettings()

    assert settings.input_log_format.get() == "{time:%H:%M}"
    assert settings.com_port_name.get() == "COM 100%"
