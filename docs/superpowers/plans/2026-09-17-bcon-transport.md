# bcon対応 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PokeCon Modifiedからbcon（Pico 2 W・バイナリ専用）へネイティブ対応し、有線優先で120Hz低遅延送信できるようにする。

**Architecture:** Phase 1は同一プロセスに`BconTransport`（CRC/SEQ/HELLO/BAUD＋独立スレッド120Hz）を追加し計測で固める。Phase 2分離に備え親→子は最新姿勢のみ・子→親はSTATUS/PONG/統計のみの境界で作る。wakecon凍結ファイルには触れず並置新設する。

**Tech Stack:** Python >=3.12（pin 3.12.10）、pyserial、tkinter（UIのみ）、pytest、ruff、mypy、`tools/check_core.py`（bounds）、`tools/check_user_api.py`（userapi）

**Spec:** `docs/superpowers/specs/2026-09-17-bcon-design.md`

## Global Constraints

- Python floorは3.12（`X | None`・builtin generics・`super()`・`type`文・`except*`・`Self`可、Ruff `I`＋`UP006/007/008/035`）。
- 絶対importのみ（`SerialController/`が`sys.path`上にある前提、例`from core.transport.base import Transport`）。相対import禁止。
- 4-space indent。コメントとユーザー可視文字列は日本語で既存に合わせる。
- `core/`はtkinter禁止・アプリ層import禁止、`services/`はtkinter禁止・UI-module import禁止（`task bounds`で強制）。
- `Commands.*`公開面（`Keys`／`PythonCommandBase`／`McuCommandBase`／`WakeLink`／`CommandVision`／`CommandAudio`）は凍結。新規公開面を作らない（`task userapi`）。
- `settings*.ini`書式は凍結。新規設定は`SerialController/config.py`（tkinter-free）に追加し`Settings.GuiSettings`は鏡＋IOのみ。
- Windows優先だがmac/Linuxは落とさない（`Transport.open`不正baud・不良ポートはFalse＋log、例外なし）。
- `Sender`送信間隔の安易な短縮禁止（フロー制御なしのため溢れは取りこぼし）。
- 資源pathは`BASE_DIR`基準（`Window.py`が`os.chdir(BASE_DIR)`済み）。cwd基準禁止。
- 全タスク終了時に`task ci`相当（`ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`test`）が緑であること。
- 1ファイルの破損が他コマンドを巻き込まない（`CommandLoader`のper-module import維持）。コマンドファイルにモジュールレベル副作用を置かない。

---

## File Structure

- 作成: `SerialController/core/transport/bcon_protocol.py` — CRC-8/SMBUS・TYPE/LEN表・`frame_build`・スライディング受信パーサ・SEQ・BAUD表・ERRCODE。Pico/BTstack非依存の純粋層（`C:\pico-bcon\src/proto/protocol.[hc]`のPC側写し）。
- 作成: `SerialController/core/serial/bcon_mapping.py` — Modified行（`<btn-hex> <hat> [lx ly [rx ry]]`＋`end`）→中間姿勢（buttons u32＋sticks u16域0-4095中央0x0800）へのLEN非依存写像。Y反転はここ1箇所。
- 作成: `SerialController/core/transport/bcon.py` — `BconTransport(Transport)`本体。姿勢受付・フレーム化・SEQ・独立スレッド送信ループ（120Hz絶対時刻）・バイナリRXポンプ・HELLO/BAUD状態機械。内部4区画でPhase 2移設可能にする。
- 修正: `SerialController/core/transport/base.py` — `BCON_STATE`定数＋`LIVE_WORKER_CAPABILITIES`へ追加（2-3行）。
- 修正: `SerialController/core/transport/registry.py` — `bcon`のbuiltin登録（説明文付き、3-6行）。
- 修正: `SerialController/core/transport/__init__.py` — 新名の再公開（`BconTransport`・`BCON_STATE`・protocol別名）。
- 修正: `SerialController/core/serial/sender.py` — `_liveCapable`に`BCON_STATE`対応（1行条件追加）。送信周期の本体は`BconTransport`側に寄せ、Sender側の大改造はしない。
- 作成: `SerialController/BconSetup.py` — `WakeSetup.py`並置のbcon設定小窓（CAPTURE/BEACON/COLOR/KEY/WIRED/STATUS_REQ/PING）。`WakeSetup.py`無改変。
- 修正: `SerialController/ui/serial_panel.py` — transport候補表示がregistry経由なら無改変、直書き列挙なら`bcon`追加（調査して最小差分）。
- 修正: `SerialController/config.py` — `Transport.name`コメントに`bcon`を追記（既定値は`legacy_text`のまま）。
- 作成: `tools/bcon_jitter.py` — 送信周期p50/p99・SEQ欠番・STATUS差分の計測器（実機・loopback両用）。
- 作成: `tests/test_bcon_protocol.py`、`tests/test_bcon_mapping.py`、`tests/test_bcon_transport.py`、`tests/test_bcon_session.py`、`tests/test_bcon_live.py` — 上記の順にhostベクタ。

---

### Task 1: bconプロトコル純粋層（CRC・組立・パーサ）

**Files:**
- Create: `SerialController/core/transport/bcon_protocol.py`
- Test: `tests/test_bcon_protocol.py`

**Interfaces:**
- Consumes: なし（`C:\pico-bcon\spec\protocol_v3.md:36-52,161-168`、`src/proto/protocol.h:11-52`、`protocol.c:31-50,78-155`を仕様源にする）。
- Produces: `crc8(data: bytes) -> int`、`frame_build(type: int, payload: bytes, seq: int) -> bytes`、`class BconParser`（`feed(data: bytes) -> list[tuple[int, bytes, int]]`で`(type, payload, seq)`確定分を返す）、`BAUD_TABLE: dict[int, int]`、`proto_expected_len(type: int) -> int`、`proto_state_len_ok(plen: int) -> bool`、`TYPE_*`／`RESULT_*`／`ERR_*`定数。Task 3-4がこれを使う。

- [ ] **Step 1: Write the failing test**

```python
def test_bcon_crc8_vector():
    from core.transport.bcon_protocol import crc8
    assert crc8(b"123456789") == 0xF4


