"""Blockly Phase 1（編集体験）の静的検証。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def read_editor() -> str:
    """編集画面の全文を読む。"""
    return (BLOCKLY / "editor.html").read_text(encoding="utf-8")


def test_inject_has_workspace_options() -> None:
    """ズーム・ゴミ箱・グリッド・レンダラが指定されていること。"""
    html = read_editor()
    assert "trashcan: true" in html
    assert "zoom:" in html
    assert "controls: true" in html
    assert "grid:" in html
    assert "renderer: 'zelos'" in html


def test_preview_and_error_markup_exists() -> None:
    """プレビュー・エラー集約・未保存表示の要素があること。"""
    html = read_editor()
    for token in [
        'id="previewtoggle"',
        'id="previewcopy"',
        'id="previewwrap"',
        'id="preview"',
        'id="previewstatus"',
        'id="saveerrors"',
        'id="stemerror"',
        'id="dirty"',
        'id="help"',
    ]:
        assert token in html


def test_status_regions_are_live() -> None:
    """状態表示がスクリーンリーダへ伝わること。"""
    html = read_editor()
    assert 'id="status" role="status" aria-live="polite"' in html
    assert 'id="capstatus" role="status" aria-live="polite"' in html
    assert 'id="saveerrors" role="alert"' in html


def test_client_side_guards_exist() -> None:
    """保存前検証・未保存警告・Ctrl+Sがあること。"""
    html = read_editor()
    assert "validateStemLocal" in html
    assert "STEM_RE" in html
    assert "beforeunload" in html
    assert "doSave" in html
    assert "Ctrl+S" in html


def test_single_inline_script_for_probe() -> None:
    """検証スタブが想定する単一inline scriptを保つこと。"""
    html = read_editor()
    assert html.count("<script>") == 1
