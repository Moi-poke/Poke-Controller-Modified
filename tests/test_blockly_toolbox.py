"""ツールボックスとブロック形の検証（分類・影ブロック・単位・範囲欄・呼出名）。"""

from __future__ import annotations

import re

from blockly_node import BLOCKLY, NEEDS_NODE, run_blockly


def toolbox_source() -> str:
    """editor.html の toolbox 定義部分を切り出す。"""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    start = html.index("var TOOLBOX = {")
    return html[start : html.index("};", start)]


def toolbox_types() -> list[str]:
    """toolbox に並ぶブロック種（影ブロックを含む）。"""
    return re.findall(r"type: '([a-z_A-Z0-9]+)'", toolbox_source())


def test_empty_preview_placeholder_is_fully_transparent() -> None:
    """画像未選択の縮小表示は完全な透明にすること（色付きの四角を出さない）。"""
    import base64

    import cv2
    import numpy as np

    # Given: 編集画面とブロック定義に埋め込んだ代替画像すべて
    text = (BLOCKLY / "editor.html").read_text(encoding="utf-8") + (
        BLOCKLY / "pokecon_blocks.js"
    ).read_text(encoding="utf-8")
    uris = set(re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", text))
    assert uris
    for b64 in uris:
        # When: 復号する
        img = cv2.imdecode(
            np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_UNCHANGED
        )
        # Then: 透明度つきで、全画素が透明
        assert img is not None and img.ndim == 3 and img.shape[2] == 4
        assert int(img[:, :, 3].max()) == 0, b64


def test_toolbox_is_split_into_purpose_named_categories() -> None:
    """目的別の分類（基本操作・流れ・画像認識・音声・入出力・計算・変数・サブルーチン）に分かれていること。"""
    # Given: toolbox 定義
    src = toolbox_source()
    # When: 分類名を集める
    names = re.findall(r"kind: 'category', name: '([^']+)'", src)
    # Then: 目的別の8分類が順に並び、色が付いている
    assert names == [
        "よく使う形",
        "基本操作",
        "流れ",
        "画像認識",
        "音声",
        "入出力",
        "計算・論理",
        "変数",
        "サブルーチン",
    ]
    assert src.count("colour: '") >= 8
    assert "custom: 'VARIABLE'" in src


def test_value_slots_come_with_ready_to_edit_shadows() -> None:
    """値の穴は影ブロック付きで出し、置いてすぐ数・文字を打てること。"""
    # Given: toolbox 定義
    src = toolbox_source()
    # When/Then: 回数指定の繰り返し・表示・Discordに影がある
    assert re.search(
        r"type: 'controls_repeat_ext'[^\n]*TIMES: \{ shadow: \{ type: 'math_number'",
        src,
    )
    assert re.search(
        r"type: 'pokecon_print'[^\n]*TEXT: \{ shadow: \{ type: 'text'", src
    )
    assert re.search(
        r"type: 'pokecon_discord'[^\n]*CONTENT: \{ shadow: \{ type: 'text'", src
    )


@NEEDS_NODE
def test_every_toolbox_block_type_is_registered() -> None:
    """toolbox に並ぶ種はすべて定義・生成器が登録済みであること（打ち間違い防止）。"""
    # Given: toolbox の種一覧
    types = sorted(set(toolbox_types()))
    assert len(types) >= 40
    # When: 本物の Blockly で登録を確かめる
    res = run_blockly(
        "const types = "
        + repr(types).replace("'", '"')
        + """;
        done({ missing: types.filter((t) => !Blockly.Blocks[t]),
               nogen: types.filter((t) => typeof Blockly.Python.forBlock[t] !== 'function') });
        """
    )
    # Then: 欠けは無い
    assert res == {"missing": [], "nogen": []}


@NEEDS_NODE
def test_every_pokecon_block_explains_itself_with_a_tooltip() -> None:
    """PokeConブロックはすべてツールチップで用途を説明すること。"""
    # Given/When: 登録済みの pokecon_* を作ってツールチップを読む
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const bare = Object.keys(Blockly.Blocks).filter((t) => t.indexOf('pokecon_') === 0)
          .filter((t) => { const b = ws.newBlock(t); const tip = b.getTooltip();
                           return !tip || !String(tip).trim(); });
        done({ bare });
        """
    )
    # Then: 説明の無いブロックは無い
    assert res == {"bare": []}


@NEEDS_NODE
def test_durations_and_timeouts_say_seconds() -> None:
    """長さ・待ち・上限の数値には「秒」が付くこと。"""
    # Given/When: 時間を持つブロックの表示文を集める
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const text = (t) => ws.newBlock(t).inputList
          .map((i) => i.fieldRow.map((f) => f.getText()).join(' ')).join(' / ');
        done({ press: text('pokecon_press'), rep: text('pokecon_press_rep'),
               hold: text('pokecon_hold'), appear: text('pokecon_vision_wait_appear'),
               tone: text('pokecon_audio_wait_tone') });
        """
    )
    # Then: 秒の単位が出る
    assert res["press"].count("秒") == 2
    assert res["rep"].count("秒") == 3
    assert res["hold"].count("秒") == 1
    assert "秒" in res["appear"]
    assert "秒" in res["tone"]