def test_bcon_frame_build_state_neutral():
    from core.transport.bcon_protocol import T_STATE, frame_build
    payload = bytes([0x00, 0x00, 0x00, 0x00, 0x80, 0x80, 0x80, 0x80])
    frame = frame_build(T_STATE, payload, 0x00)
    assert frame[0] == 0xAB
    assert frame[1] == T_STATE
    assert frame[2] == 8
    assert len(frame) == 13


def test_bcon_parser_slides_on_bad_crc():
    from core.transport.bcon_protocol import T_PING, BconParser, frame_build
    good = frame_build(T_PING, b"", 0x05)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    parser = BconParser()
    out = parser.feed(bytes(bad) + good)
    assert len(out) == 1
    assert out[0][0] == T_PING
    assert out[0][2] == 0x05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_protocol.py -q`
Expected: FAIL（`core.transport.bcon_protocol`が無い、`ModuleNotFoundError`）。

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_protocol.py - bconバイナリの組立と受信（純粋層）。

Pico側 `src/proto/protocol.[hc]` のPC側写し。BTstack・TinyUSB・
tkinterに依存しない。仕様は `C:\pico-bcon\spec\protocol_v3.md` が正。
"""

from __future__ import annotations

PROTO_VER = 0x04
PROTO_SYNC = 0xAB
PROTO_MAX_PAYLOAD = 32

T_STATE = 0x01
T_NEUTRAL = 0x02
T_PING = 0x03
T_HELLO = 0x10
T_HELLO_ACK = 0x11
T_STATUS = 0x20
T_PONG = 0x21
T_RUMBLE = 0x22
T_PLAYER_INFO = 0x23
T_CAPTURE_START = 0x30
T_BEACON_START = 0x31
T_COLOR_SET = 0x32
T_KEY_DELETE = 0x33
T_WIRED_MODE = 0x34
T_STATUS_REQ = 0x35
T_BAUD_SET = 0x36

RESULT_OK = 0x00
RESULT_DOWNGRADED = 0x01
RESULT_UNSUPPORTED = 0x02

ERR_OK = 0x00
ERR_BAD_LEN = 0x01
ERR_BAD_CRC = 0x02
ERR_SEQ_GAP = 0x03
ERR_UNSUPPORTED = 0x04
ERR_OVERRUN = 0x05
ERR_OVERFLOW = 0x06

BAUD_TABLE: dict[int, int] = {
    0: 115200,
    1: 460800,
    2: 921600,
    3: 1000000,
    4: 2000000,
}
DEFAULT_BAUD_INDEX = 3

_CRC_TABLE: list[int] = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = ((_c << 1) ^ 0x07) & 0xFF if _c & 0x80 else (_c << 1) & 0xFF
    _CRC_TABLE.append(_c)


def crc8(data: bytes) -> int:
    """CRC-8/SMBUS（poly 0x07・init 0x00）。検査値 `crc8(b"123456789")==0xF4`。"""
    crc = 0x00
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def proto_expected_len(type_: int) -> int:
    """既知型の正確長。未知型は-1（32B上限で可変skip）。STATE正準は8。"""
    table = {
        T_STATE: 8,
        T_NEUTRAL: 0,
        T_PING: 0,
        T_HELLO: 2,
        T_HELLO_ACK: 4,
        T_STATUS: 7,
        T_PONG: 1,
        T_RUMBLE: 2,
        T_PLAYER_INFO: 2,
        T_CAPTURE_START: 1,
        T_BEACON_START: 0,
        T_COLOR_SET: 12,
        T_KEY_DELETE: 0,
        T_WIRED_MODE: 1,
        T_STATUS_REQ: 0,
        T_BAUD_SET: 1,
    }
    return table.get(type_, -1)


def proto_state_len_ok(plen: int) -> bool:
    """STATEは8/12のみ受理し他はERR_BAD_LEN（正準報告は8のまま）。"""
    return plen in (8, 12)


def frame_build(type_: int, payload: bytes, seq: int) -> bytes:
    """1フレームを組む。`len(payload)>32`はValueError（黙って切らない）。"""
    if len(payload) > PROTO_MAX_PAYLOAD:
        raise ValueError(f"payloadが32Bを超えています: {len(payload)}")
    body = bytes([type_ & 0xFF, len(payload) & 0xFF]) + bytes(payload) + bytes([seq & 0xFF])
    return bytes([PROTO_SYNC]) + body + bytes([crc8(body)])


class BconParser:
    """蓄積型パーサ。SYNC整列→LEN検証→CRC検証、不一致は先頭1B前進。

    SYNCはpayload中にも出るため最終判定は必ずCRCで行う。
    SEQの欠番計数は呼び出し側（Transport）が行い、ここは数えない。
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.err_crc = 0
        self.err_overrun = 0

    def feed(self, data: bytes) -> list[tuple[int, bytes, int]]:
        """確定フレームの`(type, payload, seq)`一覧を返す。部分は残す。"""
        self._buf.extend(data)
        out: list[tuple[int, bytes, int]] = []
        while True:
            sync_at = self._buf.find(PROTO_SYNC)
            if sync_at < 0:
                if len(self._buf) > 96:
                    self.err_overrun += 1
                    del self._buf[: len(self._buf) - 96 :]
                else:
                    self._buf.clear()
                break
            if sync_at > 0:
                del self._buf[:sync_at]
            if len(self._buf) < 5:
                break
            type_ = self._buf[1]
            plen = self._buf[2]
            expect = proto_expected_len(type_)
            if expect >= 0:
                if type_ == T_STATE:
                    ok_len = proto_state_len_ok(plen)
                else:
                    ok_len = plen == expect
                if not ok_len:
                    self.err_crc += 1
                    del self._buf[:1]
                    continue
            else:
                if plen > PROTO_MAX_PAYLOAD:
                    self.err_crc += 1
                    del self._buf[:1]
                    continue
            total = plen + 5
            if len(self._buf) < total:
                break
            body = bytes(self._buf[1 : total - 1])
            if crc8(body) != self._buf[total - 1]:
                self.err_crc += 1
                del self._buf[:1]
                continue
            payload = bytes(self._buf[3 : 3 + plen])
            seq = self._buf[3 + plen]
            out.append((type_, payload, seq))
            del self._buf[:total]
        if len(self._buf) > 96:
            self.err_overrun += 1
            del self._buf[: len(self._buf) - 96 :]
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_bcon_protocol.py -q`
Expected: PASS（3件）。

