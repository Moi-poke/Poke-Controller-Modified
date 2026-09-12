# Live入力スケジューラ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pico live経路を実コントローラー模倣にする（8ms毎slot送出＋最低16ms保持＋短押下の自動延長）。

**Architecture:** 新設`core/serial/live_scheduler.py`（仮想時刻で検証可能な純粋ロジック＋自前ロック）がエッジ列とdwell門を持つ。`Sender`のmailbox（容量1・上書き）はスケジューラへ置換し、workerは毎slot `advance→current→send` するだけにする。設定は`config.py`既定＋`Settings`＋`SenderSpec`の既存配線に1キーを足す。

**Tech Stack:** Python 3.12 / threading.Event 8ms worker / pyserial越しS行 / pytest, ruff, mypy, bounds, userapi

**Spec:** `docs/superpowers/specs/2026-09-12-live-input-scheduler-design.md`

## Global Constraints

- `SerialController/`を`sys.path`前提の絶対import、相対import禁止。
- `core/`はtkinter・アプリ層import禁止、`services/`はtkinter・画面部品import禁止、`ui/`は`Window` import禁止（`task bounds`で強制）。
- `Commands.*`の公開APIパス（`Keys`/`PythonCommandBase`/`McuCommandBase`/`WakeLink`/`CommandVision`/`CommandAudio`）は凍結（`task userapi`）。
- コメントと利用者向け文字列は日本語。4-space indent。`X | None`・builtin generics可（floor 3.12）。
- GUIログは`print`→LogPane経路のみ重要操作に使い、通常はファイル`logger`。ワーカースレッドからwidgetを触らない。
- 既存ini書式は凍結（新キーは既定＋backfillのみ）。`Transport`/`Sender`公開面の名前・引数を壊さない。
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `pytest` が全緑（ベースライン 470 passed / 1 skipped）。
- コミットはユーザーの明示指示があるときのみ（計画書内のcommit手順は実行しない）。

---

## File Structure

- Create: `SerialController/core/serial/live_scheduler.py` — エッジ列・dwell門・stick畳み・統計を持つ`LiveScheduler`（Task 1）。
- Create: `tests/test_live_scheduler.py` — 仮想時刻の単体テスト（Task 1）。
- Modify: `SerialController/core/serial/sender.py` — live区画のみ（`LIVE_SLOT_S`付近〜`clearLiveStats`、現行1494〜1870行）。mailbox→scheduler、毎slot再送、`setLiveMinDwell`追加（Task 2）。
- Modify: `tests/test_live_pico.py` — 既存テスト維持＋dwell/repeatの追加テスト（Task 2）。
- Modify: `SerialController/config.py` — `Transport`既定へ`live_min_dwell_ms: 16`＋補正（Task 3）。
- Modify: `SerialController/Settings.py` — tk変数＋保存辞書（Task 3）。
- Modify: `SerialController/services/serial_service.py` — `SenderSpec`へ1項目＋`build_sender`で適用（Task 3）。
- Modify: `SerialController/ui/serial_panel.py` — `_sender_spec`で1項目受け渡し（Task 3）。
- Modify: `tests/test_config.py` — dwell補正の追加テスト（Task 3）。
- Create: `tools/pico_wire_log.py` — 実線ワイヤロガー（Task 4、pytest収集対象外の`tools/`配置）。

各タスクは独立にテスト可能な成果物で終わる。Task 2はTask 1の`LiveScheduler`を使う。Task 3はTask 2の`setLiveMinDwell`を使う。Task 4はTask 2の完成品を実線で測る。

---

### Task 1: LiveScheduler 本体＋単体テスト

**Files:**
- Create: `SerialController/core/serial/live_scheduler.py`
- Test: `tests/test_live_scheduler.py`

