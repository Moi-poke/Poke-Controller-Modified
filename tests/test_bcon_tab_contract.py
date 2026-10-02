"""Bconタブのhostベクタ（実機・Tk不要・RED先行）。

Bcon設定を別窓（Toplevel）から「シリアル」の隣のタブへ移す。
タブは Transport の選択値が bcon 系（能力 BCON_STATE）のときだけ出る。
Tk は作らず、偽の Notebook と偽の BconSetup で出し入れの順序と
後始末だけを見る。実機・実 Tk の見た目は手動 QA で確かめる。
"""

from pathlib import Path
from typing import Any

import pytest

BCON_TAB_TEXT = "Bcon"


class _FakeTab:
    """ttk.Frame の代役。str() が Notebook 上の識別子になる。"""

    def __init__(self, name: str) -> None:
        self._name = name

    def __str__(self) -> str:
        return self._name


class _FakeNotebook:
    """ttk.Notebook の代役。tabs / insert / forget / select だけ持つ。"""

    def __init__(self, tabs: list[_FakeTab]) -> None:
        self._tabs = list(tabs)
        self.texts: dict[str, str] = {}
        self.selected: str = ""

    def tabs(self) -> tuple[str, ...]:
        return tuple(str(tab) for tab in self._tabs)

    def insert(self, pos: int, child: _FakeTab, **kw: Any) -> None:
        self._tabs = [tab for tab in self._tabs if str(tab) != str(child)]
        self._tabs.insert(pos, child)
        self.texts[str(child)] = str(kw.get("text", ""))

    def forget(self, child: _FakeTab) -> None:
        self._tabs = [tab for tab in self._tabs if str(tab) != str(child)]

    def select(self, child: Any = None) -> str:
        if child is not None:
            self.selected = str(child)
        return self.selected


class _FakeSerial:
    """SerialService の代役。通信方式名 → bcon か、だけ答える。"""

    def __init__(self) -> None:
        self.sender = object()

    def uses_bcon(self, name: str) -> bool:
        return name in ("switch-bcon", "switch-bcon-proc")


class _FakeVar:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class _FakeSetup:
    """BconSetup の代役。作られ方と閉じられ方だけ覚える。"""

    made: list["_FakeSetup"] = []

    def __init__(self, master: Any, sender: Any, **kw: Any) -> None:
        self.master = master
        self.sender = sender
        self.kw = kw
        self.closed = 0
        _FakeSetup.made.append(self)

    def close(self) -> None:
        self.closed += 1


def _make_app(monkeypatch: pytest.MonkeyPatch, transport: str) -> Any:
    """Tk を作らず BconPanelMixin の殻だけ用意する。"""
    import ui.bcon_panel as bcon_panel
    from ui.bcon_panel import BconPanelMixin

    _FakeSetup.made = []
    monkeypatch.setattr(bcon_panel, "BconSetup", _FakeSetup)
    app: Any = BconPanelMixin.__new__(BconPanelMixin)
    tabs = [
        _FakeTab(".nb.serial"),
        _FakeTab(".nb.controller"),
        _FakeTab(".nb.audio"),
        _FakeTab(".nb.command"),
    ]
    app.tab_serial = tabs[0]
    app.tab_bcon = _FakeTab(".nb.bcon")
    app.setting_nb = _FakeNotebook(tabs)
    app.serial = _FakeSerial()
    app.transport_name = _FakeVar(transport)
    app._bcon_setup = None
    return app


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("switch-bcon", True),
        ("switch-bcon-proc", True),
        ("bcon", True),  # 旧名は現行名へ読み替える
        ("legacy_text", False),
        ("", False),
        ("no-such-transport", False),
    ],
)
def test_Given_通信方式名_When_能力を引く_Then_bcon系だけがBCON_STATEになる(
    name: str, expected: bool
) -> None:
    # Given/When: 登録簿から能力を引く（名前の直書きで判定しない）
    from core.transport import transport_capability
    from core.transport.base import BCON_STATE

    # Then
    assert (transport_capability(name) == BCON_STATE) is expected


def test_Given_未知の通信方式名_When_能力を引く_Then_画面へ何も出さない(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: 画面更新のたびに呼ぶ判定なので、警告の print を出してはいけない
    from core.transport import transport_capability

    # When
    transport_capability("no-such-transport")

    # Then
    assert capsys.readouterr().out == ""


def test_Given_サービス_When_bcon方式かを問う_Then登録簿の能力で答える() -> None:
    from services.serial_service import SerialService

    assert SerialService.uses_bcon("switch-bcon") is True
    assert SerialService.uses_bcon("switch-bcon-proc") is True
    assert SerialService.uses_bcon("legacy_text") is False


def test_Given_bcon方式を選択_When_タブを更新_Then_シリアルの直後に出て中身を作る(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "switch-bcon")

    # When
    app._refresh_bcon_tab()

    # Then: 「シリアル」の次（index 1）。他のタブの順序は変えない
    assert app.setting_nb.tabs() == (
        ".nb.serial",
        ".nb.bcon",
        ".nb.controller",
        ".nb.audio",
        ".nb.command",
    )
    assert app.setting_nb.texts[".nb.bcon"] == BCON_TAB_TEXT
    assert len(_FakeSetup.made) == 1
    setup = _FakeSetup.made[0]
    assert setup.master is app.tab_bcon
    assert setup.kw.get("embedded") is True
    # 送り先は作り直されうるので、握り込まず都度引ける口を渡す
    provider = setup.kw.get("sender_provider")
    assert callable(provider)
    assert provider() is app.serial.sender


def test_Given_legacy方式を選択_When_タブを更新_Then_タブは出ない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "legacy_text")

    # When
    app._refresh_bcon_tab()

    # Then
    assert ".nb.bcon" not in app.setting_nb.tabs()
    assert _FakeSetup.made == []


