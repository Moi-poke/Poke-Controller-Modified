"""接続成功時のランプ初期化が裏仕事でSTATUSを取ることの確認（実機不要）。

契約（serial_panel._sync_bcon_display の裏仕事）：
STATUS取得 → 写しが空のときだけ既定（全消灯相当 b"\\x00"）で種付け
→ 画面反映は既存の after(0) 経路に任せる。500ms巡回
（_poll_player_lamp）は購読写しを読むだけで、Tkから線へは
取りに行かない（Tk固まり防止）。

Tkは作らない（__new__＋窓stubの偽物で代用）。別スレッドは
その場実行の偽物（_InlineThread）で同期的になぞる。4件目は
本物の裏スレッドでTk外走行だけ確かめる。
"""

import threading
import time
from typing import Any

import pytest


class _StubCanvas:
    """ランプ描画の代役。fillだけ記録する。"""

    def __init__(self) -> None:
        self.fills: dict[int, str] = {}

    def itemconfigure(self, item: int, fill: str) -> None:
        self.fills[item] = fill


class _StubLabel:
    """隣接ラベルの代役。文面だけ持つ。"""

    def __init__(self) -> None:
        self.text = ""

    def configure(self, text: str) -> None:
        self.text = text

    config = configure


class _StubVar:
    """StringVarの代役。get/setだけ持つ。"""

    def __init__(self, value: str = "無線") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class _StubRoot:
    """Tk窓の代役。after予約は溜めるだけで走らせない。"""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, Any]] = []

    def after(self, ms: int, func: Any) -> int:
        self.scheduled.append((ms, func))
        return len(self.scheduled)

    def flush(self) -> None:
        """溜めた予約を呼出し順にその場実行する。"""
        queued, self.scheduled = list(self.scheduled), []
        for _, func in queued:
            func()


class _StatusSpyTransport:
    """線の代役。request_status呼び出し口だけ記録する。"""

    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self, mode: str = "ok", lamp: bytes = b"") -> None:
        # 呼び出し記録：要求timeout列・呼出しスレッド列。
        self.timeouts: list[float] = []
        self.idents: list[int] = []
        # 応答の振る舞い：ok（写し返却）/none（未達）/boom（例外）。
        self.mode = mode
        self.rx_subs: list[Any] = []
        # 運搬器の保持する直近PLAYER_INFO写し（付随受信済みの想定）。
        self.lamp = bytes(lamp)
        # 購読と要求の順序記録（購読→要求の順であることの確認用）。
        self.events: list[str] = []
        # last_status()の写し。有線bitつき（SSOTはprotocol_v3.mdのbit5）。
        self.status: dict[str, int] = {
            "flags": 0x20,
            "last_seq": 7,
            "err_crc": 0,
            "err_drop": 0,
            "errcode": 0,
        }

    def subscribe_rx(self, func: Any) -> Any:
        self.events.append("subscribe")
        if func not in self.rx_subs:
            self.rx_subs.append(func)

        def _unsub() -> None:
            if func in self.rx_subs:
                self.rx_subs.remove(func)

        return _unsub

    def last_status(self) -> dict[str, int]:
        """直近STATUSの写し。呼ぶだけで線に触れない。"""
        return dict(self.status)

    def last_player_info(self) -> bytes:
        """直近PLAYER_INFOの写し。呼ぶだけで線に触れない。"""
        return bytes(self.lamp)

    def request_status(self, timeout: float = 1.0) -> dict[str, int] | None:
        """STATUS_REQ往復の代役。口とtimeoutだけ記録する。"""
        self.events.append("request")
        self.timeouts.append(float(timeout))
        self.idents.append(threading.get_ident())
        if self.mode == "none":
            return None
        if self.mode == "boom":
            raise RuntimeError("線が切れた想定")
        return dict(self.status)


class _InlineThread:
    """threading.Threadの代役。start()でその場実行する。"""

    def __init__(self, target: Any = None, daemon: Any = None) -> None:
        self._target = target
        self.daemon = daemon

    def start(self) -> None:
        self._target()


def _make_panel(transport: Any) -> Any:
    """Tkを作らずSerialPanelMixinの殻だけ用意する。"""
    from ui.serial_panel import SerialPanelMixin

    mixin: Any = SerialPanelMixin.__new__(SerialPanelMixin)
    mixin._player_lamp_items = [20, 21, 22, 23]
    mixin.player_lamp_canvas = _StubCanvas()
    mixin.rumble_label = _StubLabel()
    mixin.bcon_wired = _StubVar("無線")
    mixin.root = _StubRoot()
    # 運搬器はbcon固定の偽物（能力ではなく直結で返す）。
    mixin._bcon_transport = lambda: transport  # type: ignore[method-assign]
    # 巡回が読む購読写し。未受信の初期値は空。
    mixin._player_info_cache = b""
    mixin._rumble_cache = b""
    mixin._player_lamp_after = None
    mixin._player_lamp_unsub = None
    mixin._player_lamp_transport = None
    return mixin


