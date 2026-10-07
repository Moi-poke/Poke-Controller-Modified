"""上段道具バー（1行・「⋯」メニュー・狭幅の記号化）の検証。

幅 1280px で 1 行に収める作り直しの約束を固定する。見た目の寸法は
実ブラウザでしか確かめられないため、ここでは構成（どの id がどこに
あるか・既存の配線が残るか）と、最小DOMでの開閉・移動・従来動作を
確かめる。
"""

from __future__ import annotations

import re

from blockly_node import BLOCKLY, NEEDS_NODE, run_editor


def read_editor() -> str:
    """編集画面の全文を読む。"""
    return (BLOCKLY / "editor.html").read_text(encoding="utf-8")


def header_html(html: str) -> str:
    """上段（header#bar）の範囲を切り出す。"""
    start = html.index('<header id="bar"')
    return html[start : html.index("</header>", start)]


#: 上段にあるべき操作の id（⋯メニューの中身と開閉部品を含む）。
REQUIRED_IDS = [
    "newws",
    "files",
    "open",
    "stem",
    "save",
    "dirty",
    "runbtn",
    "pausebtn",
    "stepbtn",
    "stopbtn",
    "recbtn",
    "runstate",
    "undo",
    "redo",
    "quickbtn",
    "previewtoggle",
    "morebtn",
    "moremenu",
]

#: ⋯メニューに入れる既存操作（id を付け直さず項目として置く）。
MENU_IDS = ["cleanup", "fit", "capget", "del", "help"]


def test_header_holds_every_listed_id_exactly_once() -> None:
    """上段の一覧（⋯メニュー部品を含む）の id がちょうど1回ずつあること。"""
    # Given: 編集画面の全文
    html = read_editor()
    # When/Then: 重複も欠落もない
    for token in REQUIRED_IDS + MENU_IDS:
        found = len(re.findall(r'id="' + re.escape(token) + r'"', html))
        assert found == 1, f"{token} が {found} 回ある"


def test_overflow_actions_live_inside_the_more_menu() -> None:
    """整列・全体表示・テンプレ作成・削除・使い方が moremenu の中にあること。"""
    # Given: 上段と⋯メニューの範囲
    html = read_editor()
    header = header_html(html)
    menu_start = header.index('id="moremenu"')
    # When/Then: 5 操作はメニュー開始より後・上段終わりより前にある
    for token in MENU_IDS:
        pos = header.index(f'id="{token}"')
        assert menu_start < pos, f"{token} が moremenu の外にある"


def test_more_menu_has_the_expected_roles_and_labels() -> None:
    """⋯ボタンとメニューに想定の role・aria・見出しがあること。"""
    # Given: 上段
    header = header_html(read_editor())
    # When/Then: 開閉部品の約束
    assert 'aria-haspopup="menu"' in header
    assert 'aria-controls="moremenu"' in header
    assert 'title="その他の操作"' in header
    assert ">⋯<" in header
    # When/Then: メニュー本体と項目の約束
    assert 'id="moremenu" role="menu"' in header
    assert header.count('role="menuitem"') >= 5


def test_header_keeps_the_designed_left_to_right_order() -> None:
    """上段の並びが設計どおり（ファイル→保存→試し実行→編集→右寄せ）であること。"""
    # Given: 上段
    header = header_html(read_editor())
    # When: 設計の並び順に位置を取る
    order = [
        "newws",
        "files",
        "open",
        "stem",
        "save",
        "dirty",
        "runbtn",
        "pausebtn",
        "stepbtn",
        "stopbtn",
        "recbtn",
        "undo",
        "redo",
        "quickbtn",
        "previewtoggle",
        "morebtn",
        "moremenu",
        "cleanup",
        "fit",
        "capget",
        "del",
        "help",
    ]
    positions = [header.index(f'id="{token}"') for token in order]
    # Then: 単調に増える（右寄せの塊が末尾にある）
    assert positions == sorted(positions), "上段の並びが設計と違う"


