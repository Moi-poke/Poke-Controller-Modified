"""Wave 2 / TD: the renderer key survives load -> widget -> save.

``[General Setting] renderer`` already exists in ``config.py`` (default ``auto``,
re-validated by ``complete_missing``), but nothing mirrored it into
``GuiSettings`` and nothing wrote it back, so the value could not reach
``settings.ini`` at all. These tests pin both ends of that path:

* ``Settings.py`` -- the tk mirror is a ``StringVar`` seeded with a bare
  ``get()`` (no ``fallback=``: the backfill already guarantees the key, same as
  ``show_size``), and the dict ``save()`` rebuilds carries the key.
* ``Window.py`` -- the single save funnel is the only writer, and the widget is
  populated before ``_settings_ready`` flips so the first autosave cannot write
  a stale value.

The ``Window`` half is asserted against the parsed source, because a live
``PokeControllerApp`` needs a display. The ``Settings.save()`` half runs for
real against an ``object.__new__`` instance whose tk variables are stubs --
the no-Tk convention this repo already uses.
"""

from __future__ import annotations

import ast
import configparser
from types import SimpleNamespace
from typing import Any

import Settings
from gdi_source_readers import (
    SERIAL_CONTROLLER,
    WINDOW_SOURCE,
    call_attr_names,
    class_method,
    class_node,
    dotted_name,
    enclosing_function,
    module_tree,
)
from pytest import CaptureFixture, MonkeyPatch
from ui.camera_panel import CameraPanelMixin

SETTINGS_SOURCE = SERIAL_CONTROLLER / "Settings.py"
CAMERA_PANEL_SOURCE = SERIAL_CONTROLLER / "ui" / "camera_panel.py"

# 既定（"auto"）と違う値。保存されなければ「保存できていない」ことに気づけない。
RENDERER_CHOICE = "photo"
RENDERER_TYPO = "gd1"
# 表示モードの既定（"fixed"）と違う値。同じく保存された与否が分かる値。
SHOW_MODE_CHOICE = "fit"


class _Var:
    """tk 変数の代用。get/set だけを持ち、Tk も画面も要らない。"""

    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: object) -> None:
        self._value = str(value)


class _SaveTarget(Settings.GuiSettings):
    """``save()`` が触る tk 変数をすべてスタブにした設定（``__init__`` を飛ばす）。

    ``__getattr__`` は「存在しない属性」だけを返すので、``setting`` やメソッドは
    本物のまま解決される。``renderer`` だけは明示的に拒む：ミラーが無いことを
    スタブで隠さず、「save() が読めない」ことをそのまま出す。
    """

    def __getattr__(self, name: str) -> Any:
        if name == "renderer":
            raise AttributeError("renderer ミラーが無いので save() が読めない")
        var = _Var()
        object.__setattr__(self, name, var)
        return var


def _noop(*_args: Any) -> None:
    """ファイル IO とキー割り当ての読み直しを何もしないダミーに差し替える。"""


def _self_attr_assignments(function: ast.FunctionDef, attr: str) -> list[ast.Assign]:
    """``function`` 内で ``self.<attr>`` へ代入する文を全部。"""
    return [
        child
        for child in ast.walk(function)
        if isinstance(child, ast.Assign)
        and any(dotted_name(target) == f"self.{attr}" for target in child.targets)
    ]


def _calls_to(function: ast.AST, callee: str, method: str) -> list[ast.Call]:
    """``function`` 内で ``<callee>.<method>(...)`` にする呼び出しを全部。"""
    return [
        child
        for child in ast.walk(function)
        if isinstance(child, ast.Call)
        and dotted_name(child.func) == f"{callee}.{method}"
    ]


def _keyword_value(call: ast.Call, name: str) -> ast.expr:
    """``name=``  keyword の値。複数・無しは読み違いとして落とす。"""
    values = [keyword.value for keyword in call.keywords if keyword.arg == name]
    assert len(values) == 1, f"{name}= が {len(values)} 個（1 個だけ）"
    return values[0]


