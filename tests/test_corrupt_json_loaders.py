"""壊れた UTF-8 の保存ファイルで起動を止めないことの検証。実機・GUIなしで回す。"""

from __future__ import annotations

from pathlib import Path

from core import CommandHistory, CommandStats
from services import blockly_save

#: UTF-8 として解釈できないバイト列。JSON の字面以前に復号で落ちる。
BROKEN_BYTES = b'{"name": "\xff\xfe"}'


def test_stats_load_returns_empty_for_invalid_utf8(tmp_path: Path) -> None:
    # Given: 不正な UTF-8 で書かれた使用履歴
    path = tmp_path / "command_stats.json"
    path.write_bytes(BROKEN_BYTES)
    original = CommandStats.STATS_JSON
    CommandStats.STATS_JSON = str(path)
    try:
        # When: 読む
        stats = CommandStats.load()
    finally:
        CommandStats.STATS_JSON = original
    # Then: 例外を漏らさず空で返す
    assert stats == {}


def test_stats_load_reads_valid_json(tmp_path: Path) -> None:
    # Given: 正しい使用履歴
    path = tmp_path / "command_stats.json"
    path.write_text(
        '{"A": {"count": 2, "last": "2026-10-23T10:00:00"}}',
        encoding="utf-8",
    )
    original = CommandStats.STATS_JSON
    CommandStats.STATS_JSON = str(path)
    try:
        # When: 読む
        stats = CommandStats.load()
    finally:
        CommandStats.STATS_JSON = original
    # Then: 従来どおり読める
    assert stats == {"A": {"count": 2, "last": "2026-10-23T10:00:00"}}


def test_history_load_returns_empty_for_invalid_utf8(tmp_path: Path) -> None:
    # Given: 不正な UTF-8 で書かれた実行履歴
    path = tmp_path / "command_history.json"
    path.write_bytes(BROKEN_BYTES)
    original = CommandHistory.HISTORY_JSON
    CommandHistory.HISTORY_JSON = str(path)
    try:
        # When: 読む
        entries = CommandHistory.load()
    finally:
        CommandHistory.HISTORY_JSON = original
    # Then: 例外を漏らさず空で返す
    assert entries == []


def test_history_load_reads_valid_json(tmp_path: Path) -> None:
    # Given: 正しい実行履歴
    path = tmp_path / "command_history.json"
    path.write_text(
        '[{"id": 1, "kind": "python", "name": "A", "result": "完了",'
        ' "started": "2026-10-23 10:00:00", "seconds": 1.5}]',
        encoding="utf-8",
    )
    original = CommandHistory.HISTORY_JSON
    CommandHistory.HISTORY_JSON = str(path)
    try:
        # When: 読む
        entries = CommandHistory.load()
    finally:
        CommandHistory.HISTORY_JSON = original
    # Then: 従来どおり読める
    assert [entry.name for entry in entries] == ["A"]


def test_load_blockly_returns_failed_for_invalid_utf8(tmp_path: Path) -> None:
    # Given: 不正な UTF-8 で書かれた `.blockly.json`
    py_dir = tmp_path / blockly_save.PY_DIR_REL
    py_dir.mkdir(parents=True)
    (py_dir / "Bad.blockly.json").write_bytes(BROKEN_BYTES)
    # When: 開く
    result = blockly_save.load_blockly(tmp_path, "Bad")
    # Then: 例外を漏らさず failed で返す
    assert result.status == "failed"


def test_load_blockly_reads_valid_json(tmp_path: Path) -> None:
    # Given: 正しい `.blockly.json`
    py_dir = tmp_path / blockly_save.PY_DIR_REL
    py_dir.mkdir(parents=True)
    workspace = '{"blocks": {"languageVersion": 0, "blocks": []}}'
    (py_dir / "Good.blockly.json").write_text(workspace, encoding="utf-8")
    # When: 開く
    result = blockly_save.load_blockly(tmp_path, "Good")
    # Then: 従来どおり読める
    assert result.status == "ok"
    assert result.workspace_json == workspace