@NEEDS_NODE
def test_vision_blocks_wrap_settings_onto_a_second_row() -> None:
    """画像認識ブロックは設定を2段目へ折り返し、横に長くなりすぎないこと。"""
    # Given/When: 各画像ブロックの行数（入力の数）を数える
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const rows = {};
        ['pokecon_vision_contains', 'pokecon_vision_wait_appear', 'pokecon_vision_wait_gone',
         'pokecon_vision_press_until', 'pokecon_vision_press_until_gone',
         'pokecon_vision_wait_count', 'pokecon_vision_count', 'pokecon_vision_position']
          .forEach((t) => { rows[t] = ws.newBlock(t).inputList.length; });
        const pv = ws.newBlock('pokecon_vision_contains').getField('PREVIEW');
        done({ rows, previewWidth: pv.getSize().width });
        """
    )
    # Then: すべて2段以上で、縮小表示は小さめ
    assert all(n >= 2 for n in res["rows"].values()), res["rows"]
    assert res["previewWidth"] <= 64


@NEEDS_NODE
def test_crop_field_shows_whole_frame_when_empty_and_rejects_bad_text() -> None:
    """範囲欄は空なら「全体」と見せ、壊れた書式は受け付けず、正しい書式は整えること。"""
    # Given: 範囲欄を持つブロック
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const b = ws.newBlock('pokecon_vision_contains');
        const f = b.getField('CROP');
        const empty = f.getText();
        // When: 壊れた書式・逆転・正しい書式（空白あり）を順に入れる
        f.setValue('10,20');
        const afterBad = f.getValue();
        f.setValue('110,20,10,120');
        const afterReversed = f.getValue();
        f.setValue(' 10 , 20 , 110 , 120 ');
        const afterGood = f.getValue();
        done({ empty, afterBad, afterReversed, afterGood, shown: f.getText() });
        """
    )
    # Then: 空は「全体」表示、不正は元の値のまま、正しい値は詰めて保存
    assert res["empty"] == "全体"
    assert res["afterBad"] == ""
    assert res["afterReversed"] == ""
    assert res["afterGood"] == "10,20,110,120"
    assert res["shown"] == "10,20,110,120"


@NEEDS_NODE
def test_fullwidth_and_halfwidth_variable_names_do_not_silently_alias() -> None:
    """全角・半角で見た目だけ違う変数は、Pythonの正規化（NFKC）で同じ名前に化けないこと。"""
    import ast

    # Given: 「ａ」（全角）と「a」（半角）を別の変数として使う
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const set = (id, n) => ({ type: 'variables_set', fields: { VAR: { id } },
          inputs: { VALUE: { block: { type: 'math_number', fields: { NUM: n } } } } });
        const a = set('全角', 1), b = set('半角', 2);
        a.next = { block: b };
        Blockly.serialization.workspaces.load({
          variables: [{ name: 'ａ', id: '全角' }, { name: 'a', id: '半角' }],
          blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'W' }, inputs: { DO: { block: a } } },
          ] } }, ws);
        // When: 生成する
        done({ code: Blockly.Python.workspaceToCode(ws) });
        """
    )
    code = res["code"]
    # Then: Pythonが同一視しない別々の名前（a と a2）になる
    tree = ast.parse(code)
    assigned = sorted(
        {
            t.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
    )
    assert [n for n in assigned if n not in ("NAME", "TAGS")] == ["a", "a2"], code


@NEEDS_NODE
def test_renaming_a_subroutine_is_undone_in_one_step() -> None:
    """定義の改名（呼出の追従を含む）は、元に戻す1回で定義も呼出も戻ること。"""
    # Given: 定義と呼出が1組
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_program', fields: { NAME: 'Sub' }, inputs: { DO: { block:
            { type: 'pokecon_sub_call', id: 'call', fields: { NAME: 'walk' } } } } },
          { type: 'pokecon_sub_def', id: 'def', y: 300, fields: { NAME: 'walk', ARGS: '' } },
        ] } }, ws);
        ws.clearUndo();
        // When: 利用者の編集1回として改名し、追従を待ってから元に戻す
        Blockly.Events.setGroup(true);
        ws.getBlockById('def').setFieldValue('stroll', 'NAME');
        Blockly.Events.setGroup(false);
        await new Promise((r) => setTimeout(r, 30));
        const followed = ws.getBlockById('call').getFieldValue('NAME');
        ws.undo(false);
        await new Promise((r) => setTimeout(r, 30));
        done({ followed, def: ws.getBlockById('def').getFieldValue('NAME'),
               call: ws.getBlockById('call').getFieldValue('NAME') });
        """
    )
    # Then: 追従していて、1回で両方戻る
    assert res == {"followed": "stroll", "def": "walk", "call": "walk"}


