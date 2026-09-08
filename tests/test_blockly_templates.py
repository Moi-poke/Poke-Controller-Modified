"""blockly_templates（一覧＋参照検査）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

from pathlib import Path

from services import blockly_templates


def make_app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    (app / "Template" / "my-pack").mkdir(parents=True)
    return app


def write_images(app: Path) -> None:
    (app / "Template" / "root.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "Template" / "my-pack" / "b.JPG").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "Template" / "my-pack" / "skip.txt").write_text("not image")
    (app / "Template" / "my-pack" / "c.gif").write_bytes(b"GIF89a")


def vision_code(tpl: str = "my-pack/a.png") -> str:
    return (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        f'        self.waitTemplate("{tpl}", timeout=10.0, threshold=0.7)\n'
    )


def test_list_returns_sorted_image_rels(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    write_images(app)
    assert blockly_templates.list_image_templates(app) == [
        "my-pack/a.png",
        "my-pack/b.JPG",
        "root.png",
    ]


def test_list_missing_dir_returns_empty(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    assert blockly_templates.list_image_templates(app) == []


def test_good_ref_passes() -> None:
    assert blockly_templates.validate_template_refs(vision_code()) == []
    assert blockly_templates.warn_template_refs(vision_code()) == []


def test_empty_ref_rejected() -> None:
    errors = blockly_templates.validate_template_refs(vision_code(""))
    assert errors != []


def test_dotdot_ref_rejected() -> None:
    errors = blockly_templates.validate_template_refs(vision_code("../evil.png"))
    assert any(".." in e for e in errors)


def test_absolute_ref_rejected() -> None:
    assert blockly_templates.validate_template_refs(vision_code("/abs.png")) != []
    assert blockly_templates.validate_template_refs(vision_code("C:/x.png")) != []


def test_bare_name_warns_but_passes() -> None:
    assert blockly_templates.validate_template_refs(vision_code("a.png")) == []
    warnings = blockly_templates.warn_template_refs(vision_code("a.png"))
    assert len(warnings) == 1
    assert "配布" in warnings[0]


def test_broken_code_is_safe() -> None:
    assert blockly_templates.validate_template_refs("def broken(:\n") == []
    assert blockly_templates.warn_template_refs("def broken(:\n") == []
