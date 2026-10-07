"""編集画面の構成（1段ヘッダー・横並びコード欄・旧来道具の格納）の検証。

見た目そのものは実ブラウザでしか確かめられないため、ここでは
「どの操作がどこにあるか」という構成の約束を固定する。
"""

from __future__ import annotations

from blockly_node import BLOCKLY, NEEDS_NODE


def read_editor() -> str:
    """編集画面の全文を読む。"""
    return (BLOCKLY / "editor.html").read_text(encoding="utf-8")


def section(html: str, start_marker: str, end_marker: str) -> str:
    """start_marker から end_marker までを切り出す。"""
    start = html.index(start_marker)
    return html[start : html.index(end_marker, start)]


def test_header_holds_every_everyday_action_in_one_bar() -> None:
    """新規・開く・保存・元に戻す・やり直し・整列・全体表示・テンプレ作成・コードが1段にあること。"""
    # Given: 編集画面のヘッダー
    header = section(read_editor(), '<header id="bar"', "</header>")
    # When/Then: 日常の操作がすべてヘッダー内にある
    for token in [
        'id="newws"',
        'id="files"',
        'id="open"',
        'id="stem"',
        'id="save"',
        'id="dirty"',
        'id="undo"',
        'id="redo"',
        'id="cleanup"',
        'id="fit"',
        'id="capget"',
        'id="previewtoggle"',
        'id="help"',
    ]:
        assert token in header, f"ヘッダーに {token} が無い"


def test_generated_code_sits_beside_the_workspace_not_above_it() -> None:
    """生成コードはワークスペースの横（右ペイン）に出し、キャンバスを押し下げないこと。"""
    # Given: 本体の並び
    main = section(read_editor(), '<main id="main"', "</main>")
    # When/Then: ワークスペース→仕切り→コード欄の順に並ぶ
    assert main.index('id="ws"') < main.index('id="splitter"')
    assert main.index('id="splitter"') < main.index('id="previewwrap"')
    assert 'id="previewcopy"' in main


def test_legacy_tools_are_folded_away_but_still_present() -> None:
    """従来の反映・切出し区画は折りたたみ内に残し、日常の画面から外すこと。"""
    # Given: 折りたたみ区画
    legacy = section(read_editor(), '<details id="legacy"', "</details>")
    # When/Then: 旧来の要素はそこにある（既存の保存物・手順を壊さない）
    for token in [
        'id="tpllist"',
        'id="tplapply"',
        'id="capblockapply-legacy"',
        'id="capwrap"',
        'id="capname"',
        'id="capsave"',
        'id="capstatus"',
    ]:
        assert token in legacy, f"折りたたみに {token} が無い"


def test_new_actions_are_wired_through_guarded_helpers() -> None:
    """新しい操作は要素が無くても落ちない on() 経由で結線されていること。"""
    # Given: inline script
    html = read_editor()
    script = html[html.index("<script>\n") :]
    # When/Then: 検証スタブ（最小DOM）でも落ちない登録方法を使う
    for action in ["newws", "undo", "redo", "cleanup", "fit", "splitter"]:
        assert f"on('{action}'" in script, f"{action} が on() で結線されていない"


def test_every_element_id_is_unique() -> None:
    """要素IDは重複しないこと（重複すると描画先や結線が別の要素へ化ける）。"""
    import re

    # Given: 編集画面の全ID
    ids = re.findall(r'\sid="([^"]+)"', read_editor())
    # When: 重複を数える
    dups = sorted({i for i in ids if ids.count(i) > 1})
    # Then: 重複は無い
    assert dups == []


def test_status_line_and_error_banner_follow_the_header() -> None:
    """状態行とエラー欄はヘッダー直下にあり、読み上げ対象であること。"""
    # Given: 編集画面
    html = read_editor()
    # When/Then: 状態行・エラー欄の位置と属性
    assert html.index("</header>") < html.index('id="status"')
    assert html.index('id="status"') < html.index('<main id="main"')
    assert 'id="status" role="status" aria-live="polite"' in html
    assert 'id="saveerrors" role="alert"' in html


@NEEDS_NODE
def test_editor_loads_blockly_media_from_the_bundled_folder() -> None:
    """Blocklyの画像・音は同梱の media/ から読むこと（既定の static.blockly.com を見に行かない）。"""
    from blockly_node import run_editor

    res = run_editor(
        """
        let opts = null;
        Blockly.inject = (_id, o) => { opts = o; return ws; };
        boot();
        await flush(300);
        done({ media: opts && opts.media });
        """
    )
    assert res["media"] == "./media/"