@NEEDS_NODE
def test_subroutine_call_picks_from_defined_names_and_follows_renames() -> None:
    """呼出の名前は定義済みから選べ、定義の名前を変えると呼出側も追従すること。"""
    # Given: 定義と呼出が1組ある
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_program', fields: { NAME: 'Sub' }, inputs: { DO: { block:
            { type: 'pokecon_sub_call', id: 'call', fields: { NAME: 'walk' } } } } },
          { type: 'pokecon_sub_def', id: 'def', y: 300, fields: { NAME: 'walk', ARGS: '' } },
          { type: 'pokecon_sub_def', y: 500, fields: { NAME: 'run', ARGS: '' } },
        ] } }, ws);
        const call = ws.getBlockById('call');
        const options = call.getField('NAME').getOptions(false).map((o) => o[1]);
        // When: 定義の名前を変える（追従は変更事象で動くため、流れきるのを待つ）
        ws.getBlockById('def').setFieldValue('stroll', 'NAME');
        await new Promise((r) => setTimeout(r, 30));
        done({ options, renamed: call.getFieldValue('NAME'),
               code: Blockly.Python.workspaceToCode(ws) });
        """
    )
    # Then: 候補に定義名が並び、改名に呼出が追従する
    assert "walk" in res["options"] and "run" in res["options"]
    assert res["renamed"] == "stroll"
    assert "self.stroll()" in res["code"]
    assert "def stroll(self) -> None:" in res["code"]


@NEEDS_NODE
def test_japanese_variable_names_stay_readable_in_generated_code() -> None:
    """日本語の変数名は `_E9_A0_85` のように潰さず、そのまま（記号だけ _ に）出すこと。"""
    import ast

    from core import blockly_validate

    # Given: 日本語・数字始まり・記号入りの変数を使うプログラム
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        const set = (name, n) => ({ type: 'variables_set', fields: { VAR: { id: name } },
          inputs: { VALUE: { block: { type: 'math_number', fields: { NUM: n } } } } });
        const a = set('回数', 3), b = set('2個目', 1), c = set('色・形', 2);
        a.next = { block: b }; b.next = { block: c };
        Blockly.serialization.workspaces.load({
          variables: [{ name: '回数', id: '回数' }, { name: '2個目', id: '2個目' },
                      { name: '色・形', id: '色・形' }],
          blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'Vars' }, inputs: { DO: { block: a } } },
          ] } }, ws);
        // When: 生成する
        done({ code: Blockly.Python.workspaceToCode(ws) });
        """
    )
    code = res["code"]
    # Then: 読める名前で出て、Pythonとして正しく、検査も通る
    assert "回数 = 3" in code
    assert "my_2個目 = 1" in code
    assert "色_形 = 2" in code
    assert "_E9_" not in code
    ast.parse(code)
    assert blockly_validate.validate_generated_code(code) == []


@NEEDS_NODE
def test_saved_template_name_survives_loading_even_when_not_in_the_list() -> None:
    """候補一覧に無い（未到着・改名済み）画像名でも、読込で黙って書き換えないこと。"""
    # Given: 候補一覧は空、保存物は画像名を持つ
    res = run_blockly(
        """
        Blockly.PokeconTemplates = [];
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_vision_contains', id: 'v',
            fields: { TEMPLATE: 'old-pack/gone.png', THRESHOLD: 0.7 } } ] } }, ws);
        // When: 値と表示を読む
        const f = ws.getBlockById('v').getField('TEMPLATE');
        const fresh = ws.newBlock('pokecon_vision_contains').getField('TEMPLATE');
        done({ value: f.getValue(), text: f.getText(),
               options: f.getOptions(false).map((o) => o[1]),
               freshValue: fresh.getValue(), freshText: fresh.getText() });
        """
    )
    # Then: 保存値は残り、候補にも出る。新しく置いたものは未選択
    assert res["value"] == "old-pack/gone.png"
    assert res["text"] == "old-pack/gone.png"
    assert "old-pack/gone.png" in res["options"]
    assert res["freshValue"] == ""
    assert res["freshText"] == "（画像を選ぶ）"


@NEEDS_NODE
def test_saved_call_to_a_not_yet_loaded_definition_keeps_its_name() -> None:
    """定義より先に読まれた呼出も、保存されていた名前を失わないこと。"""
    # Given: 呼出が定義より前に並ぶ保存物
    res = run_blockly(
        """
        const ws = new Blockly.Workspace();
        Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
          { type: 'pokecon_sub_call_value', id: 'v', fields: { NAME: 'later' } },
          { type: 'pokecon_sub_def', y: 300, fields: { NAME: 'later', ARGS: '' } },
        ] } }, ws);
        // When/Then: 名前が残っている
        done({ name: ws.getBlockById('v').getFieldValue('NAME') });
        """
    )
    assert res == {"name": "later"}
