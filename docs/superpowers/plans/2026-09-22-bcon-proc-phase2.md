# bcon送出路の別プロセス化 (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Note:** sub-agent dispatch is disabled in this environment — execute inline.

**Goal:** 120Hz STATE送出をGUIプロセスから分離し、p99ジッタを改善する。

**Architecture:** 子プロセスがシリアルポート・RXポンプ・live loop・会話opを所持する。親は `BconProcTransport` プロキシ経由で同一操作面を使う。フレーム化・SEQ（第2区画）は親に残し、送信ループ（第3区画）だけ移す。Sender・BconTransport本体は無改変。

**Tech Stack:** Python 3.12 multiprocessing (spawn), Pickle IPC (Pipe/Queue), pyserial (child side), tkinter-free core only.

**Spec:** `C:\Users\moilo\AppData\Local\Temp\opencode\ulw-bcon-proc.md` (scenarios S1-S3 are binding)

## Global Constraints

- Python floor 3.12 (`X | None`, builtin generics). 4-space indent. Japanese user-visible strings.
- `core/**` tkinter禁止・アプリ層import禁止 (`task bounds`). `services/**` tkinter禁止.
- 利用者スクリプト公開API面の変更なし (`task userapi`).
- 例外は投げない・輸送系規律（False/None＋可視log）。既存テスト全緑を保つ。
- Windows spawn：子は `core.transport.bcon_proc_worker` のモジュール関数。`freeze_support` 不要（source実行）。daemon=True＋明示terminateの二重 orphan 対策。
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `pytest tests -q`.

---

## File map

- Create: `SerialController/core/transport/bcon_proc.py` — proxy `BconProcTransport`＋子入口＋IPC定義（唯一の新規実装）。
- Modify: `SerialController/core/transport/registry.py` — `switch-bcon-proc` 登録（`replace` 不要の新規キー）。
- Modify: `SerialController/core/transport/bcon.py` — `send_config(type, payload) -> bool` 追加のみ（`_send_session_frame` の薄い公開口）。
- Modify: `SerialController/BconSetup.py` — `send_config_frame` を公開口優先に変更（旧経路は fallback）。
- Modify: `SerialController/ui/serial_panel.py` — `_bcon_transport` を capability 判定に拡張。
- Create: `tests/test_bcon_proc.py` — fake-worker full stack＋spawn smoke＋crash。
- Modify: `docs/BCON_TRANSPORT.md` — proc preset 追記。

---

### Task 1: IPCプロトコルと子ワーカーの骨格

**Files:**
- Create: `SerialController/core/transport/bcon_proc.py`
- Test: `tests/test_bcon_proc.py`

**Interfaces:**
- Consumes: `BconTransport` (実体), `BconParser`/`frame_build` (不要・子が持つ), `multiprocessing.get_context("spawn")`
- Produces: `_Cmd`/`_Evt` メッセージ形、`_worker_main(cmd_q, evt_q, cfg)`、`BconProcTransport`

メッセージ形（全てpicklable）:
- cmd: `("open", portNum, portName, baudrate)` / `("close",)` / `("stop",)` / `("call", call_id, method, args_tuple)`
- evt: `("rx", ftype, payload_bytes, seq)` / `("reply", call_id, ok, result)` / `("hook", kind, row, show)` / `("log", text)`

- [ ] **Step 1: Write the failing test**

```python
def test_proc_proxy_opens_and_closes_with_fake_worker():
    from core.transport import bcon_proc

    made = bcon_proc.BconProcTransport(_spawn=_FakeSpawn)
    assert made.open(3, "COM3", 1000000) is True
    assert made.is_open() is True
    made.close()
    assert made.is_open() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_proc.py::test_proc_proxy_opens_and_closes_with_fake_worker -q`
Expected: FAIL with "cannot import name 'bcon_proc'" (ImportError — right reason, not syntax)

- [ ] **Step 3: Write minimal implementation**

`BconProcTransport`: `name = "switch-bcon-proc"`, `capability = BCON_STATE`。`_spawn` 既定は本物のspawn、テストは偽を注入。cmd/evt/reply queue、dispatchスレッド（evt→購読・print・reply解決）。`open` はspawn＋RPC。`close` はRPC close＋terminate＋join。`sys.stdout` 転送は子側で行う（親は通常）。

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest. Expected: PASS

- [ ] **Step 5: Run gate subset**

Run: `uv run --frozen ruff check SerialController/core/transport/bcon_proc.py tests/test_bcon_proc.py && uv run --frozen mypy SerialController/core/transport/bcon_proc.py`
Expected: clean

### Task 2: 会話opのRPC化（hello/ping/status/beacon/wired/emulate/baud/config）

**Files:**
- Modify: `SerialController/core/transport/bcon_proc.py`
- Test: `tests/test_bcon_proc.py`

**Interfaces:**
- Consumes: Task 1 の `("call", ...)` / `("reply", ...)` 経路
- Produces: proxy上の `hello/ping/request_status/last_status/last_player_info/baud_hunt/set_baud_index/set_wired_mode/set_emulate_mode/send_beacon/send_config/start_live_loop/stop_live_loop/live_loop_running/live_stats/is_open`（引数・戻りは本家と同一形）

- [ ] **Step 1: Write the failing test**

```python
def test_proc_session_ops_round_trip():
    made = _open_proc_with_fake_ser()
    assert made.hello(timeout=0.5) is True
    assert made.send_beacon(timeout=0.5) is not None
    assert made.live_stats()["sent"] >= 0
```

