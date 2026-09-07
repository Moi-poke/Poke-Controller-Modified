"""Blockly vendored資産の存在検証。"""

from __future__ import annotations

from pathlib import Path

from core import blockly_validate

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def test_version_pinned() -> None:
    assert (BLOCKLY / "VERSION").read_text(encoding="utf-8").strip() == "13.2.1"


def test_vendored_files_exist() -> None:
    for rel in (
        "blockly_compressed.js",
        "blocks_compressed.js",
        "python_compressed.js",
        "msg/ja.js",
    ):
        path = BLOCKLY / rel
        assert path.is_file(), f"missing: {rel}"
        assert path.stat().st_size > 10 * 1024, f"too small: {rel}"


def test_media_has_sound_and_icons() -> None:
    media = BLOCKLY / "media"
    assert media.is_dir()
    names = {p.name for p in media.iterdir()}
    assert "click.mp3" in names
    assert "delete-icon.svg" in names


def test_editor_and_generator_assets_exist() -> None:
    for rel in ("editor.html", "pokecon_blocks.js"):
        path = BLOCKLY / rel
        assert path.is_file(), f"missing: {rel}"
        assert path.stat().st_size > 1024, f"too small: {rel}"


def test_generator_output_shape_validates() -> None:
    code = (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "MyBlock"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A, duration=0.1, wait=0.1)\n"
        "        self.wait(0.5)\n"
    )
    assert '    NAME = "MyBlock"' in code
    body = code.split("    def do(self) -> None:\n", 1)[1]
    assert body
    for line in body.splitlines():
        assert line.startswith("        "), f"bad indent: {line!r}"
    assert code.endswith("\n")
    assert blockly_validate.validate_generated_code(code) == []
    assert blockly_validate.validate_stem("MyBlock") == []
