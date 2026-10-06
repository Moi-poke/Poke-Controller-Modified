"""core.log_model（ログ1行の型と水準の推定）の検証。Tk を使わない。"""

from __future__ import annotations

from core.log_model import (
    LineClassifier,
    entries_for,
    split_lines,
    visible_levels,
)


def test_traceback_block_is_error_and_only_its_last_line_is_collected() -> None:
    # Given: 例外の traceback が1行ずつ print される
    c = LineClassifier()
    lines = [
        "Traceback (most recent call last):",
        '  File "x.py", line 3, in do',
        "    raise ValueError('x')",
        "ValueError: x",
        "next",
    ]
    # When: 1行ずつ推定する
    got = [c.classify(line) for line in lines]
    # Then: 塊はエラー、集めるのは例外名の行だけ、塊の後は通常に戻る
    assert got == [
        ("error", False),
        ("error", False),
        ("error", False),
        ("error", True),
        ("info", False),
    ]


def test_chained_traceback_stays_one_error_block() -> None:
    # Given: 連鎖した例外（During handling ...）
    c = LineClassifier()
    lines = [
        "Traceback (most recent call last):",
        '  File "a.py", line 1, in f',
        "KeyError: 'k'",
    ]
    first = [c.classify(line) for line in lines]
    # Then: 最初の塊の終わりで1件集まる
    assert first[-1] == ("error", True)
    c2 = LineClassifier()
    chained = [
        "Traceback (most recent call last):",
        '  File "a.py", line 1, in f',
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        '  File "b.py", line 2, in g',
        "RuntimeError: r",
    ]
    got = [c2.classify(line) for line in chained]
    # Then: 継ぎ目では終わらず、最後の例外の行だけが集まる
    assert [pin for _lv, pin in got] == [False] * 7 + [True]
    assert all(level == "error" for level, _pin in got)


def test_explicit_markers_are_classified_and_plain_text_is_not() -> None:
    c = LineClassifier()
    # Then: 目印のある行だけを水準つきにする
    assert c.classify("ERROR: 接続できません") == ("error", True)
    assert c.classify("[WARN] 遅延しています") == ("warning", True)
    assert c.classify("警告: 画像が暗い") == ("warning", True)
    assert c.classify("-- finished successfully. --") == ("success", False)
    assert c.classify("-- paused. --") == ("system", False)
    # Then: 「error」を含むだけの普通の文はエラーにしない（誤警報を避ける）
    assert c.classify("error count is 0") == ("info", False)
    assert c.classify("例外が発生しました。") == ("error", False)


def test_split_lines_drops_only_one_trailing_newline() -> None:
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a") == ["a"]
    assert split_lines("\n") == [""]
    assert split_lines("a\n\n") == ["a", ""]


def test_entries_for_pins_all_first_or_none() -> None:
    raw = "one\ntwo\n"
    assert [e.pin for e in entries_for(raw, "result", "all")] == [True, True]
    assert [e.pin for e in entries_for(raw, "error", "first")] == [True, False]
    assert [e.pin for e in entries_for(raw, "input")] == [False, False]


def test_visible_levels_hides_whole_groups() -> None:
    shown = visible_levels({"info": False})
    # Then: 通常の出力の束（info・system・success…）がまとめて消え、他は残る
    assert "info" not in shown and "system" not in shown
    assert {"result", "warning", "error"} <= shown
