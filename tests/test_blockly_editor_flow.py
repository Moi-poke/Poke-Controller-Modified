"""編集画面の操作の流れ（起動・控えの復元・上書き確認・保存前検査）の検証。

editor.html の inline script を、本物の Blockly（描画なし）と最小DOMで
起動し、ボタン操作相当の手順で確かめる。
"""

from __future__ import annotations

from blockly_node import NEEDS_NODE, run_editor

pytestmark = NEEDS_NODE


def test_fresh_start_shows_one_program_block_and_is_clean() -> None:
    """控えが無い起動では、プログラム1個の初期形が出て「保存済み」扱いになること。"""
    # Given: 控え無し
    res = run_editor(
        """
        // When: 起動して一覧・候補の到着を待つ
        boot();
        await flush(400);
        done({ types: ws.getTopBlocks(false).map((b) => b.type),
               dirty: els.dirty.textContent, hint: els.status.textContent });
        """
    )
    # Then: 初期形・未保存なし・最初の一歩の案内
    assert res["types"] == ["pokecon_program"]
    assert res["dirty"] == "保存済み"
    assert "プログラム" in res["hint"]


def test_unsaved_draft_is_restored_on_next_start_and_stays_unsaved() -> None:
    """保存しないまま閉じた編集は次回起動で戻り、未保存のまま扱われること。"""
    # Given: 未保存の控え（待ち1個入り）
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        store['pokecon.blockly.draft.v1.app1'] = JSON.stringify({ stem: 'Draft1', dirty: true,
          state: { blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'x' }, inputs: { DO: { block:
              { type: 'pokecon_wait', fields: { SEC: 3 } } } } } ] } } });
        // When: 起動する
        boot();
        await flush(400);
        done({ stem: els.stem.value, dirty: els.dirty.textContent,
               types: ws.getAllBlocks(false).map((b) => b.type).sort(),
               status: els.status.textContent });
        """
    )
    # Then: 中身と保存名が戻り、未保存表示と復元の知らせが出る
    assert res["stem"] == "Draft1"
    assert res["types"] == ["pokecon_program", "pokecon_wait"]
    assert res["dirty"] == "● 未保存"
    assert "復元" in res["status"]


def test_saving_under_another_commands_name_asks_before_overwriting() -> None:
    """開いていない別の保存物と同じ名前で保存するときは、上書きを確かめること。"""
    # Given: 「Existing」が保存済みで、新規の編集中
    res = run_editor(
        """
        routes.list = { stems: ['Existing'] };
        boot();
        await flush(400);
        els.stem.value = 'Existing';
        // When: 保存を押し、確認で「いいえ」を選ぶ
        confirmAnswer = false;
        els.save.handlers.click();
        await flush(200);
        const declined = calls.filter((c) => c.url === './save').length;
        // When: もう一度押して「はい」を選ぶ
        confirmAnswer = true;
        els.save.handlers.click();
        await flush(200);
        done({ confirms, declined, saved: calls.filter((c) => c.url === './save').length });
        """
    )
    # Then: 確認が出て、「いいえ」では保存せず、「はい」で保存する
    assert len(res["confirms"]) == 2
    assert "上書き" in res["confirms"][0]
    assert res["declined"] == 0
    assert res["saved"] == 1


def test_saving_the_command_that_was_opened_does_not_ask() -> None:
    """開いた保存物をそのまま上書き保存するときは確かめないこと。"""
    # Given: 「Existing」を開いた
    res = run_editor(
        """
        routes.list = { stems: ['Existing'] };
        routes.load = { ok: true, stem: 'Existing', externalEdit: false,
          workspaceJson: JSON.stringify({ blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'x' } } ] } }) };
        boot();
        await flush(400);
        els.files.value = 'Existing';
        els.open.handlers.click();
        await flush(200);
        // When: そのまま保存する
        els.save.handlers.click();
        await flush(200);
        done({ confirms, saved: calls.filter((c) => c.url === './save').length });
        """
    )
    # Then: 確認なしで保存される
    assert res["confirms"] == []
    assert res["saved"] == 1


def test_vision_block_without_a_chosen_image_is_stopped_before_saving() -> None:
    """画像を選んでいない画像認識ブロックは、保存前に件数付きで知らせて止めること。"""
    # Given: 置いたばかり（画像未選択）の画像待ちブロック
    res = run_editor(
        """
        boot();
        await flush(400);
        const prog = ws.getTopBlocks(false)[0];
        const v = ws.newBlock('pokecon_vision_wait_appear');
        prog.getInput('DO').connection.connect(v.previousConnection);
        // When: 保存を押す
        els.save.handlers.click();
        await flush(200);
        done({ tpl: v.getFieldValue('TEMPLATE'), errors: els.saveerrors.textContent,
               saved: calls.filter((c) => c.url === './save').length });
        """
    )
    # Then: 既定は未選択で、保存は止まり、理由が出る
    assert res["tpl"] == ""
    assert res["saved"] == 0
    assert "画像" in res["errors"] and "1 個" in res["errors"]


def test_file_list_does_not_preselect_a_name_that_no_longer_exists() -> None:
    """前回の保存名が一覧に無いとき、空の選択にせず先頭を選んだままにすること。"""
    # Given: 前回は「Gone」を保存して閉じたが、今は一覧に無い
    res = run_editor(
        """
        store['pokecon.blockly.draft.v1.app1.last'] = 'Gone';
        routes.list = { stems: ['A1', 'B2'], appId: 'app1' };
        // When: 起動する
        boot();
        await flush(400);
        done({ selected: els.files.value });
        """
    )
    # Then: 先頭が選ばれている
    assert res["selected"] == "A1"


def test_overwrite_check_ignores_letter_case() -> None:
    """大文字小文字だけ違う名前（Windowsでは同じファイル）でも上書きを確かめること。"""
    # Given: 「MyBlock」が保存済み
    res = run_editor(
        """
        routes.list = { stems: ['MyBlock'] };
        boot();
        await flush(400);
        els.stem.value = 'myblock';
        // When: 小文字だけの名前で保存し、確認で「いいえ」
        confirmAnswer = false;
        els.save.handlers.click();
        await flush(200);
        done({ confirms, saved: calls.filter((c) => c.url === './save').length });
        """
    )
    # Then: 確認が出て、保存しない
    assert len(res["confirms"]) == 1 and "上書き" in res["confirms"][0]
    assert res["saved"] == 0


def test_closing_right_after_an_edit_keeps_it_as_unsaved_draft() -> None:
    """編集の直後（判定の待ち時間内）に閉じても、その編集を未保存の控えとして残すこと。"""
    # Given: 起動直後
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        // When: ブロックを足してすぐ閉じる（判定の待ちより前）
        const prog = ws.getTopBlocks(false)[0];
        const w = ws.newBlock('pokecon_wait');
        prog.getInput('DO').connection.connect(w.previousConnection);
        const ev = { preventDefault() {}, returnValue: undefined };
        winHandlers.beforeunload(ev);
        const draft = JSON.parse(store['pokecon.blockly.draft.v1.app1'] || 'null');
        done({ dirty: draft && draft.dirty, asked: ev.returnValue === '' });
        """
    )
    # Then: 控えは未保存扱いで、閉じる確認も出る
    assert res == {"dirty": True, "asked": True}