def test_Given_bconタブ表示中_When_legacyへ切替_Then_タブを外し中身を閉じる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "switch-bcon")
    app._refresh_bcon_tab()
    setup = _FakeSetup.made[0]

    # When
    app.transport_name.set("legacy_text")
    app._refresh_bcon_tab()

    # Then: 購読・期限予約を残さないよう close を通す
    assert ".nb.bcon" not in app.setting_nb.tabs()
    assert setup.closed == 1
    assert app._bcon_setup is None


def test_Given_bconタブ表示中_When_再度更新_Then_作り直さない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "switch-bcon")
    app._refresh_bcon_tab()

    # When: switch-bcon から switch-bcon-proc への切替でも呼ばれる
    app.transport_name.set("switch-bcon-proc")
    app._refresh_bcon_tab()

    # Then: 二重に作ると PLAYER_INFO の購読と poll が重複する
    assert len(_FakeSetup.made) == 1
    assert app.setting_nb.tabs().count(".nb.bcon") == 1


def test_Given_切替を往復_When_再びbcon_Then_新しく作り直す(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "switch-bcon")
    app._refresh_bcon_tab()
    app.transport_name.set("legacy_text")
    app._refresh_bcon_tab()

    # When
    app.transport_name.set("switch-bcon")
    app._refresh_bcon_tab()

    # Then
    assert len(_FakeSetup.made) == 2
    assert _FakeSetup.made[0].closed == 1
    assert _FakeSetup.made[1].closed == 0
    assert app.setting_nb.tabs()[1] == ".nb.bcon"


def test_Given_作成が失敗_When_タブを更新_Then_タブを出さず例外を出さない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 公開操作は例外を外へ出さない（規約）
    import ui.bcon_panel as bcon_panel

    app = _make_app(monkeypatch, "switch-bcon")

    class _Boom:
        def __init__(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("構築失敗")

    monkeypatch.setattr(bcon_panel, "BconSetup", _Boom)

    # When
    app._refresh_bcon_tab()

    # Then
    assert ".nb.bcon" not in app.setting_nb.tabs()
    assert app._bcon_setup is None


def test_Given_bconタブ_When_選択を要求_Then_前面にして真を返す(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "switch-bcon")
    app._refresh_bcon_tab()

    # When
    shown = app.select_bcon_tab()

    # Then
    assert shown is True
    assert app.setting_nb.selected == ".nb.bcon"


def test_Given_bconタブ無し_When_選択を要求_Then_偽を返す(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "legacy_text")
    app._refresh_bcon_tab()

    # When / Then
    assert app.select_bcon_tab() is False


def test_Given_bconタブ表示中_When_終了処理_Then_中身を閉じる(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    app = _make_app(monkeypatch, "switch-bcon")
    app._refresh_bcon_tab()
    setup = _FakeSetup.made[0]

    # When
    app._close_bcon_tab()

    # Then: 二重に呼んでも close は1回
    app._close_bcon_tab()
    assert setup.closed == 1
    assert app._bcon_setup is None


# -- BconSetup 側（窓を作らず __new__＋stub で閉鎖規律を見る）----------------


class _StubWidget:
    def __init__(self) -> None:
        self.destroyed = 0

    def winfo_exists(self) -> bool:
        return True

    def destroy(self) -> None:
        self.destroyed += 1


def test_Given_埋め込みのBconSetup_When_閉じる_Then_タブ枠は残し中身だけ壊す() -> None:
    # Given: タブ枠は Notebook が持つ。close で枠ごと壊すと次に作り直せない
    import queue

    from BconSetup import BconSetup

    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = object()
    obj._queue = queue.Queue()
    obj._busy = True
    obj._stop = False
    obj._closed = False
    obj.window = _StubWidget()
    obj._body = _StubWidget()
    obj._embedded = True

    # When
    obj.close()

    # Then
    assert obj.window.destroyed == 0
    assert obj._body.destroyed == 1
    assert obj._busy is False


def test_Given_埋め込みのBconSetup_When_送り先が作り直される_Then_最新を引く() -> None:
    # Given: serial サービスは Sender を作り直しうる。窓を開いた時点の
    #   Sender を握り続けると、タブは古い（閉じた）運搬器へ送ってしまう。
    from BconSetup import BconSetup

    class _Sender:
        def __init__(self, transport: object) -> None:
            self.transport = transport

    first, second = _Sender("old"), _Sender("new")
    current = {"sender": first}
    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = first
    obj._sender_provider = lambda: current["sender"]

    # When
    current["sender"] = second

    # Then
    assert obj._current_sender() is second


def test_Given_別窓モードのBconSetup_When_送り先を引く_Then_渡された物を返す() -> None:
    from BconSetup import BconSetup

    sender = object()
    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = sender

    assert obj._current_sender() is sender


# -- 配線（源の静的検査）--------------------------------------------------


def test_Window_は_BconPanelMixin_を混ぜ_シリアルとコントローラの間に枠を作る() -> None:
    src = Path("SerialController/Window.py").read_text(encoding="utf-8")
    assert "from ui.bcon_panel import BconPanelMixin" in src
    assert "BconPanelMixin," in src
    assert "self._build_bcon_tab()" in src
    # 初期状態でタブを足さない（bcon 選択時だけ _refresh_bcon_tab が足す）
    assert "add(self.tab_bcon" not in src


def test_Menubar_のBcon設定は_別窓を作らずタブを選ぶ() -> None:
    src = Path("SerialController/Menubar.py").read_text(encoding="utf-8")
    body = src[src.index("def OpenBconSetup") : src.index("def OpenSerialMonitor")]
    assert "select_bcon_tab" in body
    assert " BconSetup(" not in body  # 別窓の生成は残さない
