"""クイック追加（名前で探して入れる）の検証。

Blockly 編集画面の「＋ ブロックを探す」窓と、その裏の判定
（`Blockly.PokeconQuick`：候補一覧・検索・挿入）を確かめる。
"""

from __future__ import annotations

from blockly_node import NEEDS_NODE, run_blockly, run_editor
from test_blockly_toolbox import toolbox_source

pytestmark = NEEDS_NODE

#: editor.html の TOOLBOX 定義をそのまま評価し、候補一覧を作る前置き。
#: toolbox_source() は末尾の `};` を含まないため、閉じを足して評価する。
_SETUP_INDEX = (
    toolbox_source()
    + """};
const Q = Blockly.PokeconQuick;
const entries = Q.index(TOOLBOX);
"""
)

#: 描画なしでは Block#select が無いため、選択は SELECTED 事象の直送で表す。
_FIRE_SELECT = """
function fireSelect(ws, id) {
  ws.fireChangeListener({ type: 'selected', newElementId: id,
    oldElementId: null, workspaceId: ws.id, isUiEvent: true });
}
"""


def test_index_lists_press_block_with_japanese_heading() -> None:
    """候補一覧に pokecon_press が入り、見出しに「押す」を含むこと。"""
    # Given: ツールボックスから作った候補一覧
    res = run_blockly(
        _SETUP_INDEX
        + """
        const press = entries.filter((e) => e.type === 'pokecon_press');
        const plain = press.filter((e) => Object.keys(e.block).length === 1);
        done({ n: entries.length, types: press.map((e) => e.type),
               label: press.length ? press[0].label : '',
               category: press.length ? press[0].category : '',
               plainCategory: plain.length ? plain[0].category : '',
               cats: press.map((e) => e.category) });
        """
    )
    # Then: 素の項目と「よく使う形」の定番形が入り、見出しは日本語で分類つき
    # （定番形は欄・つなぎ済みのため型が重複する。素が「基本操作」にいること）
    assert res["n"] >= 40
    assert res["types"] == ["pokecon_press", "pokecon_press"]
    assert "押す" in res["label"]
    assert res["plainCategory"] == "基本操作"
    assert "よく使う形" in res["cats"]


def test_search_matches_readings_across_scripts_and_widths() -> None:
    """ひらがな・英語・全角・カタカナの読みで期待の型が先頭に来ること。"""
    # Given: 候補一覧
    res = run_blockly(
        _SETUP_INDEX
        + """
        const top = (q) => Q.search(entries, q, 5).map((e) => e.type);
        done({ hiragana: top('おす'), english: top('press'),
               fullwidth: top('ｗａｉｔ'), katakana: top('クリカエシ'),
               nothing: Q.search(entries, 'zzzz', 5),
               empty: Q.search(entries, '', 5),
               spaces: Q.search(entries, '   ', 5) });
        """
    )
    # Then: 読みの違いを吸収し、空・不一致は空配列
    assert res["hiragana"][0] == "pokecon_press"
    assert res["english"][0] == "pokecon_press"
    assert res["fullwidth"][0] == "pokecon_wait"
    assert "controls_repeat_ext" in res["katakana"][:2]
    assert res["nothing"] == []
    assert res["empty"] == []
    assert res["spaces"] == []


def test_insert_chains_after_selected_statement_and_relinks_successor() -> None:
    """選択中の文ブロックの直後へつなぎ、元の後続を後ろへ付け直すこと。"""
    # Given: press → wait と並んだプログラム
    res = run_blockly(
        _SETUP_INDEX
        + """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' }, inputs: { DO: { block:
            { type: 'pokecon_press', id: 'p1',
              fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
              next: { block: { type: 'pokecon_wait', id: 'w1', fields: { SEC: 1 } } } } } } } ] } }, ws);
        ws.clearUndo();
        // When: press を選んで stick を入れる
        const stick = entries.filter((e) => e.type === 'pokecon_stick')[0];
        const made = Q.insert(ws, stick, ws.getBlockById('p1'));
        done({ made: made.type, madeId: made.id,
               next: ws.getBlockById('p1').getNextBlock().id,
               followed: made.getNextBlock().id });
        """
    )
    # Then: press → stick → wait の順になる
    assert res["made"] == "pokecon_stick"
    assert res["next"] == res["madeId"]
    assert res["followed"] == "w1"


def test_insert_places_value_block_into_open_slot() -> None:
    """値ブロックは選択中ブロックの空いた値の穴へ入ること。"""
    # Given: 入力の空いた四則ブロック
    res = run_blockly(
        _SETUP_INDEX
        + """
        const ws = new Blockly.Workspace();
        const arith = ws.newBlock('math_arithmetic');
        ws.clearUndo();
        // When: 数値を選んだ四則へ入れる
        const num = entries.filter((e) => e.type === 'math_number')[0];
        const made = Q.insert(ws, num, arith);
        done({ made: made.type, arithId: arith.id, madeId: made.id,
               childA: arith.getInputTargetBlock('A').id,
               parentId: made.getParent().id });
        """
    )
    # Then: A の穴に入る
    assert res["made"] == "math_number"
    assert res["childA"] == res["madeId"]
    assert res["parentId"] == res["arithId"]