def test_unrestorable_draft_is_kept_aside_and_reported() -> None:
    """読めない控え（消えたブロック種など）は黙って捨てず、退避して知らせること。"""
    # Given: 存在しないブロック種を含む未保存の控え
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        const bad = JSON.stringify({ stem: 'X', dirty: true, state: { blocks: {
          languageVersion: 0, blocks: [{ type: 'no_such_block' }] } } });
        store['pokecon.blockly.draft.v1.app1'] = bad;
        // When: 起動する
        boot();
        await flush(400);
        done({ status: els.status.textContent,
               backup: store['pokecon.blockly.draft.v1.app1.broken'] === bad,
               types: ws.getTopBlocks(false).map((b) => b.type) });
        """
    )
    # Then: 初期形で始まり、控えは退避され、知らせが出る
    assert res["types"] == ["pokecon_program"]
    assert res["backup"] is True
    assert "復元できません" in res["status"]


def test_draft_from_another_install_is_not_restored() -> None:
    """別の置き場（別インストール）の控えは復元しないこと。"""
    # Given: 別アプリの控えだけがある
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'mine' };
        store['pokecon.blockly.draft.v1.other'] = JSON.stringify({ stem: 'Theirs', dirty: true,
          state: { blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'theirs' } } ] } } });
        // When: 起動する
        boot();
        await flush(400);
        done({ stem: els.stem.value,
               name: ws.getTopBlocks(false)[0].getFieldValue('NAME') });
        """
    )
    # Then: 自分の初期形で始まり、よその保存名も入らない
    assert res["stem"] != "Theirs"
    assert res["name"] == "新しいコマンド"


def test_restored_draft_still_asks_before_overwriting_an_existing_name() -> None:
    """控えから戻した編集は、同名の保存物があれば保存前に上書きを確かめること。"""
    # Given: 「Shared」を開いて編集中に閉じた控え（同名の保存物あり）
    res = run_editor(
        """
        routes.list = { stems: ['Shared'], appId: 'app1' };
        store['pokecon.blockly.draft.v1.app1'] = JSON.stringify({ stem: 'Shared',
          current: 'Shared', dirty: true, state: { blocks: { languageVersion: 0, blocks: [
            { type: 'pokecon_program', fields: { NAME: 'x' } } ] } } });
        boot();
        await flush(400);
        // When: そのまま保存する（確認には「いいえ」）
        confirmAnswer = false;
        els.save.handlers.click();
        await flush(200);
        done({ confirms: confirms.length, saved: calls.filter((c) => c.url === './save').length });
        """
    )
    # Then: 確認が出る（控えの対応先は信用しない）
    assert res == {"confirms": 1, "saved": 0}


