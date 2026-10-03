"""config.py（ini 書式知識）の検証。Tkなし・ファイルなしで回す。"""

import configparser

import config


def make_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[assignment]
    return parser


def test_sanitize_profile() -> None:
    assert config.sanitize_profile("") == ""
    assert config.sanitize_profile("switch1") == "switch1"
    # フォルダ外へ出られない
    assert "/" not in config.sanitize_profile("../../foo")
    assert "\\" not in config.sanitize_profile("..\\foo")
    # 日本語のみでも一意な名前になる
    a = config.sanitize_profile("あいう")
    b = config.sanitize_profile("えお")
    assert a and b and a != b
    # 長すぎは切る
    assert len(config.sanitize_profile("x" * 100)) <= 32


def test_profile_path() -> None:
    base = "/tmp/settings.ini"
    assert config.profile_path(base, "") == base
    assert config.profile_path(base, "p1") == "/tmp/settings.p1.ini"


def test_default_sections_fresh() -> None:
    first = config.default_sections()
    first["General Setting"]["fps"] = 999
    second = config.default_sections()
    assert second["General Setting"]["fps"] == 45


def test_complete_missing_fills_all() -> None:
    parser = make_parser()
    changed = config.complete_missing(parser)
    assert changed  # 空からは全部足される
    assert config.complete_missing(parser) == []  # 2度目は直す所なし


def test_complete_missing_fixes_invalid() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    general = parser["General Setting"]
    general["fps"] = "999"
    general["baud_rate"] = "0"
    general["show_size"] = "1x1"
    parser["Transport"]["name"] = "   "
    parser["Arbitration"]["mode"] = "typo"
    parser["Arbitration"]["cooldown"] = "-1"
    changed = config.complete_missing(parser)
    assert general["fps"] == "45"
    assert general["baud_rate"] == "9600"
    assert general["show_size"] == "640x360"
    assert parser["Transport"]["name"] == "legacy_text"
    assert parser["Arbitration"]["mode"] == "off"
    assert parser["Arbitration"]["cooldown"] == "2.0"
    assert "General Setting.fps" in changed
    assert "Arbitration.mode" in changed


def test_renderer_default_and_sanitize() -> None:
    """renderer の既定値と、不正値を候補値へ戻す動きの検証。"""
    import WindowUtils

    assert config.default_sections()["General Setting"]["renderer"] == "auto"
    assert WindowUtils.RENDERER_VALUES == ["auto", "gdi", "photo"]
    parser = make_parser()
    config.complete_missing(parser)
    general = parser["General Setting"]
    assert general["renderer"] == "auto"
    assert config.complete_missing(parser) == []
    general["renderer"] = "typo"
    changed = config.complete_missing(parser)
    assert general["renderer"] == "auto"
    assert "General Setting.renderer" in changed
    assert config.complete_missing(parser) == []
    del general["renderer"]
    changed = config.complete_missing(parser)
    assert general["renderer"] == "auto"
    assert "General Setting.renderer" in changed


def test_show_mode_default_and_sanitize() -> None:
    """show_mode の既定値と、不正値を候補値へ戻す動きの検証。"""
    from core.display_mode import DEFAULT_SHOW_MODE, SHOW_MODES

    defaults = config.default_sections()["General Setting"]
    assert defaults["show_mode"] == DEFAULT_SHOW_MODE
    parser = make_parser()
    config.complete_missing(parser)
    general = parser["General Setting"]
    assert general["show_mode"] == "fixed"
    assert DEFAULT_SHOW_MODE in SHOW_MODES
    assert config.complete_missing(parser) == []
    general["show_mode"] = "typo"
    changed = config.complete_missing(parser)
    assert general["show_mode"] == "fixed"
    assert "General Setting.show_mode" in changed
    assert config.complete_missing(parser) == []
    # キーごと消して補完が効くことも確かめる（追加したキーの抜け防止）
    del general["show_mode"]
    changed = config.complete_missing(parser)
    assert general["show_mode"] == "fixed"
    assert "General Setting.show_mode" in changed


def test_show_size_accepts_the_960x540_preset() -> None:
    """固定サイズのプリセットに 960x540 が加わった（補正で潰されない）。"""
    from core.display_mode import SHOW_SIZES

    assert "960x540" in SHOW_SIZES
    parser = make_parser()
    config.complete_missing(parser)
    general = parser["General Setting"]
    general["show_size"] = "960x540"
    assert config.complete_missing(parser) == []
    assert general["show_size"] == "960x540"
    # 候補に無い値は従来どおり既定へ戻す（緩めたわけではない）
    general["show_size"] = "800x600"
    changed = config.complete_missing(parser)
    assert general["show_size"] == "640x360"
    assert "General Setting.show_size" in changed


def test_migrate_legacy_keymap() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    hat = parser["KeyMap-Hat"]
    hat["Hat.TOP"] = "10000"
    hat["Hat.RIGHT"] = "1"  # 1文字の数字は押せるキーなので残す
    hat["Hat.LEFT"] = "Key.left"
    changed = config.migrate_legacy_keymap(parser)
    assert hat["Hat.TOP"] == ""
    assert hat["Hat.RIGHT"] == "1"
    assert hat["Hat.LEFT"] == "Key.left"
    assert changed == ["KeyMap-Hat.Hat.TOP"]


def test_ini_round_trip_stable() -> None:
    """既定で書いて読み直しても補正が出ない（書式の安定性）。"""
    import io

    parser = make_parser()
    config.complete_missing(parser)
    buf = io.StringIO()
    parser.write(buf)
    reread = make_parser()
    reread.read_string(buf.getvalue())
    assert config.complete_missing(reread) == []
    assert config.migrate_legacy_keymap(reread) == []


def test_live_min_dwell_backfill_and_clamp() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    # 空からの生成は区画名で報告する既存仕様のため、欠落の補完は
    # キー削除で確かめる（値が既定へ戻り、箇所が報告されること）
    del parser["Transport"]["live_min_dwell_ms"]
    changed = config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    assert "Transport.live_min_dwell_ms" in changed
    assert config.complete_missing(parser) == []  # 既定は安定（round-trip不変）
    parser["Transport"]["live_min_dwell_ms"] = "5"
    config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    parser["Transport"]["live_min_dwell_ms"] = "abc"
    config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    parser["Transport"]["live_min_dwell_ms"] = "32"
    assert config.complete_missing(parser) == []
