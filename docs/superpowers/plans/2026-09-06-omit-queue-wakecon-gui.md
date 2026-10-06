# Q機能オミット + wakeCon対応コマンドGUI化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pico側に存在しないQ/R時刻付きキュー経路を削除し、残りのwakeCon対応コマンドをGUIから叩けるようにする。

**Architecture:** Q削除は`Sender`/`encoding`/`CommandOperate`の純粋な削減で送信経路をS一本化。GUI追加は既存`WakeSetup`小窓の拡張に留め、新規窓や新層は作らない。応答読みは`WakeLink.query/send_line/read_lines`を作業スレッドで使う既存パターンを踏襲し、Tkスレッドをブロックしない。

**Tech Stack:** Python 3.12 / tkinter / pyserial越しWakeLink / pytest, ruff, mypy, bounds, userapi

**Spec:** ユーザー合意 (ファーム`main`の`UI_CMDS`にQ/Rなし、`S,N,O,C,L,B,X,?,D,M,K,P,W`のみ。`N`は正規中立として残す)。範囲確定: (1) Task B範囲は`P, ?+usb, W表示/切替, D, M, X, K` + O誘導、(2) `D`/`M`表示はログ1行のみ、(3) プランファイルはコミットしない。

## Global Constraints

- `SerialController/`を`sys.path`前提の絶対import、相対import禁止。
- `core/`はtkinter・アプリ層import禁止、`services/`はtkinter・画面部品import禁止、`ui/`は`Window` import禁止 (`task bounds`で強制)。
- `Commands.*`の公開APIパス (`Keys`/`PythonCommandBase`/`McuCommandBase`/`WakeLink`/`CommandVision`) は凍結 (`task userapi`)。
- GUIログは`print`→LogPane経路のみ重要操作に使い、通常はファイル`logger`。ワーカースレッドからwidgetを触らない (`after`ポンプ経由)。
- 既存ini書式・`Transport`/`Sender`公開面の互換を壊さない。Q公開名の削除は破壊的変更として明示する。
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `pytest` が全緑。実機確認は別途明記。
- コミットはユーザーの明示指示があるときのみ (計画書内のcommit手順は実行しない)。

---

### Task A: Q経路の削除 (S一本化)

**Files:**
- Create: `tests/test_no_queue.py`
- Modify: `SerialController/core/serial/encoding.py`
- Modify: `SerialController/core/serial/__init__.py`
- Modify: `SerialController/core/serial/sender.py`
- Modify: `SerialController/core/CommandOperate.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: なし (削除のみ)。
- Produces: `press()`は常に`keys.input/wait/inputEnd`経路。`Sender`のlive S worker (`putLive/takeLive/startLiveWorker`等) と`releaseAll/sendNeutralAll`は不変。`N`の中立用途は残る。

- [ ] **Step 1: Write the failing test**

```python
"""Q経路の残存を禁止する。pico-wakeConにQ/Rは無いため、PC側にQ公開面を残さない。"""

from core import CommandOperate
from core.serial import encoding, sender


def test_queue_surface_removed() -> None:
    assert not hasattr(encoding, "encode_queued_state")
    for name in (
        "shouldQueue",
        "runQueued",
        "queueThreshold",
        "encodeQueuedState",
        "_runQueueExchange",
        "_waitQueueDone",
        "_sendQueueLine",
        "_queue_unsupported",
        "_noteQueueSupported",
        "_forgetQueueSupport",
        "_queueKnownUnsupported",
        "QUEUE_THRESHOLD_S",
        "QUEUE_WAIT_MARGIN_S",
    ):
        assert not hasattr(sender.Sender, name), name
    assert not hasattr(CommandOperate.OperateMixin, "_pressQueued")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_no_queue.py -q`
Expected: FAIL (現状は属性が存在するため)。

- [ ] **Step 3: `encoding.py`と`__init__.py`からQを削除**

`encoding.py`の`encode_queued_state`全関数を削除。残る公開関数は`format_legacy_row` / `pico_field` / `encode_pico_state` / `verify_pico_encoder`のみ。`__init__.py`の`encode_queued_state as encode_queued_state,`の1行を削除。

- [ ] **Step 4: `sender.py`からQ状態機械を削除**

削除対象 (定義+参照の両方): `QUEUE_THRESHOLD_S`, `QUEUE_WAIT_MARGIN_S`, `queueThreshold`, `shouldQueue`, `_queueKnownUnsupported`, `_noteQueueSupported`, `_forgetQueueSupport` (定義 + `openSerial`/`open`内呼び出し2箇所), `runQueued`, `_runQueueExchange`, `encodeQueuedState`, `_sendQueueLine`, `_waitQueueDone`, および`runQueued`内限定の`from core.WakeLink import expect`。残すもの: `discardLive` (中立送出で使用)、live worker一式、R失敗時`N`掃除を含むQブロック全体 (N自体の定義は別箇所のため消えない)。

- [ ] **Step 5: `CommandOperate.press`を一本化**

```python
# press button at duration times(s)
def press(self, buttons: Any, duration: float = 0.1, wait: float = 0.1) -> None:
    self._gate()
    self.keys.input(buttons)
    self.wait(duration)
    self.keys.inputEnd(buttons)
    self.wait(wait)
    self.checkIfAlive()
