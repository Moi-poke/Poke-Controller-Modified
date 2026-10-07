"""編集状態（未保存判定・宙に浮いたブロック・新規の初期形）の検証。

editor.html は描画が要るため、判定の本体は pokecon_editor.js に置き、
本物の Blockly（描画なしの Workspace）の上で確かめる。
"""

from __future__ import annotations

from blockly_node import BLOCKLY, NEEDS_NODE, run_blockly

PROGRAM_WITH_STRAY = """
const state = { blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_program', x: 0, y: 0, fields: { NAME: 'Stray' },
    inputs: { DO: { block: { type: 'pokecon_wait', fields: { SEC: 1 } } } } },
  { type: 'pokecon_press', id: 'stray', x: 0, y: 400,
    fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } },
] } };
"""


def test_editor_html_loads_the_state_helpers_before_the_inline_script() -> None:
    """editor.html が pokecon_editor.js を読み、判定をそこへ寄せていること。"""
    # Given: 編集画面のHTML
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    # When/Then: 補助を読み込み、未保存判定と浮きブロック対策に使っている
    assert '<script src="./pokecon_editor.js"></script>' in html
    assert html.index("pokecon_editor.js") < html.index("<script>\n")
    assert "isContentEvent" in html
    assert "attachOrphanGuard" in html
    assert "defaultState" in html


@NEEDS_NODE
def test_selection_events_are_not_counted_as_edits() -> None:
    """ブロックを選んだだけ（UI事象）では未保存扱いにしないこと。"""
    # Given: 選択事象と作成事象
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const b = ws.newBlock('pokecon_wait');
        const sel = new Blockly.Events.Selected(null, b.id, ws.id);
        const create = new Blockly.Events.BlockCreate(b);
        // When: 判定にかける
        done({ sel: E.isContentEvent(sel), create: E.isContentEvent(create),
               nil: E.isContentEvent(null) });
        """
    )
    # Then: 選択は編集ではなく、作成は編集
    assert res == {"sel": False, "create": True, "nil": False}


@NEEDS_NODE
def test_freshly_loaded_workspace_still_matches_its_baseline_after_events_flush() -> (
    None
):
    """読込直後に遅れて届く事象があっても、中身が同じなら未保存にならないこと。"""
    # Given: サンプル相当を読み込み、直後に基準を取る
    res = run_blockly(
        PROGRAM_WITH_STRAY
        + """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load(state, ws);
        const base = E.stateKey(ws);
        // When: 事象が流れきるのを待つ／その後に値を1つ変える
        await new Promise((r) => setTimeout(r, 30));
        const same = E.stateKey(ws) === base;
        ws.getAllBlocks(false).find((b) => b.type === 'pokecon_wait').setFieldValue(2, 'SEC');
        const changed = E.stateKey(ws) !== base;
        done({ same, changed, nonEmpty: base.length > 10 });
        """
    )
    # Then: 流れきっても同じ、値を変えたら違う
    assert res == {"same": True, "changed": True, "nonEmpty": True}


@NEEDS_NODE
def test_stray_statement_block_is_disabled_and_left_out_of_generated_code() -> None:
    """プログラム外に置いたブロックは無効化され、生成コードに出ないこと。"""
    from core import blockly_validate

    # Given: 浮きブロック対策を付けたワークスペースに、外れたpressがある
    res = run_blockly(
        PROGRAM_WITH_STRAY
        + """
        const ws = new Blockly.Workspace();
        E.attachOrphanGuard(ws);
        Blockly.serialization.workspaces.load(state, ws);
        await new Promise((r) => setTimeout(r, 30));
        // When: 生成する
        const code = Blockly.Python.workspaceToCode(ws);
        done({ code, enabled: ws.getBlockById('stray').isEnabled(),
               orphans: E.orphanBlocks(ws).length });
        """
    )
    # Then: 外れたpressは無効で、モジュール直下に self. が出ない
    assert res["enabled"] is False
    assert res["orphans"] == 1
    assert "self.press(Button.A" not in res["code"]
    assert not any(line.startswith("self.") for line in res["code"].splitlines())
    assert blockly_validate.validate_generated_code(res["code"]) == []


@NEEDS_NODE
def test_reattached_block_is_enabled_again_and_generated_inside_do() -> None:
    """外れたブロックをプログラムへ戻すと再び有効になり、do()内に出ること。"""
    # Given: 外れて無効になったpress
    res = run_blockly(
        PROGRAM_WITH_STRAY
        + """
        const ws = new Blockly.Workspace();
        E.attachOrphanGuard(ws);
        Blockly.serialization.workspaces.load(state, ws);
        await new Promise((r) => setTimeout(r, 30));
        // When: waitの後ろへつなぐ
        const wait = ws.getAllBlocks(false).find((b) => b.type === 'pokecon_wait');
        const stray = ws.getBlockById('stray');
        wait.nextConnection.connect(stray.previousConnection);
        await new Promise((r) => setTimeout(r, 30));
        done({ enabled: stray.isEnabled(), orphans: E.orphanBlocks(ws).length,
               code: Blockly.Python.workspaceToCode(ws) });
        """
    )
    # Then: 有効に戻り、do() 本体（8桁字下げ）に出る
    assert res["enabled"] is True
    assert res["orphans"] == 0
    assert "\n        self.press(Button.A, duration=0.1, wait=0.1)" in res["code"]


@NEEDS_NODE
def test_undo_reconnects_a_block_that_was_pulled_out_of_the_program() -> None:
    """プログラムから外したブロックは、元に戻すで元の場所へ戻ること（無効化が履歴を汚さない）。"""
    # Given: 浮きブロック対策つきで、プログラム内に待ちがある
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        E.attachOrphanGuard(ws);
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_program', id: 'p', fields: { NAME: 'x' }, inputs: { DO: { block:
            { type: 'pokecon_wait', id: 'w', fields: { SEC: 1 } } } } } ] } }, ws);
        ws.clearUndo();
        await new Promise((r) => setTimeout(r, 30));
        // When: 外して（無効化される）から、元に戻す
        const w = ws.getBlockById('w');
        w.unplug();
        await new Promise((r) => setTimeout(r, 30));
        const strayed = w.isEnabled();
        ws.undo(false);
        await new Promise((r) => setTimeout(r, 30));
        done({ strayed, parent: w.getParent() ? w.getParent().id : null,
               enabled: w.isEnabled(), redo: ws.getRedoStack().length });
        """
    )
    # Then: 外した間は無効、元に戻すと元の場所で有効、やり直しも残る
    assert res == {"strayed": False, "parent": "p", "enabled": True, "redo": 1}


@NEEDS_NODE
def test_new_workspace_starts_with_one_program_block_that_saves_cleanly() -> None:
    """新規の初期形はプログラム1個で、そのまま保存検査を通ること。"""
    from core import blockly_validate

    # Given: 新規の初期形
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        // When: 読み込んで生成する
        Blockly.serialization.workspaces.load(E.defaultState(), ws);
        done({ types: ws.getTopBlocks(false).map((b) => b.type),
               code: Blockly.Python.workspaceToCode(ws) });
        """
    )
    # Then: プログラム1個だけで、生成コードは検査を通る
    assert res["types"] == ["pokecon_program"]
    assert blockly_validate.validate_generated_code(res["code"]) == []
