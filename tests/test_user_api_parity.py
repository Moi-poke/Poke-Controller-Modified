"""公開API面の正本一致（check_user_api / blockly_validate / pack_zip）の検証。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from core import blockly_validate, pack_zip
from core.user_api_allowlist import ALLOWED_COMMANDS_SUBS, THIRD_PARTY

ROOT = Path(__file__).resolve().parent.parent


def _load_check_user_api():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "check_user_api", str(ROOT / "tools" / "check_user_api.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_user_api"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_allowlist_sets_equal() -> None:
    check = _load_check_user_api()
    assert set(check.ALLOWED_COMMANDS_SUBS) == set(ALLOWED_COMMANDS_SUBS)
    assert set(check.THIRD_PARTY) == set(THIRD_PARTY)
    assert set(blockly_validate._ALLOWED_COMMANDS_SUBS) == set(ALLOWED_COMMANDS_SUBS)
    assert set(blockly_validate._THIRD_PARTY) == set(THIRD_PARTY)
    assert set(pack_zip._ALLOWED_COMMANDS_SUBS) == set(ALLOWED_COMMANDS_SUBS)
    assert set(pack_zip._THIRD_PARTY) == set(THIRD_PARTY)


def _good_code(extra_import: str) -> str:
    return (
        f"{extra_import}\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class ParityCmd(PythonCommand):\n"
        '    NAME = "parity"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        pass\n"
    )


def test_command_audio_accepted_everywhere(tmp_path: Path) -> None:
    assert "CommandAudio" in ALLOWED_COMMANDS_SUBS
    # blockly_validate は異常なし（error扱いの表面）。
    code = _good_code("from Commands.CommandAudio import AudioPythonCommand")
    assert blockly_validate.validate_generated_code(code) == []
    # pack_zip は異常・注意なし。
    entry = tmp_path / "audio.py"
    entry.write_text(code, encoding="utf-8")
    errors, warnings = pack_zip.scan_entry_imports(entry)
    assert errors == []
    assert warnings == []
    # check_user_api の集合にも含まれる。
    check = _load_check_user_api()
    assert "CommandAudio" in set(check.ALLOWED_COMMANDS_SUBS)


def test_sounddevice_accepted_everywhere(tmp_path: Path) -> None:
    assert "sounddevice" in THIRD_PARTY
    code = _good_code("import sounddevice")
    assert blockly_validate.validate_generated_code(code) == []
    entry = tmp_path / "snd.py"
    entry.write_text(code, encoding="utf-8")
    errors, warnings = pack_zip.scan_entry_imports(entry)
    assert errors == []
    assert warnings == []
    check = _load_check_user_api()
    assert "sounddevice" in set(check.THIRD_PARTY)


def test_unknown_third_party_severity(tmp_path: Path) -> None:
    # blockly_validate: 未知は異常（error）。
    code = _good_code("import somelib_xyz")
    assert blockly_validate.validate_generated_code(code) != []
    # pack_zip: 未知は注意（warning）で導入は通す。
    entry = tmp_path / "third.py"
    entry.write_text("import somelib_xyz\n", encoding="utf-8")
    errors, warnings = pack_zip.scan_entry_imports(entry)
    assert errors == []
    assert warnings != []
    # check_user_api: 未知は許可外（違反扱い）。
    check = _load_check_user_api()
    assert "somelib_xyz" not in set(check.THIRD_PARTY)
    assert "somelib_xyz" not in set(sys.stdlib_module_names)