def test_last_saved_command_stays_selected_across_restarts() -> None:
    """保存したコマンドは、何度起動し直しても一覧で選ばれたままであること。"""
    # Given: 「Saved」を保存した
    res = run_editor(
        """
        routes.list = { stems: ['A1', 'Saved'], appId: 'app1' };
        boot();
        await flush(400);
        els.stem.value = 'Saved';
        confirmAnswer = true;
        els.save.handlers.click();
        await flush(300);
        // When: 2回起動し直す（再読込で保存名欄は既定に戻る。初期形の読込で
        // 控えが書き換わっても）
        els.stem.value = 'MyBlock';
        boot();
        await flush(1500);
        els.stem.value = 'MyBlock';
        boot();
        await flush(1500);
        done({ selected: els.files.value });
        """
    )
    # Then: 「Saved」が選ばれている
    assert res["selected"] == "Saved"


def test_unparsable_draft_is_kept_aside_too() -> None:
    """壊れた（JSONとして読めない）控えも、黙って上書きせず退避すること。"""
    # Given: 壊れた控え
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        store['pokecon.blockly.draft.v1.app1'] = '{broken';
        // When: 起動し、自動控えが走るまで待つ
        boot();
        await flush(1500);
        done({ backup: store['pokecon.blockly.draft.v1.app1.broken'],
               status: els.status.textContent });
        """
    )
    # Then: 退避されて知らせが出る
    assert res["backup"] == "{broken"
    assert "復元できません" in res["status"]


def test_drafts_are_off_when_the_install_cannot_be_identified() -> None:
    """一覧が置き場の識別子を返さないときは、共有の鍵へ控えを書かないこと。"""
    # Given: 識別子なしの一覧
    res = run_editor(
        """
        routes.list = { stems: [] };
        boot();
        await flush(400);
        // When: 編集して閉じる
        const prog = ws.getTopBlocks(false)[0];
        const w = ws.newBlock('pokecon_wait');
        prog.getInput('DO').connection.connect(w.previousConnection);
        await flush(1200);
        winHandlers.beforeunload({ preventDefault() {}, returnValue: undefined });
        done({ keys: Object.keys(store).filter((k) => k.indexOf('draft') !== -1) });
        """
    )
    # Then: 控えは書かれない
    assert res == {"keys": []}


def test_other_tab_notice_is_shown_only_once() -> None:
    """別タブの控え書き込みの知らせは1回だけで、以降の状態表示を上書きし続けないこと。"""
    # Given: 起動済み
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const key = 'pokecon.blockly.draft.v1.app1';
        // When: 別タブの書き込みが2回届き、その間に状態が変わる
        winHandlers.storage({ key, newValue: '{}' });
        const first = els.status.textContent;
        els.status.textContent = '保存しました';
        winHandlers.storage({ key, newValue: '{}' });
        done({ first, after: els.status.textContent });
        """
    )
    # Then: 1回目は知らせ、2回目は上書きしない
    assert "別のタブ" in res["first"]
    assert res["after"] == "保存しました"


def test_closing_the_capture_modal_returns_focus_to_the_opener() -> None:
    """拡大モーダルを閉じると、開く前の場所へフォーカスが戻ること。"""
    # Given: 起動済み＋プログラム内の画像待ち（開く前のフォーカス先を用意する）
    res = run_editor(
        """
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const prog = ws.getTopBlocks(false)[0];
        const v = ws.newBlock('pokecon_vision_wait_appear');
        prog.getInput('DO').connection.connect(v.previousConnection);
        const focused = [];
        els.runbtn.focus = () => { focused.push('runbtn'); };
        els.capbigimg.src = 'blob:fake';
        // 最小DOMに開く前のフォーカス先を置く（実ブラウザの activeElement 相当。
        // 本文からは sandbox.document 経由で触る。document 直参照は
        // inline 用の別グローバルのため未定義になる）
        sandbox.document.activeElement = els.runbtn;
        // When: ブロック用に開いて取り消しで閉じる
        Blockly.PokeconOpenBlockModal(v.id);
        await flush(300);
        const opened = els.capmodal.style.display;
        els.capcancel.handlers.click();
        done({ opened, display: els.capmodal.style.display, focused });
        """
    )
    # Then: 開いて閉じて、開く前のrunbtnへフォーカスが戻る
    assert res["opened"] == "block"
    assert res["display"] == "none"
    assert res["focused"] == ["runbtn"]
