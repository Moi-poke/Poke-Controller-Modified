"""macro_save（録画マクロの保存）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

from pathlib import Path

import pytest
from services import macro_save


def good_code() -> str:
    """録画の描画行を do() に貼った想定の保存コード。日本語NAME付き。"""
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class MacroCmd(PythonCommand):\n"
        '    NAME = "録画マクロ"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A, duration=0.12)\n"
        "        self.wait(0.42)\n"
        "        self.press(Button.B, duration=0.08)\n"
    )


def make_app(base: Path) -> Path:
    """保存先の偽ツリーを作る。実物のCommands配下には触らない。"""
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    return app


def py_dir(app: Path) -> Path:
    """保存先のディレクトリ。空かどうかの判定に使う。"""
    return app / "Commands" / "PythonCommands"


def test_macro_save_roundtrip(tmp_path: Path) -> None:
    """保存→読み戻しで中身が保たれる。末尾改行と日本語注釈も残る。"""
    app = make_app(tmp_path)
    res = macro_save.save_macro(app, "MyMacro", good_code())
    assert res.status == "saved"
    assert res.py_rel == "Commands/PythonCommands/MyMacro.py"
    text = (app / res.py_rel).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "録画マクロ" in text
    # 一時ファイルの残骸を残さない（tmp+os.replaceの約束）。
    assert list(py_dir(app).glob(".tmp_*")) == []


def test_macro_save_trailing_newline(tmp_path: Path) -> None:
    """末尾改行なしで渡しても保存物は改行で終わる。"""
    app = make_app(tmp_path)
    res = macro_save.save_macro(app, "MyMacro", good_code().rstrip("\n"))
    assert res.status == "saved"
    text = (app / res.py_rel).read_text(encoding="utf-8")
    assert text.endswith("\n")


def test_macro_save_bad_stem_fails_without_files(tmp_path: Path) -> None:
    """不正な保存名では何も書かずfailedで返す。"""
    app = make_app(tmp_path)
    res = macro_save.save_macro(app, "my-pack", good_code())
    assert res.status == "failed"
    assert list(py_dir(app).iterdir()) == []


@pytest.mark.parametrize("stem", ["", "   ", "../evil", "a/b", "a.b", "my-pack"])
def test_macro_save_stem_validation(tmp_path: Path, stem: str) -> None:
    """区切り・空白・不正文字の保存名はどれも書かずに落とす。"""
    app = make_app(tmp_path)
    res = macro_save.save_macro(app, stem, good_code())
    assert res.status == "failed"
    assert list(py_dir(app).iterdir()) == []


def test_macro_save_bad_code_fails_without_files(tmp_path: Path) -> None:
    """壊れたコードでは何も書かずfailedで返す。"""
    app = make_app(tmp_path)
    res = macro_save.save_macro(app, "MyMacro", "from . import foo\n")
    assert res.status == "failed"
    assert list(py_dir(app).iterdir()) == []


def test_macro_save_rollback_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置き換え失敗で途中物を残さない（tornなし・残骸なし）。"""
    import os as _os

    app = make_app(tmp_path)
    orig_replace = _os.replace

    def faulty_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError("故障注入")

    monkeypatch.setattr(_os, "replace", faulty_replace)
    try:
        res = macro_save.save_macro(app, "MyMacro", good_code())
    finally:
        monkeypatch.setattr(_os, "replace", orig_replace)
    assert res.status == "failed"
    assert not (py_dir(app) / "MyMacro.py").exists()
    assert list(py_dir(app).glob(".tmp_*")) == []
