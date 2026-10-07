"""`.py` 手編集検出の検証（更新時刻ではなく中身で判定する）。

更新時刻は git の取り出し・複写・整形で前後が入れ替わるため、
保存時に記録した `.py` の要約値と今の中身を比べる。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from services import blockly_save


def code() -> str:
    """保存検査を通る最小の生成コード。"""
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "手編集検出"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
    )


def workspace() -> str:
    """プログラム1個のワークスペースJSON。"""
    return json.dumps(
        {
            "blocks": {
                "languageVersion": 0,
                "blocks": [{"type": "pokecon_program", "fields": {"NAME": "x"}}],
            }
        }
    )


def saved_app(tmp_path: Path) -> Path:
    """保存済みの対を1組持つアプリ置き場を作る。"""
    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    res = blockly_save.save_blockly(app, "Edit", workspace(), code())
    assert res.status == "saved"
    return app


def pair(app: Path) -> tuple[Path, Path]:
    """`.py` と `.blockly.json` の場所。"""
    base = app / "Commands" / "PythonCommands"
    return base / "Edit.py", base / "Edit.blockly.json"


def test_untouched_pair_is_not_flagged_even_when_py_mtime_is_newer(
    tmp_path: Path,
) -> None:
    """中身が同じなら、`.py` の更新時刻が新しくても手編集扱いにしないこと。"""
    # Given: 保存直後の対
    app = saved_app(tmp_path)
    py_path, json_path = pair(app)
    # When: `.py` だけ更新時刻を未来へずらす（git取り出し・複写相当）
    st = json_path.stat()
    os.utime(py_path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    res = blockly_save.load_blockly(app, "Edit")
    # Then: 手編集とはみなさない
    assert res.status == "ok"
    assert res.external_edit is False


def test_pair_whose_py_content_changed_is_flagged(tmp_path: Path) -> None:
    """`.py` の中身が保存時と違えば手編集の可能性として知らせること。"""
    # Given: 保存直後の対
    app = saved_app(tmp_path)
    py_path, json_path = pair(app)
    # When: `.py` に1行足す（更新時刻は `.json` より古く戻す）
    py_path.write_text(code() + "        self.wait(1)\n", encoding="utf-8")
    st = json_path.stat()
    os.utime(py_path, ns=(st.st_atime_ns, st.st_mtime_ns - 5_000_000_000))
    res = blockly_save.load_blockly(app, "Edit")
    # Then: 更新時刻に関係なく手編集として知らせる
    assert res.status == "ok"
    assert res.external_edit is True


def test_legacy_pair_without_record_is_not_flagged(tmp_path: Path) -> None:
    """記録の無い旧保存物（同梱サンプル等）は判定不能として黙ること。"""
    # Given: 記録なしの `.blockly.json` と、生成直後とは違う `.py`
    app = tmp_path / "app"
    base = app / "Commands" / "PythonCommands"
    base.mkdir(parents=True)
    (base / "Edit.blockly.json").write_text(workspace(), encoding="utf-8")
    (base / "Edit.py").write_text(code().replace('"', "'"), encoding="utf-8")
    # When: 開く
    res = blockly_save.load_blockly(app, "Edit")
    # Then: 警告しない
    assert res.status == "ok"
    assert res.external_edit is False


def test_saved_json_keeps_blocks_and_records_the_py_digest(tmp_path: Path) -> None:
    """保存物はブロックをそのまま保ち、`.py` の要約値を別欄に持つこと。"""
    # Given/When: 保存する
    app = saved_app(tmp_path)
    _, json_path = pair(app)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # Then: blocks は元のまま、記録欄に64桁の要約値
    assert data["blocks"] == json.loads(workspace())["blocks"]
    digest = data["pokecon"]["pySha256"]
    assert isinstance(digest, str) and len(digest) == 64


def test_load_hands_back_workspace_without_the_record(tmp_path: Path) -> None:
    """開いたときに返すJSONは記録欄を含まない（Blocklyへそのまま渡せる）。"""
    # Given: 保存済みの対
    app = saved_app(tmp_path)
    # When: 開く
    res = blockly_save.load_blockly(app, "Edit")
    # Then: 記録欄は取り除かれている
    assert "pokecon" not in json.loads(res.workspace_json)
