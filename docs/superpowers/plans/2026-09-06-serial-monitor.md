# シリアルモニタ (全二重コンソール) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pico→PC 方向 (Rx) を通常時に継続監視できる、送受信時系列のシリアルモニタ窓を追加する。

**Architecture:** 受信の所有者を1本の読みポンプに一本化し (`Transport` が所有)、受信行をモニタ表示と `WakeLink` の応答待ちへ分配する。`WakeLink.query/expect` の公開シグネチャは凍結のまま、実体だけ「ポンプ経由→なければ従来の直接読み」に切替える。表示は Tx (既存 `add_listener` 系) と Rx (新規購読) の到着順マージ。

**Tech Stack:** Python 3.12 / tkinter / pyserial / 既存 `LogPane.DropOldestQueue` / pytest, ruff, mypy, bounds, userapi

**Spec:** ユーザー合意 — (1) Tx+Rx 時系列表示を含む、(2) D/M 大量行は既定フィルタあり + 全文はファイル `log/` に残す。窓名・メニュー名は「シリアルモニタ」。プランファイルはコミットしない。

## Global Constraints

- `SerialController/` を `sys.path` 前提の絶対 import、相対 import 禁止。
- `core/` は tkinter・アプリ層 import 禁止、`services/` は tkinter・画面部品 import 禁止 (`task bounds`)。読みポンプのスレッド所有は `core/transport` 内に閉じる。窓はアプリ層 (`SerialController/` 直下、`WakeSetup.py` と同格) に置く。
- `Commands.WakeLink` の公開名 (`drain/expect/query/read_lines/send_line` + `_lock_of/_ser_of`) は凍結 (`task userapi`)。シグネチャ・戻り値の意味を変えない。
- ワーカースレッドから widget を触らない。表示更新は `after` ポンプ経由。受信の取りこぼしは出さない代わりに、表示側は溢れたら古い方を捨てる (`DropOldestQueue` 流用)。
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `pytest` が全緑。実機確認は別途明記。
- コミットはユーザーの明示指示があるときのみ。

---

### Task 1: Rx ポンプと分配 (`core/transport`)

**Files:**
- Modify: `SerialController/core/transport/base.py`
- Modify: `SerialController/core/transport/text_serial.py`
- Test: `tests/test_rx_pump.py` (新規)

**Interfaces:**
- Consumes: 既存 `ser` (pyserial, `timeout=READ_TIMEOUT`)、既存 `_lock` (書き専用のまま)。
- Produces: `subscribe_rx(func) -> 解除用callable` / `wait_rx(prefixes, timeout) -> str | None` / `rx_pump_running() -> bool` / 内部用 `start_rx_pump` / `stop_rx_pump`。

- [ ] **Step 1: Write the failing test**

```python
"""Rxポンプの headless 検証。実機なしで回す。"""

from core import Transport


class ScriptedSer:
    """read() で仕込みバイトを返す偽シリアル。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.is_open = True
        self.written: list[bytes] = []

    def read(self, n: int) -> bytes:
        _ = n
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        self._chunks.clear()


def _pump_transport(chunks: list[bytes]) -> Transport.TextSerialTransport:
    t = Transport.TextSerialTransport()
    t.ser = ScriptedSer(chunks)  # type: ignore[assignment]
    t.start_rx_pump()
    return t


def test_subscriber_receives_lines() -> None:
    t = _pump_transport([b"PONG\r\n", b"st host=1 cid=0"])
    try:
        got: list[str] = []
        unsub = t.subscribe_rx(got.append)
        try:
            assert t.wait_rx(("PONG",), timeout=2.0) == "PONG"
        finally:
            unsub()
        assert "PONG" in got
    finally:
        t.stop_rx_pump()


def test_query_and_monitor_do_not_steal() -> None:
    t = _pump_transport([b"PONG\r\n"])
    try:
        got: list[str] = []
        unsub = t.subscribe_rx(got.append)
        try:
            assert t.wait_rx(("PONG",), timeout=2.0) == "PONG"
        finally:
            unsub()
        assert got == ["PONG"]
    finally:
        t.stop_rx_pump()


def test_base_transport_has_no_rx() -> None:
    from tests.fakes import NoSerTransport

    t = NoSerTransport()
    assert t.rx_pump_running() is False
    assert t.wait_rx(("PONG",), timeout=0.05) is None
    unsub = t.subscribe_rx(lambda line: None)
    unsub()
```

`tests/fakes.py` の import 形式は `from fakes import ...` が既存流儀 (`test_transport.py` 参照)。`tests/` 配下の解決に合わせること。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_rx_pump.py -q`
Expected: FAIL (`start_rx_pump` が無い等の AttributeError)。

- [ ] **Step 3: Write minimal implementation**

`base.Transport` に既定実装5点 (`subscribe_rx` / `wait_rx` / `rx_pump_running` / `start_rx_pump` / `stop_rx_pump`) を追加。既存の抽象5点・`add_listener`・`get_raw_serial`・`acquire_write_lock` は触らない。`text_serial.TextSerialTransport` に実体: `open` 成功でポンプ起動、`close` で停止。ポンプは daemon スレッド1本で `ser.read(64)` → `\n` で割って `strip` → 購読者全員 + 待機中の `wait_rx` へ配送。例外は握ってファイル `logger` へ。読みタイムアウトは既存 `READ_TIMEOUT` (0.5s)、停止は `threading.Event` で最大0.5秒で抜ける。行組み立て規則は `WakeLink.read_lines` と同じ (`\n` 区切り、`strip`、`ascii/errors=replace`)。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_rx_pump.py -q`
Expected: PASS。