def test_existing_menu_actions_keep_their_wiring() -> None:
    """項目化した操作が既存の結線（on/addEventListener）を保つこと。"""
    # Given: inline script
    html = read_editor()
    script = html[html.index("<script>\n") :]
    # When/Then: 既存の結線が残り、⋯の開閉が足されている（別 id への転送は作らない）
    for action in ["cleanup", "fit", "del"]:
        assert f"on('{action}'" in script, f"{action} の on() 結線が無い"
    assert "getElementById('capget').addEventListener('click'" in script
    assert "on('morebtn'" in script
    assert "on('moremenu'" in script
    for invented in ["morecleanup", "morefit", "morecapget", "moredel", "morehelp"]:
        assert invented not in html, f"転送用の別 id {invented} を作らない"


def test_header_stays_on_one_row_with_symbol_only_narrow_mode() -> None:
    """上段は折り返さず、狭幅では文字付きボタンを記号だけにすること。"""
    # Given: 編集画面の CSS
    html = read_editor()
    style = html[html.index("<style>") : html.index("</style>")]
    bar = style[style.index("#bar {") : style.index("#bar .grp")]
    # When/Then: 上段は1行分（折り返しなし・44px前後）
    assert "nowrap" in bar
    assert "44px" in bar
    # When/Then: 1100px 未満で文字部を隠し記号部を出す規則がある
    assert "1099px" in style
    narrow = style[style.index("1099px") - 60 : style.index("1099px") + 300]
    assert ".t" in narrow and ".n" in narrow


def test_text_buttons_keep_accessible_names_for_narrow_mode() -> None:
    """文字付きボタンは狭幅で記号だけになっても aria-label と title を保つこと。"""
    # Given: 上段
    header = header_html(read_editor())
    # When/Then: 文字付きボタンに両方ある
    text_buttons = [
        "newws",
        "open",
        "save",
        "runbtn",
        "recbtn",
        "quickbtn",
        "previewtoggle",
    ]
    for token in text_buttons:
        tag_start = header.index(f'id="{token}"')
        begin = header.rindex("<button", 0, tag_start)
        tag = header[begin : header.index(">", tag_start) + 1]
        assert "aria-label=" in tag, f"{token} に aria-label が無い"
        assert "title=" in tag, f"{token} に title が無い"


def test_help_mentions_the_more_menu() -> None:
    """使い方の箇条に⋯メニューの中身の案内があること。"""
    # Given: 使い方（details#help）の範囲
    html = read_editor()
    start = html.index('id="help"')
    help_body = html[start : html.index("</details>", start)]
    # When/Then: ⋯メニューの案内がある
    assert "⋯ メニュー" in help_body
    for word in ["整列", "全体表示", "テンプレ作成", "削除", "使い方"]:
        assert word in help_body, f"案内に {word} が無い"


@NEEDS_NODE
def test_more_button_toggles_the_menu_and_focuses_the_first_item() -> None:
    """⋯を押すと開いて最初の項目へ焦点が移り、aria-expanded が true になること。"""
    # Given: 起動直後の編集画面（焦点の記録だけ差し替える）
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        const seen = [];
        for (const id of ['cleanup', 'fit', 'capget', 'del', 'help', 'morebtn']) {
          el(id).focus = () => { seen.push(id); };
        }
        // When: ⋯を押す
        el('morebtn').handlers.click();
        await flush(50);
        done({ expanded: el('morebtn').attrs['aria-expanded'],
               display: el('moremenu').style.display, seen });
        """
    )
    # Then: 開いて最初の項目（整列）へ焦点が移る
    assert res["expanded"] == "true"
    assert res["display"] == "block"
    assert res["seen"] == ["cleanup"]


@NEEDS_NODE
def test_more_menu_escape_closes_and_returns_focus() -> None:
    """Esc で閉じて⋯へ焦点が戻り、aria-expanded が false になること。"""
    # Given: ⋯メニューを開いた状態
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        const seen = [];
        el('morebtn').focus = () => { seen.push('morebtn'); };
        el('morebtn').handlers.click();
        await flush(50);
        // When: メニュー内で Esc を押す
        el('moremenu').handlers.keydown({ key: 'Escape', preventDefault() {} });
        await flush(50);
        done({ expanded: el('morebtn').attrs['aria-expanded'],
               display: el('moremenu').style.display, seen });
        """
    )
    # Then: 閉じて⋯へ焦点が戻る
    assert res["expanded"] == "false"
    assert res["display"] == "none"
    assert res["seen"] == ["morebtn"]


