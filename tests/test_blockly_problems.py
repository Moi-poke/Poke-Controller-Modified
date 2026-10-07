"""Blockly編集画面の「問題」欄（組立中の検査と一覧表示）の検証。

保存を押す前から、足りない所を一覧で見せ、選ぶとそのブロックへ飛べる。
判定は `Blockly.PokeconEditor.problems`（描画なしで検証できる純粋部）、
表示は editor.html の問題ボタン・問題タブ。
"""

from __future__ import annotations

from blockly_node import NEEDS_NODE, run_blockly, run_editor

pytestmark = NEEDS_NODE

#: プログラム（press → wait）の土台。問題なしの組み立てになる。
_PROGRAM_WITH_PRESS = (
    "{ blocks: { languageVersion: 0, blocks: ["
    " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
    "   inputs: { DO: { block:"
    "    { type: 'pokecon_press', id: 'a1',"
    "      fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },"
    "      next: { block: { type: 'pokecon_wait', id: 'w1',"
    "        fields: { SEC: 1 } } } } } } } ] } }"
)


def _problems_body(state_js: str, tail: str = "") -> str:
    """状態を読んで問題を数える probe 本文を作る。"""
    head = "const ws = new Blockly.Workspace();\n"
    load = "Blockly.serialization.workspaces.load(" + state_js + ", ws);\n"
    last = "done({ problems: E.problems(ws) });\n"
    return head + load + tail + last


def test_complete_assembly_has_no_problems() -> None:
    """そろった組み立てでは問題が出ないこと。"""
    # Given: プログラムにpress→waitと並べた組み立て
    res = run_blockly(_problems_body(_PROGRAM_WITH_PRESS))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 空配列
    assert res["problems"] == []


def test_missing_program_is_an_error_without_a_block() -> None:
    """プログラムが無いときは、ブロックに結び付かないerrorが1件出ること。"""
    # Given: 空のワークスペース
    res = run_blockly(_problems_body("{ blocks: { languageVersion: 0, blocks: [] } }"))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: errorが1件で、指すブロックは無い
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "error"
    assert item["blockId"] is None
    assert "プログラム" in item["message"]


def test_second_program_is_an_error_pointing_at_it() -> None:
    """プログラムが2個あるときは、2個目にerrorが付くこと。"""
    # Given: 中身のあるプログラムが2個
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_press', id: 'a1',"
        "     fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } },"
        " { type: 'pokecon_program', id: 'p2', fields: { NAME: 'y' },"
        "   inputs: { DO: { block: { type: 'pokecon_wait', id: 'w2',"
        "     fields: { SEC: 1 } } } } } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 2個目へのerrorだけ
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "error"
    assert item["blockId"] == "p2"


def test_vision_block_without_an_image_is_an_error_pointing_at_it() -> None:
    """画像未選択の画像認識ブロックは、そのブロックへのerrorになること。"""
    # Given: 置いたばかり（画像未選択）の画像待ちをプログラムにつないだ形
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_vision_wait_appear',"
        "     id: 'v1' } } } } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 画像のerrorが1件だけ（範囲が空＝全体なので範囲のerrorは無い）
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "error"
    assert item["blockId"] == "v1"
    assert "画像" in item["message"]


def test_bad_crop_is_an_error_pointing_at_it() -> None:
    """範囲の書式が不正（x2<=x1）なときは、そのブロックへのerrorになること。"""
    # Given: 画像は選んだが範囲が「10,20,10,30」の画像待ち
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_vision_wait_appear',"
        "     id: 'v1', fields: { TEMPLATE: 'a.png' } } } } } ] } }"
    )
    res = run_blockly(
        _problems_body(
            state,
            "const v = ws.getBlockById('v1');\n"
            "const orig = v.getFieldValue.bind(v);\n"
            "v.getFieldValue = (n) =>"
            " (n === 'CROP' ? '10,20,10,30' : orig(n));\n",
        )
    )
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 範囲のerrorが1件
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "error"
    assert item["blockId"] == "v1"
    assert "範囲" in item["message"]


