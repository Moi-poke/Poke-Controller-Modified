"""エージェント仕様から作った保存用コードを、本体の実行器で走らせる検証。

`blockly_author.check_spec` は本物の Blockly で保存用 `.py` を作るが、
それが実際に `PythonCommand` として動くかは別の関門。ここでは偽の線
（FakeTransport）越しに `blockly_run.build_trial_class` で走らせ、
入力が線へ出て、最後まで完了することを確かめる。実機は使わない。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from blockly_node import NEEDS_NODE
from core import Sender
from fakes import FakeTransport
from services import blockly_author, blockly_run

pytestmark = NEEDS_NODE

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def _app(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    shutil.copytree(BLOCKLY, app / "assets" / "blockly")
    return app


def _spec() -> dict[str, Any]:
    return {
        "stem": "ExecAgent",
        "name": "実行確認",
        "tags": ["blockly", "test"],
        "do": [
            {"press": "A", "duration": 0.02, "wait": 0},
            {"repeat": 2, "do": [{"press": "B", "duration": 0.02, "wait": 0}]},
            {"hold": "X", "wait": 0.02},
            {"release": "X"},
            {"print": "done"},
        ],
    }


def test_generated_spec_runs_through_the_command_engine(tmp_path: Path) -> None:
    """仕様→保存用コード→本体の実行器、の順に通り、入力が線へ出ること。"""
    checked = blockly_author.check_spec(_spec(), _app(tmp_path))
    assert checked.ok, checked.errors
    assert checked.code

    session = blockly_run.TrialSession(run_id=1)
    cls, errors = blockly_run.build_trial_class(checked.code, session)
    assert errors == []
    assert cls is not None

    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    cmd = cls()
    done = threading.Event()
    assert cmd.start(sender, done.set) is True
    assert done.wait(10), "生成コードの実行が終わらない"

    # A と B（2回）が線へ出ている。行はボタンのビットマスクを含む。
    rows = transport.rows
    assert "0x0010 8" in rows  # A
    assert rows.count("0x0008 8") >= 2  # B ×2
    assert len(rows) >= 8
    assert session.result == "完了"
    assert any("done" in text for _, text in session.logs_since(0))