**Interfaces:**
- Consumes: なし（新規。`threading`・`time`以外のimportなし）。
- Produces: `core.serial.live_scheduler.LiveScheduler`（`__init__(slot_s=0.008, min_dwell_s=0.016, capacity=32)`、`push(snap)`、`advance(now)`、`current()`、`clear()`、`pending_empty()`、`stats()`、`set_min_dwell(seconds)`、`reset_stats()`）。`reset_stats()`はTask 2で追加した統計リセット（put/merged/droppedを0へ）。`snap`は`btn/hat/lx/ly/rx/ry/revision`を持つdict。Task 2が`Sender`から使う。

- [ ] **Step 1: Write the failing test**

```python
"""LiveScheduler の検証。仮想時刻を通すため実スレッドも実線も要らない。"""

from core.serial.live_scheduler import LiveScheduler


def _snap(btn: int = 0, rev: int = 0) -> dict:
    return {"btn": btn, "hat": 8, "lx": 128, "ly": 128, "rx": 128, "ry": 128, "revision": rev}


def test_short_press_is_extended_to_min_dwell() -> None:
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(btn=4, rev=1))  # t=0 press
    sched.advance(0.0)
    assert sched.current()["btn"] == 4
    sched.push(_snap(btn=0, rev=2))  # t=0.008 release（dwell未満）
    sched.advance(0.008)
    assert sched.current()["btn"] == 4  # まだpressを保持
    sched.advance(0.016)
    assert sched.current()["btn"] == 0  # ここでreleaseへ進む


def test_long_press_is_not_extended() -> None:
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(btn=4, rev=1))
    sched.advance(0.0)
    sched.push(_snap(btn=0, rev=2))
    sched.advance(0.100)
    assert sched.current()["btn"] == 0
    assert sched.pending_empty()


def test_stick_only_merges_button_edge_never_drops() -> None:
    sched = LiveScheduler()
    sched.push(_snap(btn=4, rev=1))
    sched.push(dict(_snap(btn=4, rev=2), lx=200))  # btn同値→畳む
    sched.push(_snap(btn=0, rev=3))  # btn変化→残す
    assert sched.stats()["merged"] == 1
    sched.advance(0.0)
    assert sched.current()["lx"] == 200  # 畳まれた最新値が出る
    sched.advance(1.0)
    assert sched.current()["btn"] == 0  # releaseは消えていない


def test_overflow_counts_dropped() -> None:
    sched = LiveScheduler(capacity=2)
    sched.push(_snap(btn=1, rev=1))
    sched.push(_snap(btn=2, rev=2))
    sched.push(_snap(btn=3, rev=3))  # 最古が捨てられる
    assert sched.stats()["dropped"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_live_scheduler.py -q`
Expected: FAIL with "No module named 'core.serial.live_scheduler'"（collectエラー）。

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_scheduler.py - Pico live送出の時刻門（エッジ列＋最低保持）。

mailbox（容量1・上書き）の置換である。上書きは8ms未満の押下を
消すため、エッジは列に溜め、送出開始から最低保持秒が経つまで
次のエッジへ進めない。短い押下は押下側が延長される（要求より
短くは絶対にしない）。スティックのみの差分は畳んでよい
（legacyの間引きと同じ基準。ボタンのエッジは絶対に捨てない）。