(fake worker内で本物の `BconTransport`＋ScriptedSerを動かし、HELLO_ACK/STATUSを自動応答させる)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_proc.py::test_proc_session_ops_round_trip -q`
Expected: FAIL with AttributeError (method missing — right reason)

- [ ] **Step 3: Write minimal implementation**

子側：`getattr(transport, method)(*args)` を実行し結果をreply（例外は `(False, {"error": repr})` 化）。`send_row`/`flush_pending` はRPC化せず子へ直接積む専用cmd（`("stage", row, measure)`）とし、120Hz loopのevent起床へ載せる。`subscribe_rx` は親側登録＋子は全frame転送。`set_hooks` は親側保持＋子hook転送。`add_listener` は `False`（本家同値）。`get_raw_serial` は `None`（本家同値）。`print` は子でqueue転送。

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest. Expected: PASS

- [ ] **Step 5: Commit** — Skip (no-commit policy this session; leave uncommitted on branch `feat/ulw-v40-gui-bcon-loop`)

### Task 3: 本家公開口の追加と呼び側の移行

**Files:**
- Modify: `SerialController/core/transport/bcon.py` (`send_config` 追加)
- Modify: `SerialController/BconSetup.py` (`send_config_frame` 公開口優先)
- Modify: `SerialController/ui/serial_panel.py` (`_bcon_transport` capability判定)
- Modify: `SerialController/core/transport/registry.py` (preset登録)
- Test: `tests/test_bcon_proc.py`, `tests/test_bcon_setup.py`

**Interfaces:**
- Consumes: Task 2
- Produces: GUIから `switch-bcon-proc` を選べる状態

- [ ] **Step 1: Write the failing test**

```python
def test_send_config_public_path():
    from core.transport import create_transport

    made = create_transport("switch-bcon")
    assert made.send_config(0x35, b"") in (True, False)  # 口の存在確認
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_proc.py::test_send_config_public_path -q`
Expected: FAIL with AttributeError

- [ ] **Step 3: Write minimal implementation**

`BconTransport.send_config`（`_send_session_frame` の薄い公開口）。`send_config_frame` は `getattr(transport, "send_config")` 優先・旧経路fallback。`_bcon_transport` は `name == "bcon" or capability == BCON_STATE`。registryに `switch-bcon-proc` 登録（descriptionに別プロセス送出の旨）。

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest + `tests/test_bcon_setup.py -q`. Expected: PASS

### Task 4: スポーン実機（smoke）＋クラッシュ安全

**Files:**
- Test: `tests/test_bcon_proc.py`

- [ ] **Step 1: Write the failing test**

```python
def test_proc_spawn_and_crash_safe():
    made = BconProcTransport()  # 本物のspawn
    assert made.open(9999, "", 1000000) is False  # 存在しない口で丁寧に失敗
    made.close()  # 二重安全
    made.close()
```

```python
def test_proc_worker_crash_fail_safe():
    made = _open_proc_with_fake_ser()
    made._terminate_worker_for_test()
    assert made.request_status(timeout=0.3) is None
    assert made.send_beacon(timeout=0.3) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_proc.py -q -k "spawn or crash"`
Expected: FAIL (class/method missing)

- [ ] **Step 3: Write minimal implementation**

子engel `timeBeginPeriod(1)`（ctypes防御）。親はRPC前 `is_alive` 確認＋EOF時dead化。`open` はdead時respawn。`close` はterminate＋join（一重・例外握り）。atexitで残骸terminate。

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest. Expected: PASS

### Task 5: 文書＋フルゲート＋HW A/B

**Files:**
- Modify: `docs/BCON_TRANSPORT.md`

- [ ] **Step 1: Run full gate**

Run: `uv run --frozen ruff check SerialController tools tests && uv run --frozen ruff format --check SerialController tools tests && uv run --frozen mypy SerialController tools tests && uv run --frozen python tools/check_core.py && uv run --frozen python tools/check_user_api.py && uv run --frozen pytest tests -q`
Expected: all green (610+ new tests)

- [ ] **Step 2: HW A/B (SURFACE)**

Run: `uv run --frozen python tools/bcon_jitter.py --port COM3 --baud 1000000 --secs 30` (in-proc baseline, GUI停止中) → probe script via `switch-bcon-proc` 同条件 → p50/p99/seq_gap比較＋STATUS差分。bcon1MのTransportを新presetへ（利用者操作）→再起動→タイトル確認。

- [ ] **Step 3: Teardown**

QA資源の後片付け：子プロセス残骸確認（`Get-CimInstance Win32_Process python.exe` にbcon worker無し）、COM3解放、tempスクリプト残置の記録。

## No Placeholders

- RPC対象メソッドは列挙の全件を実装する。`wait_rx` は呼出し系統ゼロのためv1除外。`use_len12` は子へ透過。
- 例外→安全既定値の変換を全RPCに施す（ハング禁止。RPC timeoutはop timeout＋5秒）。

## Self-Review

- [x] Spec coverage: S1 (Task 2+5) / S2 (Task 4) / S3 (Task 5＋既存suite)。_
- [x] Placeholder scan: 具体的メソッド名・コマンド列挙済み。
- [x] Type consistency: proxy戻り形は本家と同一（`bool/None/dict`）。frame形 `BconFrame` を親子で共有（`bcon_protocol` import）。
- Open risk: `open(**extra)` の非picklable混入 → 送信前pickle検査＋丁寧なFalse。
- Open risk: 子のloguru未設定 → print転送＋logging NullHandler方針。
- Open risk: `use_len12` 引数 — proxyは受けて子へ透過（要確認：現行呼出し系統に有無）。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-22-bcon-proc-phase2.md`. Sub-agent dispatch is disabled — executing inline, Test-first per task.
