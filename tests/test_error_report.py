"""error_report の組み立て検証（GUIなしで回せる部分）。

報告文に秘密を混ぜない・末尾だけに絞る・ログが無くても落ちない、
の3点を押さえる。 dialog 自体は手動確認扱いにする。
"""

from __future__ import annotations

import os

from core import error_report


def test_resolve_log_dir_is_absolute_and_cwd_independent() -> None:
    """log 置き場は APP_DIR 基準の絶対パス。cwd を見ない。"""
    from core import error_report as er

    app_dir = os.path.normpath(os.path.join("dummy", "SerialController"))
    got = er.resolve_log_dir(app_dir)
    assert os.path.isabs(got)
    want = os.path.normpath(os.path.abspath(os.path.join("dummy", "log")))
    assert got == want


def test_latest_log_file_picks_newest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """log_*.log のうち更新時刻が新しい物を選ぶ。無ければ None。"""
    import time

    from core import error_report as er

    old = tmp_path / "log_20200101-000000.log"
    new = tmp_path / "log_20200102-000000.log"
    old.write_text("old", encoding="utf-8")
    # 時刻差が確実に出るよう少しずらす
    time.sleep(0.02)
    new.write_text("new", encoding="utf-8")

    assert er.latest_log_file(str(tmp_path)) == str(new)
    assert er.latest_log_file(str(tmp_path / "missing")) is None


def test_read_tail_lines_caps_lines_and_bytes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """末尾 N 行・最大バイトで打ち切る。無い物は空で返す。"""
    from core import error_report as er

    path = tmp_path / "a.log"
    path.write_text("\n".join(f"line{i}" for i in range(10)) + "\n", encoding="utf-8")

    tail = er.read_tail_lines(str(path), max_lines=3, max_bytes=1024)
    assert tail == ["line7", "line8", "line9"]
    assert er.read_tail_lines(str(tmp_path / "missing.log")) == []


def test_build_report_contains_env_and_tail_without_secrets() -> None:
    """報告文は環境＋例外＋ログ末尾を持ち、設定の中身は混ぜない。"""
    from core import error_report as er

    text = er.build_report(
        app_version="v4.0.1 Modified",
        os_name="Windows",
        python_version="3.12.10",
        profile="switch1",
        transport="pico_uart",
        error_text="ValueError: boom",
        log_path="C:\\x\\log_20200102-000000.log",
        log_tail=["line8", "line9"],
    )
    assert "v4.0.1 Modified" in text
    assert "Windows" in text
    assert "3.12.10" in text
    assert "switch1" in text
    assert "pico_uart" in text
    assert "ValueError: boom" in text
    assert "line9" in text
    # webhook のような秘密を引数で渡さない設計の確認。
    # build_report 自体が settings.ini を読まないことの裏付けとして、
    # シグネチャに settings / webhook が無いことを見る。
    import inspect

    params = set(inspect.signature(er.build_report).parameters)
    assert "settings" not in params
    assert "webhook" not in params


def _stats(i: int) -> str:
    return (
        f"2026-10-04 02:26:{i:02d},000 ui.log_panel _log_video_stats [DEBUG]: "
        f"映像: 取込 {i}fps / 表示 {i}fps"
    )


def test_condense_keeps_every_normal_line_and_only_the_latest_video_stats() -> None:
    # Given: 5 秒ごとの映像統計が通常の行の間に大量に挟まったログ（実ログでは 6 割超）。
    lines: list[str] = []
    for i in range(300):
        lines.append(_stats(i))
        if i % 3 == 0:
            lines.append(f"通常の行 {i}")

    # When
    out = error_report.condense_tail(lines, max_lines=200)

    # Then: 通常の行は全部残る（統計に押し出されない）。統計は最新の 1 行だけ
    #       残し、省いた件数を書く（カメラが映らない不具合は最新行で分かる）。
    normal = [line for line in lines if line.startswith("通常の行")]
    assert [line for line in out if line.startswith("通常の行")] == normal
    kept_stats = [line for line in out if "_log_video_stats" in line]
    assert kept_stats == [_stats(299)]
    assert any("映像の統計 299 行を省略" in line for line in out)


def test_condense_leaves_a_log_without_stats_alone_except_the_line_cap() -> None:
    lines = [f"行 {i}" for i in range(250)]

    out = error_report.condense_tail(lines, max_lines=200)

    # Then: 統計が無ければ何も足さず、末尾 200 行に切るだけ。
    assert out == lines[-200:]
