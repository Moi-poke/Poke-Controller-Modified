"""作例ギャラリー（新規→作例の窓）の検証。"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest
from blockly_node import BLOCKLY, NEEDS_NODE, run_editor
from services import blockly_save

ROOT = Path(__file__).resolve().parent.parent
SERIAL = ROOT / "SerialController"


def test_bundled_samples_all_appear_with_required_fields() -> None:
    """同梱の作例が stem・name・blocks・summary 付きで全部返ること。"""
    # Given: 出荷時の置き場（SerialController を置き場にする）
    # When: 作例の一覧を取る
    samples = blockly_save.list_samples(SERIAL)
    # Then: `.py` と対の作例が保存名順で全部ある
    py_dir = SERIAL / "Commands" / "PythonCommands"
    expect: list[str] = []
    for path in sorted(py_dir.glob("BlocklySample*.blockly.json")):
        stem = path.name[: -len(".blockly.json")]
        if (path.parent / (stem + ".py")).is_file():
            expect.append(stem)
    assert [s["stem"] for s in samples] == expect
    assert len(samples) >= 1
    for s in samples:
        assert s["stem"]
        assert s["name"]
        assert s["blocks"] > 0
        assert 1 <= len(s["summary"]) <= 40
        assert isinstance(s["tags"], list)


def test_broken_sample_file_is_skipped_without_raising(tmp_path: Path) -> None:
    """壊れた作例があっても例外を出さず飛ばすこと。"""
    # Given: 正しい対・壊れたJSON・対の無いJSONのある一時置き場
    dest = tmp_path / "app" / "Commands" / "PythonCommands"
    dest.mkdir(parents=True)
    src = SERIAL / "Commands" / "PythonCommands"
    for name in ("BlocklySampleBasic.blockly.json", "BlocklySampleBasic.py"):
        shutil.copy(src / name, dest / name)
    broken = dest / "BlocklySampleBroken.blockly.json"
    broken.write_text("{壊れている", encoding="utf-8")
    (dest / "BlocklySampleBroken.py").write_text("# broken\n", encoding="utf-8")
    orphan = dest / "BlocklySampleOrphan.blockly.json"
    orphan.write_text('{"blocks": {"blocks": []}}', encoding="utf-8")
    # When: 一覧を取る
    samples = blockly_save.list_samples(tmp_path / "app")
    # Then: 正しい作例だけ返る（例外なし）
    assert [s["stem"] for s in samples] == ["BlocklySampleBasic"]


def test_samples_endpoint_lists_samples_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本物の受け口の GET /samples が一覧を返すこと。"""
    # Given: 同梱の作例を置き場にした本物の受け口
    pytest.importorskip("tkinter")
    import WindowUtils
    from ui import blockly_editor

    monkeypatch.setattr(WindowUtils, "APP_DIR", str(SERIAL))
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{httpd.server_address[1]}"
        # When: /samples を取る
        conn = http.client.HTTPConnection(host, timeout=10)
        conn.request("GET", "/samples")
        resp = conn.getresponse()
        body: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        # Then: 成功し、同梱分の一覧が入っている
        assert resp.status == 200
        assert body["ok"] is True
        got = [s["stem"] for s in body["samples"]]
        want = [s["stem"] for s in blockly_save.list_samples(SERIAL)]
        assert got == want
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5.0)


def read_editor_html() -> str:
    """編集画面の全文を読む。"""
    return (BLOCKLY / "editor.html").read_text(encoding="utf-8")


def test_gallery_dialog_markup_is_accessible() -> None:
    """作例の窓が dialog 役割・名前・カード用の器を持ち script は1個のこと。"""
    # Given: 編集画面の全文
    html = read_editor_html()
    # When/Then: 窓の約束（id・役割・名前参照）がある
    assert 'id="newdialog"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert "aria-labelledby" in html
    assert 'id="newcards"' in html
    assert html.count("<script>") == 1


