"""メインウィンドウの16:9固定を実Tkの挙動で確認する."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator

import WindowGeometry
import pytest
from Menubar import PokeController_Menubar
from Window import PokeControllerApp
from loguru import logger

ASPECT_WIDTH = 16
ASPECT_HEIGHT = 9
LOCK_LABEL = "16:9 に固定"
UNLOCK_LABEL = "固定を解除"


def _drain(root: tk.Tk) -> None:
    """Tkのイベントループを1ターン回し、Configure後のidle処理も流す."""
    root.update()
    root.update_idletasks()


def _attach_policy(app: PokeControllerApp, root: tk.Tk) -> None:
    """本番のapp所有者と同じポリシーを、RED中は未実装にも壊さず付ける."""
    # 現在の実装にはWindowAspectLockがないため、REDでは既存のMenubar経路を使う。
    if hasattr(WindowGeometry, "WindowAspectLock"):
        app._window_aspect_lock = WindowGeometry.WindowAspectLock(root)


def _make_surface(root: tk.Tk) -> tuple[PokeControllerApp, PokeController_Menubar]:
    """実Tkと実Menubarだけの最小app表面を作る."""
    app = PokeControllerApp.__new__(PokeControllerApp)
    app.root = root
    menu = PokeController_Menubar(app)
    root.config(menu=menu)
    _attach_policy(app, root)
    return app, menu


def _reset_root(root: tk.Tk) -> None:
    root.state("normal")
    root.deiconify()
    root.minsize(1, 1)
    root.maxsize(4096, 4096)
    root.geometry("1000x700")
    _drain(root)


@pytest.fixture(scope="module")
def real_tk_root() -> Iterator[tk.Tk]:
    """表示可能なTkを1つだけ使い、テスト間で状態を明示的に戻す."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk rootを作成できません: {exc}")

    _reset_root(root)
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture
def aspect_surface(
    real_tk_root: tk.Tk,
) -> Iterator[tuple[tk.Tk, PokeController_Menubar]]:
    """テスト終了時にpolicyとMenubarの予約を必ず片付ける."""
    app, menu = _make_surface(real_tk_root)
    try:
        yield real_tk_root, menu
    finally:
        policy = getattr(app, "_window_aspect_lock", None)
        if policy is not None:
            policy.cleanup()
        menu.destroy()
        _reset_root(real_tk_root)


@pytest.fixture
def aspect_app(real_tk_root: tk.Tk) -> Iterator[PokeControllerApp]:
    """実Tkだけでappのポリシー所有者を作る."""
    app = PokeControllerApp.__new__(PokeControllerApp)
    app.root = real_tk_root
    app._init_window_aspect_lock()
    try:
        yield app
    finally:
        app._cleanup_window_aspect_lock()
        _reset_root(real_tk_root)


def _invoke_menu_item(menu: tk.Menu, label: str) -> None:
    """表示メニューから実際のcommandを呼ぶ."""
    end = menu.index("end")
    if end is None:
        raise AssertionError("メニュー項目がありません")
    for index in range(end + 1):
        if menu.type(index) != "command":
            continue
        if menu.entrycget(index, "label") == label:
            menu.invoke(index)
            return
    raise AssertionError(f"メニュー項目が見つかりません: {label}")


def _enter_non_normal_state(root: tk.Tk, requested: str) -> None:
    if requested == "withdrawn":
        root.withdraw()
    else:
        root.state(requested)
    _drain(root)
    actual = root.state()
    if actual != requested:
        pytest.skip(f"Tk状態{requested}を設定できません: actual={actual}")


def _assert_exact_16_9(root: tk.Tk) -> None:
    width = root.winfo_width()
    height = root.winfo_height()
    assert width * ASPECT_HEIGHT == height * ASPECT_WIDTH, (
        f"16:9ではありません: {width}x{height}"
    )


def test_enable_snaps_real_window_to_nearest_16_9(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
) -> None:
    """Given: 実Tkが非16:9、When: 実メニューで固定、Then: 整数比になる."""
    root, menu = aspect_surface
    root.geometry("1000x700")
    _drain(root)

    _invoke_menu_item(menu.menu_view, LOCK_LABEL)
    _drain(root)

    _assert_exact_16_9(root)