def test_call_without_a_matching_definition_is_an_error() -> None:
    """同名の定義が無い呼出は、その呼出へのerrorになること。"""
    # Given: 定義の無い名を呼ぶブロックをプログラムにつないだ形
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_sub_call', id: 'c1',"
        "     fields: { NAME: 'missing_sub' } } } } } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 未定義のerrorが呼出を指す
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "error"
    assert item["blockId"] == "c1"
    assert "未定義" in item["message"]


def test_matching_definition_silences_the_call() -> None:
    """同名の定義がある呼出は問題にならないこと。"""
    # Given: 定義fooと、それを呼ぶブロック
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_sub_call', id: 'c1',"
        "     fields: { NAME: 'foo' } } } } },"
        " { type: 'pokecon_sub_def', id: 'd1',"
        "   fields: { NAME: 'foo', ARGS: '' } } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 空配列
    assert res["problems"] == []


def test_duplicate_subroutine_definitions_point_at_the_later_ones() -> None:
    """同名の定義が2個あるときは、2個目にerrorが付くこと。"""
    # Given: 同名fooの定義が2個と、中身のあるプログラム
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_press', id: 'a1',"
        "     fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } },"
        " { type: 'pokecon_sub_def', id: 'd1',"
        "   fields: { NAME: 'foo', ARGS: '' } },"
        " { type: 'pokecon_sub_def', id: 'd2',"
        "   fields: { NAME: 'foo', ARGS: '' } } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 2個目へのerrorだけ
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "error"
    assert item["blockId"] == "d2"
    assert "重複" in item["message"]


def test_orphan_chunk_is_a_warning_pointing_at_it() -> None:
    """プログラムの外の塊は、実行されない旨のwarnになること。"""
    # Given: プログラム（中身あり）と、外に置いたpress
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_press', id: 'a1',"
        "     fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } },"
        " { type: 'pokecon_press', id: 's1', x: 400, y: 0,"
        "   fields: { BUTTON: 'B', DURATION: 0.1, WAIT: 0.1 } } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 外の塊へのwarnだけ
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "warn"
    assert item["blockId"] == "s1"


def test_empty_program_is_a_warning() -> None:
    """中身の無いプログラムはwarnになること（新規の初期形）。"""
    # Given: 新規の初期形
    res = run_blockly(
        "done({ problems: E.problems((() => {\n"
        "const ws = new Blockly.Workspace();\n"
        "Blockly.serialization.workspaces.load(E.defaultState(), ws);\n"
        "return ws; })()) });\n"
    )
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 空のwarnが1件
    assert len(res["problems"]) == 1
    item = res["problems"][0]
    assert item["level"] == "warn"
    assert item["blockId"] is not None
    assert "空" in item["message"]


def test_manually_disabled_blocks_are_not_counted() -> None:
    """手で無効にしたブロックとその中は数えないこと。"""
    # Given: 画像未選択の画像待ちを無効にしてプログラムにつないだ形
    state_tpl = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_vision_wait_appear',"
        "     id: 'v1' } } } } ] } }"
    )
    res = run_blockly(
        _problems_body(
            state_tpl, "ws.getBlockById('v1').setDisabledReason(true, 'MANUAL');\n"
        )
    )
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 画像のerrorは出ない（中身はあるので空のwarnも無い）
    assert res["problems"] == []

    # Given: 中身の無いプログラムと、手で無効にした外の塊
    state_orphan = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1',"
        "   fields: { NAME: 'x' } },"
        " { type: 'pokecon_press', id: 's1', x: 400, y: 0,"
        "   fields: { BUTTON: 'B', DURATION: 0.1, WAIT: 0.1 } } ] } }"
    )
    res2 = run_blockly(
        _problems_body(
            state_orphan,
            "ws.getBlockById('s1').setDisabledReason(true, 'MANUAL');\n",
        )
    )
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 外の塊のwarnは出ず、空のwarnだけ残る
    assert len(res2["problems"]) == 1
    assert res2["problems"][0]["level"] == "warn"
    assert res2["problems"][0]["blockId"] == "p1"

    # Given: 無効にしたプログラムの中にある未定義の呼出
    state_sub = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' },"
        "   inputs: { DO: { block: { type: 'pokecon_sub_call', id: 'c1',"
        "     fields: { NAME: 'missing_sub' } } } } } ] } }"
    )
    res3 = run_blockly(
        _problems_body(
            state_sub,
            "ws.getBlockById('p1').setDisabledReason(true, 'MANUAL');\n",
        )
    )
    # When: 問題を数える（上は probe 本文に含む）
    # Then: 中の呼出は数えず、無効なプログラムは無いものとして扱う
    assert len(res3["problems"]) == 1
    assert res3["problems"][0]["level"] == "error"
    assert res3["problems"][0]["blockId"] is None