@NEEDS_NODE
def test_new_button_opens_gallery_with_empty_and_sample_cards() -> None:
    """「新規」で窓が開き、空のカード＋作例の数だけカードが並ぶこと。"""
    # Given: 作例2件の受け口
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'gal1' };
        routes.samples = { ok: true, samples: [
          { stem: 'BlocklySampleBasic', name: 'サンプル基本',
            tags: ['blockly'], blocks: 5, summary: '押す' },
          { stem: 'BlocklySampleVision', name: '画像認識例',
            tags: ['blockly'], blocks: 12, summary: '画像認識' } ] };
        boot();
        await flush(600);
        // When: 「新規」を押す
        els.newws.handlers.click();
        await flush(600);
        done({ display: els.newdialog.style.display,
               cards: els.newcards.options.length,
               first: els.newcards.options[0].attrs['data-stem'],
               names: els.newcards.options.map((c) => c.attrs['data-stem']) });
        """
    )
    # Then: 窓が開き、先頭は空＋作例2件の計3枚
    assert res["display"] == "block"
    assert res["cards"] == 3
    assert res["first"] == ""
    assert res["names"] == ["", "BlocklySampleBasic", "BlocklySampleVision"]


@NEEDS_NODE
def test_choosing_sample_opens_as_new_command_with_my_name() -> None:
    """作例を選ぶと /load が呼ばれ、新しい保存名・未保存扱いで開くこと。"""
    # Given: 作例1件と、同名の保存物がある受け口
    res = run_editor(
        """
        routes.list = { stems: ['MyBasic'], appId: 'gal1' };
        routes.samples = { ok: true, samples: [
          { stem: 'BlocklySampleBasic', name: 'サンプル基本',
            tags: ['blockly'], blocks: 5, summary: '押す' } ] };
        routes.load = { ok: true, stem: 'BlocklySampleBasic', externalEdit: false,
          workspaceJson: JSON.stringify({ blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'x' } } ] } }) };
        boot();
        await flush(600);
        els.newws.handlers.click();
        await flush(600);
        // When: 作例カードを選ぶ
        els.newcards.options[1].handlers.click();
        await flush(600);
        const afterSelect = { stem: els.stem.value, dirty: els.dirty.textContent,
          status: els.status.textContent,
          loads: calls.filter((c) => String(c.url).indexOf('./load') === 0).map((c) => c.url),
          display: els.newdialog.style.display };
        // When: 既にある名前で保存しようとする（新しい扱いなら確認が出る）
        els.stem.value = 'MyBasic';
        confirmAnswer = false;
        els.save.handlers.click();
        await flush(600);
        done({ afterSelect, confirms,
               saved: calls.filter((c) => c.url === './save').length });
        """
    )
    # Then: 作例の中身を読み、新しい保存名（同名あり→連番）で未保存扱い
    assert res["afterSelect"]["loads"] == ["./load?stem=BlocklySampleBasic"]
    assert res["afterSelect"]["stem"] == "MyBasic2"
    assert res["afterSelect"]["dirty"] == "● 未保存"
    assert "作例「サンプル基本」" in res["afterSelect"]["status"]
    assert res["afterSelect"]["display"] == "none"
    # Then: 同名の保存物があれば保存時に上書き確認が出る（対応先なしの証拠）
    assert len(res["confirms"]) == 1 and "上書き" in res["confirms"][0]
    assert res["saved"] == 0


@NEEDS_NODE
def test_escape_closes_gallery_without_changing_anything() -> None:
    """Esc では窓が閉じるだけで、編集内容も読み込みも変わらないこと。"""
    # Given: 開いた作例の窓
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'gal1' };
        routes.samples = { ok: true, samples: [
          { stem: 'BlocklySampleBasic', name: 'サンプル基本',
            tags: [], blocks: 5, summary: '押す' } ] };
        boot();
        await flush(600);
        const before = ws.getTopBlocks(false).map((b) => b.type).join(',') + '|' + els.stem.value;
        els.newws.handlers.click();
        await flush(600);
        const opened = els.newdialog.style.display;
        // When: Esc を押す
        els.newdialog.handlers.keydown({ key: 'Escape', preventDefault() {} });
        await flush(200);
        done({ opened, display: els.newdialog.style.display,
               before,
               after: ws.getTopBlocks(false).map((b) => b.type).join(',') + '|' + els.stem.value,
               loads: calls.filter((c) => String(c.url).indexOf('./load') === 0).length });
        """
    )
    # Then: 開いていた窓が閉じ、中身・読込はそのまま
    assert res["opened"] == "block"
    assert res["display"] == "none"
    assert res["after"] == res["before"]
    assert res["loads"] == 0


@NEEDS_NODE
def test_empty_card_starts_fresh_as_before() -> None:
    """「空のプログラム」カードは従来の初期形で始めること。"""
    # Given: 開いた作例の窓
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'gal1' };
        routes.samples = { ok: true, samples: [
          { stem: 'BlocklySampleBasic', name: 'サンプル基本',
            tags: [], blocks: 5, summary: '押す' } ] };
        boot();
        await flush(600);
        els.newws.handlers.click();
        await flush(600);
        // When: 先頭（空）のカードを選ぶ
        els.newcards.options[0].handlers.click();
        await flush(400);
        done({ types: ws.getTopBlocks(false).map((b) => b.type),
               name: ws.getTopBlocks(false)[0].getFieldValue('NAME'),
               stem: els.stem.value, display: els.newdialog.style.display });
        """
    )
    # Then: プログラム1個の初期形・保存名は既定・窓は閉じる
    assert res["types"] == ["pokecon_program"]
    assert res["name"] == "新しいコマンド"
    assert res["stem"] == "MyBlock"
    assert res["display"] == "none"


@NEEDS_NODE
def test_gallery_cards_are_reached_by_arrow_keys() -> None:
    """カード間を矢印キーで移動できること。"""
    # Given: 開いた作例の窓（空＋作例1件）
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'gal1' };
        routes.samples = { ok: true, samples: [
          { stem: 'BlocklySampleBasic', name: 'サンプル基本',
            tags: [], blocks: 5, summary: '押す' } ] };
        boot();
        await flush(600);
        els.newws.handlers.click();
        await flush(600);
        // When: ↓↑と動かす
        els.newdialog.handlers.keydown({ key: 'ArrowDown', preventDefault() {} });
        const moved = els.newcards.attrs['data-sel'];
        els.newdialog.handlers.keydown({ key: 'ArrowUp', preventDefault() {} });
        done({ moved, back: els.newcards.attrs['data-sel'] });
        """
    )
    # Then: 選択位置が進み、戻る
    assert res == {"moved": "1", "back": "0"}


@NEEDS_NODE
def test_gallery_with_no_samples_shows_only_the_empty_card() -> None:
    """作例が無い・読めないときは空のカードだけ出し、理由を小さく出すこと。"""
    # Given: 作例の取得に失敗する受け口
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'gal1' };
        routes.samples = { ok: false, message: '作例を読めません' };
        boot();
        await flush(600);
        // When: 「新規」を押す
        els.newws.handlers.click();
        await flush(600);
        done({ display: els.newdialog.style.display,
               cards: els.newcards.options.length,
               error: els.newdialogerror.textContent });
        """
    )
    # Then: 空のカードだけで、理由が出る
    assert res["display"] == "block"
    assert res["cards"] == 1
    assert "作例を読めません" in res["error"]
