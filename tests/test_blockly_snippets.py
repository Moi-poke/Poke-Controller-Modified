"""定番の組み合わせ（よく使う形）の検証。

ツールボックス先頭の「よく使う形」分類（8個以上の完成形スニペット）が、
本物の Blockly で生成・検査を通り、クイック追加からも引けることを確かめる。
"""

from __future__ import annotations

import re
from typing import Any

from blockly_node import NEEDS_NODE, run_blockly
from test_blockly_toolbox import toolbox_source

#: 道具箱の定義を閉じて評価し、先頭分類を取り出す前置き。
_SNIPPETS_SETUP = (
    toolbox_source()
    + """};
const SNIPCAT = TOOLBOX.contents[0];
const SNIPITEMS = SNIPCAT.contents.filter((c) => c.kind === 'block');
function fillTemplate(node, name) {
  if (!node || typeof node !== 'object') { return; }
  if (node.fields && node.fields.TEMPLATE === '') {
    node.fields.TEMPLATE = name;
  }
  if (node.inputs) {
    Object.keys(node.inputs).forEach((k) => {
      const s = node.inputs[k];
      if (s && s.block) { fillTemplate(s.block, name); }
    });
  }
  if (node.next && node.next.block) { fillTemplate(node.next.block, name); }
}
function snippetCode(item) {
  const ws = new Blockly.Workspace();
  const state = JSON.parse(JSON.stringify(item));
  fillTemplate(state, 'controller_check/btn_a.png');
  Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0,
    blocks: [{ type: 'pokecon_program', fields: { NAME: 'Snip' },
               inputs: { DO: { block: state } } }] } }, ws);
  const code = Blockly.Python.workspaceToCode(ws);
  ws.dispose();
  return code;
}
"""
)


def test_snippets_category_comes_first_before_basic_operations() -> None:
    """「よく使う形」分類が先頭にあり、既存の8分類が後に残ること。"""
    # Given: 道具箱の定義
    src = toolbox_source()
    # When: 分類名を集める
    names = re.findall(r"kind: 'category', name: '([^']+)'", src)
    # Then: 先頭が「よく使う形」で、既存の分類が順に続く
    assert names[0] == "よく使う形"
    assert names[1:] == [
        "基本操作",
        "流れ",
        "画像認識",
        "音声",
        "入出力",
        "計算・論理",
        "変数",
        "サブルーチン",
    ]


@NEEDS_NODE
def test_snippets_category_holds_eight_or_more_items() -> None:
    """「よく使う形」分類が8個以上の項目（と短い説明）を持つこと。"""
    # Given: 道具箱の先頭分類
    res: dict[str, Any] = run_blockly(
        _SNIPPETS_SETUP
        + "done({ name: SNIPCAT.name, n: SNIPITEMS.length, "
        + "hasLabel: SNIPCAT.contents.some((c) => c.kind === 'label') });"
    )
    # Then: 名前・件数・説明の有無
    assert res["name"] == "よく使う形"
    assert res["n"] >= 8
    assert res["hasLabel"] is True


@NEEDS_NODE
def test_each_snippet_generates_valid_python() -> None:
    """各項目をプログラムへつなぐと検査を通りcompileできること。"""
    from core import blockly_validate

    # Given: 先頭分類の各項目をプログラムの中で生成する
    res: dict[str, Any] = run_blockly(
        _SNIPPETS_SETUP
        + "done({ codes: SNIPITEMS.map(snippetCode), "
        + "types: SNIPITEMS.map((it) => it.type) });"
    )
    codes: list[str] = res["codes"]
    types: list[str] = res["types"]
    # When/Then: 8個以上あり、すべて検査を通ってPythonとして読める
    assert len(codes) >= 8
    for i, (kind, code) in enumerate(zip(types, codes)):
        assert code.strip(), f"snippet {i} ({kind}) の生成コードが空"
        errors = blockly_validate.validate_generated_code(code)
        assert errors == [], f"snippet {i} ({kind}) の異常: {errors}\n{code}"
        compile(code, f"<snippet-{i}-{kind}>", "exec")


@NEEDS_NODE
def test_quick_search_finds_snippet_entries_by_reading() -> None:
    """クイック追加がこの分類を含み「連打」「時間切れ」で引けること。"""
    # Given: 道具箱からの候補一覧
    res: dict[str, Any] = run_blockly(
        _SNIPPETS_SETUP
        + """
        const Q = Blockly.PokeconQuick;
        const entries = Q.index(TOOLBOX);
        const snip = entries.filter((e) => e.category === 'よく使う形');
        const catOf = (q) => Q.search(entries, q, 10)
          .map((e) => e.category + ':' + e.type);
        done({ snipN: snip.length, renda: catOf('連打'),
               jikangire: catOf('時間切れ') });
        """
    )
    # Then: 分類の項目が候補にあり、読みで引ける
    assert res["snipN"] >= 8
    assert any(str(r).startswith("よく使う形:") for r in res["renda"])
    assert any(str(r).startswith("よく使う形:") for r in res["jikangire"])


@NEEDS_NODE
def test_quick_search_prefers_plain_blocks_for_generic_queries() -> None:
    """汎い問合せでは素の項目が定番形より先に出ること。"""
    # Given: 道具箱からの候補一覧
    res: dict[str, Any] = run_blockly(
        _SNIPPETS_SETUP
        + """
        const Q = Blockly.PokeconQuick;
        const entries = Q.index(TOOLBOX);
        const top = (q) => Q.search(entries, q, 5)
          .map((e) => e.category + ':' + e.type + ':'
            + (Q.isPlain(e.block) ? 'plain' : 'rich'));
        done({ osu: top('おす'), matsu: top('まつ'),
               kurikaeshi: top('くりかえし') });
        """
    )
    # Then: 「おす」は基本操作の素・「まつ」は待つ素が先頭（定番形に押しのけられない）
    # （「くりかえし」は影だけの素が定番形より先。素性の定義は実装と同一）
    assert res["osu"][0] == "基本操作:pokecon_press:plain"
    assert res["matsu"][0] == "基本操作:pokecon_wait:plain"
    assert res["kurikaeshi"][0].endswith(":plain")
    assert "controls_repeat_ext" in res["kurikaeshi"][0]


@NEEDS_NODE
def test_snippet_with_chained_blocks_keeps_followers_on_quick_insert() -> None:
    """つなぎ済み定番形をクイック挿入すると後続が残ること。"""
    # Given: 道具箱からの候補一覧
    res: dict[str, Any] = run_blockly(
        _SNIPPETS_SETUP
        + """
        const Q = Blockly.PokeconQuick;
        const entries = Q.index(TOOLBOX);
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0,
          blocks: [{ type: 'pokecon_program', fields: { NAME: 'Snip' },
                     inputs: { DO: { block: { type: 'pokecon_press',
                       fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } }] } }, ws);
        const sel = ws.getAllBlocks(false).find((b) => b.type === 'pokecon_press');
        // When: 「HOMEからゲームを再開」（press＋後続press）を選択中の後ろへ入れる
        const entry = entries.find((e) => e.category === 'よく使う形'
          && e.type === 'pokecon_press');
        const made = Q.insert(ws, entry, sel);
        const nextType = made && made.getNextBlock() ? made.getNextBlock().type : null;
        const btn = made ? made.getFieldValue('BUTTON') : null;
        ws.dispose();
        done({ nextType, btn });
        """
    )
    # Then: 先頭はHOME・後続の押すブロックが消えていない
    assert res["btn"] == "HOME"
    assert res["nextType"] == "pokecon_press"
