"""SERVICES層レビュー指摘の回帰検証。実挙動・偽物で回す。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np
import pytest


def _frame() -> np.ndarray:
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = (60, 60, 60)
    img[30:70, 60:140] = (255, 255, 255)
    img[40:60, 80:120] = (0, 0, 0)
    return img


def _png_of(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def _make_pack_src(base: Path, version: str, min_app: str) -> Path:
    src = base / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True, exist_ok=True)
    (src / "Template" / "my-pack").mkdir(parents=True, exist_ok=True)
    data = {
        "name": "my-pack",
        "version": version,
        "author": "alice",
        "description": "sample",
        "entry": "MyPack.py",
        "minAppVersion": min_app,
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n",
        encoding="utf-8",
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return src


def test_uninstall_skips_traversal_rels(tmp_path: Path) -> None:
    """改ざんされた導入記録の `..` は消さずに無視する。"""
    from core import pack_zip
    from services import script_pack

    app = tmp_path / "app"
    app.mkdir()
    src = _make_pack_src(tmp_path / "v1", "1.0.0", "4.0.0")
    out = tmp_path / "p.zip"
    pack_zip.create_pack(src, out)
    assert script_pack.install_zip(app, out).status == "installed"
    # 導入記録へ外を指す相対を混ぜる（改ざんの再現）。
    rec_path = app / "InstalledPacks" / "my-pack.json"
    data = json.loads(rec_path.read_text(encoding="utf-8"))
    data["files"].append("../outside.txt")
    rec_path.write_text(json.dumps(data), encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("大切なファイル", encoding="utf-8")
    res = script_pack.uninstall_package(app, "my-pack")
    assert res.status == "removed"
    # 外のファイルは残り、配下の正規ファイルは消える。
    assert outside.is_file()
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").exists()


def test_install_rolls_back_on_mid_copy_failure(tmp_path: Path) -> None:
    """途中で書けなくても前半だけ残さない（退避から戻す）。"""
    import shutil

    from core import pack_zip
    from services import script_pack

    app = tmp_path / "app"
    app.mkdir()
    src1 = _make_pack_src(tmp_path / "v1", "1.0.0", "4.0.0")
    z1 = tmp_path / "p1.zip"
    pack_zip.create_pack(src1, z1)
    assert script_pack.install_zip(app, z1).status == "installed"
    entry = app / "Commands" / "PythonCommands" / "MyPack.py"
    entry.write_text("old-entry", encoding="utf-8")
    # 2件目の書込みでこけるよう、配下をファイルで塞ぐ（実挙動の失敗）。
    tpl_dir = app / "Template" / "my-pack"
    shutil.rmtree(tpl_dir)
    tpl_dir.write_text("ふさぎ", encoding="utf-8")
    src2 = _make_pack_src(tmp_path / "v2", "2.0.0", "4.0.0")
    (src2 / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        "new-entry", encoding="utf-8"
    )
    z2 = tmp_path / "p2.zip"
    pack_zip.create_pack(src2, z2)
    res = script_pack.install_zip(app, z2, allow_overwrite=True)
    assert res.status == "failed"
    # 先に書けた1件目は退避から戻り、新版で上書きされない。
    assert entry.read_text(encoding="utf-8") == "old-entry"


def test_install_rejects_too_new_min_app_version(tmp_path: Path) -> None:
    """要る版が今より新しければ導入しない。"""
    from core import pack_zip
    from services import script_pack

    app = tmp_path / "app"
    app.mkdir()
    src = _make_pack_src(tmp_path / "v9", "9.0.0", "99.0.0")
    out = tmp_path / "p9.zip"
    pack_zip.create_pack(src, out)
    res = script_pack.install_zip(app, out)
    assert res.status == "failed"
    assert "99.0.0" in res.message
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").exists()


def test_save_template_clears_cpu_cache(tmp_path: Path) -> None:
    """保存後はCPU側の古い画像を読まない。"""
    from core.CommandVision import _imread_or_raise
    from services import blockly_capture

    png = _png_of(np.zeros((20, 20, 3), dtype=np.uint8) + 10)
    tpl_dir = tmp_path / "Template" / "pack"
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "old.png").write_bytes(png)
    _imread_or_raise(str(tmp_path / "Template" / "pack" / "old.png"), 0)
    assert _imread_or_raise.cache_info().currsize >= 1
    full = {"x": 0, "y": 0, "width": 1, "height": 1}
    res = blockly_capture.save_template(tmp_path, "newmark", _png_of(_frame()), full)
    assert res.status == "saved"
    assert _imread_or_raise.cache_info().currsize == 0


def test_save_template_rejects_slash_name(tmp_path: Path) -> None:
    """保存名の `/`・`\\` は固定配置のため断る。"""
    from services import blockly_capture

    png = _png_of(_frame())
    full = {"x": 0, "y": 0, "width": 1, "height": 1}
    assert blockly_capture.save_template(tmp_path, "a/b", png, full).status == "failed"
    assert blockly_capture.save_template(tmp_path, "a\\b", png, full).status == "failed"
    assert not (tmp_path / "Template" / "blockly" / "a").exists()


def test_match_rejects_symlink_escape(tmp_path: Path) -> None:
    """Template内の外へのリンクは読まない（editorと同じ閉じ込め）。"""
    from services import blockly_match

    frame = _frame()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_img = frame[30:70, 60:140]
    (outside_dir / "real.png").write_bytes(_png_of(outside_img))
    app = tmp_path / "app"
    link_dir = app / "Template" / "pack"
    link_dir.mkdir(parents=True)
    try:
        (link_dir / "link.png").symlink_to(outside_dir / "real.png")
    except OSError:
        pytest.skip("symlinkを作れません")
    # 壊れリンクでなく実画像への外しなので、読めば当たる。断るのが正しい。
    res = blockly_match.match_template(app, _png_of(frame), "pack/link.png", 0.7)
    assert res.status == "failed"


def test_switch_transport_failure_returns_false() -> None:
    """差し替えに失敗したら成功の知らせを出さない。"""
    from core.serial.sender import Sender
    from fakes import FakeTransport
    from services.serial_service import SerialService

    class StuckSender(Sender):
        def stopLiveWorker(self, timeout: float = 1.0) -> bool:
            return False

    notices: list[str] = []
    service = SerialService(
        notify_user=notices.append, base_dir=".", input_log_emit=lambda line: None
    )
    service.sender = StuckSender(is_show_serial=False, transport=FakeTransport())
    old_name = service.sender.getTransportName()
    switched, linked = service.switch_transport("pico_uart")
    assert (switched, linked) == (False, False)
    assert service.sender.getTransportName() == old_name
    assert not any("切り替えました" in m for m in notices)
    assert any("失敗" in m or "できません" in m or "られません" in m for m in notices)


def test_switch_to_unlinked_still_reports_switched() -> None:
    """入力ログの無い方式への切替は成功扱いを保つ。"""
    from typing import Any

    from core import Transport
    from core.transport.base import Transport as BaseTransport
    from fakes import FakeTransport
    from services.serial_service import SenderSpec, SerialService

    class DummyNoLog(BaseTransport):
        name = "dummy_nolog_review"
        capability = Transport.LEGACY_ROW

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        def open(self, *args: Any, **kwargs: Any) -> bool:
            _ = (args, kwargs)
            return True

        def close(self) -> None:
            return None

        def is_open(self) -> bool:
            return False

        def send_row(self, row: str, measure_perf: bool = True) -> None:
            _ = (row, measure_perf)

    assert Transport.register_transport("dummy_nolog_review", DummyNoLog) is True
    try:
        notices: list[str] = []
        service = SerialService(
            notify_user=notices.append, base_dir=".", input_log_emit=lambda line: None
        )
        service.build_sender(
            SenderSpec(
                transport_name="legacy_text",
                is_show_serial=False,
                arbitration_mode="off",
                arbitration_cooldown=2.0,
                input_log_format="simple",
                input_log_actions="",
                input_log_enabled=True,
                input_log_stick_change=False,
                # ライブ入力の最低保持ミリ秒（SenderSpecの必須項目）
                live_min_dwell_ms=16,
            )
        )
        _ = FakeTransport
        switched, linked = service.switch_transport("dummy_nolog_review")
        assert (switched, linked) == (True, False)
        assert any("切り替えました" in m for m in notices)
    finally:
        assert Transport.unregister_transport("dummy_nolog_review") is True


def test_stop_waited_doc_says_ms() -> None:
    """stop_waitedの説明は秒でなくmsにする。"""
    from services.command_runner import CommandRunner

    doc = str(CommandRunner.stop_waited.__doc__ or "")
    assert "秒数" not in doc
    assert "ms" in doc or "ミリ秒" in doc


def test_post_clears_watch_id() -> None:
    """後始末が済んだら見張りの予約は残さない。"""
    from fakes import FakeCommand, FakeScheduler, FakeSer, FakeThread
    from services.command_runner import CommandRunner

    clock = FakeScheduler()
    runner = CommandRunner(
        stats={},
        schedule=clock.schedule,
        cancel=clock.cancel,
        notify_user=lambda m: None,
        on_state_changed=lambda: None,
        on_list_refresh=lambda: None,
    )
    runner.request_start(FakeCommand(thread=FakeThread(alive=False)), FakeSer())
    runner.request_stop()
    assert runner._watch_id is not None
    clock.run_next()
    assert runner.state == "idle"
    assert runner._watch_id is None


def test_save_returns_failed_on_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """json書込みの想定外の例外もHTTP無応答にしない。"""
    import json as std_json

    from services import blockly_save

    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    code = (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "x"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
    )
    ws = std_json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    real = blockly_save._atomic_write
    calls = {"n": 0}

    def flaky(target: Path, text: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("想定外の書込み失敗")
        real(target, text)

    monkeypatch.setattr(blockly_save, "_atomic_write", flaky)
    res = blockly_save.save_blockly(app, "MyBlock", ws, code)
    assert res.status == "failed"
    assert not (app / "Commands" / "PythonCommands" / "MyBlock.py").exists()


def test_keyword_template_arg_validated() -> None:
    """キーワードのテンプレ指定も検査する。"""
    from services import blockly_templates

    code = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "class C(ImageProcPythonCommand):\n"
        '    NAME = "x"\n'
        "    def do(self):\n"
        '        self.waitTemplate(template_path="../evil.png")\n'
    )
    assert blockly_templates.validate_template_refs(code) != []


def test_dynamic_template_arg_warns() -> None:
    """変数・f文字列の参照は落とさず注意する。"""
    from services import blockly_templates

    code_var = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "class C(ImageProcPythonCommand):\n"
        '    NAME = "x"\n'
        "    def do(self):\n"
        "        self.waitTemplate(path_var)\n"
    )
    code_f = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "class C(ImageProcPythonCommand):\n"
        '    NAME = "x"\n'
        "    def do(self):\n"
        '        self.waitTemplate(f"{name}.png")\n'
    )
    assert blockly_templates.validate_template_refs(code_var) == []
    assert blockly_templates.warn_template_refs(code_var) != []
    assert blockly_templates.validate_template_refs(code_f) == []
    assert blockly_templates.warn_template_refs(code_f) != []


def test_decode_upload_double_decode_note() -> None:
    """uploadはbase64復号の二重実行を避けても文言は変えない。"""
    raw = _png_of(_frame())
    b64 = base64.b64encode(raw).decode()
    from services import blockly_match

    assert blockly_match.decode_upload_image(b64) == raw


def _vision_code(expr: str) -> str:
    """vision系API呼び出し1行を持つ検査用コードを作る。"""
    return (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "class C(ImageProcPythonCommand):\n"
        '    NAME = "x"\n'
        "    def do(self):\n"
        f"        {expr}\n"
    )


def test_mask_path_traversal_rejected() -> None:
    """mask_pathの`..`は落とす（素名は注意に留める）。"""
    from services import blockly_templates

    code = _vision_code('self.waitTemplate("ok.png", mask_path="../evil.png")')
    assert blockly_templates.validate_template_refs(code) != []
    code_ok = _vision_code('self.waitTemplate("a/b.png", mask_path="a/m.png")')
    assert blockly_templates.validate_template_refs(code_ok) == []


def test_gpu_api_traversal_rejected() -> None:
    """GPU系APIの`..`も落とす。"""
    from services import blockly_templates

    code = _vision_code('self.isContainTemplateGPU("../evil.png")')
    assert blockly_templates.validate_template_refs(code) != []


def test_max_api_list_traversal_rejected() -> None:
    """一覧指定（位置・キーワード）の`..`は落とす。"""
    from services import blockly_templates

    code_pos = _vision_code('self.isContainTemplate_max(["../evil.png"])')
    assert blockly_templates.validate_template_refs(code_pos) != []
    code_kw = _vision_code(
        'self.isContainTemplate_max(template_path_list=["../evil.png"])'
    )
    assert blockly_templates.validate_template_refs(code_kw) != []
    code_pre = _vision_code('self.preloadTemplates(["../evil.png"])')
    assert blockly_templates.validate_template_refs(code_pre) != []


def test_dynamic_list_warns_not_fails() -> None:
    """一覧・maskの動的指定は落とさず注意だけ出す。"""
    from services import blockly_templates

    code_var = _vision_code("self.isContainTemplate_max(path_list)")
    assert blockly_templates.validate_template_refs(code_var) == []
    assert blockly_templates.warn_template_refs(code_var) != []
    code_mask = _vision_code('self.waitTemplate("a/b.png", mask_path=mask_var)')
    assert blockly_templates.validate_template_refs(code_mask) == []
    assert blockly_templates.warn_template_refs(code_mask) != []


def test_install_rejects_escape_rel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """staged配置の外向き相対は導入せず外を壊さない（閉じ込め）。"""
    from pathlib import PurePosixPath

    from core import pack_manifest, pack_zip
    from services import script_pack

    app = tmp_path / "app"
    app.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("大切なファイル", encoding="utf-8")
    manifest = pack_manifest.PackManifest(
        name="my-pack",
        version="1.0.0",
        author="alice",
        description="sample",
        entry="../../../outside.txt",
        minAppVersion="4.0.0",
        templates=[],
    )
    entry_rel = "Commands/PythonCommands/" + PurePosixPath(manifest.entry).as_posix()

    def fake_extract(zip_path: object, staged: object) -> list[str]:
        _ = zip_path
        base = Path(str(staged))
        src = base / PurePosixPath(entry_rel).as_posix()
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("evil", encoding="utf-8")
        return [entry_rel]

    def fake_validate(staged: object) -> pack_zip.StagedReport:
        _ = staged
        return pack_zip.StagedReport(manifest=manifest, errors=[], warnings=[])

    monkeypatch.setattr(pack_zip, "safe_extract", fake_extract)
    monkeypatch.setattr(pack_zip, "validate_staged", fake_validate)
    res = script_pack.install_zip(app, tmp_path / "dummy.zip")
    assert res.status == "failed"
    assert outside.read_text(encoding="utf-8") == "大切なファイル"
