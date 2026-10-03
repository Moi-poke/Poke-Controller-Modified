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
    assert "wm_aspect" in src


def test_the_preview_size_combobox_is_gone_from_the_camera_panel() -> None:
    """表示サイズ欄はダイアログへ移し、コンボボックスと確認ダイアログは無い。"""
    from ui.camera_panel import CameraPanelMixin

    assert hasattr(CameraPanelMixin, "applyDisplaySettings")
    assert not hasattr(CameraPanelMixin, "applyWindowSize"), (
        "コンボボックス前提の applyWindowSize が残っている"
    )
    src = Path("SerialController/ui/camera_panel.py").read_text(encoding="utf-8")
    assert "show_size_cb" not in src
    assert "show_size_tmp" not in src
    assert "askokcancel" not in src, "拡大縮小でモーダル確認している"
    assert "preview_layout" in src


def test_the_display_settings_dialog_offers_two_modes_and_one_size_picker() -> None:
    """ダイアログは表示モード2つと固定サイズ欄だけを持つ。Tkは作らず源を見る。"""
    import ast

    from ui.display_settings import DisplaySettingsDialog

    src = Path("SerialController/ui/display_settings.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    modes = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "固定サイズ" in modes
    assert "ウィンドウに合わせる（16:9 を維持）" in modes
    for label in ("適用", "OK", "キャンセル"):
        assert label in modes, f"{label} ボタンが無い"
    assert "disabled" in modes, "fit 時に固定サイズ欄を無効化する文字列が無い"
    # Window 本体は読まない（循環になる）
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "Window" not in imported
    assert callable(DisplaySettingsDialog)


def test_window_moves_the_show_mode_between_the_setting_and_the_panel() -> None:
    """読込・保存の両方で show_mode を設定ファイルへ往復させる（片道だと残る）。"""
    window = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert "self.show_mode.set(self.settings.show_mode.get())" in window
    assert "self.settings.show_mode.set(self.show_mode.get())" in window
    # 消したコンボボックス経路は残さない
    assert "show_size_cb" not in window
    assert "show_size_tmp" not in window
    settings = Path("SerialController/Settings.py").read_text(encoding="utf-8")
    assert 'self.show_mode = tk.StringVar(value=general.get("show_mode"))' in settings
    assert '"show_mode": self.show_mode.get()' in settings


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
    """最小サイズは小さい下限だけ。収まらない分はスクロールバーで届く。

    中身の要求寸法を下限にすると、複数台を 1 画面に並べられない。見切れは
    ScrollHost が受け持つ（tests/test_scroll_host_contract.py）。
    """
    from Window import MIN_COMPACT_WIDTH, MIN_WINDOW_WIDTH, PokeControllerApp

    assert hasattr(PokeControllerApp, "_apply_content_minsize")
    assert MIN_COMPACT_WIDTH < MIN_WINDOW_WIDTH
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert ".minsize(" in src
    assert "ScrollHost(self.root)" in src
    # レイアウトが確定してからでないと下限を誤る。構築時厳禁。
    assert src.index("_build_preview()") < src.index("self._apply_content_minsize()")
    build_ui = src[src.index("def _build_ui") : src.index("def _build_setting_tabs")]
    assert "self._apply_content_minsize()" not in build_ui


def test_window_puts_the_three_areas_on_draggable_panes() -> None:
    """プレビュー・設定タブ・ログは仕切り（PaneArranger）に載せる。

    行の重みで固定していた頃は大きさを変えられなかった。並べ方と仕切りの
    振る舞いは tests/test_pane_layout_contract.py が実物の Tk で見る。
    """
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert "PaneArranger(" in src
    for area in (
        '"preview": self.camera_lf',
        '"tabs": self.setting_nb',
        '"log": self.log_area',
    ):
        assert area in src
    assert "self.frame_1.rowconfigure(1, weight=1)" not in src


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


def test_audio_tab_has_no_diagnostic_record_or_latency_buttons() -> None:
    """Test Rec と遅延計測は外した。Monitor と Level で疎通は確かめられ、
    実測値はどこからも使われていなかったため（計測本体は tools 用に core に残す）。
    """
    from ui.audio_panel import AudioPanelMixin

    assert not hasattr(AudioPanelMixin, "recordAudioTest")
    assert not hasattr(AudioPanelMixin, "measureLatency")
    src = Path("SerialController/ui/audio_panel.py").read_text(encoding="utf-8")
    assert "Test Rec" not in src
    assert "遅延計測" not in src