時計を持たない。時刻は引数でもらう（検証は仮想時刻を通す）。
排他は自前ロックで行う（workerと申告スレッドが同時に触る）。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class LiveScheduler:
    """送出する状態の順序と最低保持を守る門。"""

    def __init__(
        self,
        slot_s: float = 0.008,
        min_dwell_s: float = 0.016,
        capacity: int = 32,
    ) -> None:
        self._slot_s = float(slot_s)
        self._min_dwell_s = float(min_dwell_s)
        self._pending: deque[dict[str, Any]] = deque()
        self._capacity = max(1, int(capacity))
        self._current: dict[str, Any] | None = None
        self._current_sent_at: float | None = None
        self._put = 0
        self._merged = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @staticmethod
    def _stick_only(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
        """btnとhatが等しければスティックのみの差分とみなす。"""
        try:
            return int(prev["btn"]) == int(nxt["btn"]) and int(prev["hat"]) == int(
                nxt["hat"]
            )
        except (KeyError, TypeError, ValueError):
            return False

    def push(self, snap: dict[str, Any]) -> None:
        """1エッジを列へ積む。捨てずに数える（溢れは最古を捨て計数）。"""
        with self._lock:
            self._put += 1
            if self._pending and self._stick_only(self._pending[-1], snap):
                self._pending[-1] = dict(snap)
                self._merged += 1
                return
            if len(self._pending) >= self._capacity:
                self._pending.popleft()
                self._dropped += 1
            self._pending.append(dict(snap))

    def advance(self, now: float) -> None:
        """dwell満了なら最古を送出中へ進める。満了前は何もしない。"""
        with self._lock:
            if not self._pending:
                return
            if (
                self._current is not None
                and self._current_sent_at is not None
                and float(now) - self._current_sent_at < self._min_dwell_s
            ):
                return
            self._current = self._pending.popleft()
            self._current_sent_at = float(now)

    def current(self) -> dict[str, Any] | None:
        """いま送出すべき状態。無ければNone（workerは送らない）。"""
        with self._lock:
            return None if self._current is None else dict(self._current)

    def clear(self) -> bool:
        """未送出を破棄する。破棄物があればTrue（discardLive用）。"""
        with self._lock:
            had = bool(self._pending)
            self._pending.clear()
            return had

    def pending_empty(self) -> bool:
        with self._lock:
            return not self._pending

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"put": self._put, "merged": self._merged, "dropped": self._dropped}

    def set_min_dwell(self, seconds: float) -> None:
        """最低保持を変える。8ms〜64ms以外は無視（現状維持）。"""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if not 0.008 <= value <= 0.064:
            return
        with self._lock:
            self._min_dwell_s = value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_live_scheduler.py -q`
Expected: PASS（4 passed）。

- [ ] **Step 5: Run gates for touched scope**

Run: `uv run --frozen ruff check SerialController/core/serial/live_scheduler.py tests/test_live_scheduler.py` / `uv run --frozen ruff format --check SerialController/core/serial/live_scheduler.py tests/test_live_scheduler.py` / `uv run --frozen mypy SerialController/core/serial/live_scheduler.py tests/test_live_scheduler.py`
Expected: 全PASS。

---

### Task 2: Sender live経路の接続（mailbox→scheduler、毎slot再送）

**Files:**
- Modify: `SerialController/core/serial/sender.py`（live区画のみ。`LIVE_SLOT_S`定義〜`clearLiveStats`）
- Test: `tests/test_live_pico.py`（既存維持＋追加）

**Interfaces:**
- Consumes: Task 1の`LiveScheduler`（`from core.serial.live_scheduler import LiveScheduler`。`core/`内相互参照は`core.*`絶対importの規則通り）。
- Produces: `Sender.setLiveMinDwell(ms: int) -> None`（Task 3が呼ぶ）。`putLive/takeLive/discardLive/waitLiveDrained/getLiveStats/clearLiveStats/startLiveWorker/stopLiveWorker`の名前・引数は不変。

- [ ] **Step 1: Write the failing test**（`tests/test_live_pico.py`へ追加）

```python
def test_short_press_reaches_wire_with_min_dwell() -> None:
    sender, fake = make_live_sender()
    try:
        sender.setLiveMinDwell(16)
        sender.pressButtons([Button.A])
        sender.releaseButtons([Button.A])  # 直後に離しても潰れない
        assert wait_for(lambda: len(fake.written) >= 3, timeout=2.0)
        # pressの送出は中立のdwell待ちで3行目になるため、後続行も待つ。
        assert wait_for(
            lambda: "S 4 8 80 80 80 80" in rows(fake)
            and rows(fake).index("S 4 8 80 80 80 80") < len(rows(fake)) - 1,
            timeout=2.0,
        )
        sent = rows(fake)
        assert "S 4 8 80 80 80 80" in sent  # pressがワイヤに出た
        assert sent.index("S 4 8 80 80 80 80") < len(sent) - 1  # 単発で終わらない
    finally:
        sender.stopLiveWorker(1.0)