def _grid_column(function: ast.AST, owner: str) -> int:
    """``owner.grid(column=...)`` の column 値。"""
    grids = _calls_to(function, owner, "grid")
    assert len(grids) == 1, f"{owner}.grid() が {len(grids)} 箇所（1 箇所）"
    column = _keyword_value(grids[0], "column")
    assert isinstance(column, ast.Constant) and isinstance(column.value, int)
    return column.value


def _calls(function: ast.AST) -> list[ast.Call]:
    """``function`` 内の呼び出しを全部（入れ子も含めて）。"""
    return [child for child in ast.walk(function) if isinstance(child, ast.Call)]


def _renderer_mirror_seed() -> ast.Call:
    """``self.renderer = tk.StringVar(value=...)`` の ``value`` にある呼び出し。"""
    init = class_method(
        class_node(module_tree(SETTINGS_SOURCE), "GuiSettings"), "__init__"
    )
    mirrors = _self_attr_assignments(init, "renderer")
    assert len(mirrors) == 1, f"renderer ミラーの定義が {len(mirrors)} 個（1 個だけ）"
    mirror = mirrors[0].value
    assert (
        isinstance(mirror, ast.Call) and dotted_name(mirror.func) == "tk.StringVar"
    ), "renderer ミラーは tk.StringVar で用意する"
    seeds = [keyword.value for keyword in mirror.keywords if keyword.arg == "value"]
    assert len(seeds) == 1, "renderer ミラーは value= で初期化する"
    seed = seeds[0]
    assert isinstance(seed, ast.Call), "renderer ミラーは設定から get() で初期化する"
    return seed


