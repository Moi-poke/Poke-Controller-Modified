"""配布文書のリンク・必須作例・指示書 import を実 CLI で検査する。"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "tools" / "check_docs.py"


@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    config = runpy.run_path(str(CHECKER))
    for name in config["REQUIRED"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (tmp_path / "taskfile.yml").write_text(
        "version: '3'\ntasks:\n  docs:\n    cmds: []\n", encoding="utf-8"
    )
    (tmp_path / "opencode.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "[導入](docs/AI_AUTOMATION.md)\n", encoding="utf-8"
    )
    (tmp_path / "docs/blockly-agent/references/vision.md").write_text(
        "[手順](../README.md)\n[CLI](cli.md)\n", encoding="utf-8"
    )
    return tmp_path


def _check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        check=False,
    )


def test_relative_links_and_instruction_import(doc_tree: Path) -> None:
    result = _check(doc_tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_nested_vision_link_missing(doc_tree: Path) -> None:
    (doc_tree / "docs/blockly-agent/references/vision.md").write_text(
        "[画像の準備](../missing-image-guide.md)\n", encoding="utf-8"
    )
    result = _check(doc_tree)
    assert result.returncode == 1
    assert "docs/blockly-agent/references/vision.md" in result.stdout
    assert "../missing-image-guide.md" in result.stdout


def test_required_example_missing(doc_tree: Path) -> None:
    target = "docs/blockly-agent/examples/vision.json"
    (doc_tree / target).unlink()
    result = _check(doc_tree)
    assert result.returncode == 1
    assert target in result.stdout


def test_instruction_import_missing(doc_tree: Path) -> None:
    (doc_tree / "CLAUDE.md").write_text("@missing.md\n", encoding="utf-8")
    result = _check(doc_tree)
    assert result.returncode == 1
    assert "CLAUDE.md" in result.stdout
    assert "missing.md" in result.stdout


def test_fenced_import_is_not_an_instruction(doc_tree: Path) -> None:
    (doc_tree / "CLAUDE.md").write_text(
        "@AGENTS.md\n\n```text\n@missing.md\n```\n", encoding="utf-8"
    )
    result = _check(doc_tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_directory_is_not_a_file(doc_tree: Path) -> None:
    (doc_tree / "CLAUDE.md").write_text("@docs\n", encoding="utf-8")
    result = _check(doc_tree)
    assert result.returncode == 1
    assert "CLAUDE.md" in result.stdout
    assert "docs" in result.stdout