def test_idle_repeats_every_slot() -> None:
    sender, fake = make_live_sender()
    try:
        assert wait_for(lambda: len(fake.written) >= 3, timeout=2.0)
        assert all(r.startswith("S ") for r in rows(fake))
    finally:
        sender.stopLiveWorker(1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_live_pico.py -q`
Expected: FAIL（`setLiveMinDwell`が無いためAttributeError、repeatが無いため行数不足）。

- [ ] **Step 3: Write minimal implementation**（`sender.py`のlive区画のみを書き換える）

1. 先頭importへ1行追加: `from core.serial.live_scheduler import LiveScheduler`。
2. 定数: `LIVE_KEEPALIVE_S = 0.088`を残すが使用停止ではなく削除する（参照が無くなるため）。`CLOSE_DRAIN_S = 0.05`を`0.10`へ（中立がdwell待ち最大16msのため）。
3. `_ensureLiveState`: `_live_box`の代わりに`self._live_sched`（無ければ`LiveScheduler(min_dwell_s=self._live_min_dwell_s)`）を持つ。`__init__`直後（`self._transport_gen = 0`の次）に`self._live_min_dwell_s = 0.016`を置く。統計dictへ`"dropped": 0`を追加。
4. `putLive`: mailbox上書きをやめ`snap`を`sched.push`へ。`_live_closing`判定は維持。`put/priority`計数は維持。`replaced`計数はやめる（統計は`sched.stats()["merged"]`を読む）。
5. `takeLive`: 互換のため残し、中身は`sched`経由の取出し相当にする（`_liveLoop`からは呼ばない）。
6. `_liveLoop`: takeLive＋keepalive分岐をやめ、毎slot次を行う。

```python
now = time.perf_counter()
self._live_sched.advance(now)
out = self._live_sched.current()
if out is None:
    continue
```

送出部は従来通り`transport.send_row(self.encodePicoState(out), ...)`。`_live_last_snap`更新・`_recordLiveOrder(out)`・`sent`計数は維持し、同revisionの再送なら`keepalive`計数を増やす（repeatの意味に更新）。`_showLiveRow(snap, now, is_repeat)`の第3引数を改名する（呼び出しは位置引数のため壊れない）。
7. `discardLive`: `sched.clear()`へ。`waitLiveDrained`: `pending_empty()`待ちへ（worker停止中はFalse）。`getLiveStats`: `put/merged→replaced/sent/keepalive(repeat)/priority/dropped/last_revision/inversions`を返す。`clearLiveStats`: `dropped`も初期化し、`sched.reset_stats()`（`LiveScheduler`へ追加する統計リセット。put/merged/droppedを0へ）を呼ぶ。
8. 追加メソッド（調停の`setArbitration`の隣に置く）:

```python
def setLiveMinDwell(self, ms: int) -> None:
    """live送出の最低保持を変える。8〜64ms以外は無視する。"""
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return
    if not 8 <= value <= 64:
        return
    self._live_min_dwell_s = value / 1000.0
    sched = getattr(self, "_live_sched", None)
    if sched is not None:
        sched.set_min_dwell(value / 1000.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_live_pico.py tests/test_live_scheduler.py -q`
Expected: PASS。

- [ ] **Step 5: Run gates for touched scope**

Run: `uv run --frozen ruff check SerialController/core/serial/sender.py tests/test_live_pico.py` / `uv run --frozen ruff format --check SerialController/core/serial/sender.py tests/test_live_pico.py` / `uv run --frozen mypy SerialController/core/serial/sender.py` / `uv run --frozen python tools/check_core.py`
Expected: 全PASS。

---

### Task 3: 設定キー＋配線（dwell値のini→Sender）

**Files:**
- Modify: `SerialController/config.py`（`Transport`既定＋補正）
- Modify: `SerialController/Settings.py`（tk変数＋保存）
- Modify: `SerialController/services/serial_service.py`（`SenderSpec`＋適用）
- Modify: `SerialController/ui/serial_panel.py`（`_sender_spec`）
- Test: `tests/test_config.py`（追加）

**Interfaces:**
- Consumes: Task 2の`Sender.setLiveMinDwell`。
- Produces: iniキー`Transport.live_min_dwell_ms`（既定`"16"`、範囲8〜64）。画面欄は作らない（値はini＋既定のみ。設計書§6対象外通り）。

- [ ] **Step 1: Write the failing test**（`tests/test_config.py`へ追加）

```python
def test_live_min_dwell_backfill_and_clamp() -> None:
    parser = make_parser()
    config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    # 空からの生成は区画名で報告する既存仕様のため、欠落の補完は
    # キー削除で確かめる（値が既定へ戻り、箇所が報告されること）
    del parser["Transport"]["live_min_dwell_ms"]
    changed = config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    assert "Transport.live_min_dwell_ms" in changed
    assert config.complete_missing(parser) == []  # 既定は安定（round-trip不変）
    parser["Transport"]["live_min_dwell_ms"] = "5"
    config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    parser["Transport"]["live_min_dwell_ms"] = "abc"
    config.complete_missing(parser)
    assert parser["Transport"]["live_min_dwell_ms"] == "16"
    parser["Transport"]["live_min_dwell_ms"] = "24"
    assert config.complete_missing(parser) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_config.py -q`
Expected: FAIL（KeyError: 'live_min_dwell_ms'）。

- [ ] **Step 3: Write minimal implementation**

`config.py`の`default_sections()`の`"Transport"`辞書へ1行追加:

```python
"live_min_dwell_ms": 16,
```

`complete_missing()`の`Transport`ブロック（現行230〜234行）へ補正を足す:

```python
try:
    dwell = int(transport.get("live_min_dwell_ms", ""))
except (TypeError, ValueError):
    dwell = None
if dwell is None or not 8 <= dwell <= 64:
    transport["live_min_dwell_ms"] = "16"
    changed.append("Transport.live_min_dwell_ms")
```

`Settings.py`のtransport区画（現行100〜107行）へ追加:

```python
self.live_min_dwell_ms = tk.IntVar(
    value=transport.getint("live_min_dwell_ms", fallback=16)
)
```

保存辞書（現行340〜343行）へ1行追加（`str()`で包む。ini辞書は`str:str`のためmypy制約。ini上の値は`"16"`で同一）:

```python
"live_min_dwell_ms": str(self.live_min_dwell_ms.get()),
```

`serial_service.py`の`SenderSpec`（現行37〜48行）へ1項目追加:

```python
live_min_dwell_ms: int
```

`build_sender`（現行149〜151行）の`setArbitration`の直後へ追加:

```python
self.sender.setLiveMinDwell(spec.live_min_dwell_ms)
```

`serial_panel.py`の`_sender_spec`（現行271〜280行）へ1項目追加:

```python
live_min_dwell_ms=self.settings.live_min_dwell_ms.get(),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_config.py tests/test_serial_service.py tests/test_services_review.py -q`
Expected: PASS。`test_serial_service.py`は`SenderSpec(...)`を位置引数で作っている箇所があれば新項目で更新する（実行エラーの内容に従い最小修正）。

- [ ] **Step 5: Run gates for touched scope**

Run: `uv run --frozen ruff check SerialController/config.py SerialController/Settings.py SerialController/services/serial_service.py SerialController/ui/serial_panel.py tests/test_config.py` / `uv run --frozen ruff format --check ...`（同集合） / `uv run --frozen mypy SerialController/config.py SerialController/Settings.py SerialController/services/serial_service.py SerialController/ui/serial_panel.py` / `uv run --frozen python tools/check_core.py` / `uv run --frozen python tools/check_user_api.py`
Expected: 全PASS。

---

### Task 4: 実線ワイヤロガー（検証用ツール）

**Files:**
- Create: `tools/pico_wire_log.py`

**Interfaces:**
- Consumes: Task 2完成品（`Sender`＋`PicoUartTransport`）。既存`tools/pico_probe.py`の`BTN`表・`send_state`書式・`drain`作法を踏襲する。
- Produces: 実行のみの道具（importされない）。`log/pico_wire_<時刻>.csv`へ`時刻,S行,press/release判定`を書く。pytest収集対象外。

- [ ] **Step 1: Write the tool**（検証道具のためTDDの失敗テストは書かない。仕様は固定）

```python
"""実線のS行を時刻付きで記録する（入力抜けの切り分け用）。

使い方:
    uv run --frozen python tools/pico_wire_log.py --port COM3 --duration 0.016 --times 100

press dwell＝press行→release行の間隔を測り、press行なしでrelease行が
来た回数（ワイヤ抜け）を数える。Switch本体の入力テスト画面と併用し、
ワイヤOK／表示NGを分離する。
"""
```

実装要点: `serial.Serial(port, 115200, timeout=0.5)`を開き、`P`疎通後に指定`duration`のpress/releaseを指定回数送る。各`S`行送出の直前直後に`time.perf_counter()`を取りCSVへ書く。終了後は`N`で中立化する（`pico_probe.py:104-108`と同一）。受信は`read_all`で捨てつつ`ERR`だけ表示する。

- [ ] **Step 2: Dry-run without hardware**（ハード無しで落ち方が正しいことを確認）

Run: `uv run --frozen python tools/pico_wire_log.py --port COM_NOT_EXIST --times 1`
Expected: 例外のtracebackではなく理由の1行を出して終了コード非0（`TextSerialTransport._open_serial`と同一作法: `print`＋`logger.error`して`False`相当で抜ける）。

- [ ] **Step 3: Run gates**

Run: `uv run --frozen ruff check tools/pico_wire_log.py` / `uv run --frozen ruff format --check tools/pico_wire_log.py` / `uv run --frozen mypy tools/pico_wire_log.py`
Expected: 全PASS。

---

### Task 5: 全体ゲート＋回帰

**Files:** なし（検証のみ）。

- [ ] **Step 1: Run full gates**

Run: `uv run --frozen ruff check SerialController tools tests` / `uv run --frozen ruff format --check SerialController tools tests` / `uv run --frozen mypy SerialController tools tests` / `uv run --frozen python tools/check_core.py` / `uv run --frozen python tools/check_user_api.py` / `uv run --frozen pytest tests -q`
Expected: 全PASS（470 passed / 1 skipped 以上。新規テストぶん増える）。

- [ ] **Step 2: 実機計測の手順書通りの実行**（ハードがある環境で実施。無ければ明記して残す）

1. `tools/pico_wire_log.py --duration 0.008/0.016/0.032/0.100 --times 100`を各条件で実行し、ワイヤdwellヒストグラム＋未送出率を記録する。
2. Switch本体のボタン入力テスト画面で同条件を目視計数し、ワイヤOK／表示NGを分離する。
3. 受け入れ: ワイヤ抜け率`<1%/100試行@16ms`、8ms要求のワイヤdwell`>=16ms`。

---

## Self-Review

1. **Spec coverage:** §3.2→Task 1、§3.3/§3.6/§3.7→Task 2、§3.5→Task 3、§5実機→Task 4/5。§6対象外（FW・legacy・GUI欄）に触れるタスクなし。
2. **Placeholder scan:** 各ステップに実コード・実行コマンド・期待結果を記載。TBD/TODOなし。「適宜」等の曖昧語なし。
3. **Type consistency:** `LiveScheduler`の7メソッド名はTask 1定義とTask 2使用で一致。`setLiveMinDwell(ms: int)`はTask 2定義とTask 3呼び出しで一致。`SenderSpec.live_min_dwell_ms: int`はservice定義とpanel受け渡しで一致。統計キー（put/replaced/sent/keepalive/priority/dropped/last_revision/inversions）はTask 2内で閉じる。
