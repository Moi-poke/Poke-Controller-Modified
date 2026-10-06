"""コマンド実行履歴の純粋層の検証。本体より先に書くため今は RED。"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from core import CommandHistory, CommandStats


def _now() -> datetime:
    """検証で使う固定の日時。"""
    return datetime(2026, 1, 2, 3, 4, 5)


def test_begin_puts_the_new_entry_first() -> None:
    # Given: 履歴に古い件が 1 件ある
    entries: list[Any] = []
    older = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "古い", _now())
    # When: 新しい実行を始める
    newer = CommandHistory.begin(entries, CommandHistory.KIND_MCU, "新しい", _now())
    # Then: 先頭が新しい件で id は最大 +1、結果は実行中、started は指定時刻
    assert entries[0] is newer
    assert newer.id == older.id + 1
    assert newer.kind == CommandHistory.KIND_MCU
    assert newer.result == CommandHistory.RESULT_RUNNING
    assert newer.started == "2026-01-02 03:04:05"
    assert newer.seconds is None


def test_begin_drops_the_oldest_beyond_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 上限を 3 件に絞る
    monkeypatch.setattr(CommandHistory, "MAX_ENTRIES", 3)
    entries: list[Any] = []
    # When: 4 件始める
    for i in range(4):
        CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, f"cmd{i}", _now())
    # Then: 最も古い件から捨てて新しい順に 3 件だけ残る
    assert [e.name for e in entries] == ["cmd3", "cmd2", "cmd1"]


def test_begin_after_remove_keeps_ids_unique() -> None:
    # Given: 2 件ある履歴から新しい方を消す
    entries: list[Any] = []
    first = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    second = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "b", _now())
    assert CommandHistory.remove(entries, [second.id]) == 1
    assert CommandHistory.remove(entries, [9999]) == 0
    # When: 次を始める
    third = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "c", _now())
    # Then: 残っている件と id が衝突せず一意なまま
    assert third.id == first.id + 1
    assert sorted(e.id for e in entries) == [first.id, third.id]


def test_finish_records_result_and_seconds() -> None:
    # Given: 始めたばかりの件
    entries: list[Any] = []
    entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    # When: 完了で閉じる
    CommandHistory.finish(entry, CommandHistory.RESULT_DONE, 12.5)
    # Then: 結果と秒が入る
    assert entry.result == CommandHistory.RESULT_DONE
    assert entry.seconds == 12.5


def test_finish_clamps_negative_seconds_to_zero() -> None:
    # Given: 始めたばかりの件
    entries: list[Any] = []
    entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    # When: 負の秒で閉じる
    CommandHistory.finish(entry, CommandHistory.RESULT_ERROR, -3.0)
    # Then: 0.0 になる
    assert entry.result == CommandHistory.RESULT_ERROR
    assert entry.seconds == 0.0


def test_save_and_load_roundtrip_keeps_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 相対パス配下で保存できる作業場所
    monkeypatch.chdir(tmp_path)
    entries: list[Any] = []
    first = CommandHistory.begin(
        entries,
        CommandHistory.KIND_PYTHON,
        "日本語コマンド",
        _now(),
    )
    CommandHistory.finish(first, CommandHistory.RESULT_DONE, 75.0)
    second = CommandHistory.begin(entries, CommandHistory.KIND_MCU, "mcu-cmd", _now())
    CommandHistory.finish(second, CommandHistory.RESULT_ERROR, 5.0)
    # When: 保存して読み直す
    assert CommandHistory.save(entries, "") is True
    raw = (tmp_path / "Commands" / "command_history.json").read_text(
        encoding="utf-8",
    )
    loaded = CommandHistory.load("")
    # Then: 日本語は逃げず、内容と新しい順の並びが保たれる
    assert "日本語コマンド" in raw
    wanted = [(e.id, e.kind, e.name, e.result, e.seconds) for e in entries]
    found = [(e.id, e.kind, e.name, e.result, e.seconds) for e in loaded]
    assert found == wanted


def test_load_marks_running_entries_as_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 実行中のまま保存された履歴
    monkeypatch.chdir(tmp_path)
    entries: list[Any] = []
    CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    assert CommandHistory.save(entries, "") is True
    # When: 読み直す
    loaded = CommandHistory.load("")
    # Then: 前回取り残した実行中は中断に変わる
    assert loaded[0].result == CommandHistory.RESULT_ABORTED


def test_load_returns_empty_for_missing_broken_or_non_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 作業場所に履歴が無い
    monkeypatch.chdir(tmp_path)
    # When/Then: 無い・壊れた・トップが list でない場合は空
    assert CommandHistory.load("") == []
    (tmp_path / "Commands").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Commands" / "command_history.json").write_text(
        "壊れた{",
        encoding="utf-8",
    )
    assert CommandHistory.load("") == []
    (tmp_path / "Commands" / "command_history.json").write_text(
        '{"a": 1}',
        encoding="utf-8",
    )
    assert CommandHistory.load("") == []


def test_load_drops_only_the_invalid_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 正しい 1 件に紛れた不正な件の一覧
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Commands").mkdir(parents=True, exist_ok=True)
    payload = (
        '[{"id": 1, "kind": "python", "name": "ok",'
        ' "started": "2026-01-02 03:04:05",'
        ' "seconds": 1.0, "result": "完了"},'
        ' {"id": "x"}, {}, [1, 2], "str", 42, null, true]'
    )
    (tmp_path / "Commands" / "command_history.json").write_text(
        payload,
        encoding="utf-8",
    )
    # When: 読み直す
    loaded = CommandHistory.load("")
    # Then: 不正な件だけ捨てて正しい件は残る
    assert [e.name for e in loaded] == ["ok"]


def test_load_renumbers_duplicate_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: id が重複した一覧
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Commands").mkdir(parents=True, exist_ok=True)
    payload = (
        '[{"id": 5, "kind": "python", "name": "a",'
        ' "started": "2026-01-02 03:04:05",'
        ' "seconds": null, "result": "完了"},'
        ' {"id": 5, "kind": "mcu", "name": "b",'
        ' "started": "2026-01-02 03:04:06",'
        ' "seconds": null, "result": "完了"}]'
    )
    (tmp_path / "Commands" / "command_history.json").write_text(
        payload,
        encoding="utf-8",
    )
    # When: 読み直す
    loaded = CommandHistory.load("")
    # Then: 読み込み順を保ったまま 1 から振り直して一意になる
    assert [e.name for e in loaded] == ["a", "b"]
    assert [e.id for e in loaded] == [1, 2]


def test_load_keeps_only_the_first_max_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: 上限 2 件で 3 件保存された履歴
    monkeypatch.setattr(CommandHistory, "MAX_ENTRIES", 2)
    monkeypatch.chdir(tmp_path)
    entries: list[Any] = []
    for name in ("a", "b"):
        entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, name, _now())
        CommandHistory.finish(entry, CommandHistory.RESULT_DONE, 1.0)
    assert CommandHistory.save(entries, "") is True
    monkeypatch.setattr(CommandHistory, "MAX_ENTRIES", 2)
    # When: 読み直す
    loaded = CommandHistory.load("")
    # Then: 先頭の 2 件だけ残る
    assert [e.name for e in loaded] == ["b", "a"][:2]


def test_path_for_matches_stats_rule() -> None:
    # Given/When/Then: プロファイル無しは定数、ありは CommandStats と同じ規則
    assert CommandHistory.path_for("") == CommandHistory.HISTORY_JSON
    assert CommandHistory.path_for("sw1") == "Commands/command_history.sw1.json"
    stats_path = CommandStats.pathFor("sw1")
    expected = stats_path.replace("command_stats", "command_history")
    assert CommandHistory.path_for("sw1") == expected


def test_clear_keeps_only_the_running_entry() -> None:
    # Given: 3 件の履歴
    entries: list[Any] = []
    for name in ("a", "b", "c"):
        CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, name, _now())
    keep = entries[1]
    # When: 1 件だけ残して消す
    CommandHistory.clear(entries, keep=keep)
    # Then: その 1 件だけ残る
    assert entries == [keep]


def test_clear_without_keep_empties() -> None:
    # Given: 2 件の履歴
    entries: list[Any] = []
    for name in ("a", "b"):
        CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, name, _now())
    # When: そのまま消す
    CommandHistory.clear(entries)
    # Then: 空になる
    assert entries == []


def test_matches_requires_all_words_case_insensitive() -> None:
    # Given: Python の完了した件
    entries: list[Any] = []
    entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "MyCmd", _now())
    CommandHistory.finish(entry, CommandHistory.RESULT_DONE, 1.0)
    # When/Then: 複数語は AND で大文字小文字を問わない
    assert CommandHistory.matches(entry, "mycmd 完了") is True
    assert CommandHistory.matches(entry, "mycmd 存在しない語") is False


def test_matches_finds_kind_label() -> None:
    # Given: MCU の件と Python の件
    entries: list[Any] = []
    mcu = CommandHistory.begin(entries, CommandHistory.KIND_MCU, "Abc", _now())
    py = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "Xyz", _now())
    # When/Then: 種別ラベルでも当たる
    assert CommandHistory.matches(mcu, "mcu") is True
    assert CommandHistory.matches(mcu, "python") is False
    assert CommandHistory.matches(py, "PYTHON") is True


def test_empty_query_matches_all() -> None:
    # Given: 1 件
    entries: list[Any] = []
    entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    # When/Then: 空・空白だけは全件扱い
    assert CommandHistory.matches(entry, "") is True
    assert CommandHistory.matches(entry, "   ") is True


def test_filtered_keeps_order() -> None:
    # Given: 新しい順に 3 件
    entries: list[Any] = []
    for name in ("alpha", "beta", "alpha2"):
        CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, name, _now())
    # When: 絞る
    result = CommandHistory.filtered(entries, "alpha")
    # Then: 並びを保ったまま該当だけ残る
    assert [e.name for e in result] == ["alpha2", "alpha"]


def test_kind_label_maps_known_kinds() -> None:
    # Given/When/Then: 既知の種別は表示名、それ以外はそのまま
    assert CommandHistory.kind_label(CommandHistory.KIND_PYTHON) == "Python"
    assert CommandHistory.kind_label(CommandHistory.KIND_MCU) == "MCU"
    assert CommandHistory.kind_label("other") == "other"


def test_format_duration_formats() -> None:
    # Given/When/Then: None は空、切り捨て秒で m:ss / h:mm:ss
    assert CommandHistory.format_duration(None) == ""
    assert CommandHistory.format_duration(0.9) == "0:00"
    assert CommandHistory.format_duration(5) == "0:05"
    assert CommandHistory.format_duration(75) == "1:15"
    assert CommandHistory.format_duration(3725) == "1:02:05"


def test_sort_key_orders_none_seconds_first_and_breaks_ties_by_id() -> None:
    # Given: 秒が無い件と 0 秒の件、名前が同じで id が違う 2 件
    entries: list[Any] = []
    first = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "same", _now())
    second = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "same", _now())
    CommandHistory.finish(first, CommandHistory.RESULT_DONE, 0.0)
    # When/Then: None は数値より前、同値は id で安定する
    none_key = CommandHistory.sort_key(second, "seconds")
    zero_key = CommandHistory.sort_key(first, "seconds")
    assert none_key < zero_key
    first_key = CommandHistory.sort_key(first, "name")
    second_key = CommandHistory.sort_key(second, "name")
    assert first_key < second_key


def test_sort_key_treats_unknown_column_as_started() -> None:
    # Given: 1 件
    entries: list[Any] = []
    entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    # When/Then: 未知の列は started 扱い
    unknown_key = CommandHistory.sort_key(entry, "unknown")
    started_key = CommandHistory.sort_key(entry, "started")
    assert unknown_key == started_key


def test_entry_dict_roundtrip() -> None:
    # Given: 完了した件
    entries: list[Any] = []
    entry = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "a", _now())
    CommandHistory.finish(entry, CommandHistory.RESULT_DONE, 2.5)
    # When: 辞書にして戻す
    restored = CommandHistory.from_dict(entry.to_dict())
    # Then: 中身が等しい
    assert restored is not None
    assert (restored.id, restored.kind, restored.name) == (
        entry.id,
        entry.kind,
        entry.name,
    )
    assert (restored.started, restored.result, restored.seconds) == (
        entry.started,
        entry.result,
        entry.seconds,
    )


def test_from_dict_rejects_bad_shapes() -> None:
    # Given: 必須の形
    good: dict[str, Any] = {
        "id": 1,
        "kind": "python",
        "name": "a",
        "started": "2026-01-02 03:04:05",
        "seconds": 1.0,
        "result": "完了",
    }
    # When/Then: dict でない・必須キー欠落・型不正・bool 混じりは None
    assert CommandHistory.from_dict(None) is None
    assert CommandHistory.from_dict([]) is None
    assert CommandHistory.from_dict({}) is None
    bad_id = dict(good)
    bad_id["id"] = True
    assert CommandHistory.from_dict(bad_id) is None
    bad_seconds = dict(good)
    bad_seconds["seconds"] = True
    assert CommandHistory.from_dict(bad_seconds) is None
    bad_name = dict(good)
    bad_name["name"] = 123
    assert CommandHistory.from_dict(bad_name) is None
