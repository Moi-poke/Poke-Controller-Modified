"""仮想コントローラをメイン画面（コントローラタブ）に埋め込む契約。実 Tk。

別窓しか無いと、窓の後ろに隠れて探しに行く手間が出る。タブの中に常に置き、
必要なら別ウィンドウへ出せる。押しっぱなしの解放（閉じたら必ず離す）は
埋め込みでも別窓でも同じに守る。
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Iterator
from typing import Any

import pytest
from Commands.Keys import Button, Hat
from core.pad_layout import SHAPES
from ui.controller_dock import ControllerDock
from ui.controller_pad import ControllerGUI
from ui.scroll_host import ScrollHost


class _Sender:
    """送り先の偽物。押した・離したの記録だけ取る。"""

    def __init__(self) -> None:
        self.pressed: list[int] = []
        self.released: list[int] = []
        self.hats: list[int | None] = []
        self.sticks: list[tuple[str, int, int]] = []
        self.flushed = 0

    def isOpened(self) -> bool:
        return True

    def pressButtons(self, buttons: list[int], source: str = "") -> bool:
        self.pressed.extend(buttons)
        return True

    def releaseButtons(self, buttons: list[int], source: str = "") -> bool:
        self.released.extend(buttons)
        return True

    def holdHat(self, value: int, source: str = "") -> bool:
        self.hats.append(value)
        return True

    def releaseHat(self, source: str = "") -> bool:
        self.hats.append(None)
        return True

    def setStick(self, stick: str, x: int, y: int, source: str = "") -> bool:
        self.sticks.append((stick, x, y))
        return True

    def flushPending(self) -> None:
        self.flushed += 1

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: True


@pytest.fixture
def area(tk_root: tk.Tk) -> Iterator[tk.Toplevel]:
    window = tk.Toplevel(tk_root)
    try:
        yield window
    finally:
        window.destroy()


def _placement(dock: ControllerDock) -> str:
    """出している場所（mypy の型の絞り込みを避けるため関数で読む）。"""
    if dock.embedded is not None and dock.floating is None:
        return "embedded"
    if dock.embedded is None and dock.floating is not None:
        return "floating"
    return "broken"


def _toplevels(root: tk.Misc) -> list[tk.Misc]:
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


def test_a_container_builds_the_controller_inside_it_without_a_new_window(
    area: tk.Toplevel,
) -> None:
    holder = tk.Frame(area)
    holder.pack()
    before = len(_toplevels(area))

    pad = ControllerGUI(area, _Sender(), container=holder)

    # Then: 新しい窓は開かず、渡した枠の中に 1 枚の絵としてボタンが並ぶ。
    assert len(_toplevels(area)) == before
    assert pad.window.master is holder
    assert isinstance(pad.canvas, tk.Canvas)
    for shape in SHAPES:
        assert pad.canvas.find_withtag(f"key:{shape.name}"), shape.name


def test_controller_buttons_never_take_keyboard_focus(area: tk.Toplevel) -> None:
    pad = ControllerGUI(area, _Sender(), container=tk.Frame(area))

    # Then: キーボード操作中に Space でボタンが押されて入力が飛ばないよう、
    #       操作盤はフォーカスを取らない。
    assert str(pad.canvas.cget("takefocus")) == "0"


def _shown(area: tk.Toplevel, width: int = 0, height: int = 0) -> ControllerGUI:
    """枠に入れて表示まで済ませた操作盤。width/height で置き場所の広さを決める。"""
    holder = tk.Frame(area, width=width, height=height)
    if width and height:
        # 指定の広さのまま置く（窓の幅に引き伸ばさない）。
        holder.pack_propagate(False)
        holder.pack(anchor="nw")
    else:
        holder.pack(fill="both", expand=True)
    pad = ControllerGUI(area, _Sender(), container=holder)
    area.update()
    return pad


def _centre(pad: ControllerGUI, name: str) -> tuple[int, int]:
    """描いた的の中心（画面座標）。描画結果から測る。"""
    x0, y0, x1, y1 = pad.canvas.bbox(f"key:{name}")
    return (x0 + x1) // 2, (y0 + y1) // 2


def _click(pad: ControllerGUI, sequence: str, x: int, y: int) -> None:
    pad.canvas.event_generate(sequence, x=x, y=y)


def test_the_embedded_pad_needs_at_most_half_the_old_window_area(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    base = float(area.winfo_fpixels("1i")) / 96.0

    # Then: 旧 UI（600x300 の窓）の半分以下の場所しか求めない。
    need = pad.canvas.winfo_reqwidth() * pad.canvas.winfo_reqheight()
    assert need <= 600 * 300 / 2 * base * base


def test_clicking_a_drawn_button_presses_it_until_the_mouse_is_released(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    sender = pad.ser
    x, y = _centre(pad, "A")
    face = pad.canvas.find_withtag("face:A")[0]
    idle = pad.canvas.itemcget(face, "fill")

    # When: the A button drawn on the pad is pressed.
    _click(pad, "<ButtonPress-1>", x, y)

    # Then: 押している間は A が入り、見た目も変わる（押しっぱなしが目で分かる）。
    assert sender.pressed == [int(Button.A)] and not sender.released
    assert pad.canvas.itemcget(face, "fill") != idle

    # When: released.
    _click(pad, "<ButtonRelease-1>", x, y)

    # Then: 離した時点で離す。色も戻る。
    assert sender.released == [int(Button.A)]
    assert pad.canvas.itemcget(face, "fill") == idle


def test_dragging_off_a_pressed_button_releases_it(area: tk.Toplevel) -> None:
    pad = _shown(area)
    sender = pad.ser
    x, y = _centre(pad, "B")
    _click(pad, "<ButtonPress-1>", x, y)

    # When: the mouse is dragged away from B with the button still down.
    _click(pad, "<B1-Motion>", 2, pad.canvas.winfo_height() - 2)

    # Then: 枠外で指を離しても押しっぱなしが残らない。
    assert sender.released == [int(Button.B)]


def test_sliding_across_the_dpad_switches_direction_and_corners_are_diagonals(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    sender = pad.ser
    x0, y0, x1, y1 = pad.canvas.bbox("key:DPAD")
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    arm = (x1 - x0) // 3

    # When: up is pressed, then the pointer slides to the up-right corner.
    _click(pad, "<ButtonPress-1>", cx, cy - arm)
    _click(pad, "<B1-Motion>", cx + arm, cy - arm)
    _click(pad, "<ButtonRelease-1>", cx + arm, cy - arm)

    # Then: 上 → 右上 → 離す、の順に十字キーが送られる（旧 UI の斜めボタンの代わり）。
    assert sender.hats == [int(Hat.TOP), int(Hat.TOP_RIGHT), None]


def _width_of(pad: ControllerGUI, name: str) -> int:
    x0, _y0, x1, _y1 = pad.canvas.bbox(f"key:{name}")
    return int(x1 - x0)


def test_the_embedded_pad_grows_into_a_large_tab_and_shrinks_in_a_narrow_one(
    area: tk.Toplevel,
) -> None:
    base = _width_of(_shown(area, 420, 170), "X")

    # When: the tab gives it far more room than it needs.
    roomy = _shown(area, 840, 340)

    # Then: 空いた場所を空白のまま残さず、縦横比を保って大きく描く。
    assert _width_of(roomy, "X") > base * 1.5
    # 求める高さは等倍の分だけ（タブ全体の高さを押し広げない）。
    assert roomy.canvas.winfo_reqheight() <= 180 * base / 28

    # When: the tab is narrower than the pad.
    narrow = _shown(area, 210, 170)

    # Then: 縮めて全部見せる（はみ出して横スクロールにしない）。
    assert _width_of(narrow, "X") < base * 0.6


def test_the_docked_pad_fills_a_tall_tab_and_clicks_still_land(
    area: tk.Toplevel,
) -> None:
    holder = tk.Frame(area, width=420, height=600)
    holder.pack_propagate(False)
    holder.pack(anchor="nw")
    sender = _Sender()
    dock = ControllerDock(area, holder, lambda: sender)
    area.update()
    pad = dock.embedded
    assert pad is not None

    # Then: 縦に余った場所まで操作盤の欄が伸びる（下に空白の帯を残さない）。
    assert pad.canvas.winfo_height() >= 600 * 0.7

    # When: X is clicked where it is drawn.
    x, y = _centre(pad, "X")
    _click(pad, "<ButtonPress-1>", x, y)

    # Then: 伸ばしても押した所のボタンが入る。
    assert sender.pressed == [int(Button.X)]
    dock.shutdown()


def test_the_docked_pad_fits_the_visible_part_of_a_scrolling_tab_area(
    area: tk.Toplevel,
) -> None:
    # Given: タブ欄はスクロールする入れ物の中にあり、一番高いタブ（Bcon）に
    #        合わせて中身が見える高さより高い。
    area.geometry("1000x280")
    host = ScrollHost(area)
    host.pack(fill="both", expand=True)
    host.inner.rowconfigure(0, weight=1)
    host.inner.columnconfigure(0, weight=1)
    tab = tk.Frame(host.inner)
    tab.grid(row=0, column=0, sticky="nsew")
    tk.Frame(host.inner, width=10, height=900).grid(row=0, column=1)
    tk.Label(tab, text="上の設定").pack(fill="x")
    holder = tk.Frame(tab)
    holder.pack(fill="both", expand=True)
    dock = ControllerDock(area, holder, lambda: _Sender(), viewport=host)
    area.update()
    pad = dock.embedded
    assert pad is not None

    # Then: スクロールしなくても操作盤の全体が見える（見える範囲に収めて描く）。
    _x0, _y0, _x1, y1 = pad.canvas.bbox("all")
    bottom = pad.canvas.winfo_rooty() + y1
    assert bottom <= host.canvas.winfo_rooty() + host.canvas.winfo_height() + 1

    # When: the window is made taller.
    before = _width_of(pad, "X")
    area.geometry("1000x600")
    area.update()

    # Then: 見える高さが増えた分だけ大きく描く。
    assert _width_of(pad, "X") > before * 1.3
    dock.shutdown()


def test_the_floating_pad_grows_with_its_window_and_clicks_still_land(
    area: tk.Toplevel,
) -> None:
    sender = _Sender()
    pad = ControllerGUI(area, sender)
    area.update()
    before = _width_of(pad, "X")

    # When: the floating window is enlarged.
    pad.window.geometry("900x380")
    area.update()

    # Then: 別窓では窓に合わせて大きく描く（縦横比は保つ）。
    x0, y0, x1, y1 = pad.canvas.bbox("key:X")
    assert x1 - x0 > before * 1.3
    assert (x1 - x0) == pytest.approx(y1 - y0, abs=2)

    # When: the enlarged X is clicked.
    x, y = _centre(pad, "X")
    _click(pad, "<ButtonPress-1>", x, y)

    # Then: 大きくしても押した所のボタンが入る。
    assert sender.pressed == [int(Button.X)]
    pad.destroy()


def test_the_dock_pops_out_to_a_window_and_returns_when_it_closes(
    area: tk.Toplevel,
) -> None:
    holder = tk.Frame(area)
    holder.pack()
    dock = ControllerDock(area, holder, lambda: _Sender())

    # Given: 最初はタブの中に埋め込まれている。
    assert _placement(dock) == "embedded"

    # When: popped out with the header button.
    dock.pop_button.invoke()
    area.update()

    # Then: 別窓に移り、同じボタンが「戻す」に変わる（行を増やさない）。
    assert _placement(dock) == "floating"
    assert isinstance(getattr(dock.floating, "window", None), tk.Toplevel)
    assert "戻す" in str(dock.pop_button.cget("text"))

    # When: the same button is pressed again.
    dock.pop_button.invoke()
    area.update()

    # Then: タブへ戻る。
    assert _placement(dock) == "embedded"
    assert "別ウィンドウ" in str(dock.pop_button.cget("text"))

    # When: popped out, then the floating window is closed by its title bar.
    dock.pop_out()
    dock.close_floating()

    # Then: やはりタブへ戻る。
    assert _placement(dock) == "embedded"


def test_popping_out_twice_just_raises_the_existing_window(area: tk.Toplevel) -> None:
    holder = tk.Frame(area)
    dock = ControllerDock(area, holder, lambda: _Sender())
    dock.pop_out()
    first = dock.floating

    dock.pop_out()

    # Then: 2 つ目は開かない（同じ入力を 2 箇所から送らない）。
    assert dock.floating is first
    dock.close_floating()


def test_the_docked_controller_uses_the_sender_created_after_it(
    area: tk.Toplevel,
) -> None:
    # Given: タブは起動時に組み立てるが、送り先（Sender）はその後に作られ、
    #        通信方式を変えると作り直される。最初は送り先が無い。
    current: dict[str, Any] = {"sender": None}
    holder = tk.Frame(area)
    dock = ControllerDock(area, holder, lambda: current["sender"])
    assert dock.embedded is not None

    # When: the sender appears after the dock was built, then A is pressed.
    sender = _Sender()
    current["sender"] = sender
    dock.embedded._onPress("A")
    dock.embedded._onRelease("A")

    # Then: 押した時点の送り先へ届く（組み立て時の送り先を掴みっぱなしにしない）。
    assert sender.pressed and sender.released == sender.pressed

    # When: the sender is rebuilt (transport switched) and A is pressed again.
    rebuilt = _Sender()
    current["sender"] = rebuilt
    dock.embedded._onPress("A")

    # Then: 作り直した送り先へ届く。
    assert rebuilt.pressed


def _face_centre(pad: ControllerGUI, name: str) -> tuple[float, float]:
    x0, y0, x1, y1 = pad.canvas.coords(pad.canvas.find_withtag(f"face:{name}")[0])
    return (x0 + x1) / 2, (y0 + y1) / 2


def test_dragging_a_stick_tilts_it_and_letting_go_returns_it_to_neutral(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    sender = pad.ser
    x, y = _centre(pad, "LCLICK")
    knob = _face_centre(pad, "LCLICK")
    far = pad.canvas.winfo_width() // 3

    # When: the left stick is dragged far to the right.
    _click(pad, "<ButtonPress-1>", x, y)
    _click(pad, "<B1-Motion>", x + far, y)

    # Then: 左スティックが右へ倒し切りで送られ、頭も右へずれて見える。
    #       ボタンとしての押し込み（LCLICK）は入らない。
    assert sender.sticks[-1] == ("L", 255, 128)
    assert _face_centre(pad, "LCLICK")[0] > knob[0] + 3
    assert not sender.pressed

    # When: the pointer leaves the pad while still dragging.
    _click(pad, "<Leave>", x + far, y)

    # Then: 外まで引っ張っても倒したまま（離すまで戻さない）。
    assert sender.sticks[-1] == ("L", 255, 128)

    # When: released (even outside the stick).
    _click(pad, "<ButtonRelease-1>", x + far, y)

    # Then: 中立へ戻して送り切る（倒したまま残さない）。頭も中央へ戻る。
    assert sender.sticks[-1] == ("L", 128, 128)
    assert sender.flushed >= 1
    assert _face_centre(pad, "LCLICK") == pytest.approx(knob, abs=1)


def test_the_right_stick_sends_the_right_side(area: tk.Toplevel) -> None:
    pad = _shown(area)
    x, y = _centre(pad, "RCLICK")
    _click(pad, "<ButtonPress-1>", x, y)
    _click(pad, "<B1-Motion>", x, y - pad.canvas.winfo_height())

    # Then: 右スティックを上へ倒し切り。左は触らない。
    assert pad.ser.sticks[-1] == ("R", 128, 0)
    assert all(side == "R" for side, _x, _y in pad.ser.sticks)


def test_clicking_a_stick_without_moving_taps_the_stick_press(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    sender = pad.ser
    x, y = _centre(pad, "RCLICK")

    # When: the stick is clicked without dragging.
    _click(pad, "<ButtonPress-1>", x, y)
    _click(pad, "<ButtonRelease-1>", x, y)

    # Then: 押し込みが 1 回入る（Switch が拾える長さだけ押してから離す）。
    assert sender.pressed == [int(Button.RCLICK)]
    area.after(250, lambda: None)
    time.sleep(0.25)
    area.update()
    assert sender.released == [int(Button.RCLICK)]
    # 倒してはいないので、スティックの値は送らない。
    assert not [s for s in sender.sticks if s[1:] != (128, 128)]


def test_right_button_holds_the_stick_press_until_released(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    sender = pad.ser
    x, y = _centre(pad, "LCLICK")

    # When: right button is held on the stick.
    _click(pad, "<ButtonPress-3>", x, y)

    # Then: 離すまで押し込みっぱなし（長押しが要る場面用）。
    assert sender.pressed == [int(Button.LCLICK)] and not sender.released
    _click(pad, "<ButtonRelease-3>", x, y)
    assert sender.released == [int(Button.LCLICK)]


def test_closing_the_pad_while_a_stick_is_tilted_returns_it_to_neutral(
    area: tk.Toplevel,
) -> None:
    pad = _shown(area)
    sender = pad.ser
    x, y = _centre(pad, "LCLICK")
    _click(pad, "<ButtonPress-1>", x, y)
    _click(pad, "<B1-Motion>", x, y + pad.canvas.winfo_height())
    assert sender.sticks[-1] == ("L", 128, 255)

    # When: the pad is torn down mid-drag (popped out / app closing).
    pad.destroy()

    # Then: 倒したまま Switch 側に残さない。
    assert sender.sticks[-1] == ("L", 128, 128)
