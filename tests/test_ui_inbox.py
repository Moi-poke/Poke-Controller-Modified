"""裏スレッドから GUI への受け渡し箱の検証。Tk は要らない。"""

from __future__ import annotations

import threading
from typing import Any

from core.ui_inbox import UiInbox, post_latest_to_gui, post_to_gui


def test_post_delivers_every_item_in_order() -> None:
    """Given: 2 件積んだ箱 / When: 取り出す / Then: 積んだ順に全部実行されること。"""
    inbox = UiInbox()
    seen: list[str] = []
    inbox.post(seen.append, "a")
    inbox.post(seen.append, "b")
    assert inbox.drain() == 2
    assert seen == ["a", "b"]


def test_post_latest_keeps_only_the_newest_per_key() -> None:
    """Given: 同じ key で 3 件積む / When: 取り出す / Then: 最後の 1 件だけ実行されること。"""
    inbox = UiInbox()
    seen: list[str] = []
    for value in ("a", "b", "c"):
        inbox.post_latest("k", seen.append, value)
    assert inbox.drain() == 1
    assert seen == ["c"]


def test_drain_does_not_stop_at_a_failing_job() -> None:
    """Given: 例外を投げる 1 件 / When: 取り出す / Then: 残りも実行されること。"""
    inbox = UiInbox()
    seen: list[str] = []

    def _boom() -> None:
        raise RuntimeError("わざと")

    inbox.post(_boom)
    inbox.post(seen.append, "after")
    assert inbox.drain() == 2
    assert seen == ["after"]


def test_worker_thread_does_not_touch_tk() -> None:
    """Given: 裏スレッドからの積み込み / When: 完了を待つ / Then: Tk に触らず積まれること。"""

    class _Root:
        def __init__(self) -> None:
            self.main_ident = threading.get_ident()
            self.afters = 0

        def after(self, ms: int, cb: Any, *args: Any) -> str:
            assert threading.get_ident() == self.main_ident, "裏から after を呼んだ"
            self.afters += 1
            return "id"

    class _Host:
        def __init__(self) -> None:
            self.root = _Root()
            self.inbox = UiInbox()

    host = _Host()
    seen: list[str] = []
    done = threading.Event()

    def _work() -> None:
        post_to_gui(host, seen.append, "from-worker")
        post_latest_to_gui(host, "k", seen.append, "latest")
        done.set()

    thread = threading.Thread(target=_work)
    thread.start()
    assert done.wait(2.0)
    thread.join(2.0)
    assert host.root.afters == 0, "inbox があるのに after を呼んでいる"
    host.inbox.drain()
    # 必達（状態を変える処理）を先に、coalesce した表示更新を後に流す。
    assert seen == ["from-worker", "latest"]


def test_host_without_inbox_falls_back_to_after() -> None:
    """Given: inbox を持たない宿主 / When: 積む / Then: 従来どおり after に回ること。"""

    class _Root:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def after(self, ms: int, cb: Any, *args: Any) -> str:
            self.calls.append((ms, cb, args))
            return "id"

    class _Host:
        def __init__(self) -> None:
            self.root = _Root()

    host = _Host()
    post_to_gui(host, print, "x")
    assert len(host.root.calls) == 1
    assert host.root.calls[0][0] == 0


def test_drain_runs_must_before_the_coalesced_updates() -> None:
    """Given: 表示の更新と必達を積んだ箱 / When: 取り出す / Then: 必達が先に走ること。"""
    inbox = UiInbox()
    seen: list[str] = []
    inbox.post_latest("k", seen.append, "display")
    inbox.post(seen.append, "state")
    inbox.drain()
    assert seen == ["state", "display"]


def test_close_runs_the_on_drop_of_pending_items() -> None:
    """Given: on_drop 付きで積んだ必達 / When: 閉じる / Then: 実行せず後始末だけ走ること。"""
    inbox = UiInbox()
    ran: list[str] = []
    dropped: list[str] = []
    inbox.post(ran.append, "a", on_drop=lambda: dropped.append("a"))
    assert inbox.close() == 1
    assert ran == []
    assert dropped == ["a"]
    # 閉じた後の post は、その場で on_drop を実行する（積んで漏らさない）。
    inbox.post(ran.append, "b", on_drop=lambda: dropped.append("b"))
    assert ran == []
    assert dropped == ["a", "b"]


def test_close_runs_pending_on_drop_on_the_closing_thread() -> None:
    """Given: 必達が残った箱 / When: 閉じる / Then: 呼んだスレッドで on_drop が走ること。"""
    inbox = UiInbox()
    seen: list[str] = []
    inbox.post(
        print, "残り", on_drop=lambda: seen.append(threading.current_thread().name)
    )
    assert inbox.close() == 1
    # 残りは実行せずに捨てるが、後始末は「閉じた側」のその場で走らせる。
    assert seen == ["MainThread"]


def test_post_after_close_runs_on_drop_on_the_posting_thread() -> None:
    """Given: 閉じた箱 / When: 別スレッドから積む / Then: そのスレッドで on_drop が走ること。"""
    inbox = UiInbox()
    inbox.close()
    seen: list[str] = []

    def _post() -> None:
        inbox.post(
            print,
            "捨てる",
            on_drop=lambda: seen.append(threading.current_thread().name),
        )

    worker = threading.Thread(target=_post, name="CameraOpener")
    worker.start()
    worker.join()
    # 実行はされないが、後始末は積んだ側でその場で走る（カメラを閉じる用途）。
    assert seen == ["CameraOpener"]


def test_drain_does_nothing_after_close() -> None:
    """Given: 閉じた箱 / When: 取り出す / Then: 表示の更新も実行しないこと。"""
    inbox = UiInbox()
    seen: list[str] = []
    inbox.post_latest("k", seen.append, "display")
    inbox.close()
    # 閉じた後に積んでも保持しない（破棄済み widget を掴まない）。
    inbox.post_latest("k", seen.append, "display2")
    assert inbox.drain() == 0
    assert seen == []
