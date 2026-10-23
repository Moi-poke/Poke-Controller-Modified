#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pad_source.py - コントローラ読み取り口の選択（Tkなし）。

XInput 系（Xbox など）と SDL 系（Switch Pro コンなど）の2つの
読み取り口のうち、指定番号で繋がっている方を選ぶ。呼び出し側は
XInputReader と同じ read(index) / scan() / available だけ使う。

なぜ選ぶのか:
  ・XInput は Xbox 系しか読めない。Switch Pro コンは繋がって
    いても未接続として扱われ、「操作できない」の正体になる。
  ・SDL（pygame）は両方読めるが、常駐の初期化とイベントポンプが要る。
    XInput だけで足りる環境では軽い方を使う。

選び方:
  ・番号ごとに XInput → SDL の順で読み、繋がっている方を使う。
  ・一度選んだら覚える。毎回両方読むと、抜き差しのたびに遅くなる。
  ・抜けたら選び直す（繋がっていない読みが続いたら忘れる）。
"""

from __future__ import annotations

import threading
from typing import Any

from core.sdl_pad import SdlPadReader
from core.xinput import MAX_PADS, PadState, XInputReader


class PadSource:
    """番号ごとの読み取り口の選択と読み取り。"""

    # プロセス全体で共有する読み取り口。作り直すたび SDL の列挙が
    # 追いつかず、短時間の stop→listen 繰り返しで未接続に落ちる。
    # ハンドルと初期化状態を使い回すため、共有の実体を1つだけ持つ。
    _shared_sdl: SdlPadReader | None = None
    _shared_lock = threading.Lock()

    def __init__(
        self,
        xinput: XInputReader | None = None,
        sdl: SdlPadReader | None = None,
        hid: Any | None = None,
    ) -> None:
        self.xinput = xinput if xinput is not None else XInputReader()
        if sdl is not None:
            self.sdl = sdl
        else:
            with PadSource._shared_lock:
                if PadSource._shared_sdl is None:
                    PadSource._shared_sdl = SdlPadReader()
                self.sdl = PadSource._shared_sdl
        # HID直読みはProCon 1台のみ。遅延生成（hid無し環境では作らない）。
        self._hid_arg = hid
        self._hid: Any = hid
        self._hid_opened = hid is not None
        # 番号 → "xinput" / "sdl" / "hid"。選んでいない番号は無い。
        self._route: dict[int, str] = {}

    @property
    def available(self) -> bool:
        """どちらかの読み取り口が使えるか。SDLを先に触る。

        xinput1_4.dllを先に掴むとSDLのProCon USB接続が開けなくなる実測
        （Couldn't setup USB mode。2026-10-18）。初期化順序はSDL優先で、
        選択順序（read/scan）は従来どおりXInput優先のまま変えない。
        """
        try:
            sdl_ok = bool(self.sdl.available)
        except Exception:
            sdl_ok = False
        try:
            if self.xinput.available:
                return True
        except Exception:
            pass
        return sdl_ok

    def _read_xinput(self, index: int) -> PadState:
        try:
            return self.xinput.read(index)
        except Exception:
            return PadState()

    def _read_sdl(self, index: int) -> PadState:
        try:
            return self.sdl.read(index)
        except Exception:
            return PadState()

    def _read_hid(self, index: int) -> PadState:
        """ProCon直読み。index 0のみ。開いていなければ開く。"""
        if int(index) != 0:
            return PadState()
        try:
            if self._hid is None:
                from core.nintendo_hid import get_shared_reader

                self._hid = get_shared_reader()
                self._hid_opened = False
            reader = self._hid
            is_open = getattr(reader, "is_open", None)
            if not self._hid_opened or (callable(is_open) and not is_open()):
                opener = getattr(reader, "open", None)
                if not callable(opener) or not opener():
                    return PadState()
                self._hid_opened = True
            read_pad = getattr(reader, "read_pad", None)
            if not callable(read_pad):
                return PadState()
            state = read_pad()
            if not getattr(state, "connected", False):
                self._hid_opened = False
            return state
        except Exception:
            return PadState()

    def read(self, index: int = 0) -> PadState:
        """指定番号を読む。どれも繋がっていなければ未接続を返す。

        未選択の番号はSDL→HID→XInputの順に試す。SDL優先はXInputの
        DLL掴みが先行するとSDLのProCon USB接続が開けなくなる実測のため。
        HIDはSteam起動中のSDL不通時の受け皿（ProCon直読み）。
        """
        if not 0 <= int(index) < MAX_PADS:
            return PadState()
        route = self._route.get(int(index))
        if route == "xinput":
            state = self._read_xinput(index)
            if state.connected:
                return state
            self._route.pop(int(index), None)
        elif route == "sdl":
            state = self._read_sdl(index)
            if state.connected:
                return state
            self._route.pop(int(index), None)
        elif route == "hid":
            state = self._read_hid(index)
            if state.connected:
                return state
            self._route.pop(int(index), None)
        state = self._read_sdl(index)
        if state.connected:
            self._route[int(index)] = "sdl"
            return state
        state = self._read_hid(index)
        if state.connected:
            self._route[int(index)] = "hid"
            return state
        state = self._read_xinput(index)
        if state.connected:
            self._route[int(index)] = "xinput"
            return state
        return state

    def scan(self) -> list[int]:
        """繋がっている番号の一覧。XInput・SDL・HID の和集合。"""
        found: set[int] = set()
        for reader in (self.xinput, self.sdl):
            try:
                for i in reader.scan():
                    found.add(int(i))
            except Exception:
                continue
        # HIDはProCon 1台のみ。列挙できれば0番に載せる。
        try:
            from core.nintendo_hid import NintendoHidReader

            if NintendoHidReader.enumerate():
                found.add(0)
        except Exception:
            pass
        return sorted(found)

    def names(self) -> dict[int, str]:
        """番号 → 表示名。SDLの実名＋HID直結＋XInputの汎名。"""
        try:
            names = self.sdl.names()
            out = {int(k): str(v) for k, v in names.items()}
        except Exception:
            out = {}
        if 0 not in out:
            try:
                from core.nintendo_hid import NintendoHidReader

                devs = NintendoHidReader.enumerate()
                if devs:
                    product = ""
                    try:
                        product = str(devs[0].get("product_string", "") or "")
                    except Exception:
                        product = ""
                    out[0] = product if product else "Pro Controller (HID直結)"
            except Exception:
                pass
        for index in range(MAX_PADS):
            if index in out:
                continue
            try:
                state = self.xinput.read(index)
            except Exception:
                continue
            if state.connected:
                out[index] = "XInput パッド"
        return out

    def route_of(self, index: int) -> str:
        return self._route.get(int(index), "")
