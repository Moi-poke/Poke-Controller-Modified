"""config.py の [Audio] 既定・補正の検証。Tkなしで回す。"""

import configparser

import config


def make_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment]
    return parser


def test_audio_defaults() -> None:
    defaults = config.default_sections()["Audio"]
    assert defaults["monitor_enabled"] is False
    assert defaults["monitor_volume"] == 0.8
    assert defaults["input_device"] == ""
    assert defaults["output_device"] == ""


def test_audio_complete_missing_backfills() -> None:
    parser = make_parser()
    changed = config.complete_missing(parser)
    assert "Audio" in changed
    assert parser["Audio"]["monitor_enabled"] == "False"
    assert config.complete_missing(parser) == []


def test_audio_complete_missing_clamps() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["Audio"]["monitor_volume"] = "9.9"
    parser["Audio"]["monitor_enabled"] = "typo"
    changed = config.complete_missing(parser)
    assert parser["Audio"]["monitor_volume"] == "0.8"
    assert parser["Audio"]["monitor_enabled"] == "False"
    assert "Audio.monitor_volume" in changed
    assert "Audio.monitor_enabled" in changed