- [ ] **Step 5: Run Task 1 gates**

Run: ruff check / format --check / mypy / pytest。
Expected: 全 PASS (既存 53 件 + 新規)。

### Task 2: `WakeLink` をポンプ経由に切替 (互換維持)

**Files:**
- Modify: `SerialController/core/WakeLink.py`
- Test: `tests/test_rx_pump.py` に1件追加 + 既存 `test_transport.py::test_wakelink_without_serial` を維持

**Interfaces:**
- Consumes: Task 1 の `wait_rx` / `subscribe_rx` / `get_raw_serial` / `acquire_write_lock`。
- Produces: 変更なし (`drain/expect/query/read_lines/send_line` のシグネチャ・戻り値の意味は不変)。

- [ ] **Step 1: Write the failing test**

```python
def test_expect_prefers_pump_when_available() -> None:
    from core import WakeLink

    t = _pump_transport([b"color 313131 0f0f0f 0ab9e6 ff3c28\r\n"])
    try:
        assert (
            WakeLink.expect(t, "O 313131 0f0f0f 0ab9e6 ff3c28", ("color ",), timeout=2.0)
            == "color 313131 0f0f0f 0ab9e6 ff3c28"
        )
    finally:
        t.stop_rx_pump()
```

注: `WakeLink.expect` は送信に `ser.write` を使う。`ScriptedSer.write` が記録のみで応答を足さない場合、ポンプは起動時の仕込み chunk を読む。テストの前提が合わなければ偽物を足す。`WakeLink` 本体の振る舞い期待は変えない。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_rx_pump.py -q`
Expected: FAIL。

- [ ] **Step 3: Write minimal implementation**

`expect`: `drain` → `send_line` の順序は維持。待ち部分を「`wait_rx` が使える (transport に口があり、かつ `rx_pump_running()` が真) なら `wait_rx`、そうでなければ従来の `ser.read` 直読み」に分岐。`query`: 同じ分岐で期限まで集める部分を切替。prefix の前方一致規則は現状通り。`drain`: ポンプ稼働中は `reset_input_buffer` を呼ばない (ポンプ未処理分まで捨てるため)。ポンプ停止中のみ従来通り。`read_lines`: ポンプ稼働中は購読で期限まで集める。ser 直持ちの非シリアル方式では従来通り False/空。ログ方針は現状維持。

- [ ] **Step 4: Run tests**

Run: `uv run --frozen pytest tests -q`
Expected: 全 PASS。

### Task 3: モニタ窓 (Tx+Rx 時系列、既定フィルタ付き)

**Files:**
- Create: `SerialController/SerialMonitor.py`
- Modify: `SerialController/Menubar.py`

**Interfaces:**
- Consumes: `SerialService.sender.transport` (都度参照)、Tx は `transport.add_listener`、Rx は Task 1 の `subscribe_rx`。
- Produces: 解除用 callable を `close` で必ず呼ぶ。

- [ ] **Step 1: Write the window (no placeholders)**

構成: 1行目に操作段 (開始/停止ボタン、フィルタ `ttk.Combobox` 既定値「通常のみ」、クリア、コピー)、2行目に `tk.Text` (高さ20・幅90・`state=DISABLED`)。行形式: `[HH:MM:SS.mmm] TX> <行>` / `[HH:MM:SS.mmm] RX< <行>`。フィルタ4分類: 「通常のみ」(既定。`mon ` 始まりと `hci ` 含みを畳んで件数表示) / 「全部」/ 「mon のみ」/ 「エラー・状態のみ (`WD`/`ERR`/`st `/`usb `)」。畳んだ行も含め全行をファイル `logger` へ残す。Tx 購読が `False` の方式では Rx のみ + 1行注記。`Menubar` 追加は `wake_setup` と同型。メニューラベルは「シリアルモニタ」。

- [ ] **Step 2: Verify without hardware**

Run: `python -m py_compile` + ruff check / format --check / mypy / bounds / userapi。
Expected: 全 PASS。

- [ ] **Step 3: Run full gates**

`task ci` 相当全緑。

- [ ] **Step 4: Hardware verification (実機のみ)**

P→`TX> P`/`RX< PONG` 時系列表示。M 付けっぱなしで既定フィルタの畳みと「全部」表示。`WakeSetup` 操作中の横取りゼロ。切断→停止表示→再接続で再開。終了時に購読漏れ例外なし。

---

## Self-Review

1. **Spec coverage:** 通常時 Rx 監視→Task 1+3。送受信時系列→Task 3。既定フィルタ+全文ファイル記録→Task 3。クエリ競合解消→Task 1+2。不足なし。
2. **Placeholder scan:** 具体の関数名・表示形式・フィルタ4分類・検証手順を記載。TBD/TODO なし。
3. **Type consistency:** `subscribe_rx` / `wait_rx` / `rx_pump_running` / `start_rx_pump` / `stop_rx_pump` は Task 1 で定義し Task 2・3 で同一名を使用。`WakeLink` 公開6名のシグネチャは不変。`Menubar` の窓管理は `wake_setup` と同型。