def test_save_writes_the_renderer_mirror_back_into_general_setting(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: load で読み込んだ値を持つミラー（load -> widget は Window 側の責務）。
    # ファイル IO とキー割り当ての読み直しだけ差し替え、Tk は作らない。
    monkeypatch.setattr(Settings.GuiSettings, "_reload_key_maps", _noop)
    monkeypatch.setattr(Settings.GuiSettings, "_write_ini", _noop)
    target: Any = object.__new__(_SaveTarget)
    target.setting = configparser.ConfigParser()
    target.renderer = _Var(RENDERER_CHOICE)

    # When: 唯一の保存関数を通す
    target.save()

    # Then: 組み直した dict に renderer が残る
    assert target.setting["General Setting"]["renderer"] == RENDERER_CHOICE


def test_renderer_mirror_is_seeded_from_the_general_setting_section() -> None:
    # Given / When
    seed = _renderer_mirror_seed()

    # Then: General Setting セクションの renderer キーから読む
    assert dotted_name(seed.func) == "general.get"
    assert [arg.value for arg in seed.args if isinstance(arg, ast.Constant)] == [
        "renderer"
    ]


def test_renderer_mirror_get_declares_no_fallback_default() -> None:
    # Given / When
    seed = _renderer_mirror_seed()

    # Then: 補完済みなので fallback= を二重に持たない（show_size と同じ形）
    assert [keyword.arg for keyword in seed.keywords] == []


def test_save_writes_the_show_mode_mirror_back_into_general_setting(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: 表示モードのミラーだけ読み込んだ値を持つ設定（save() は実物で通す）。
    # ミラーが無い場合はスタブが空文字を返すので、保存されないまま終わる。
    monkeypatch.setattr(Settings.GuiSettings, "_reload_key_maps", _noop)
    monkeypatch.setattr(Settings.GuiSettings, "_write_ini", _noop)
    target: Any = object.__new__(_SaveTarget)
    target.setting = configparser.ConfigParser()
    # renderer ミラーは _SaveTarget が意図的に拒むので閉じておく
    target.renderer = _Var(RENDERER_CHOICE)
    target.show_mode = _Var(SHOW_MODE_CHOICE)

    # When: 唯一の保存関数を通す
    target.save()

    # Then: 組み直した dict に show_mode が残る（次に起動した위가保存値に従う）
    assert target.setting["General Setting"]["show_mode"] == SHOW_MODE_CHOICE


def test_show_mode_mirror_is_seeded_from_the_general_setting_section() -> None:
    # Given / When
    init = class_method(
        class_node(module_tree(SETTINGS_SOURCE), "GuiSettings"), "__init__"
    )
    mirrors = _self_attr_assignments(init, "show_mode")

    # Then: General Setting セクションの show_mode キーから bare get で読む
    assert len(mirrors) == 1, f"show_mode ミラーの定義が {len(mirrors)} 個（1 個だけ）"
    mirror = mirrors[0].value
    assert isinstance(mirror, ast.Call) and dotted_name(mirror.func) == "tk.StringVar"
    seeds = [keyword.value for keyword in mirror.keywords if keyword.arg == "value"]
    assert len(seeds) == 1, "show_mode ミラーは value= で初期化する"
    seed = seeds[0]
    assert isinstance(seed, ast.Call), "show_mode ミラーは設定から get() で初期化する"
    assert dotted_name(seed.func) == "general.get"
    assert [arg.value for arg in seed.args if isinstance(arg, ast.Constant)] == [
        "show_mode"
    ]
    # 補完済みなので fallback= を二重に持たない（renderer と同じ形）
    assert [keyword.arg for keyword in seed.keywords] == []


def test_init_state_declares_the_renderer_stringvar() -> None:
    # Given / When
    state = class_method(
        class_node(module_tree(WINDOW_SOURCE), "PokeControllerApp"), "_init_state"
    )
    mirrors = _self_attr_assignments(state, "renderer")

    # Then: 画面側の変数は _init_state が 1 つだけ用意する
    assert len(mirrors) == 1, f"renderer 変数の定義が {len(mirrors)} 個（1 個だけ）"
    value = mirrors[0].value
    assert isinstance(value, ast.Call) and dotted_name(value.func) == "tk.StringVar"


def test_apply_settings_shows_the_loaded_renderer_before_settings_are_ready() -> None:
    # Given / When
    app = class_node(module_tree(WINDOW_SOURCE), "PokeControllerApp")
    apply_settings = class_method(app, "_apply_settings_to_widgets")
    seeds = [
        call
        for call in _calls(apply_settings)
        if dotted_name(call.func) == "self.renderer.set"
    ]
    selects = [
        call
        for call in _calls(apply_settings)
        if dotted_name(call.func) == "WindowUtils.selectCombobox"
        and call.args
        and dotted_name(call.args[0]) == "self.renderer_cb"
    ]
    readies = [
        child
        for child in ast.walk(apply_settings)
        if isinstance(child, ast.Assign)
        and any(
            dotted_name(target) == "self._settings_ready" for target in child.targets
        )
    ]

    # Then: 変数を流し込んでから候補を選び、最後に保存可能にする
    assert len(seeds) == 1, f"renderer の流し込みが {len(seeds)} 箇所（1 箇所）"
    assert len(selects) == 1, f"renderer_cb の選択が {len(selects)} 箇所（1 箇所）"
    assert len(readies) == 1, "_settings_ready の代入が 1 箇所"
    shown = selects[0].args[1]
    assert (
        isinstance(shown, ast.Call) and dotted_name(shown.func) == "self.renderer.get"
    )
    assert seeds[0].lineno < selects[0].lineno < readies[0].lineno


def test_the_save_funnel_is_the_only_writer_of_the_renderer_mirror() -> None:
    # Given / When
    app = class_node(module_tree(WINDOW_SOURCE), "PokeControllerApp")
    writers = [
        call
        for call in _calls(app)
        if dotted_name(call.func) == "self.settings.renderer.set"
    ]

    # Then: 保存の入口は _save_settings 1 箇所だけで、そこが save() を呼ぶ
    assert len(writers) == 1, (
        f"renderer への書き込みが {len(writers)} 箇所（1 箇所に集約）"
    )
    owner = enclosing_function(app, writers[0])
    assert owner is not None and owner.name == "_save_settings"
    saves = [
        call for call in _calls(owner) if dotted_name(call.func) == "self.settings.save"
    ]
    assert saves, "_save_settings が settings.save() を呼んでいない"
    assert writers[0].lineno < saves[0].lineno


def test_camera_panel_builds_a_readonly_renderer_combobox_from_the_shared_candidates() -> (
    None
):
    # Given / When: Window.py が selectCombobox(self.renderer_cb, ...) で触る属性の
    # 主が camera_panel 側にあることを、ファイルを解析して確かめる（Tk 不要）。
    build = class_method(
        class_node(module_tree(CAMERA_PANEL_SOURCE), "CameraPanelMixin"),
        "_build_camera_frame",
    )

    # Then: 画面用の変数とコンボボックスが camera_f2 の中に1つずつ載る
    variables = _self_attr_assignments(build, "renderer")
    assert len(variables) == 1, f"renderer 変数の定義が {len(variables)} 個（1 個だけ）"
    variable = variables[0].value
    assert isinstance(variable, ast.Call)
    assert dotted_name(variable.func) == "tk.StringVar"
    widgets = _self_attr_assignments(build, "renderer_cb")
    assert len(widgets) == 1, f"renderer_cb の定義が {len(widgets)} 個（1 個だけ）"
    widget = widgets[0].value
    assert isinstance(widget, ast.Call) and dotted_name(widget.func) == "ttk.Combobox"
    assert [dotted_name(arg) for arg in widget.args] == ["self.camera_f2"], (
        "renderer_cb は camera_f2 に載せる"
    )

    # Then: 候補は WindowUtils の表をそのまま使い、選択以外では書き換えられない
    config = _calls_to(build, "self.renderer_cb", "config")
    assert len(config) == 1, f"renderer_cb.config() が {len(config)} 箇所（1 箇所）"
    assert (
        dotted_name(_keyword_value(config[0], "values"))
        == "WindowUtils.RENDERER_VALUES"
    ), "候補は WindowUtils.RENDERER_VALUES をそのまま使う"
    state = _keyword_value(config[0], "state")
    assert isinstance(state, ast.Constant) and state.value == "readonly", (
        "renderer_cb は readonly（選択以外は書き換え不可）"
    )

    # Then: 既存の 0-7 列に重ねず、8/9 列へ置く
    assert _grid_column(build, "self.renderer_label") == 8
    assert _grid_column(build, "self.renderer_cb") == 9


def test_camera_f2_row_spans_the_renderer_columns() -> None:
    # Given / When
    build = class_method(
        class_node(module_tree(CAMERA_PANEL_SOURCE), "CameraPanelMixin"),
        "_build_camera_frame",
    )

    # Then: 9 列目を置いた行が 10 列ぶん幅を持つ（はみ出して潰さない）
    span = _keyword_value(_calls_to(build, "self.camera_f2", "grid")[0], "columnspan")
    assert isinstance(span, ast.Constant)
    assert span.value == 10


def test_build_preview_forwards_the_sanitized_renderer_from_settings() -> None:
    # Given / When
    preview = class_method(
        class_node(module_tree(CAMERA_PANEL_SOURCE), "CameraPanelMixin"),
        "_build_preview",
    )
    areas = [
        call for call in _calls(preview) if dotted_name(call.func) == "CaptureArea"
    ]

    # Then: 描画方式を 1 つだけ、末尾のキーワードで渡す
    assert len(areas) == 1, f"CaptureArea の呼び出しが {len(areas)} 箇所（1 箇所）"
    forwarded = _keyword_value(areas[0], "renderer")
    assert isinstance(forwarded, ast.Call)
    assert dotted_name(forwarded.func) == "self.settings.renderer.get", (
        "renderer は settings 側の-sanitize済み値を渡す（ウィジェット変数ではない）"
    )
    assert len(areas[0].args) == 8, "既存の位置引数は 8 個のまま（順序を変えない）"
    last_positional = areas[0].args[-1]
    assert isinstance(last_positional, ast.Call)
    assert dotted_name(last_positional.func) == "self.settings.is_take_stick_log.get"


# ===========================================================================
# Wave 4 / TE2: the selection has somewhere to go
# ===========================================================================


def _apply_target(renderer_value: str) -> tuple[Any, _Var, list[None]]:
    """Tk を持たない適用先。画面の変数、設定のミラー、変更通知の記録だけ。"""
    panel: Any = object.__new__(CameraPanelMixin)
    panel.renderer = _Var(renderer_value)
    mirror = _Var()
    panel.settings = SimpleNamespace(renderer=mirror)
    changes: list[None] = []
    panel._on_setting_changed = lambda *event: changes.append(None)
    return panel, mirror, changes


def _mixin_method(name: str) -> ast.FunctionDef:
    return class_method(
        class_node(module_tree(CAMERA_PANEL_SOURCE), "CameraPanelMixin"), name
    )


def test_current_renderer_keeps_a_choice_from_the_candidate_table() -> None:
    # Given: 候補表にある値が入った画面の変数（RENDERER_CHOICE は既定の "auto" と違う）。
    panel, _, _ = _apply_target(RENDERER_CHOICE)

    # When / Then: そのまま返す（前方一致や独自解釈で潰さない）。
    assert panel._current_renderer() == RENDERER_CHOICE


def test_current_renderer_falls_back_to_auto_and_normalises_the_var() -> None:
    # Given: 候補表に無い値（設定ファイルを書き間違えた状態）。
    panel, _, _ = _apply_target(RENDERER_TYPO)

    # When
    chosen = panel._current_renderer()

    # Then: 既定へ戻し、画面にも同じ値を書き戻す（次回の表示が嘘にならない）。
    assert chosen == "auto"
    assert panel.renderer.get() == "auto", (
        f"変数が {panel.renderer.get()!r} のまま：不正値は候補表で直さない"
    )


def test_apply_renderer_persists_the_choice_and_asks_for_a_restart(
    capsys: CaptureFixture[str],
) -> None:
    # Given: 選び直された画面の変数と、変更通知を記録するだけの設定ミラー。
    panel, mirror, changes = _apply_target(RENDERER_CHOICE)

    # When: 選択を適用する（実 Combobox と同じ <<ComboboxSelected>> 経路）。
    panel.applyRenderer()

    # Then: 設定のミラーへ入る。ここが空だと _on_setting_changed 経由の save() が
    # 画面変数しか見ない形になり、選択がファイルへ残らない。
    assert mirror.get() == RENDERER_CHOICE
    assert changes, "applyRenderer() が _on_setting_changed() を呼んでいない"

    # Then: ログ欄（stdout は LogPane が拾う）に再起動の案内が出る。反映は
    # 次回起動なので、黙って切り替えると「効いた？别れた？」になる。
    out = capsys.readouterr().out
    assert RENDERER_CHOICE in out, f"選んだ値がログ欄に出ない: {out!r}"
    assert "再起動" in out, f"再起動が必要だと伝えていない: {out!r}"


def test_apply_renderer_never_opens_a_modal_confirmation() -> None:
    # Given / When: applyRenderer() の実ソース（Tk は要らないので解析する）。
    handler = _mixin_method("applyRenderer")

    # Then: モーダルを出さない。tkmsg は同期なので、選択のたびダイアログを
    # 開くと GUI スレッドがそこで止まる。
    assert "askokcancel" not in call_attr_names(handler), (
        "applyRenderer() が askokcancel を呼ぶ（GUI スレッドを止める）"
    )


def test_renderer_combobox_applies_the_choice_on_selection() -> None:
    # Given / When: renderer_cb へ選択確定を結ぶ bind を探す。
    build = _mixin_method("_build_camera_frame")
    binds = _calls_to(build, "self.renderer_cb", "bind")

    # Then: 1 つだけ。2 つだと同じ選択で 2 回保存・2 回通知になる。
    assert len(binds) == 1, f"renderer_cb.bind() が {len(binds)} 箇所（1 箇所）"
    event = binds[0].args[0]
    assert isinstance(event, ast.Constant) and event.value == "<<ComboboxSelected>>", (
        "選択確定の仮想イベントは <<ComboboxSelected>>"
    )
    callback = binds[0].args[1]
    assert isinstance(callback, ast.Attribute)
    assert dotted_name(callback) == "self.applyRenderer"
