"""メインGUIの表示・ランプ・bcon行のhostベクタ（実機不要・Tkを作らない）。

窓自体は作らない（ヘッドレス）。Tkを起こさず、源の静的検査と
公開口の有無だけ見る。実機の見た目確認は手動QAで行う。
"""

from pathlib import Path


def test_menubar_view_menu_has_size_presets() -> None:
    """表示メニューに全体サイズの指定と比率固定がある。"""
    from Menubar import PokeController_Menubar

    assert hasattr(PokeController_Menubar, "applyWindowSize")
    assert hasattr(PokeController_Menubar, "lockAspect")
    src = Path("SerialController/Menubar.py").read_text(encoding="utf-8")
    assert "表示" in src
    assert "1280x720" in src
    assert "1920x1080" in src
    assert "16:9" in src


def test_menubar_has_no_wake_setup() -> None:
    """wakeconは非推奨でメニューに出さない。bconは残す。"""
    from Menubar import PokeController_Menubar

    assert not hasattr(PokeController_Menubar, "OpenWakeSetup")
    assert hasattr(PokeController_Menubar, "OpenBconSetup")
    src = Path("SerialController/Menubar.py").read_text(encoding="utf-8")
    assert "Switch2 Wake設定" not in src
    assert "WakeSetup" not in src
    assert "Bcon設定" in src


def test_window_has_minimum_size() -> None:
    """全ウィジェット可視のため最小サイズを制限する。

    背の高いタブの見切れ防止に、中身の要求寸法を下限にする。
    """
    from Window import PokeControllerApp

    assert hasattr(PokeControllerApp, "_apply_content_minsize")
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert "winfo_reqwidth" in src
    assert "winfo_reqheight" in src
    assert ".minsize(" in src
    # 計測はプレビュー確定後でないと小さな値になる。構築時厳禁。
    assert src.index("_build_preview()") < src.index("self._apply_content_minsize()")
    build_ui = src[src.index("def _build_ui") : src.index("def _build_setting_tabs")]
    assert "self._apply_content_minsize()" not in build_ui


def test_window_rows_stretch_vertically() -> None:
    """タブ欄とログ欄の行は縦に伸びる。"""
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert "self.frame_1.rowconfigure(1, weight=1)" in src
    assert "self.frame_1.rowconfigure(2, weight=1)" in src


def test_transport_list_has_switch_bcon_without_pico() -> None:
    """Transport候補はswitch-bcon表記でpico_uart無し。旧名も通る。"""
    from core.transport import list_transports, resolve_transport_name

    assert "switch-bcon" in list_transports()
    assert "switch-bcon-proc" in list_transports()
    assert "pico_uart" not in list_transports()
    assert resolve_transport_name("bcon") == "switch-bcon"


def test_setting_tab_change_releases_focus() -> None:
    """タブ切替でフォーカスをタブ枠へ戻す（矢印キー奪取防止）。"""
    from Window import PokeControllerApp

    assert hasattr(PokeControllerApp, "_unfocus_setting_tab")
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert "NotebookTabChanged" in src
    assert "focus_set" in src


def test_setting_tabs_separate_four_sections() -> None:
    """シリアル/コントローラ/オーディオ/コマンドはタブ分離する。"""
    import Window

    assert hasattr(Window.PokeControllerApp, "_build_setting_tabs")
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    for tab in ("シリアル", "コントローラ", "オーディオ", "コマンド"):
        assert tab in src
    for path, frame in (
        ("SerialController/ui/serial_panel.py", "tab_serial"),
        ("SerialController/ui/serial_panel.py", "tab_controller"),
        ("SerialController/ui/audio_panel.py", "tab_audio"),
        ("SerialController/ui/command_panel.py", "tab_command"),
    ):
        assert frame in Path(path).read_text(encoding="utf-8")


def test_serial_panel_has_lamp_and_bcon_rows() -> None:
    """接続設定にLED省スペース表示とbcon切替行がある。"""
    from ui.serial_panel import SerialPanelMixin

    for name in (
        "applyBconWired",
        "applyBconEmulate",
        "_bcon_transport",
        "_bcon_only_widgets",
        "_refresh_bcon_rows",
        "_sync_bcon_display",
        "_apply_bcon_wired_display",
        "_poll_player_lamp",
        "_on_bcon_rx_frame",
        "_ensure_player_lamp_subscription",
        "_cancel_player_lamp_patrol",
    ):
        assert hasattr(SerialPanelMixin, name), name
    src = Path("SerialController/ui/serial_panel.py").read_text(encoding="utf-8")
    assert "player_lamp_canvas" in src
    # 巡回は購読写しだけ読む（TkからRPCしない）。巡回本体にgetter名は無い。
    # 接続直後の裏仕事（_sync_bcon_display job）だけが保持の真値を写す。
    assert "subscribe_rx" in src
    assert "_player_info_cache" in src
    poll = src[src.index("def _poll_player_lamp") : src.index("def _start_serial")]
    assert "last_player_info" not in poll
    assert "last_rumble" not in poll
    assert "request_status" not in poll
    job = src[
        src.index("def _sync_bcon_display") : src.index("def _apply_bcon_wired_display")
    ]
    assert "last_player_info" in job
    assert "set_emulate_mode" in src
    assert "set_wired_mode" in src
    assert "grid_remove" in src


def test_bcon_only_widgets_hidden_without_bcon() -> None:
    """bcon以外ではLED・切替を見せない。ログは幅で折り返す。"""
    src = Path("SerialController/ui/serial_panel.py").read_text(encoding="utf-8")
    assert "_bcon_only_widgets" in src
    utils = Path("SerialController/WindowUtils.py").read_text(encoding="utf-8")
    assert 'wrap="word"' in utils
