"""画像欄の縮小画像グリッド用判定（Blockly.PokeconImages）の検証。"""

from __future__ import annotations

import json
from typing import Any

from blockly_node import NEEDS_NODE, run_blockly

pytestmark = NEEDS_NODE

NAMES = [
    "controller_check/btn_a.png",
    "controller_check/btn_b.png",
    "a.png",
    "blockly/mark.png",
]


def probe(body: str) -> dict[str, Any]:
    return run_blockly("const ws = new Blockly.Workspace();\n" + body)


def test_image_groups_split_folders_and_put_bare_names_last() -> None:
    """フォルダごとに分け・名前順に並べ、「（直下）」を最後にし、ラベルから拡張子を落とすこと。"""
    # Given: フォルダ付きと直下の混ざった一覧
    res = probe(
        f"""
const I = Blockly.PokeconImages;
done({{ groups: I.group({json.dumps(NAMES)}, '') }});
"""
    )
    # When: 分けた結果を読む
    # Then: フォルダ順は名前順で「（直下）」が最後、中身も名前順でラベルは拡張子なし
    assert res["groups"] == [
        {
            "folder": "blockly",
            "items": [{"name": "blockly/mark.png", "label": "mark"}],
        },
        {
            "folder": "controller_check",
            "items": [
                {"name": "controller_check/btn_a.png", "label": "btn_a"},
                {"name": "controller_check/btn_b.png", "label": "btn_b"},
            ],
        },
        {"folder": "（直下）", "items": [{"name": "a.png", "label": "a"}]},
    ]


def test_image_groups_filter_ignores_case_width_and_splits_words() -> None:
    """大文字小文字・全角半角を無視し、空白区切りの全語を含むものだけに絞ること。"""
    # Given: 同じ一覧
    # When: 半角大文字・全角・空白区切りで絞る
    res = probe(
        f"""
const I = Blockly.PokeconImages;
const flat = (g) => g.flatMap((x) => x.items.map((i) => i.name));
done({{ upper: flat(I.group({json.dumps(NAMES)}, 'BTN')),
       wide: flat(I.group({json.dumps(NAMES)}, 'ｂｔｎ')),
       words: flat(I.group({json.dumps(NAMES)}, 'check a')),
       none: I.group({json.dumps(NAMES)}, 'zzz') }});
"""
    )
    # Then: いずれも部分一致で絞り、0件は空配列になる
    assert res["upper"] == ["controller_check/btn_a.png", "controller_check/btn_b.png"]
    assert res["wide"] == ["controller_check/btn_a.png", "controller_check/btn_b.png"]
    assert res["words"] == ["controller_check/btn_a.png"]
    assert res["none"] == []


def test_image_thumb_url_encodes_folder_and_japanese_names() -> None:
    """縮小画像の受け口がフォルダ名・日本語名を正しく符号化すること。"""
    # Given: PokeconImages 判定
    # When: フォルダ付き・日本語の名で縮小画像のURLを作る
    res = probe(
        """
const I = Blockly.PokeconImages;
done({ a: I.thumbUrl('controller_check/btn_a.png'),
       jp: I.thumbUrl('blockly/あか.png') });
"""
    )
    # Then: ./template_image?name=＋encodeURIComponent の形になる
    assert res["a"] == "./template_image?name=" + "controller_check%2Fbtn_a.png"
    assert res["jp"] == "./template_image?name=" + "blockly%2F%E3%81%82%E3%81%8B.png"


def test_template_field_keeps_unknown_and_empty_values_through_save_load() -> None:
    """候補外・空の値を保ち、保存・読込で値が変わらないこと（既存の回帰確認）。"""
    # Given: 候補は空で、候補外の値と空の値を持つ保存物
    res = probe(
        """
Blockly.PokeconTemplates = [];
const ws2 = new Blockly.Workspace();
Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_vision_contains', id: 'a', fields: { TEMPLATE: 'old-pack/gone.png' } },
  { type: 'pokecon_vision_contains', id: 'b', y: 200, fields: { TEMPLATE: '' } },
] } }, ws2);
const saved = Blockly.serialization.workspaces.save(ws2).blocks.blocks;
done({ fields: saved.map((b) => b.fields.TEMPLATE),
       textA: ws2.getBlockById('a').getField('TEMPLATE').getText(),
       textB: ws2.getBlockById('b').getField('TEMPLATE').getText() });
"""
    )
    # Then: 値はそのまま残り、表示も従来どおり
    assert res["fields"] == ["old-pack/gone.png", ""]
    assert res["textA"] == "old-pack/gone.png"
    assert res["textB"] == "（画像を選ぶ）"


def test_template_field_editor_falls_back_without_a_dropdown_div() -> None:
    """描画なしではshowEditor_が例外を出さないこと（従来の一覧へ落ちる経路）。"""
    # Given: DropDownDivの無い描画なし環境
    # When: 画像欄の編集窓を開こうとする
    res = probe(
        """
const had = ('DropDownDiv' in Blockly) ? Blockly.DropDownDiv : 'absent';
try { delete Blockly.DropDownDiv; } catch (e) { Blockly.DropDownDiv = undefined; }
const b = ws.newBlock('pokecon_vision_contains');
const f = b.getField('TEMPLATE');
let threw = null;
try { f.showEditor_(); } catch (e) { threw = String(e && e.stack || e); }
done({ had: (had === 'absent') ? 'absent' : 'present', threw });
"""
    )
    # Then: 例外なく従来の一覧へ落ちる
    assert res["threw"] is None
