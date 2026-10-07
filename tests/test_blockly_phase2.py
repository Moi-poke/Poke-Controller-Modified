"""Blockly Phase 2（画像連携単純化）の静的検証。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def read_editor() -> str:
    """編集画面の全文を読む。"""
    return (BLOCKLY / "editor.html").read_text(encoding="utf-8")


def test_legacy_apply_marks_block_modal_recommended() -> None:
    """従来の反映経路に📷モーダル推奨の案内があること。"""
    html = read_editor()
    assert "capblockapply-legacy" in html or "従来" in html
    assert "📷" in html


def test_crop_presave_validation_exists() -> None:
    """保存前にCROP欄の形式を検査すること（黙って落とさない）。"""
    html = read_editor()
    assert "validateCropsLocal" in html


def test_color_filter_presets_exist() -> None:
    """色フィルタにプリセット選択があること。"""
    html = read_editor()
    assert 'id="capfilt_preset"' in html
    assert "赤" in html


def test_template_preview_fallback_exists() -> None:
    """存在しないテンプレ名で壊れ表示にしないこと。"""
    html = read_editor()
    assert "preloadPreview" in html
