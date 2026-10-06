"""Opt-in real-Tk E2E coverage for authoritative log-follow behavior."""

import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "SerialController"))
RUN_ENV = "POKECON_RUN_LOG_FOLLOW_E2E"
TEST_ID_ENV = "POKECON_LOG_FOLLOW_TEST_ID"
EVIDENCE_ENV = "POKECON_LOG_FOLLOW_EVIDENCE_DIR"


SCENARIOS = (
    "test_follow_on_scrolls_to_end_after_queued_append",
    "test_follow_transition_preserves_then_resumes_bottom",
    "test_follow_on_adjacent_log_controls_keep_existing_behavior",
)


def _write(path: Path, payload, *, jsonl: bool = False) -> None:
    text = (
        "\n".join(json.dumps(row, sort_keys=True) for row in payload)
        if jsonl
        else json.dumps(payload, sort_keys=True)
    )
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _evidence_dir(child: bool) -> Path:
    raw = os.environ.get(EVIDENCE_ENV)
    assert raw and Path(raw).is_absolute(), f"{EVIDENCE_ENV} must be absolute"
    directory = Path(raw).resolve()
    assert directory != REPO_ROOT and REPO_ROOT not in directory.parents
    if directory.exists():
        allowed = {"environment.json"} if child else set()
        assert directory.is_dir() and not (
            {path.name for path in directory.iterdir()} - allowed
        )
    else:
        assert not child, "child evidence directory was not created by parent"
    return directory


def _areas(host):
    return host.logArea, host.subLogArea, host.inputLogArea


def _state(step, host):
    return {
        "step": step,
        "areas": [(list(area.yview()), area.index("@0,0")) for area in _areas(host)],
    }


def _to_bottom(host, root):
    for view in (host.logView, host.pinView, host.inputView):
        view.scroll_to_end()
    root.update_idletasks()


def _reset_to_top(host, root):
    # 利用者が上へ読みに行く操作を、各欄が見えている状態で行う
    # （入力欄は別タブ。見えていない欄は人がスクロールできない）。
    for index, area in enumerate(_areas(host)):
        host.log_nb.select(1 if index == 2 else 0)
        root.update()
        area.see("1.0")
        root.update()
    host.log_nb.select(0)
    root.update()


def _append(host, root, logpane, step, sentinels):
    for queue, sentinel in zip(
        (logpane.text_queue, logpane.sub_log_queue, logpane.input_log_queue), sentinels
    ):
        queue.put(f"{sentinel}\n")
    host.display_text()
    root.update()
    after_id = host._display_after_id
    if after_id is not None:
        host.logArea.after_cancel(after_id)
        host.cancelled_after_ids.append(str(after_id))
        host._display_after_id = None
    return _state(step, host)


def _teardown(root, host):
    if host is not None:
        host._closing = True
        after_id = host._display_after_id
        if after_id is not None:
            try:
                host.logArea.after_cancel(after_id)
                host.cancelled_after_ids.append(str(after_id))
            except (tk.TclError, RuntimeError):
                pass
            host._display_after_id = None
    destroyed = False
    if root is not None:
        try:
            root.destroy()
            destroyed = True
        except (tk.TclError, RuntimeError):
            pass
    return {
        "root_created": root is not None,
        "root_destroyed": destroyed,
        "callbacks_cancelled": len(host.cancelled_after_ids) if host else 0,
    }


def _assert_end(areas, sentinels, label):
    # 入力欄は別タブにある。見えていない Text の yview は測れないので、
    # 利用者と同じくタブを開いてから確かめる。
    for index, (area, sentinel) in enumerate(zip(areas, sentinels)):
        notebook = area.master
        while notebook is not None and notebook.winfo_class() != "TNotebook":
            notebook = notebook.master
        assert notebook is not None, "ログ欄のノートが見つかりません"
        notebook.select(1 if index == 2 else 0)
        area.update()
        assert sentinel in area.get("1.0", "end-1c")
        assert area.yview()[1] >= 0.999, f"{label} yview={area.yview()[1]:.6f}"


def _assert_preserved(before, after):
    for old, new in zip(before["areas"], after["areas"]):
        assert old[1] == new[1], f"top index changed: {old[1]} -> {new[1]}"
        assert abs(old[0][0] - new[0][0]) <= 0.002
        assert new[0][1] < 0.999, f"follow-off moved to bottom: {new[0][1]}"