def test_cleanup_cancels_pending_configure_callback(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
) -> None:
    """Given: pending Configure、When: 再固定とcleanup、Then: Tcl callbackが残らない."""
    root, menu = aspect_surface
    _invoke_menu_item(menu.menu_view, LOCK_LABEL)
    _drain(root)
    root.event_generate("<Configure>", when="now")
    policy = menu.app._window_aspect_lock
    pending_id = policy._after_id
    assert pending_id is not None

    _invoke_menu_item(menu.menu_view, LOCK_LABEL)
    policy.cleanup()

    assert pending_id not in root.tk.call("after", "info")


def test_lock_normalizes_later_programmatic_resize(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
) -> None:
    """Given: 固定中、When: 非16:9を要求、Then: idle後に固定され再発火しない."""
    root, menu = aspect_surface
    root.minsize(1280, 720)
    root.geometry("1280x720")
    _drain(root)
    _invoke_menu_item(menu.menu_view, LOCK_LABEL)
    _drain(root)

    root.geometry("1500x800")
    _drain(root)
    _assert_exact_16_9(root)
    first_size = (root.winfo_width(), root.winfo_height())
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == first_size


def test_unlock_allows_non_16_9_resize(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
) -> None:
    """Given: 固定中、When: 解除してリサイズ、Then: 現在の形を保って解除される."""
    root, menu = aspect_surface
    root.minsize(1280, 720)
    root.geometry("1280x720")
    _drain(root)
    _invoke_menu_item(menu.menu_view, LOCK_LABEL)
    _drain(root)
    locked_size = (root.winfo_width(), root.winfo_height())

    _invoke_menu_item(menu.menu_view, UNLOCK_LABEL)
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == locked_size

    root.geometry("1500x800")
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == (1500, 800)


@pytest.mark.parametrize("requested_state", ("zoomed", "iconic", "withdrawn"))
def test_non_normal_state_is_ignored_until_normal_restore(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
    requested_state: str,
) -> None:
    """Given: 固定中、When: 非normal状態、Then: 状態を保持しnormal復帰で正規化."""
    root, menu = aspect_surface
    _invoke_menu_item(menu.menu_view, LOCK_LABEL)
    _drain(root)
    _enter_non_normal_state(root, requested_state)
    root.geometry("1500x800")
    _drain(root)
    assert root.state() == requested_state
    assert menu.app._window_aspect_lock.enabled

    root.state("normal")
    root.deiconify()
    _drain(root)
    _assert_exact_16_9(root)


def test_poke_controller_app_owns_and_cleans_real_policy(
    aspect_app: PokeControllerApp,
) -> None:
    """Given: hardware未起動のapp、When: policy生成とcleanup、Then: 実Tkを監視停止."""
    root = aspect_app.root
    assert isinstance(aspect_app._window_aspect_lock, WindowGeometry.WindowAspectLock)
    assert aspect_app.set_window_aspect_lock(True) is True
    _drain(root)
    _assert_exact_16_9(root)

    aspect_app._cleanup_window_aspect_lock()
    root.geometry("1500x800")
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == (1500, 800)


def test_impossible_bounds_leave_lock_disabled_without_recursion(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
) -> None:
    """Given: 16:9.fit不能なbounds、When: 固定、Then: 警告のみで無効."""
    root, menu = aspect_surface
    root.minsize(1000, 1000)
    root.maxsize(1200, 1200)
    root.geometry("1000x1000")
    _drain(root)
    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")

    try:
        _invoke_menu_item(menu.menu_view, LOCK_LABEL)
        _drain(root)
    finally:
        logger.remove(sink_id)

    assert any("16:9" in message for message in logs)
    assert not any("縦横比の固定を有効にしました" in message for message in logs)
    root.geometry("1100x1100")
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == (1100, 1100)
    first_size = (root.winfo_width(), root.winfo_height())
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == first_size


def test_existing_window_size_presets_remain_available(
    aspect_surface: tuple[tk.Tk, PokeController_Menubar],
) -> None:
    """Given: 表示メニュー、When: 既存プリセット、Then: 指定サイズを保つ."""
    root, menu = aspect_surface
    root.maxsize(4096, 4096)
    root.geometry("1000x700")
    _drain(root)

    _invoke_menu_item(menu.menu_view, "1280x720")
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == (1280, 720)

    _invoke_menu_item(menu.menu_view, "1920x1080")
    _drain(root)
    assert (root.winfo_width(), root.winfo_height()) == (1920, 1080)
