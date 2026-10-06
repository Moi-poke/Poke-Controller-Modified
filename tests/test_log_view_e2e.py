"""実 Tk でログ欄（上＝全出力、下＝結果とエラー）を組み立てて確かめる E2E.

LogPanelMixin._build_log_area を本物の Tk 上で呼び、キューへ行を積んで
display_text（GUI の取り出しポンプ）を回し、画面の Text に起きたことを
観測する。各シナリオは子プロセスで走らせ、証跡（report.json・
observations.jsonl・teardown.json）を外部ディレクトリへ残す。

既定では skip する（CI は画面を持たない）。実行例（cmd.exe）:

    set POKECON_RUN_LOG_VIEW_E2E=1
    set POKECON_LOG_VIEW_EVIDENCE_DIR=%TEMP%\\pokecon-log-view-e2e\\run-1
    uv run --frozen pytest tests/test_log_view_e2e.py -q

証跡ディレクトリは絶対パス・リポジトリの外・未作成であること。
シナリオごとに `<dir>/<test名>` を作る（使い回しは失敗にする）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ENV = "POKECON_RUN_LOG_VIEW_E2E"
EVIDENCE_ENV = "POKECON_LOG_VIEW_EVIDENCE_DIR"
CHILD_ENV = "POKECON_LOG_VIEW_CHILD"


def _write(path: Path, payload: Any, *, jsonl: bool = False) -> None:
    text = (
        "\n".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False) for row in payload
        )
        if jsonl
        else json.dumps(payload, sort_keys=True, ensure_ascii=False)
    )
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _evidence_root() -> Path:
    raw = os.environ.get(EVIDENCE_ENV)
    if not raw or not Path(raw).is_absolute():
        raise AssertionError(f"{EVIDENCE_ENV} は絶対パスで指定してください")
    root = Path(raw).resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        raise AssertionError("証跡はリポジトリの外に置いてください")
    return root


# -- 子プロセス側 ------------------------------------------------------------


def _pump(host: Any, root: tk.Tk) -> None:
    from log_view_support import pump

    pump(host, root)


def _line_tags(text: tk.Text, needle: str) -> list[str]:
    idx = text.search(needle, "1.0", "end", elide=True)
    assert idx, f"{needle!r} が見つかりません"
    return list(text.tag_names(f"{idx} lineend -1c"))


def _visible(text: tk.Text, needle: str) -> bool:
    idx = text.search(needle, "1.0", "end", elide=True)
    return bool(idx) and text.bbox(idx) is not None


def _click_line(text: tk.Text, root: tk.Tk, needle: str) -> None:
    idx = text.search(needle, "1.0", "end", elide=True)
    assert idx, f"{needle!r} が見つかりません"
    text.see(idx)
    root.update()
    box = text.bbox(idx)
    assert box is not None
    x, y = box[0] + 2, box[1] + 2
    text.event_generate("<ButtonPress-1>", x=x, y=y)
    text.event_generate("<ButtonRelease-1>", x=x, y=y)
    root.update()


def _scenario_errors(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    # Given: 例外の traceback と普通の進捗が print で流れる
    for line in (
        "探索中 1",
        "Traceback (most recent call last):",
        '  File "x.py", line 3, in do',
        "ValueError: 色違いが見つかりません",
        "探索中 2",
    ):
        logpane.text_queue.put(line + "\n")
    # When: GUI のポンプが1周する
    _pump(host, root)
    stream, pins = host.logView.text, host.pinView.text
    obs.append(
        {"stream": stream.get("1.0", "end-1c"), "pins": pins.get("1.0", "end-1c")}
    )
    # Then: traceback の塊はエラー、進捗は通常の出力として区別される
    assert "lv_error" in _line_tags(stream, "Traceback")
    assert "lv_error" in _line_tags(stream, "ValueError")
    assert "lv_info" in _line_tags(stream, "探索中 2")
    # Then: 何が起きたかの1行だけが下の欄へ集まる
    pin_text = pins.get("1.0", "end-1c")
    assert "ValueError: 色違いが見つかりません" in pin_text
    assert "Traceback" not in pin_text
    # Then: エラーの件数がツールバーに出る
    assert "1" in str(host.log_problem_buttons["error"].cget("text"))


def _scenario_follow(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    stream = host.logView.text
    for i in range(300):
        logpane.text_queue.put(f"line-{i}\n")
    _pump(host, root)
    # Given: 末尾にいる間は追従する
    assert host.logView.at_bottom()
    # Given: 利用者が上へスクロールして読んでいる
    stream.yview_moveto(0.0)
    root.update()
    top_before = stream.index("@0,0")
    # When: 新しい行が来る
    for i in range(5):
        logpane.text_queue.put(f"fresh-{i}\n")
    _pump(host, root)
    obs.append({"top_before": top_before, "top_after": stream.index("@0,0")})
    # Then: 読んでいる位置は動かず、新着ボタンが件数つきで現れる
    assert stream.index("@0,0") == top_before
    assert host.logView.new_button.winfo_ismapped()
    assert "5" in str(host.logView.new_button.cget("text"))
    # When: 新着ボタンを押す
    host.logView.new_button.invoke()
    root.update()
    # Then: 末尾へ戻り、ボタンは消え、以後はまた追従する
    assert host.logView.at_bottom()
    assert not host.logView.new_button.winfo_ismapped()
    logpane.text_queue.put("after-resume\n")
    _pump(host, root)
    assert host.logView.at_bottom() and _visible(stream, "after-resume")


def _scenario_pin_jump(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    stream, pins = host.logView.text, host.pinView.text
    for i in range(100):
        logpane.text_queue.put(f"before-{i}\n")
    # Given: print2 の結果が時系列の途中に出る
    logpane.sub_log_queue.put("見つかった: 色違い 3体目\n")
    for i in range(200):
        logpane.text_queue.put(f"after-{i}\n")
    _pump(host, root)
    # Then: 結果は上（時系列）と下（残す欄）の両方にある
    assert "lv_result" in _line_tags(stream, "見つかった")
    assert "見つかった" in pins.get("1.0", "end-1c")
    assert not _visible(stream, "見つかった")
    # When: 下の欄の結果をクリックする
    _click_line(pins, root, "見つかった")
    obs.append({"stream_top": stream.index("@0,0")})
    # Then: 上の欄がその行の前後を見せ、目印が付く
    assert _visible(stream, "見つかった")
    assert "jump" in _line_tags(stream, "見つかった")


def _scenario_filter_search(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    stream = host.logView.text
    logpane.text_queue.put("needle in info\n")
    logpane.text_queue.put("WARNING: needle in warning\n")
    logpane.text_queue.put("haystack\n")
    _pump(host, root)
    # Given: 検索語が2行に当たる
    host.log_search_var.set("needle")
    found = host.log_find()
    obs.append({"found_all": list(found)})
    assert found[1] == 2
    # When: 通常の出力を絞り込みで隠す
    host.log_filter_vars["info"].set(False)
    host._apply_log_filters()
    root.update()
    # Then: 通常の行は消え、警告は残り、検索は見えている行だけを数える
    assert not _visible(stream, "haystack")
    assert _visible(stream, "needle in warning")
    found = host.log_find()
    obs.append({"found_filtered": list(found)})
    assert found[1] == 1
    # Then: 当たらない語は「見つかりません」と知らせる
    host.log_search_var.set("zzz-absent")
    assert host.log_find()[1] == 0
    assert "見つかりません" in host.log_find_status.get()


def _scenario_group(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    stream = host.logView.text
    # Given: 同じ行が続けて5回来る（ポンプをまたいでも）
    for _ in range(3):
        logpane.text_queue.put("待機中...\n")
    _pump(host, root)
    for _ in range(2):
        logpane.text_queue.put("待機中...\n")
    logpane.text_queue.put("next\n")
    _pump(host, root)
    body = stream.get("1.0", "end-1c")
    obs.append({"stream": body})
    # Then: 1行にまとめ、回数を添える
    assert body.count("待機中...") == 1
    assert "×5" in body
    assert "next" in body


def _scenario_pin_trimmed(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    pins = host.pinView.text
    # Given: エラーが下の欄に集まった後、上の欄が上限を超えて古い行を捨てる
    logpane.text_queue.put("ERROR: 古いエラー\n")
    _pump(host, root)
    for i in range(logpane.MAX_LINES + 10):
        logpane.text_queue.put(f"filler-{i}\n")
        if i % 400 == 0:
            _pump(host, root)
    while not logpane.queues_idle_hint():
        _pump(host, root)
    # When: 下の欄のエラーをクリックする
    _click_line(pins, root, "古いエラー")
    obs.append({"status": host.log_status_var.get()})
    # Then: 移動できないことを黙らずに知らせる
    assert "消えて" in host.log_status_var.get()


def _scenario_hidden_tab(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    # Given: 「ログ」タブを見ている間に、入力ログが1画面を超えて溜まる
    host.log_nb.select(0)
    root.update()
    for i in range(300):
        logpane.input_log_queue.put(f"press-{i}\n")
        if i % 50 == 0:
            _pump(host, root)
    _pump(host, root)
    # When: 「入力」タブを開く
    host.log_nb.select(1)
    root.update()
    view = host.inputView
    obs.append({"yview": list(view.text.yview()), "new": view.new_count})
    # Then: 最新の操作が見えている（古い位置や新着ボタンで待たされない）
    assert _visible(view.text, "press-299")
    assert not view.new_button.winfo_ismapped()


def _scenario_logger_errors(host: Any, root: tk.Tk, logpane: Any, obs: list) -> None:
    from loguru import logger

    sink = logger.add(
        logpane.logger_sink,
        level="ERROR",
        format="{message}",
        filter=lambda record: record["extra"].get("gui", True),
    )
    try:
        # Given: 実行基盤の失敗が logger.error だけで報告される（print なし）
        logger.error("コマンドの起動に失敗しました\n詳細の2行目")
        # Given: print と二重になる失敗は gui=False でファイルにだけ出す
        logger.bind(gui=False).error("ファイルにだけ残す失敗")
        logger.warning("ERROR 未満は画面に出さない")
        _pump(host, root)
    finally:
        logger.remove(sink)
    stream, pins = host.logView.text, host.pinView.text
    body = stream.get("1.0", "end-1c")
    obs.append({"stream": body, "pins": pins.get("1.0", "end-1c")})
    # Then: エラーとして上の欄に出て、1件として下の欄に集まる
    assert "lv_error" in _line_tags(stream, "コマンドの起動に失敗しました")
    assert "コマンドの起動に失敗しました" in pins.get("1.0", "end-1c")
    assert "詳細の2行目" not in pins.get("1.0", "end-1c")
    # Then: 外した物・ERROR 未満は画面に出ない
    assert "ファイルにだけ残す失敗" not in body
    assert "ERROR 未満" not in body


SCENARIOS: dict[str, Callable[[Any, tk.Tk, Any, list], None]] = {
    "test_hidden_input_tab_shows_latest_when_opened": _scenario_hidden_tab,
    "test_logger_errors_reach_the_screen_once": _scenario_logger_errors,
    "test_errors_are_marked_and_collected_below": _scenario_errors,
    "test_scrolled_up_reader_is_not_dragged_and_gets_new_button": _scenario_follow,
    "test_clicking_a_result_below_jumps_to_its_context_above": _scenario_pin_jump,
    "test_filter_hides_levels_and_search_counts_only_visible": _scenario_filter_search,
    "test_repeated_lines_collapse_with_a_count": _scenario_group,
    "test_clicking_a_trimmed_entry_says_it_is_gone": _scenario_pin_trimmed,
}


def _child_main(test_id: str) -> int:
    evidence = _evidence_root() / test_id
    rows: list[dict[str, Any]] = []
    status, error = "failed", ""
    root: tk.Tk | None = None
    host: Any = None
    try:
        from log_view_support import make_host, reset_queues

        LogPane = reset_queues()
        root = tk.Tk()
        root.geometry("640x480+0+0")
        host = make_host(root)
        SCENARIOS[test_id](host, root, LogPane, rows)
        status = "passed"
    except BaseException as exc:  # noqa: BLE001 - 子の証跡の境界
        import traceback

        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        print(error, file=sys.stderr)
    finally:
        destroyed = False
        if host is not None:
            host._closing = True
        if root is not None:
            try:
                root.destroy()
                destroyed = True
            except tk.TclError:
                pass
        _write(evidence / "observations.jsonl", rows, jsonl=True)
        _write(
            evidence / "report.json",
            {"test_id": test_id, "status": status, "error": error},
        )
        _write(evidence / "teardown.json", {"root_destroyed": destroyed})
    return 0 if status == "passed" else 1


# -- 親（pytest）側 ----------------------------------------------------------


def _run(test_id: str) -> None:
    if os.environ.get(RUN_ENV) != "1":
        pytest.skip(f"{RUN_ENV}=1 で実 Tk のログ欄 E2E を走らせる")
    evidence = _evidence_root() / test_id
    if evidence.exists():
        raise AssertionError(f"証跡ディレクトリが既にあります: {evidence}")
    evidence.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env[CHILD_ENV] = test_id
    with (evidence / "child.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60.0,
        )
    report_path = evidence / "report.json"
    assert report_path.exists(), "子プロセスが report.json を残しませんでした"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if result.returncode != 0 or report.get("status") != "passed":
        raise AssertionError(str(report.get("error", "child failed")))


def test_hidden_input_tab_shows_latest_when_opened() -> None:
    _run("test_hidden_input_tab_shows_latest_when_opened")


def test_logger_errors_reach_the_screen_once() -> None:
    _run("test_logger_errors_reach_the_screen_once")


def test_errors_are_marked_and_collected_below() -> None:
    _run("test_errors_are_marked_and_collected_below")


def test_scrolled_up_reader_is_not_dragged_and_gets_new_button() -> None:
    _run("test_scrolled_up_reader_is_not_dragged_and_gets_new_button")


def test_clicking_a_result_below_jumps_to_its_context_above() -> None:
    _run("test_clicking_a_result_below_jumps_to_its_context_above")


def test_filter_hides_levels_and_search_counts_only_visible() -> None:
    _run("test_filter_hides_levels_and_search_counts_only_visible")


def test_repeated_lines_collapse_with_a_count() -> None:
    _run("test_repeated_lines_collapse_with_a_count")


def test_clicking_a_trimmed_entry_says_it_is_gone() -> None:
    _run("test_clicking_a_trimmed_entry_says_it_is_gone")


if __name__ == "__main__":
    raise SystemExit(_child_main(os.environ[CHILD_ENV]))