def _run_scenario(scenario, host, root, logpane, rows):
    areas = _areas(host)
    match scenario:
        case 0:
            sentinels = ("primary-sentinel", "sub-sentinel", "input-sentinel")
            # 末尾で読んでいる間は追従する（上へ読みに行った場合は case 1）。
            _to_bottom(host, root)
            rows.append(_state("at-bottom", host))
            rows.append(_append(host, root, logpane, "after-queued-append", sentinels))
            _assert_end(areas, sentinels, "follow-on")
        case 1:
            # 追従の切り替えは人が操作する物ではなくなった（2026-10）。
            # 上へ読みに行けば止まり、新着ボタン（scroll_to_end）で戻る。
            off = ("off-primary", "off-sub", "off-input")
            resume = ("resume-primary", "resume-sub", "resume-input")
            _reset_to_top(host, root)
            before = _state("before-follow-off", host)
            rows.append(before)
            after = _append(host, root, logpane, "after-follow-off", off)
            rows.append(after)
            _assert_preserved(before, after)
            for view in (host.logView, host.pinView, host.inputView):
                view.scroll_to_end()
            root.update_idletasks()
            rows.append(_append(host, root, logpane, "after-follow-on", resume))
            _assert_end(areas, resume, "resume-to-bottom")
        case 2:
            _reset_to_top(host, root)
            logpane.clearAreas(list(areas))
            assert all(area.get("1.0", "end-1c") == "" for area in areas)
            host.logArea.configure(state="normal")
            host.logArea.insert(
                "end", "".join(f"trim-{i}\n" for i in range(logpane.MAX_LINES + 5))
            )
            logpane.trim(host.logArea)
            host.logArea.configure(state="disabled")
            assert int(host.logArea.index("end-1c").split(".")[0]) == logpane.MAX_LINES
            _to_bottom(host, root)
            sentinels = ("adjacent-primary", "adjacent-sub", "adjacent-input")
            rows.append(_append(host, root, logpane, "clear-append-trim", sentinels))
            _assert_end(areas, sentinels, "adjacent-controls")
        case _:
            raise AssertionError(f"unknown scenario: {scenario}")


def _child_main() -> int:
    test_id = os.environ.get(TEST_ID_ENV, "")
    scenario = SCENARIOS.index(test_id) if test_id in SCENARIOS else -1
    if scenario < 0:
        raise AssertionError(f"unknown child test id: {test_id}")
    evidence_dir = _evidence_dir(True)
    log_path = evidence_dir.parent / f"{evidence_dir.name}.{test_id}.child.log"
    rows: list[dict[str, Any]] = []
    root = None
    host = None
    status, error = "failed", ""
    try:
        from log_view_support import make_host, pump, reset_queues

        LogPane = reset_queues()
        root = tk.Tk()
        root.geometry("700x500+0+0")
        host = make_host(root)
        host.cancelled_after_ids = []
        # 各欄を1画面より長くしておく（上へ読みに行ける状態にする）。
        for i in range(100):
            LogPane.text_queue.put(f"text-{i}\n")
            LogPane.sub_log_queue.put(f"sub-{i}\n")
            LogPane.input_log_queue.put(f"input-{i}\n")
        pump(host, root)
        _run_scenario(scenario, host, root, LogPane, rows)
        status = "passed"
    except BaseException as exc:  # noqa: BLE001 - child evidence boundary
        error = str(exc)
        print(error, file=sys.stderr)
    finally:
        teardown = _teardown(root, host)
        _write(evidence_dir / "observations.jsonl", rows, jsonl=True)
        _write(
            evidence_dir / "report.json",
            {
                "test_id": test_id,
                "scenario": scenario,
                "status": status,
                "error": error,
                "child_log": str(log_path),
            },
        )
        _write(evidence_dir / "teardown.json", teardown)
    return 0 if status == "passed" else 1


def _spawn_child(evidence_dir, test_id):
    log_path = evidence_dir.parent / f"{evidence_dir.name}.{test_id}.child.log"
    if log_path.exists():
        raise AssertionError(f"child log must be fresh: {log_path}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, str(Path(__file__).resolve())]
    try:
        with log_path.open("w", encoding="utf-8") as child_log:
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=child_log,
                stderr=subprocess.STDOUT,
                timeout=10.0,
            )
    except subprocess.TimeoutExpired:
        return None, "child timeout"
    except OSError as exc:
        return None, f"child launch failed: {exc}"
    return result.returncode, None


def _run_gated_test(test_id):
    if os.environ.get(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run real-Tk log-follow E2E")
    scenario = test_id
    if os.environ.get(TEST_ID_ENV) != test_id:
        raise AssertionError(f"{TEST_ID_ENV} must equal {test_id}")
    evidence_dir = _evidence_dir(False)
    log_path = evidence_dir.parent / f"{evidence_dir.name}.{test_id}.child.log"
    if log_path.exists():
        raise AssertionError(f"child log must be fresh: {log_path}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _write(
        evidence_dir / "environment.json",
        {
            "test_id": test_id,
            "scenario": scenario,
            "run_gate": os.environ.get(RUN_ENV),
        },
    )
    returncode, launch_error = _spawn_child(evidence_dir, test_id)
    if launch_error is not None:
        raise AssertionError(launch_error)
    report_path = evidence_dir / "report.json"
    assert report_path.exists(), "child produced no report"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if returncode != 0 or report.get("status") != "passed":
        raise AssertionError(str(report.get("error", "child failed")))


def test_follow_on_scrolls_to_end_after_queued_append() -> None:
    _run_gated_test("test_follow_on_scrolls_to_end_after_queued_append")


def test_follow_transition_preserves_then_resumes_bottom() -> None:
    _run_gated_test("test_follow_transition_preserves_then_resumes_bottom")


def test_follow_on_adjacent_log_controls_keep_existing_behavior() -> None:
    _run_gated_test("test_follow_on_adjacent_log_controls_keep_existing_behavior")


if __name__ == "__main__":
    raise SystemExit(_child_main())
