#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""per-stem保存ロック（save/delete対の直列化・有界）の検証。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from services import blockly_save


def _code(tag: int) -> str:
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "ブロック作成"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
        f"# tag={tag}\n"
    )


def _ws(tag: int) -> str:
    return json.dumps({"blocks": {"languageVersion": 0, "blocks": [{"tag": tag}]}})


def _app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    return app


def test_same_stem_saves_serialized(tmp_path: Path, monkeypatch) -> None:
    """同一stemの同時保存は重ならない（対の一貫性＝torn .py+.jsonなし）。"""
    app = _app(tmp_path)
    active = {"cur": 0, "max": 0}
    guard = threading.Lock()
    orig = blockly_save._atomic_write

    def tracked(target: Path, text: str) -> None:
        with guard:
            active["cur"] += 1
            active["max"] = max(active["max"], active["cur"])
        try:
            time.sleep(0.02)
            orig(target, text)
        finally:
            with guard:
                active["cur"] -= 1

    monkeypatch.setattr(blockly_save, "_atomic_write", tracked)

    def worker(tag: int) -> None:
        res = blockly_save.save_blockly(app, "SameStem", _ws(tag), _code(tag))
        assert res.status == "saved"

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    # 直列化されていれば同時実行の最大は1。ロック無しだと2以上になる。
    assert active["max"] == 1, f"同一stemで並走しています: max={active['max']}"


def test_same_stem_pair_consistent_and_reloadable(tmp_path: Path) -> None:
    """同時保存後も対は一致し、再読込できる。"""
    import threading as _th

    app = _app(tmp_path)

    def worker(tag: int) -> None:
        blockly_save.save_blockly(app, "PairStem", _ws(tag), _code(tag))

    threads = [_th.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    py = (app / "Commands" / "PythonCommands" / "PairStem.py").read_text(
        encoding="utf-8"
    )
    js = (app / "Commands" / "PythonCommands" / "PairStem.blockly.json").read_text(
        encoding="utf-8"
    )
    # タグを抜き出して一致を見る（混ざれば不一致になる）。
    import re

    m_py = re.search(r"# tag=(\d+)", py)
    js_tag = json.loads(js)["blocks"]["blocks"][0]["tag"]
    assert m_py is not None
    assert int(m_py.group(1)) == int(js_tag), (
        f"対が混ざっています: py={m_py.group(1)} json={js_tag}"
    )
    # 再読込できる。
    loaded = blockly_save.load_blockly(app, "PairStem")
    assert loaded.status == "ok"
    assert loaded.workspace_json == js


def test_different_stems_not_blocked_and_locks_bounded(tmp_path: Path) -> None:
    """別stemは並走できる／ロック表は有界（deleteで片付け）。"""
    # ロック表の存在自体が仕様（無ければRED）。
    locks = getattr(blockly_save, "_save_locks", None)
    assert isinstance(locks, dict), "_save_locks（stem→Lock表）がありません"
    app = _app(tmp_path)
    for i in range(5):
        assert (
            blockly_save.save_blockly(app, f"Stem{i}", _ws(i), _code(i)).status
            == "saved"
        )
    # 5件ぶん以下の有界であること（際限なく増やさない）。
    assert len(locks) <= 512
    # deleteは同じ錠を使う＋片付けで有界を保つ。
    assert blockly_save.delete_blockly(app, "Stem0").status == "deleted"
    assert len(locks) <= 512