def _run_job_inline(monkeypatch: pytest.MonkeyPatch) -> list[_InlineThread]:
    """裏仕事をその場実行の偽物に差し替え、立った仕事を返す。"""
    started: list[_InlineThread] = []

    def _factory(*args: Any, **kwargs: Any) -> _InlineThread:
        thread = _InlineThread(*args, **kwargs)
        started.append(thread)
        return thread

    monkeypatch.setattr(threading, "Thread", _factory)
    return started


def test_Given_接続直後_When_接続成功ジョブが走る_Then_STATUSをtimeout1秒で取得しランプ写しが種付けされる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裏仕事のSTATUS取得でランプ写しが種付けされる想定。"""
    transport = _StatusSpyTransport(mode="ok")
    mixin = _make_panel(transport)

    # Given：接続直後・未受信（写しは空）。When：接続成功ジョブを走らせる。
    started = _run_job_inline(monkeypatch)
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立つ。
    assert len(started) == 1
    # Then：STATUSをtimeout1.0でちょうど1回取得する（初回で届く想定）。
    assert transport.timeouts == [1.0], (
        f"裏仕事がSTATUSを取っていない。呼び出しtimeout列={transport.timeouts}"
    )
    # Then：同じSTATUSでランプ写しも種付けされる。
    assert bytes(mixin._player_info_cache) != b""
    # 予約還流を流すと有線表示には届く。
    mixin.root.flush()
    assert mixin.bcon_wired.get() == "有線"


def test_Given_STATUSがNone_When_接続成功ジョブ_Then_落とさず写しは空のまま(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未達（None）でも落とさず、写しは空のままの想定。"""
    transport = _StatusSpyTransport(mode="none")
    mixin = _make_panel(transport)

    # Given：STATUS未達。取直し待ちはその場では進めない（速く回す）。
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    started = _run_job_inline(monkeypatch)

    # When：接続成功ジョブを走らせる（raiseしないこと）。
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立ち、取得はtimeout1.0だけを使う。
    assert len(started) == 1
    assert transport.timeouts and all(t == 1.0 for t in transport.timeouts), (
        f"取得timeoutが想定外。呼び出しtimeout列={transport.timeouts}"
    )
    # Then：写しは空のまま（既定も置かない）。表示も据え置き。
    assert bytes(mixin._player_info_cache) == b""
    mixin.root.flush()
    assert mixin.bcon_wired.get() == "無線"
    # 巡回を1巡させても全消灯のまま。
    mixin._poll_player_lamp()
    assert all(color == "gray" for color in mixin.player_lamp_canvas.fills.values())


