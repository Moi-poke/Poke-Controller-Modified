#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_protocol.py - bconバイナリの組立と受信（純粋層）。

Pico側 `src/proto/protocol.[hc]` のPC側写し。BTstack・TinyUSB・
tkinterに依存しない。仕様は `C:\\PokeCon\\Switch-bcon\\spec\\protocol_v3.md` が正。
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
T_BOOTSEL = 0x37
T_EMULATE_MODE = 0x38

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

# BOOTSEL突入の合言葉。T_BOOTSELのpayload 1Bがこの値のときのみ有効。
BOOTSEL_MAGIC = 0x5A

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
        T_BOOTSEL: 1,
        T_EMULATE_MODE: 1,
    }
    return table.get(type_, -1)


def proto_state_len_ok(plen: int) -> bool:
    """STATEは8/12のみ受理し他はERR_BAD_LEN（正準報告は8のまま）。"""
    return plen in (8, 12)


def frame_build(type_: int, payload: bytes, seq: int) -> bytes:
    """1フレームを組む。`len(payload)>32`はValueError（黙って切らない）。"""
    if len(payload) > PROTO_MAX_PAYLOAD:
        raise ValueError(f"payloadが32Bを超えています: {len(payload)}")
    body = (
        bytes([type_ & 0xFF, len(payload) & 0xFF])
        + bytes(payload)
        + bytes([seq & 0xFF])
    )
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
                    # LEN不一致もerr_crcへ数える。PC側の破棄数であり
                    # Pico側STATUSのerr_crcとは別物である。
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