- [ ] **Step 5: 追加ベクタ（HELLO行列・PONG・CONFIG拒否・LEN12・SEQ初回不計数）を同ファイルへ追記し再実行**

```python
def test_bcon_hello_len_and_baud_table():
    from core.transport.bcon_protocol import (
        BAUD_TABLE,
        DEFAULT_BAUD_INDEX,
        T_BAUD_SET,
        T_HELLO,
        T_HELLO_ACK,
        frame_build,
        proto_expected_len,
    )
    assert proto_expected_len(T_HELLO) == 2
    assert proto_expected_len(T_HELLO_ACK) == 4
    assert proto_expected_len(T_BAUD_SET) == 1
    assert BAUD_TABLE[DEFAULT_BAUD_INDEX] == 1000000
    frame = frame_build(T_HELLO, bytes([0x04, 0x01]), 0x07)
    assert len(frame) == 7
```

Run: `uv run --frozen pytest tests/test_bcon_protocol.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add SerialController/core/transport/bcon_protocol.py tests/test_bcon_protocol.py
git commit -m "feat(bcon): add binary frame core with sliding resync parser"
```

### Task 2: Modified行→VIIPER写像（LEN非依存）

**Files:**
- Create: `SerialController/core/serial/bcon_mapping.py`
- Test: `tests/test_bcon_mapping.py`

**Interfaces:**
- Consumes: Task 1の`BTN`ビット定義（値は`C:\pico-bcon\src\proto\protocol.h:54-78`と同値でここに再定義しない。写像表は`2026-09-16-pokecon-compat-design.md §1.3-1.5`）。
- Produces: `wire_row_to_state(row: str) -> dict[str, int]`（`{"buttons": u32, "lx": u16, "ly": u16, "rx": u16, "ry": u16}`、u16域0-4095中央0x0800）、`is_end_row(row: str) -> bool`、`neutral_state() -> dict[str, int]`。Task 3が`send_row`内で使う。LEN8送出時は`u16>>4`相当（`0x800>>4==0x80`）でu8化する。

- [ ] **Step 1: Write the failing test**

```python
def test_bcon_map_a_and_y_hat_and_sticks():
    from core.serial.bcon_mapping import neutral_state, wire_row_to_state
    neutral = neutral_state()
    assert neutral == {"buttons": 0, "lx": 0x0800, "ly": 0x0800, "rx": 0x0800, "ry": 0x0800}
    # A押下はwire 0x10（bit4）→VIIPER BTN_A (1<<1)
    assert wire_row_to_state("10 08")["buttons"] == (1 << 1)
    # Y押下はwire 0x0004（bit2）→VIIPER BTN_Y (1<<2)
    assert wire_row_to_state("0004 08 80 80 80 80")["buttons"] == (1 << 2)
    # HAT 0=Up→BTN_UP (1<<11)
    assert wire_row_to_state("0 0")["buttons"] == (1 << 11)
    # LSのみ→LX/LYへwire lx/ly（u8 0xFF→u16 0x0FF0）
    moved = wire_row_to_state("2 08 ff 80")
    assert (moved["lx"], moved["ly"]) == (0x0FF0, 0x0800)
    assert (moved["rx"], moved["ry"]) == (0x0800, 0x0800)


def test_bcon_map_rs_quirk_and_end():
    from core.serial.bcon_mapping import is_end_row, neutral_state, wire_row_to_state
    # RSのみ→RX/RYへwire lx/lyが載る
    moved = wire_row_to_state("1 08 ff 80")
    assert (moved["rx"], moved["ry"]) == (0x0FF0, 0x0800)
    assert (moved["lx"], moved["ly"]) == (0x0800, 0x0800)
    assert is_end_row("end") is True
    assert is_end_row("END") is True
    assert wire_row_to_state("end") == neutral_state()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_mapping.py -q`