def test_Given_STATUS取得が例外_When_接続成功ジョブ_Then_落とさず写しは空のまま(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """例外でも落とさず、写しは空のままの想定。"""
    transport = _StatusSpyTransport(mode="boom")
    mixin = _make_panel(transport)

    # Given：取得が例外。取直し待ちはその場では進めない（速く回す）。
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    started = _run_job_inline(monkeypatch)

    # When：接続成功ジョブを走らせる（raiseしないこと）。
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立ち、取得はtimeout1.0だけを使う。
    assert len(started) == 1
    assert transport.timeouts and all(t == 1.0 for t in transport.timeouts), (
        f"取得timeoutが想定外。呼び出しtimeout列={transport.timeouts}"
    )
    # Then：写しは空のまま（既定も置かない）。表示も据え置き。
    assert bytes(mixin._player_info_cache) == b""
    mixin.root.flush()
    assert mixin.bcon_wired.get() == "無線"
    # 巡回を1巡させても全消灯のまま。
    mixin._poll_player_lamp()
    assert all(color == "gray" for color in mixin.player_lamp_canvas.fills.values())


def test_Given_Tkスレッド想定_When_接続成功ジョブ_Then_STATUS取得は別スレッドで走る() -> (
    None
):
    """STATUS取得はTkを止めないよう別スレッドで走る想定。"""
    transport = _StatusSpyTransport(mode="ok")
    mixin = _make_panel(transport)
    caller = threading.get_ident()

    # Given：呼出し側をTkスレッドとみなす。When：接続成功ジョブを走らせる。
    # 本物の裏スレッドで走らせ、取得が来るまで待つ（最大5秒）。
    mixin._sync_bcon_display()
    deadline = time.monotonic() + 5.0
    while not transport.timeouts and time.monotonic() < deadline:
        time.sleep(0.01)

    # Then：取得は呼出しスレッドとは別スレッドで走る。
    assert transport.idents, (
        "裏仕事がSTATUSを取っていない。呼出し側スレッドでSTATUSは取られていない。"
    )
    assert all(ident != caller for ident in transport.idents)


def test_Given_接続成功ジョブ_When_STATUS取得_Then_ランプ写しが種付けされる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """接続成功ジョブのSTATUS取得でランプ写しが種付けされる想定。

    今日の取得は有線/無線表示にだけ使われ、ランプには届かない。
    """
    transport = _StatusSpyTransport(mode="ok")
    mixin = _make_panel(transport)
    started: list[_InlineThread] = []

    def _factory(*args: Any, **kwargs: Any) -> _InlineThread:
        thread = _InlineThread(*args, **kwargs)
        started.append(thread)
        return thread

    # Given：接続成功（bcon運搬器あり）。別スレッドはその場実行の偽物にする。
    monkeypatch.setattr(threading, "Thread", _factory)

    # When：接続成功ジョブを走らせる。
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立つ（今日の契約どおり）。
    assert len(started) == 1
    # 予約還流を流すと有線表示には届く（今日の到達点の固定）。
    mixin.root.flush()
    assert mixin.bcon_wired.get() == "有線"
    # Then：同じSTATUSでランプ写しも種付けされる（今日は未配線で失敗する）。
    lamp_seeded = bytes(mixin._player_info_cache) != b"" or any(
        color != "gray" for color in mixin.player_lamp_canvas.fills.values()
    )
    assert lamp_seeded, (
        "RED想定：STATUS取得は有線表示にだけ使われランプ初期化は未配線。"
        f"取得timeout列={transport.timeouts}"
    )


def test_Given_保持に真値あり_When_接続成功ジョブ_Then_写しに真値が入る(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """運搬器の保持が非空なら写しは真値になる想定（既定では失敗する）。"""
    transport = _StatusSpyTransport(mode="ok", lamp=b"\x05")
    mixin = _make_panel(transport)

    # Given：付随受信済み（保持に真値）・写しは空。When：接続成功ジョブ。
    started = _run_job_inline(monkeypatch)
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立つ。
    assert len(started) == 1
    # Then：購読を結んでからSTATUSを取る（付随の取りこぼし防止）。
    assert transport.events.index("subscribe") < transport.events.index("request"), (
        f"購読より先にSTATUSを取っている。順序={transport.events}"
    )
    # Then：写しは保持の真値そのもの（既定の全消灯ではない）。
    assert bytes(mixin._player_info_cache) == b"\x05", (
        f"写しが真値でない。写し={mixin._player_info_cache!r}"
    )
    # 巡回を1巡させると1・3番が点く。
    mixin._poll_player_lamp()
    fills = mixin.player_lamp_canvas.fills
    assert fills[mixin._player_lamp_items[0]] == "green yellow"
    assert fills[mixin._player_lamp_items[1]] == "gray"
    assert fills[mixin._player_lamp_items[2]] == "green yellow"


def test_Given_保持が空_When_接続成功ジョブ_Then_既定で種付けされる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保持も空なら既定（全消灯相当 b"\\x00"）で種付けの想定。"""
    transport = _StatusSpyTransport(mode="ok", lamp=b"")
    mixin = _make_panel(transport)

    # Given：保持も写しも空。When：接続成功ジョブ。
    started = _run_job_inline(monkeypatch)
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立ち、写しは既定で種付けされる。
    assert len(started) == 1
    assert bytes(mixin._player_info_cache) == b"\x00", (
        f"既定で種付けされていない。写し={mixin._player_info_cache!r}"
    )


def test_Given_写しに真値あり_When_接続成功ジョブ_Then_上書きしない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写しに真値があれば保持・既定のどちらでも上書きしない想定。"""
    transport = _StatusSpyTransport(mode="ok", lamp=b"\x07")
    mixin = _make_panel(transport)
    # Given：購読で真値が届き済み（購読は結ばれ、写しに真値あり）。
    mixin._player_lamp_transport = transport
    mixin._player_lamp_unsub = lambda: None
    mixin._player_info_cache = b"\x03"

    # When：接続成功ジョブ。
    started = _run_job_inline(monkeypatch)
    mixin._sync_bcon_display()

    # Then：裏仕事は1本だけ立ち、写しは届き済みの真値のまま。
    assert len(started) == 1
    assert bytes(mixin._player_info_cache) == b"\x03", (
        f"届き済みの真値が上書きされた。写し={mixin._player_info_cache!r}"
    )
