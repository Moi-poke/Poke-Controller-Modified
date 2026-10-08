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
    ) -> None:
        self.xinput = xinput if xinput is not None else XInputReader()
        if sdl is not None:
            self.sdl = sdl
        else:
            with PadSource._shared_lock:
                if PadSource._shared_sdl is None:
                    PadSource._shared_sdl = SdlPadReader()
                self.sdl = PadSource._shared_sdl
        # 番号 → "xinput" / "sdl"。選んでいない番号は無い。
        self._route: dict[int, str] = {}

    @property
    def available(self) -> bool:
        """どちらかの読み取り口が使えるか。"""
        try:
            if self.xinput.available:
                return True
        except Exception:
            pass
        try:
            return bool(self.sdl.available)
        except Exception:
            return False

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

    def read(self, index: int = 0) -> PadState:
        """指定番号を読む。どちらも繋がっていなければ未接続を返す。"""
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
        state = self._read_xinput(index)
        if state.connected:
            self._route[int(index)] = "xinput"
            return state
        state = self._read_sdl(index)
        if state.connected:
            self._route[int(index)] = "sdl"
        return state

    def scan(self) -> list[int]:
        """繋がっている番号の一覧。XInput と SDL の和集合。"""
        found: set[int] = set()
        for reader in (self.xinput, self.sdl):
            try:
                for i in reader.scan():
                    found.add(int(i))
            except Exception:
                continue
        return sorted(found)

    def names(self) -> dict[int, str]:
        """番号 → 表示名。SDL で分かる分だけ載る。"""
        try:
            names = self.sdl.names()
            return {int(k): str(v) for k, v in names.items()}
        except Exception:
            return {}

    def route_of(self, index: int) -> str:
        """その番号がいまどちらの口で読めているか（検証・表示用）。"""
        return self._route.get(int(index), "")
