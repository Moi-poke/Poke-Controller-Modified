"""送信内容の表示可否を、送信スレッドが Tk 変数から読まないことの検証。

Tk の変数は GUI スレッドのもの。worker から読むと、GUI が忙しい間
（Tcl が回っていない間）そこで待たされる。実機で測ると 1.5 秒待たされ、
その間は姿勢が1本も送られなかった。ここでは「読まないこと」を、
読み取りを記録する代役で確かめる。
"""

import threading

from core import Sender
from fakes import FakeTransport


class RecordingVar:
    """Tk 変数の代役。どのスレッドから読まれたかを記録する。"""

    def __init__(self, value: bool = False) -> None:
        self.value = value
        self.readers: list[str] = []

    def get(self) -> bool:
        self.readers.append(threading.current_thread().name)
        return self.value


def make_sender(is_show_serial: object) -> Sender.Sender:
    return Sender.Sender(is_show_serial=is_show_serial, transport=FakeTransport())


def read_from_worker(sender: Sender.Sender) -> list[bool]:
    """送信スレッドと同じ立場（GUI 以外のスレッド）で表示可否を尋ねる。"""
    seen: list[bool] = []
    worker = threading.Thread(
        target=lambda: seen.append(sender._should_show_serial()),
        name="probe-worker",
    )
    worker.start()
    worker.join(timeout=5)
    return seen


def test_worker_thread_does_not_read_the_tk_variable_for_show_serial() -> None:
    """送信スレッドは Tk 変数を読まない（GUI が忙しいと待たされるため）。

    Given: Tk 変数の代役を渡し、表示する側へ倒した Sender
    When : GUI 以外のスレッドから表示可否を尋ねる
    Then : 代役はそのスレッドからは読まれず、押し込まれた値が返る
    """
    var = RecordingVar(False)
    sender = make_sender(var)

    sender.setShowSerial(True)
    seen = read_from_worker(sender)

    assert seen == [True]
    assert "probe-worker" not in var.readers


def test_pushed_value_is_what_the_worker_uses() -> None:
    """押し込んだ値が、そのまま送信スレッドの答えになる。

    Given: 素の bool で作った Sender
    When : 表示する側／しない側へ押し込み、そのたびに worker から尋ねる
    Then : 押し込んだ値がそのまま返る
    """
    sender = make_sender(False)

    assert read_from_worker(sender) == [False]
    sender.setShowSerial(True)
    assert read_from_worker(sender) == [True]
    sender.setShowSerial(False)
    assert read_from_worker(sender) == [False]


def test_main_thread_still_follows_the_tk_variable() -> None:
    """GUI スレッドからは今までどおり Tk 変数に従う（表示の切替を保つ）。

    Given: 代役を渡した Sender
    When : 代役の値を変え、GUI スレッドから尋ねる
    Then : 新しい値が返り、代役は読まれている
    """
    var = RecordingVar(True)
    sender = make_sender(var)
    var.readers.clear()

    var.value = False
    assert sender._should_show_serial() is False
    assert var.readers == [threading.current_thread().name]


def test_plain_bool_keeps_working() -> None:
    """素の bool を渡す従来の使い方も壊れない。

    Given: bool で作った Sender
    When : 値を押し込んで尋ねる
    Then : 押し込んだ値が返る
    """
    sender = make_sender(True)
    assert sender._should_show_serial() is True
    sender.setShowSerial(False)
    assert sender._should_show_serial() is False


def test_control_a_direct_read_from_a_worker_thread_is_detected() -> None:
    """対照: 直に読めば worker から読まれること（見張りが働くこと）を示す。

    Given: 代役の Tk 変数
    When : GUI 以外のスレッドから直に読む
    Then : そのスレッド名が記録に残る（上の検査が空振りでないこと）
    """
    var = RecordingVar(False)
    worker = threading.Thread(target=var.get, name="control-worker")
    worker.start()
    worker.join(timeout=5)

    assert var.readers == ["control-worker"]