def test_errors_come_before_warnings_in_workspace_order() -> None:
    """errorが先に並び、同じ重さはワークスペースの並び順になること。"""
    # Given: 空のプログラム＋外に置いた画像未選択の画像待ち
    state = (
        "{ blocks: { languageVersion: 0, blocks: ["
        " { type: 'pokecon_program', id: 'p1', fields: { NAME: 'x' } },"
        " { type: 'pokecon_vision_wait_appear', id: 'v1', x: 400, y: 0 } ] } }"
    )
    res = run_blockly(_problems_body(state))
    # When: 問題を数える（上は probe 本文に含む）
    # Then: error→warn→warnで、warnはプログラム・外の塊の順
    assert [p["level"] for p in res["problems"]] == [
        "error",
        "warn",
        "warn",
    ]
    assert res["problems"][0]["blockId"] == "v1"
    assert [p["blockId"] for p in res["problems"][1:]] == ["p1", "v1"]


def test_boot_with_empty_program_shows_a_warning_count() -> None:
    """起動直後（初期形＝中身が空）は問題ボタンがwarnの件数を出すこと。"""
    # Given: 控えの無い起動
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        // When: 起動して数え直しを待つ
        await flush(800);
        done({ btn: els.problemsbtn.textContent,
               cls: els.problemsbtn.className,
               rows: els.problemslist.options.map((r) => r.textContent) });
        """
    )
    # Then: warn1件の表示で、一覧にも空の行が1行出る
    assert "1" in res["btn"] and "⚠" in res["btn"]
    assert "warn" in res["cls"]
    assert len(res["rows"]) == 1
    assert "空" in res["rows"][0]


def test_adding_an_undefined_call_turns_the_button_into_an_error() -> None:
    """未定義の呼出を足すと数え直されてerrorになり、赤系の印になること。"""
    # Given: 起動直後の編集画面
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(800);
        // When: 未定義の呼出をプログラムの中へ足す
        const prog = ws.getTopBlocks(false)[0];
        const c = ws.newBlock('pokecon_sub_call');
        try { c.setFieldValue('missing_sub', 'NAME'); } catch (e) {}
        prog.getInput('DO').connection.connect(c.previousConnection);
        await flush(700);
        const btn = { text: els.problemsbtn.textContent,
                      cls: els.problemsbtn.className };
        // When: 問題ボタンを押す
        els.problemsbtn.handlers.click();
        await flush(50);
        done({ btn, main: els.main.className, tab: els.tabproblems.className,
               listShown: els.problemslist.style.display });
        """
    )
    # Then: errorの印になり、押すと右欄が開いて問題タブになる
    assert "✖" in res["btn"]["text"]
    assert "err" in res["btn"]["cls"]
    assert "with-code" in res["main"]
    assert res["tab"] == "tab on"
    assert res["listShown"] != "none"


def test_clicking_a_problem_row_selects_and_centers_the_block() -> None:
    """問題の一覧の行を押すと、そのブロックが選ばれて真ん中に来ること。"""
    # Given: 中身の無いプログラム（空のwarnが1行）の起動直後
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        const centered = [];
        ws.centerOnBlock = (id) => { centered.push(id); };
        boot();
        await flush(800);
        const progId = ws.getTopBlocks(false)[0].id;
        // When: 問題の一覧の行を押す
        els.problemslist.options[0].handlers.click();
        await flush(50);
        done({ centered, progId });
        """
    )
    # Then: そのブロックへ真ん中寄せする
    assert res["centered"] == [res["progId"]]