def test_insert_is_undone_in_one_step() -> None:
    """挿入は取り消し1回で元に戻ること。"""
    # Given: press → wait と並んだプログラム
    res = run_blockly(
        _SETUP_INDEX
        + """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' }, inputs: { DO: { block:
            { type: 'pokecon_press', id: 'p1',
              fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
              next: { block: { type: 'pokecon_wait', id: 'w1', fields: { SEC: 1 } } } } } } } ] } }, ws);
        ws.clearUndo();
        // When: stick を入れてから元に戻す
        const stick = entries.filter((e) => e.type === 'pokecon_stick')[0];
        Q.insert(ws, stick, ws.getBlockById('p1'));
        const mid = ws.getBlockById('p1').getNextBlock().type;
        // 事象は待ち行列を経て取り消し履歴へ積まれるため、流れるのを待つ。
        await new Promise((r) => setTimeout(r, 30));
        ws.undo(false);
        done({ mid, back: ws.getBlockById('p1').getNextBlock().id,
               gone: ws.getAllBlocks(false).filter((b) => b.type === 'pokecon_stick').length });
        """
    )
    # Then: 入れた直後は stick、1回で wait に戻り stick は消える
    assert res == {"mid": "pokecon_stick", "back": "w1", "gone": 0}


def test_quick_dialog_opens_searches_and_closes() -> None:
    """Ctrl+K で開き、入力で候補が出て、0 件で案内文、Esc で閉じること。"""
    # Given: 起動直後の編集画面
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        // When: Ctrl+K で開く
        winHandlers.keydown({ key: 'k', ctrlKey: true, preventDefault() {} });
        const opened = els.quick.style.display;
        // 最小DOMは子の取外しが無いため、開いた時の案内を捨てる（実DOMでは消える）。
        els.quicklist.options.length = 0;
        // When: 「おす」と打つ
        els.quickinput.value = 'おす';
        els.quickinput.handlers.input();
        await flush(50);
        const rows = els.quicklist.options.map((r) => r.attrs['data-type'] + ':' +
          (r.options || []).map((s) => s.textContent).join('|'));
        // When: 当たらない言葉で 0 件にする
        els.quicklist.options.length = 0;
        els.quickinput.value = 'zzzz';
        els.quickinput.handlers.input();
        await flush(50);
        const emptyRows = els.quicklist.options.map((r) => r.textContent);
        // When: Esc で閉じる
        winHandlers.keydown({ key: 'Escape', preventDefault() {} });
        done({ opened, rows, emptyRows, closed: els.quick.style.display,
               expanded: els.quickinput.attrs['aria-expanded'] });
        """
    )
    # Then: 開いて候補が出て、0 件は案内文で、閉じる
    assert res["opened"] == "block"
    assert res["rows"][0].startswith("pokecon_press:")
    assert "押す" in res["rows"][0]
    assert len(res["rows"]) <= 12
    assert any("見つかりません" in t for t in res["emptyRows"])
    assert res["closed"] == "none"
    assert res["expanded"] == "false"


def test_quick_enter_inserts_after_selected_block() -> None:
    """Enter で選択中ブロックの後ろへ入り、状態行に「追加しました」と出ること。"""
    # Given: プログラムの中に press を置いて選んだ状態
    res = run_editor(
        _FIRE_SELECT
        + """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const prog = ws.getTopBlocks(false)[0];
        const p1 = ws.newBlock('pokecon_press');
        prog.getInput('DO').connection.connect(p1.previousConnection);
        fireSelect(ws, p1.id);
        await flush(50);
        // When: 開いて「まつ」と打ち、Enter で入れる
        winHandlers.keydown({ key: 'k', ctrlKey: true, preventDefault() {} });
        // 最小DOMは子の取外しが無いため、開いた時の案内を捨てる（実DOMでは消える）。
        els.quicklist.options.length = 0;
        els.quickinput.value = 'まつ';
        els.quickinput.handlers.input();
        await flush(50);
        const first = els.quicklist.options[0].attrs['data-type'];
        els.quickinput.handlers.keydown({ key: 'Enter', preventDefault() {} });
        await flush(50);
        done({ first, next: p1.getNextBlock() && p1.getNextBlock().type,
               status: els.status.textContent, closed: els.quick.style.display });
        """
    )
    # Then: press の後ろに wait が入り、知らせが出て閉じる
    assert res["first"] == "pokecon_wait"
    assert res["next"] == "pokecon_wait"
    assert "追加しました" in res["status"]
    assert res["closed"] == "none"


def test_insert_goes_inside_the_program_when_nothing_fits_after() -> None:
    """プログラムを選んでいる・何も選んでいない時は、プログラムの中の末尾へ入ること（外に浮かせない）。"""
    res = run_blockly(
        _SETUP_INDEX
        + """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' }, inputs: { DO: { block:
            { type: 'pokecon_press', id: 'p1', fields: { BUTTON: 'A' } } } } } ] } }, ws);
        const wait = entries.find((e) => e.type === 'pokecon_wait');
        // When: プログラムを選んで入れる → 中の末尾（p1 の後ろ）
        const a = Q.insert(ws, wait, ws.getBlockById('P'));
        // When: 何も選ばずに入れる → やはりプログラムの中の末尾
        const b = Q.insert(ws, wait, null);
        done({ aPrev: a.getPreviousBlock() && a.getPreviousBlock().id,
               bPrev: b.getPreviousBlock() && b.getPreviousBlock().id,
               root: [a.getRootBlock().id, b.getRootBlock().id] });
        """
    )
    assert res["aPrev"] == "p1"
    assert res["root"] == ["P", "P"]
    assert res["bPrev"] is not None


def test_insert_keeps_an_outer_undo_group() -> None:
    """外側で束ねている取り消し単位の中で使っても、その束ねを壊さないこと。"""
    res = run_blockly(
        _SETUP_INDEX
        + """
        const ws = new Blockly.Workspace();
        const wait = entries.find((e) => e.type === 'pokecon_wait');
        Blockly.Events.setGroup('outer-group');
        Q.insert(ws, wait, null);
        const after = Blockly.Events.getGroup();
        Blockly.Events.setGroup(false);
        done({ after });
        """
    )
    assert res["after"] == "outer-group"
