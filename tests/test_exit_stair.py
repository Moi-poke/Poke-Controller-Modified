"""終了処理の棄権経路（プレビューが止まらない場合）を固定する。実 Tk は要らない。"""

from __future__ import annotations

from typing import Any

import Window


class _Surface:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _Preview:
    def __init__(self) -> None:
        self.surface = _Surface()

    def UnbindLeftClick(self) -> None:
        pass

    def UnbindRightClick(self) -> None:
        pass


class _Runner:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self, ser: Any) -> bool:
        self.shutdown_calls += 1
        return True


class _Serial:
    def __init__(self) -> None:
        self.stopped = 0

    def stop_keyboard(self) -> None:
        self.stopped += 1

    def shutdown(self) -> bool:
        self.stopped += 1
        return False


class _Root:
    def __init__(self) -> None:
        self.destroyed = 0

    def destroy(self) -> None:
        self.destroyed += 1


class _FakeApp:
    """_continue_exit だけを本物から借りた偽アプリ。"""

    EXIT_PREVIEW_RETRY_MAX = 3
    _continue_exit = Window.PokeControllerApp._continue_exit
    _exit_step = Window.PokeControllerApp._exit_step

    def __init__(self) -> None:
        self._exit_requested = True
        self._exit_phase = "stopping_preview"
        self._exit_retry_after_id: str | None = None
        self._exit_preview_retries = 0
        self._exit_surface_abandoned = False
        self._closing = True
        self.preview = _Preview()
        self.menu = None
        self.serial = _Serial()
        self.runner = _Runner()
        self.camera: Any = None
        self.inbox: Any = None
        self.root = _Root()
        self.ser: Any = None
        self.settings = None
        self.log_pane = None
        self.profile = "test"
        self.command_stats: dict[str, Any] = {}
        self.retries = 0

    # 実物は after を予約するが、ここでは即座に何もしない（回数だけ数える）。
    def _stop_preview_for_exit(self) -> Any:
        return Window.StopResult.PENDING

    def _schedule_preview_stop_retry(self) -> None:
        self.retries += 1

    def _close_bcon_tab(self) -> None:
        pass

    def closingController(self) -> None:
        pass

    def _save_exit_settings_and_stats(self) -> None:
        pass

    def _stop_audio(self) -> None:
        pass


def test_stuck_preview_is_abandoned_after_the_retry_limit() -> None:
    """Given: プレビューが止まらない / When: 終了を回す / Then: 上限で諦め、解放は省き、窓は閉じること。"""
    app = _FakeApp()
    for _ in range(app.EXIT_PREVIEW_RETRY_MAX + 2):
        app._continue_exit()
    assert app.retries == app.EXIT_PREVIEW_RETRY_MAX, app.retries
    assert app.preview.surface.released == 0, "止まっていない面を解放している"
    assert app.root.destroyed == 1, "窓が閉じていない"
    assert app._exit_phase == "root_destroyed"


def test_all_teardown_steps_run_even_if_one_fails() -> None:
    """Given: 途中の段が例外を投げる / When: 終了を回す / Then: 残りの段と窓の破棄まで進むこと。"""
    app = _FakeApp()
    app._exit_phase = "preview_stopped"

    def _boom() -> None:
        raise RuntimeError("わざと")

    app._close_bcon_tab = _boom  # type: ignore[method-assign]
    app._continue_exit()
    assert app.runner.shutdown_calls == 1, "後続の段が飛んでいる"
    assert app.serial.stopped >= 1, "シリアルの停止が飛んでいる"
    assert app.root.destroyed == 1


class _Camera:
    def __init__(self) -> None:
        self.destroy_calls = 0

    def destroy(self) -> None:
        self.destroy_calls += 1


def test_camera_closed_by_the_inbox_is_closed_again_by_the_exit_stair() -> None:
    """Given: 開き途中のカメラが受け渡し箱に残る / When: 終了を回す / Then: 二重に閉じても壊れないこと。"""
    from core.ui_inbox import UiInbox

    app = _FakeApp()
    app._exit_phase = "preview_stopped"
    camera = _Camera()
    app.camera = camera
    app.inbox = UiInbox()
    # 開き途中の結果が残っている（捨てられるときに on_drop で閉じる）。
    app.inbox.post(print, "開き途中", on_drop=camera.destroy)

    app._continue_exit()

    # 受け渡し箱の後始末と、終了階段の「カメラ」段の両方から閉じに来る。
    # 2 回呼ばれても壊れないことが Camera.destroy の前提（開き直しでも通る）。
    assert camera.destroy_calls == 2
    assert app.root.destroyed == 1