Expected: FAIL（`core.serial.bcon_mapping`が無い）。

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_mapping.py - Modified行からbcon中間姿勢への写像（純関数）。

入力は `<btn-hex> <hat> [lx ly [rx ry]]` と `end`。
出力はLEN非依存の中間姿勢（buttons u32＋sticks u16域0-4095中央0x0800）。
LEN8送出時は `u16>>4` でu8化する（`0x800>>4==0x80`で等価）。
Y反転はここ1箇所に集約する。Pico側は値をそのままpackする。
"""

from __future__ import annotations

BTN_B = 1 << 0
BTN_A = 1 << 1
BTN_Y = 1 << 2
BTN_X = 1 << 3
BTN_R = 1 << 4
BTN_ZR = 1 << 5
BTN_PLUS = 1 << 6
BTN_RSTICK = 1 << 7
BTN_DOWN = 1 << 8
BTN_RIGHT = 1 << 9
BTN_LEFT = 1 << 10
BTN_UP = 1 << 11
BTN_L = 1 << 12
BTN_ZL = 1 << 13
BTN_MINUS = 1 << 14
BTN_LSTICK = 1 << 15
BTN_HOME = 1 << 16
BTN_CAPTURE = 1 << 17

_WIRE_TO_VIIPER = (
    None,
    None,
    BTN_Y,
    BTN_B,
    BTN_A,
    BTN_X,
    BTN_L,
    BTN_R,
    BTN_ZL,
    BTN_ZR,
    BTN_MINUS,
    BTN_PLUS,
    BTN_LSTICK,
    BTN_RSTICK,
    BTN_HOME,
    BTN_CAPTURE,
)

_HAT_TO_BTNS = (
    (BTN_UP,),
    (BTN_UP, BTN_RIGHT),
    (BTN_RIGHT,),
    (BTN_DOWN, BTN_RIGHT),
    (BTN_DOWN,),
    (BTN_DOWN, BTN_LEFT),
    (BTN_LEFT,),
    (BTN_UP, BTN_LEFT),
    (),
)

_CENTER_U16 = 0x0800


def neutral_state() -> dict[str, int]:
    """全解放の中間姿勢。`end`受信と等価。"""
    return {
        "buttons": 0,
        "lx": _CENTER_U16,
        "ly": _CENTER_U16,
        "rx": _CENTER_U16,
        "ry": _CENTER_U16,
    }


def is_end_row(row: str) -> bool:
    """`end`行か。大文字小文字を問わない。前後空白を無視する。"""
    return row.strip().lower() == "end"


def _u8_to_u16(value: int) -> int:
    """wire/LEN8のu8（0-255中央0x80）をu16域（0-4095中央0x0800）へ。"""
    return (max(0, min(0xFF, int(value))) << 4) & 0xFFF


def wire_row_to_state(row: str) -> dict[str, int]:
    """Modified行を中間姿勢へ写す。不正行はValueError（黙って送らない）。

    LS/RS quirkを再現する。LSのみ→LX/LYへwire lx/ly、RSのみ→RX/RYへ
    wire lx/ly、両方→各々、なし→スティック不変ではなく中立維持の呼び出し側
    が保持する。ここでは欄なし行は中央のまま返す。
    """
    text = row.strip()
    if is_end_row(text):
        return neutral_state()
    parts = text.replace("\r", " ").replace("\n", " ").split()
    if len(parts) not in (2, 4, 6):
        raise ValueError(f"bconへ送れない行形式です: {row!r}")
    try:
        wire = int(parts[0], 16)
        hat = int(parts[1], 16 if parts[1].lower().startswith("0x") else 10)
    except ValueError:
        raise ValueError(f"bconへ送れない行形式です: {row!r}") from None
    if not 0 <= wire <= 0xFFFF:
        raise ValueError(f"btnが16bitを超えています: {row!r}")
    buttons = 0
    for bit in range(2, 16):
        if wire & (1 << bit):
            mapped = _WIRE_TO_VIIPER[bit]
            if mapped is not None:
                buttons |= mapped
    if 0 <= hat <= 8:
        for btn in _HAT_TO_BTNS[hat]:
            buttons |= btn
    else:
        for btn in _HAT_TO_BTNS[8]:
            buttons |= btn
    state = neutral_state()
    state["buttons"] = buttons
    if len(parts) >= 4:
        try:
            lx = int(parts[2], 16)
            ly = int(parts[3], 16)
        except ValueError:
            raise ValueError(f"bconへ送れない行形式です: {row!r}") from None
        rs_only = bool(wire & 0x0001) and not bool(wire & 0x0002)
        ls_only = bool(wire & 0x0002) and not bool(wire & 0x0001)
        both = bool(wire & 0x0001) and bool(wire & 0x0002)
        if ls_only or both:
            state["lx"] = _u8_to_u16(lx)
            state["ly"] = _u8_to_u16(ly)
        if rs_only:
            state["rx"] = _u8_to_u16(lx)
            state["ry"] = _u8_to_u16(ly)
        if both and len(parts) == 6:
            try:
                rx = int(parts[4], 16)
                ry = int(parts[5], 16)
            except ValueError:
                raise ValueError(f"bconへ送れない行形式です: {row!r}") from None
            state["rx"] = _u8_to_u16(rx)
            state["ry"] = _u8_to_u16(ry)
    return state


def state_to_len8(state: dict[str, int]) -> bytes:
    """中間姿勢からSTATE LEN8 payload（BTN u32LE＋u8×4）を作る。"""
    buttons = int(state["buttons"]) & 0x003FFFFF
    lx = (int(state["lx"]) >> 4) & 0xFF
    ly = (int(state["ly"]) >> 4) & 0xFF
    rx = (int(state["rx"]) >> 4) & 0xFF
    ry = (int(state["ry"]) >> 4) & 0xFF
    return (
        buttons.to_bytes(4, "little") + bytes([lx, ly, rx, ry])
    )


def state_to_len12(state: dict[str, int]) -> bytes:
    """中間姿勢からSTATE LEN12 payload（BTN u32LE＋u16LE×4）を作る。"""
    buttons = int(state["buttons"]) & 0x003FFFFF
    out = bytearray(buttons.to_bytes(4, "little"))
    for key in ("lx", "ly", "rx", "ry"):
        value = max(0, min(0xFFF, int(state[key])))
        out.extend(value.to_bytes(2, "little"))
    return bytes(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_bcon_mapping.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add SerialController/core/serial/bcon_mapping.py tests/test_bcon_mapping.py
git commit -m "feat(bcon): add LEN-agnostic wire to VIIPER mapper"
```

### Task 3: BconTransport骨格＋登録（120Hz境界つき）

**Files:**
- Create: `SerialController/core/transport/bcon.py`
- Modify: `SerialController/core/transport/base.py`（`BCON_STATE`＋worker集合追加）
- Modify: `SerialController/core/transport/registry.py`（builtin `bcon`登録）
- Modify: `SerialController/core/transport/__init__.py`（再公開）
- Test: `tests/test_bcon_transport.py`

**Interfaces:**
- Consumes: Task 1（`frame_build`・`BconParser`・`BAUD_TABLE`）、Task 2（`wire_row_to_state`・`state_to_len8`・`state_to_len12`）。
- Produces: `class BconTransport(Transport)`（`name="bcon"`、`capability=BCON_STATE`）。`open(portNum, portName="", baudrate=1000000, **extra) -> bool`（既定1M、不正・不良はFalse）。`send_row(row: str)`は行受付→中間姿勢→STATE組立（LEN8既定・LEN12は`use_len12`旗）。`subscribe_rx/wait_rx(TYPE一致)/rx_pump_running/start_rx_pump/stop_rx_pump`。Task 4がHELLO/BAUDを載せる。

- [ ] **Step 1: Write the failing test**

```python
def test_bcon_registers_and_opens_gracefully():
    from core.transport import create_transport, list_transports, resolve_transport_name
    assert "bcon" in list_transports()
    assert resolve_transport_name("bcon") == "bcon"
    made = create_transport("bcon")
    assert made.name == "bcon"
    assert made.open(0, "", "not-a-number") is False
    assert made.open(9999, "", 1000000) is False


def test_bcon_send_row_builds_single_binary_frame():
    from core.transport import create_transport
    made = create_transport("bcon")
    written: list[bytes] = []

    class FakeSer:
        is_open = True

        def write(self, data: bytes) -> int:
            written.append(bytes(data))
            return len(data)

    made.ser = FakeSer()
    made.send_row("10 08")
    assert len(written) == 1
    assert written[0][0] == 0xAB
    assert written[0][1] == 0x01
    assert written[0][2] == 8
    assert len(written[0]) == 13
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_transport.py -q`
Expected: FAIL（`bcon`未登録、`resolve_transport_name("bcon")`が`legacy_text`に落ちる）。

- [ ] **Step 3: base.pyへcapability追加（2-3行）**

```python
BCON_STATE = "BCON_STATE"
LIVE_WORKER_CAPABILITIES: set[str] = {PICO_LIVE_STATE, BCON_STATE}
```

対象は`SerialController/core/transport/base.py:34-40`。`VALID_CAPABILITIES`へも`BCON_STATE`を加える。

- [ ] **Step 4: bcon.py骨格を書く（RXポンプ・HELLOはTask 4、ここでは開閉＋単射＋購読土台）**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon.py - bconバイナリをpyserialで送る実装（線そのもの）。

区画は4つ。姿勢受付・フレーム化SEQ・送信ループ・受信パーサ。
Phase 2では送信ループ区画だけを別プロセスへ移設する。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from logging import DEBUG, NullHandler, getLogger
from typing import Any

import serial

from core.serial.bcon_mapping import neutral_state, state_to_len8, state_to_len12, wire_row_to_state
from core.transport.base import BCON_STATE, Transport
from core.transport.bcon_protocol import (
    BAUD_TABLE,
    DEFAULT_BAUD_INDEX,
    T_NEUTRAL,
    T_STATE,
    BconParser,
    frame_build,
)

BCON_DEFAULT_BAUDRATE = BAUD_TABLE[DEFAULT_BAUD_INDEX]
BCON_READ_TIMEOUT = 0.05
BCON_WRITE_TIMEOUT = 0.2


class BconTransport(Transport):
    """bcon用。STATEバイナリを送りSTATUS/PONG/ACKを読む。"""

    name = "bcon"
    capability = BCON_STATE

    def __init__(self, logger: Any = None, use_len12: bool = False) -> None:
        self.ser: Any = None
        self._logger = logger if logger is not None else getLogger(__name__)
        if logger is None:
            self._logger.addHandler(NullHandler())
            self._logger.setLevel(DEBUG)
            self._logger.propagate = True
        self._lock = threading.RLock()
        self._tx_seq = 0
        self.use_len12 = bool(use_len12)
        self._rx_lock = threading.Lock()
        self._rx_subs: list[Callable[[tuple[int, bytes, int]], None]] = []
        self._rx_text_subs: list[Callable[[str], None]] = []
        self._parser = BconParser()
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()
        self._last_tx = 0.0

    def open(self, portNum: int, portName: str = "", baudrate: int = BCON_DEFAULT_BAUDRATE, **extra: Any) -> bool:
        """線を開く。既定1Mbps。不正・不良はFalse＋logで落とさない。"""
        _ = extra
        try:
            rate = int(baudrate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self._logger.warning(f"bconのbaud指定が不正です: {baudrate!r}")
            return False
        if rate <= 0:
            self._logger.warning(f"bconのbaud指定が不正です: {baudrate!r}")
            return False
        name = portName.strip() if isinstance(portName, str) else ""
        if not name:
            try:
                num = int(portNum)
            except (TypeError, ValueError):
                return False
            if num < 0:
                return False
            name = f"COM{num + 1}" if __import__("os").name == "nt" else f"/dev/ttyUSB{num}"
        try:
            ser = serial.Serial(
                port=name,
                baudrate=rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=BCON_READ_TIMEOUT,
                write_timeout=BCON_WRITE_TIMEOUT,
            )
        except Exception as e:
            self._logger.warning(f"bconを開けませんでした({name} {rate}bps): {e!r}")
            return False
        with self._lock:
            self.ser = ser
        return True
```

`send_row`・`flush_pending`・`close`・`is_open`・`subscribe_rx`・`wait_rx`（TYPE一致）・`rx_pump_*`・`add_listener()->False`を同ファイルに続けて書く。`send_row`は`wire_row_to_state(row)`→`state_to_len8/12`→`frame_build(T_STATE, payload, seq)`→単一`write()`とし、錠はSEQ採番だけに使い`write`は外で行う。`wait_rx`は`(type, prefixes)`ではなく`(wanted_types: int | tuple[int, ...], timeout)`で待つ。

- [ ] **Step 5: registry.pyへbuiltin登録＋`__init__.py`再公開**

```python
register_transport(
    BconTransport.name,
    BconTransport,
    description="bconへSTATEバイナリを送る（Pico 2 W・既定1Mbps・HELLO必須）",
    builtin=True,
)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_bcon_transport.py tests/test_bcon_protocol.py tests/test_bcon_mapping.py -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add SerialController/core/transport/bcon.py SerialController/core/transport/base.py SerialController/core/transport/registry.py SerialController/core/transport/__init__.py tests/test_bcon_transport.py
git commit -m "feat(bcon): add BconTransport skeleton with registration"
```

### Task 4: RXポンプ＋HELLO/BAUD状態機械（無音ハング防止）

**Files:**
- Modify: `SerialController/core/transport/bcon.py`
- Test: `tests/test_bcon_session.py`

**Interfaces:**
- Consumes: Task 3の`BconTransport`土台、Task 1の`T_HELLO/T_HELLO_ACK/T_STATUS/T_PONG/RESULT_UNSUPPORTED/ERR_*`。
- Produces: `hello(timeout=3.0) -> bool`（ver=4送→HELLO_ACK確認）、`ping(timeout=1.0) -> float | None`（PONG SEQエコーでRTT秒）、`last_status() -> dict`（flags/last_seq/err_crc/err_drop/errcode）、`baud_hunt(candidates) -> int | None`。失敗時は例外なしでFalse/None＋GUI可視のlog文言を返す。

- [ ] **Step 1: Write the failing test**

```python
def test_bcon_hello_and_ping_with_scripted_ser():
    import threading
    import time
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_HELLO_ACK, T_PONG, frame_build
    made = create_transport("bcon")

    class ScriptedSer:
        is_open = True

        def __init__(self) -> None:
            self._chunks: list[bytes] = []
            self._lock = threading.Lock()
            self.written: list[bytes] = []

        def read(self, n: int) -> bytes:
            _ = n
            with self._lock:
                if self._chunks:
                    return self._chunks.pop(0)
            time.sleep(0.001)
            return b""

        def feed(self, data: bytes) -> None:
            with self._lock:
                self._chunks.append(data)

        def write(self, data: bytes) -> int:
            self.written.append(bytes(data))
            return len(data)

    ser = ScriptedSer()
    made.ser = ser
    made.start_rx_pump()
    try:
        assert made.rx_pump_running() is True
        ser.feed(frame_build(T_HELLO_ACK, bytes([0x04, 0x00, 0x01, 0x00]), 0x00))
        assert made.hello(timeout=2.0) is True
    finally:
        made.stop_rx_pump()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_session.py -q`
Expected: FAIL（`hello`が無い、`AttributeError`）。

- [ ] **Step 3: RXポンプ（binary・スライディング）＋TYPE一致waitを実装**

受信スレッドは1本だけにする。`ser.read(64)`→`BconParser.feed`→確定分を購読者（frame購読）と応答待ち（TYPE一致）へ同じ物を届ける。2か所でreadしない（横取り防止）。購読解除の失敗では落とさない。停止時はjoinする。

- [ ] **Step 4: HELLO/PING/STATUS_REQを実装**

`hello()`は送る前に待ちを登録してから送る（送ってから登録するとポンプが先に読んで届かない）。HELLO payloadは`[0x04, flags]`（bit0=STATUS自動送信要求）。HELLO_ACKの`[adopted, major, minor, RESULT]`を見て`adopted==0x04 and RESULT==0x00`ならTrue、UNSUPPORTEDならNEUTRAL維持＋版表示してFalse。`ping()`はPING送→PONGのpayload SEQエコーが送ったSEQと一致したらRTTを返す。`STATUS_REQ`は即時STATUS＋PLAYER_INFO付随を受けて`last_status()`を更新する。

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_bcon_session.py tests/test_bcon_transport.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add SerialController/core/transport/bcon.py tests/test_bcon_session.py
git commit -m "feat(bcon): add rx pump with HELLO and PING session"
```

### Task 5: 120Hz送信ループ＋Sender統合＋計測

**Files:**
- Modify: `SerialController/core/transport/bcon.py`（独立スレッド＋絶対時刻120Hz）
- Modify: `SerialController/core/serial/sender.py`（`_liveCapable`にBCON対応）
- Create: `tools/bcon_jitter.py`
- Test: `tests/test_bcon_live.py`

**Interfaces:**
- Consumes: Task 3-4の`BconTransport`、既存`LiveScheduler`（`core/serial/live_scheduler.py`）の語彙（slot・dwell・merged/dropped）。
- Produces: 120Hz送信（変化時即送＋8.33ms refresh、1frame 1write、SEQ方向別）。`live_stats() -> dict`（sent/merged/dropped/seq_gap/err_crc/err_drop/rtt_ms）。`tools/bcon_jitter.py`はp50/p99を出す。

- [ ] **Step 1: Write the failing test**

```python
def test_bcon_live_capable_and_stats_shape():
    from core import Sender, Transport
    probe = Sender.Sender.__new__(Sender.Sender)
    made = Transport.create_transport("bcon")
    assert probe._liveCapable(made) is True
    assert set(made.live_stats().keys()) >= {"sent", "dropped", "seq_gap", "err_crc", "err_drop"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_live.py -q`
Expected: FAIL（`_liveCapable`がFalse、`live_stats`が無い）。

- [ ] **Step 3: Sender側1行対応＋Transport側120Hz実装**

`sender.py`の`_liveCapable`は次にする。

```python
def _liveCapable(self, transport: Any) -> bool:
    cap = getattr(transport, "capability", None)
    return cap in LIVE_WORKER_CAPABILITIES
```

`LIVE_WORKER_CAPABILITIES`はTask 3で`{PICO_LIVE_STATE, BCON_STATE}`済みのためbconは自動でlive経路になる。送信ループは`tkinter.after()`に載せない。独立スレッド＋`time.perf_counter`基準の次回絶対時刻（`next_tx += 1/120`、遅延時は現在時刻へ追従しbacklogを作らない）とする。`ser.write`と購読者呼出しは錠の外、SEQ採番と保留取出しだけ錠内（`TextSerialTransport.send_row`と同一規律）。

- [ ] **Step 4: 計測器を作る**

```python
#!/usr/bin/env python3
"""bcon_jitter.py - 送信周期p50/p99とSTATUS差分の計測器。"""
```

引数は`--port COMx --baud 1000000 --secs 10 --latency-note "FTDI timer=1ms"`。出力は`p50=X.XXms p99=Y.YYms sent=N seq_gap=M err_crc=+0 err_drop=+0`の1行＋CSV。loopback時はTXをRXへ折り返してSEQ連続性を見る。

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_bcon_live.py tests/test_bcon_session.py tests/test_bcon_transport.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add SerialController/core/transport/bcon.py SerialController/core/serial/sender.py tools/bcon_jitter.py tests/test_bcon_live.py
git commit -m "feat(bcon): drive 120Hz loop with stats and jitter probe"
```

### Task 6: BconSetup新設（WakeSetup並置・分岐禁止）＋設定面

**Files:**
- Create: `SerialController/BconSetup.py`
- Modify: `SerialController/ui/serial_panel.py`（registry列挙なら無改変、直書きなら`bcon`追加）
- Modify: `SerialController/config.py`（`Transport.name`コメント追記のみ、既定値は不変）
- Test: `tests/test_bcon_setup.py`

**Interfaces:**
- Consumes: Task 4の`hello/ping/last_status`とCONFIG frame群（`T_CAPTURE_START/T_BEACON_START/T_COLOR_SET/T_KEY_DELETE/T_WIRED_MODE/T_STATUS_REQ`）。
- Produces: `class BconSetup`（`WakeSetup`と同形の小窓。作業スレッド＋`after()`還流、閉鎖後put禁止）。共通化は動作確認後に抽出する。

- [ ] **Step 1: Write the failing test**

```python
def test_bcon_setup_imports_without_touching_wake():
    import importlib.util
    from pathlib import Path
    assert Path("SerialController/BconSetup.py").exists()
    assert Path("SerialController/WakeSetup.py").exists()
    spec = importlib.util.spec_from_file_location("bcon_setup", "SerialController/BconSetup.py")
    assert spec is not None and spec.loader is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_bcon_setup.py -q`
Expected: FAIL（`BconSetup.py`が無い）。

- [ ] **Step 3: BconSetupを書く（WakeSetupの同形・文言だけbcon用）**

窓題は`Bcon設定`。ボタンは取込開始（秒1-60→範囲外は送らず注意）・BEACON再生・状態確認（STATUS_REQ）・疎通（PING→PONG）・W表示/W0無線/W1有線（再起動注意）・鍵削除（Switch側登録解除も案内）・色変更案内（`COLOR_SET`は別コマンドに寄せるかここに置くかは実装時に1か所に統一）。読書きは作業スレッド、画面更新だけ`after()`。`WakeSetup.py`の1行も変えない。`bounds`（UI→Window禁止・servicesのtkinter禁止）に触れない。

- [ ] **Step 4: 設定面の最小追記**

`config.py`の`Transport.name`コメントに`bcon`を追記する（値は`legacy_text`のまま）。`serial_panel.py`が`list_transports()/describe_transports()`経由なら無改変、直書き列挙なら`bcon`を1件追加する。`complete_missing`のname存在確認はしない（利用者定義Transportのため。空だけ既定へ）。

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_bcon_setup.py -q`
Expected: PASS。続けて `uv run --frozen python tools/check_core.py` が緑。

- [ ] **Step 6: Commit**

```bash
git add SerialController/BconSetup.py SerialController/ui/serial_panel.py SerialController/config.py tests/test_bcon_setup.py
git commit -m "feat(bcon): add BconSetup window alongside WakeSetup"
```

### Task 7: 実機受入（有線優先・latency timer・USB干渉・切分け）

**Files:**
- Modify: `tools/bcon_jitter.py`（実測取込みがあれば微修正）
- Test: 既存pytestの回帰（新規テストは作らない。実機手順の記録を残す）

**Interfaces:**
- Consumes: Task 5の計測器、Task 4のSTATUS/PONG。
- Produces: 有線での押下確認→A連打完走→遅延分布の記録。NG時はdisc 0x05がPico側かPCジッタかを切分ける手順。

- [ ] **Step 1: latency timer前後でp99を取る**

Run: `uv run --frozen python tools/bcon_jitter.py --port COMx --baud 1000000 --secs 10`
Expected: FTDI timer=16ms既定でp99悪化→1ms設定で改善すること。改善しなければ変換器（CH340/CP2102緩衝）かUSBポーリング干渉を疑う。

- [ ] **Step 2: 有線でHELLO→押下→連打を通す**

Run: 実機で`BconSetup`の疎通→状態確認→入力テスト画面で押下→A連打script完走。
Expected: `err_crc/err_drop`差分ほぼ0、timeout-neutralに落ちない（120Hz保持）、終了時にNEUTRALで静止。

- [ ] **Step 3: WIRED往復と起動時復元を確認**

Run: `WIRED_MODE=0→再起動→CAPTURE/BEACON可`、`WIRED_MODE=1→再起動→有線のみ`。
Expected: 有線中のCAPTURE/BEACONは0x10/0x11拒否になり、案内文言が出ること。

- [ ] **Step 4: Commit（手順記録のみ）**

```bash
git add tools/bcon_jitter.py
git commit -m "docs(bcon): record wired acceptance with jitter gate"
```

---

## Self-Review（執筆時確認）

- Spec coverage: §3.2（Transport・capability・境界）→Task 3、§3.3（フレーム・再同期）→Task 1/4、写像・LEN非依存→Task 2、§3.4（独立スレッド・latency・p99ゲート・USB干渉）→Task 5/7、§3.5（HELLO/BAUD・無音防止）→Task 4、§3.6（BconSetup新設）→Task 6、§4（Phase 2境界）→Task 3/5の区画分け、§5（hostベクタ・ci緑・実機）→Task 1-2/4-7。
- Placeholder scan: TBD/TODO/後で・適切に・同様になどの残骸なし。各手順は実コード・実コマンド・期待結果つき。
- Type consistency: 中間姿勢は`dict[str, int]`（buttons u32＋lx/ly/rx/ry u16）で全タスク統一。`frame_build(type, payload, seq)->bytes`、`BconParser.feed(data)->list[tuple[int, bytes, int]]`、`wire_row_to_state(row)->dict`、`BconTransport.hello()->bool`・`ping()->float | None`・`live_stats()->dict`で統一。