```

`_pressQueued`全削除。

- [ ] **Step 6: `ARCHITECTURE.md`の1行修正**

```markdown
- Pico live: 姿勢スナップショットを 8ms スロットで送出、無変化時は
  88ms で再送（200ms watchdog 対策）。8ms 未満の押下はPC側タイミング
  (S行で押して待って離す) で送る (pico-wakeCon にQ/Rは無いためQ経路は持たない)
```

- [ ] **Step 7: Run Task A gates**

Run: `uv run --frozen ruff check SerialController tools tests` / `uv run --frozen ruff format --check SerialController tools tests` / `uv run --frozen mypy SerialController tools tests` / `uv run --frozen python tools/check_core.py` / `uv run --frozen python tools/check_user_api.py` / `uv run --frozen pytest tests -q`
Expected: 全PASS (`test_transport.py`の`WakeLink.expect(NoSerTransport(), "Q", ...)`はWakeLink汎用テストのため残す)。

---

### Task B: 残りコマンドのGUI化 (`WakeSetup`拡張、窓は増やさない)

対象 (現状のC取込/L一覧/?状態/B再生に足す): `P`疎通、`?`詳細 (現状`st/saved/color`のみ→`usb`行も取得)、`W`表示+`W 0/1`切替、`D`/`M`表示切替、`X`破棄、`K`鍵削除。`S/N`はController/Keyboardで済み、`O`色は`set_procon_color`コマンドが既存のため重複実装せず窓内に誘導文のみ置く。

**Files:**
- Modify: `SerialController/WakeSetup.py` のみ (行追加。`Menubar.py`変更なし、`core/`/`services/`変更なし)

**Interfaces:**
- Consumes: `Commands.WakeLink`の`drain/query/send_line/read_lines` (既存importのまま)、`threading`+`queue.Queue`+`after(120)`ポンプ (既存)。
- Produces: ボタン操作→作業スレッド→`self._queue.put(("log", ...))`→GUIスレッド表示。破壊操作のみGUIスレッドで`messagebox.askquestion`確認後に投入。

- [ ] **Step 1: `?`取得に`usb`行を追加**

```python
found = query(transport, "?", ("st ", "saved ", "color ", "usb "), timeout=3.0)
```

- [ ] **Step 2: 疎通・表示系ボタンを追加 (`P`, `W表示`, `D`, `M`)**

```python
def _on_ping(self) -> None:
    def job() -> None:
        transport = self._transport()
        if transport is None:
            return
        found = query(transport, "P", ("PONG",), timeout=2.0)
        self._queue.put(("log", "PONG: 疎通OK" if found else "応答がありません。"))
    self._run(job)
```

`D`/`M`は`query(transport, "D", ("hci verbose ",), ...)` / `query(transport, "M", ("monitor ",), ...)`で返却1行をそのままログへ (トグルなので状態保持しない)。`W`表示は`query(transport, "W", ("usb ",), timeout=2.0)`。

- [ ] **Step 3: `W 0/1`切替 (確認あり)**

GUIスレッドで`messagebox.askquestion` → yesのみ作業スレッドで`query(transport, "W 1", ("usb ",), timeout=3.0)` (または`"W 0"`) し、`usb`応答行をログへ。`store_wired`はファーム側が行うためPC側保存は不要。

- [ ] **Step 4: 破壊操作 `X`/`K` (確認あり・応答の非対称に注意)**

```python
# X: 成功時は無応答、実行中のみ "X ERR BUSY" が返る
found = query(transport, "X", ("X ERR",), timeout=2.0)
self._queue.put(("log", found[0] if found else "破棄しました (応答なしは正常)。"))
# K: 成功時は "link keys deleted. unpair on Switch too"
found = query(transport, "K", ("link keys deleted", "K ERR"), timeout=3.0)
```

- [ ] **Step 5: `_set_busy`対象に新規ボタンを追加 + O誘導文**

既存タプルへ新規ボタンを追加。`O`は実装せず、説明ラベルに「プロコンの色変更はコマンド一覧の『プロコンの色を変える』から (O行)」と1行追記。

- [ ] **Step 6: 手動GUI検証 (headless不可分を明記)**

接続→各ボタン→ログ期待行、busy中の二重起動防止、未接続時の文言、X/K/Wの確認ダイアログ、既存C/L/?/Bの回帰。`task ci`相当も再実行。

---

## Self-Review

1. **Spec coverage:** ファームQ/R不在→Task AでQ公開面を全削除しテストで固定。残りコマンドGUI化→Task Bで`P/?+usb/W/D/M/X/K`を追加し`S/N` (既存操作系) と`O` (既存色コマンド) の重複を避ける。`N`中立は残す。不足なし。
2. **Placeholder scan:** 具体の関数名・応答prefix・タイムアウト値を記載。TBD/TODOなし。
3. **Type consistency:** `query(transport, line, prefixes, timeout)` / `send_line` / `read_lines` / `drain` のシグネチャは`core/WakeLink.py`通り。Task Bは`WakeSetup`内の既存`_transport`/`_run`/`_queue`名と同一。
