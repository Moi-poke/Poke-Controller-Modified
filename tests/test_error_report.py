"""error_report の組み立て検証（GUIなしで回せる部分）。

報告文に秘密を混ぜない・末尾だけに絞る・ログが無くても落ちない、
の3点を押さえる。 dialog 自体は手動確認扱いにする。
"""

from __future__ import annotations

import os


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