@NEEDS_NODE
def test_more_menu_arrow_home_end_keys_move_between_items() -> None:
    """↑↓・Home・End で項目間を移動できること。"""
    # Given: ⋯メニューを開いた状態（開いた直後の整列への焦点は捨てる）
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        const seen = [];
        for (const id of ['cleanup', 'fit', 'capget', 'del', 'help']) {
          el(id).focus = () => { seen.push(id); };
        }
        el('morebtn').handlers.click();
        await flush(50);
        seen.length = 0;
        // When: ↓・End・Home・↑を順に押す
        el('moremenu').handlers.keydown({ key: 'ArrowDown', preventDefault() {} });
        el('moremenu').handlers.keydown({ key: 'End', preventDefault() {} });
        el('moremenu').handlers.keydown({ key: 'Home', preventDefault() {} });
        el('moremenu').handlers.keydown({ key: 'ArrowUp', preventDefault() {} });
        done({ seen, stillOpen: el('morebtn').attrs['aria-expanded'] });
        """
    )
    # Then: 全体表示→使い方→整列→整列→使い方の順に移り、開いたまま
    assert res["seen"] == ["fit", "help", "cleanup", "help"]
    assert res["stillOpen"] == "true"


@NEEDS_NODE
def test_more_menu_window_escape_closes_and_returns_focus() -> None:
    """窓全体の Esc でも閉じて⋯へ焦点が戻ること。"""
    # Given: ⋯メニューを開いた状態
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        const seen = [];
        el('morebtn').focus = () => { seen.push('morebtn'); };
        el('morebtn').handlers.click();
        await flush(50);
        // When: 窓全体で Esc を押す
        winHandlers.keydown({ key: 'Escape', preventDefault() {} });
        await flush(50);
        done({ expanded: el('morebtn').attrs['aria-expanded'],
               display: el('moremenu').style.display, seen });
        """
    )
    # Then: 閉じて⋯へ焦点が戻る
    assert res["expanded"] == "false"
    assert res["display"] == "none"
    assert res["seen"] == ["morebtn"]


@NEEDS_NODE
def test_menu_fit_and_cleanup_still_drive_the_workspace() -> None:
    """メニュー内の全体表示・整列が従来どおり描画へ届き、選んだら閉じること。"""
    # Given: 起動直後の編集画面（描画の呼び出しを記録する）
    res = run_editor(
        """
        const worked = [];
        ws.zoomToFit = () => { worked.push('fit'); };
        ws.cleanUp = () => { worked.push('cleanup'); };
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        el('morebtn').handlers.click();
        await flush(50);
        // When: 全体表示・整列を順に押す
        el('fit').handlers.click();
        el('cleanup').handlers.click();
        await flush(50);
        done({ worked, expanded: el('morebtn').attrs['aria-expanded'],
               display: el('moremenu').style.display });
        """
    )
    # Then: 従来どおり働き、選んだら閉じる
    assert res["worked"] == ["fit", "cleanup"]
    assert res["expanded"] == "false"
    assert res["display"] == "none"


@NEEDS_NODE
def test_menu_delete_still_asks_before_deleting() -> None:
    """メニュー内の削除が従来どおり確認を出してから消すこと。"""
    # Given: 保存済み「Old」がある起動直後の編集画面
    res = run_editor(
        """
        routes.list = { stems: ['Old'] };
        routes.delete = { ok: true, message: '削除しました' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        el('files').value = 'Old';
        // When: 削除を押して「いいえ」を選ぶ
        confirmAnswer = false;
        el('del').handlers.click();
        await flush(200);
        const declined = calls.filter((c) => c.url === './delete').length;
        // When: もう一度押して「はい」を選ぶ
        confirmAnswer = true;
        el('del').handlers.click();
        await flush(200);
        done({ confirms, declined,
               deleted: calls.filter((c) => c.url === './delete').length,
               status: el('status').textContent });
        """
    )
    # Then: 確認が出て、「いいえ」では消さず、「はい」で消す
    assert len(res["confirms"]) == 2
    assert "削除" in res["confirms"][0]
    assert res["declined"] == 0
    assert res["deleted"] == 1
    assert "削除しました" in res["status"]
