"""config.py の [PreviewFilter] 既定・補正の検証。Tkなしで回す。"""

import configparser

import config


def make_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment]
    return parser


def test_preview_filter_defaults_match_core() -> None:
    """[PreviewFilter] の既定は core の正本と一致する。"""
    from core import preview_filter

    defaults = config.default_sections()["PreviewFilter"]
    for key, value in preview_filter.DEFAULT_CORRECTION.items():
        assert float(defaults[key]) == float(value)
    assert defaults["lower_h"] == 0
    assert defaults["upper_h"] == 179
    assert defaults["mode"] == "gray_out"


def test_preview_filter_complete_missing_backfills() -> None:
    parser = make_parser()
    changed = config.complete_missing(parser)
    assert "PreviewFilter" in changed
    assert parser["PreviewFilter"]["gamma"] == "1.0"
    assert config.complete_missing(parser) == []


def test_preview_filter_complete_missing_clamps() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    parser["PreviewFilter"]["gamma"] = "0.01"
    parser["PreviewFilter"]["contrast"] = "9.9"
    parser["PreviewFilter"]["brightness"] = "abc"
    parser["PreviewFilter"]["hue_shift"] = "91"
    parser["PreviewFilter"]["upper_h"] = "200"
    parser["PreviewFilter"]["mode"] = "typo"
    changed = config.complete_missing(parser)
    assert parser["PreviewFilter"]["gamma"] == "1.0"
    assert parser["PreviewFilter"]["contrast"] == "0.0"
    assert parser["PreviewFilter"]["brightness"] == "0"
    assert parser["PreviewFilter"]["hue_shift"] == "0"
    assert parser["PreviewFilter"]["upper_h"] == "179"
    assert parser["PreviewFilter"]["mode"] == "gray_out"
    assert "PreviewFilter.gamma" in changed
    assert "PreviewFilter.mode" in changed
