#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pad_source.py - コントローラ読み取り口の選択（Tkなし）。

XInput 系（Xbox など）と SDL 系（Switch Pro コンなど）の2つの
読み取り口のうち、指定番号で繋がっている方を選ぶ。呼び出し側は
read(index) / scan() / available に加え、選択肢の表示名（names）と、
抜き差しで番号が動いても同じ個体を選び続けるための識別子
（identities）を使える。

なぜ選ぶのか:
  ・XInput は Xbox 系しか読めない。Switch Pro コンは繋がって
    いても未接続として扱われ、「操作できない」の正体になる。
  ・SDL（pygame）は両方読めるが、常駐の初期化とイベントポンプが要る。
    XInput だけで足りる環境では軽い方を使う。

選び方:
  ・番号ごとに XInput → SDL の順で読み、繋がっている方を使う。
  ・一度選んだら覚える。毎回両方読むと、抜き差しのたびに遅くなる。
  ・抜けたら選び直す（繋がっていない読みが続いたら忘れる）。
  ・HID直読み（ProCon）はボタンの受け皿だが、12bitスティックの校正が
    無い。SDL の生joystick が同じ個体の軸を読めるなら、スティックだけ
    SDL（校正済み -1..1）で置き換える。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from typing import Any

from core.sdl_pad import SdlPadReader
from core.xinput import MAX_PADS, PadState, XInputReader
from loguru import logger


def sdl_debug_enabled() -> bool:
    """SDL経路をデバッグ用に解禁するか（環境変数 POKECON_SDL_DEBUG）。"""
    return os.environ.get("POKECON_SDL_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class PadSource:
    """番号ごとの読み取り口の選択と読み取り。"""

    # プロセス全体で共有する読み取り口。作り直すたび SDL の列挙が
    # 追いつかず、短時間の stop→listen 繰り返しで未接続に落ちる。
    # ハンドルと初期化状態を使い回すため、共有の実体を1つだけ持つ。
    _shared_sdl: SdlPadReader | None = None
    _shared_lock = threading.Lock()

    # HID open失敗後の再試行間隔(秒)。8ms周期の読みから毎回
    # 5回×0.3秒の再試行を呼ぶと、その間の読みが止まる。
    HID_RETRY_S = 0.5

    # HID で読んでいる間に SDL を試し直す間隔(秒)。SDL の列挙は
    # 起動直後に間に合わないことがあり、その一度の失敗で HID に
    # 固定されると、校正済みの軸（SDL）へ戻れない。open は失敗時
    # 0.5秒バックオフするため、ここから試しても読みは止まらない。
    SDL_REPROBE_S = 5.0

    def __init__(
        self,
        xinput: XInputReader | None = None,
        sdl: SdlPadReader | None = None,
        hid: Any | None = None,
        allow_sdl: bool | None = None,
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
        # HID open失敗の次回試行時刻と、SDL→HIDフォールバックのログ済み印。
        self._hid_retry_at = 0.0
        self._fallback_logged = False
        # HID で読んでいる間に SDL を試し直す次回時刻。
        self._sdl_reprobe_at = 0.0
        # スティック補正に SDL 軸を使えなかったことを1回だけ残す。
        self._axis_warned = False
        # HID直結を通常経路にし、SDLはデバッグ時だけ解禁する。
        # HidHideでSteam/SDLから実機を隠したうえで、アプリ内部でも
        # SDLとHIDの二重オープン（レポートモード競合）を避ける。
        # 既定は従来互換（SDLを試す）。アプリ本体は
        # `allow_sdl=PadSource.sdl_debug_enabled()` を明示して、通常は
        # HID有線に固定する。
        self.allow_sdl = True if allow_sdl is None else bool(allow_sdl)
        self._hid_present = hid is not None
        self._hid_probe_at = 0.0
        if not self._hid_present:
            self._hid_present = self._probe_hid()
        # HID open は handshake/subcmd で最大数秒かかる。8ms の読み取り
        # スレッドで待つと、その間コントローラ操作が一切通らなくなる
        # （実機の中華コンで5秒スタック）。open は専用スレッドで行い、
        # 読み取り側は未接続を返して次のtickへ進む。
        self._hid_open_thread: threading.Thread | None = None
        self._hid_open_lock = threading.Lock()

    @staticmethod
    def sdl_debug_enabled() -> bool:
        """後方互換の入口。実体はモジュール関数。"""
        return sdl_debug_enabled()

    def _probe_hid(self) -> bool:
        """Nintendo HIDが存在するか（列挙のみ・openしない）。"""
        try:
            from core.nintendo_hid import NintendoHidReader

            return bool(NintendoHidReader.enumerate())
        except Exception:
            return False

    def _route_order(self) -> tuple[str, ...]:
        """通常はHID有線、デバッグ時だけSDLを先に試す。"""
        if self.allow_sdl:
            return ("sdl", "hid", "xinput")
        if not self._hid_present:
            now = time.monotonic()
            if now >= self._hid_probe_at:
                self._hid_probe_at = now + 5.0
                self._hid_present = self._probe_hid()
        if self._hid_present:
            return ("hid",)
        return ("sdl", "xinput")

    @property
    def available(self) -> bool:
        """どちらかの読み取り口が使えるか。SDLを先に触る。

        xinput1_4.dllを先に掴むとSDLのProCon USB接続が開けなくなる実測
        （Couldn't setup USB mode。2026-10-18）。初期化順序はSDL優先で、
        選択順序（read/scan）は従来どおりXInput優先のまま変えない。
        """
        if not self.allow_sdl and self._hid_present:
            return True
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

    def invalidate(self) -> None:
        """読み取り口のキャッシュを捨て、次の read で列挙し直す。

        手動の「再接続」と、SDL の列挙が空になったときの復帰で使う。
        HID のハンドルは閉じない（IMU 側と共有しており、閉じると
        ジャイロが途切れる）。閉じているかは毎回 is_open で見る。
        """
        self._route.clear()
        self._hid_retry_at = 0.0
        self._hid_opened = False
        self._fallback_logged = False
        self._sdl_reprobe_at = 0.0
        self._axis_warned = False
        try:
            self.sdl.invalidate()
        except Exception as e:
            logger.debug(f"SDLのキャッシュ破棄で例外: {e}")

    def _read_route(self, route: str, index: int) -> PadState:
        """名前で選んだ読み取り口から1回読む。"""
        if route == "xinput":
            return self._read_xinput(index)
        if route == "sdl":
            return self._read_sdl(index)
        if route == "hid":
            return self._read_hid(index)
        return PadState()

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
            is_opening = getattr(reader, "is_opening", None)
            if callable(is_opening):
                try:
                    # open処理中は待たない。待つと8msの読みがUSBの
                    # handshake待ち（最大1秒超）で止まる。
                    if is_opening():
                        return PadState()
                except Exception:
                    pass
            is_open = getattr(reader, "is_open", None)
            if callable(is_open):
                handle_open = bool(is_open())
                if not handle_open:
                    self._hid_opened = False
            else:
                # 判定口が無い実体は従来どおり `_hid_opened` を信じる。
                handle_open = self._hid_opened
            # 開いているハンドルにはopenを呼ばない。openは既存ハンドルを
            # 閉じて開き直すため、IMU側が開いた直後やprobe直後に呼ぶと
            # close→openの往復になり、パッド読みが未接続で返る。
            if not handle_open:
                now = time.monotonic()
                if now < self._hid_retry_at:
                    return PadState()
                opener = getattr(reader, "open", None)
                if not callable(opener):
                    return PadState()
                # open は handshake/subcmd で最大数秒かかる。ここで
                # 同期実行すると Gamepad の読み取りが止まり、スタック中
                # の操作が一切通らなくなる。専用スレッドへ投げ、この
                # tick は未接続で返す。完了後の read から使う。
                if not self._start_hid_open(reader, opener):
                    return PadState()
                # テスト用の即時openや、既に完了したopenはこのtickで
                # 拾う。実機のhandshakeは数msを超えるため、ここで待つのは
                # ごく短時間だけにし、待ち切れなければ次のtickへ回す。
                thread = self._hid_open_thread
                if thread is not None:
                    thread.join(0.002)
                try:
                    if not callable(is_open) or not bool(is_open()):
                        return PadState()
                except Exception:
                    return PadState()
            read_pad = getattr(reader, "read_pad", None)
            if not callable(read_pad):
                return PadState()
            state = read_pad()
            # 未接続は「ハンドルが閉じた」か「0x30が鮮度切れ」のどちらか。
            # 開いている間は open を呼ばない（動いているハンドルを閉じる）。
            # ハンドルが閉じていれば次の読みで開き直す。
            self._hid_opened = bool(getattr(state, "connected", False))
            return self._with_sdl_sticks(index, state)
        except Exception:
            return PadState()

    def _start_hid_open(self, reader: Any, opener: Any) -> bool:
        """HID open を専用スレッドで開始する（既に実行中なら何もしない）。

        8ms 周期の読み取りを open の handshake 待ちで止めないための入口。
        戻り値は「新しく開始したか」。実行中・直近失敗のバックオフ中は
        False を返し、呼び出し側は未接続のまま次のtickへ進む。
        """
        now = time.monotonic()
        with self._hid_open_lock:
            thread = self._hid_open_thread
            if thread is not None and thread.is_alive():
                return False
            if now < self._hid_retry_at:
                return False
            worker = threading.Thread(
                target=self._hid_open_worker,
                args=(reader, opener),
                name="pad-hid-open",
                daemon=True,
            )
            self._hid_open_thread = worker
            worker.start()
            return True

    def _hid_open_worker(self, reader: Any, opener: Any) -> None:
        """HID open の実体（読み取りスレッドを止めない）。"""
        started = time.monotonic()
        ok = False
        try:
            ok = bool(opener(retries=1))
        except Exception:
            ok = False
        duration = time.monotonic() - started
        if duration >= 1.0:
            # この待ちが以前は Gamepad の読み取りを止め、操作不能の
            # 5秒スタックになっていた。非同期化後も原因切り分け用に残す。
            logger.warning(f"HID openに{duration:.1f}秒かかりました (ok={ok})")
        else:
            logger.debug(f"HID open {duration:.3f}s (ok={ok})")
        try:
            if ok:
                self._hid_opened = True
                self._hid_retry_at = 0.0
            else:
                self._hid_retry_at = time.monotonic() + self.HID_RETRY_S
        finally:
            with self._hid_open_lock:
                self._hid_open_thread = None

    def _with_sdl_sticks(self, index: int, state: PadState) -> PadState:
        """HID直結の未校正スティックを SDL の校正済み軸で置き換える。

        ProCon の 0x30 12bit は 0..4095 の全域を使わない（実機実測で
        左X 456..3545）。nintendo_hid の換算は中心0x800・フル0x800
        なので、HID だけでは全倒しが約78%にしかならない。SDL の
        生joystick は同じ個体を校正済みの -1..1 で返すため、ボタン・
        トリガは HID のまま、スティックだけ SDL で補う。
        GameController を開けなくても生joystick は読めることがある。
        """
        if not self.allow_sdl:
            return state
        if not bool(getattr(state, "connected", False)):
            return state
        read_axes = getattr(self.sdl, "read_axes", None)
        if not callable(read_axes):
            return state
        try:
            # HID直結は ProCon 1台。同じ番号に別機種がいても混ぜない。
            values = read_axes(int(index), name_hint="pro controller")
        except Exception:
            values = None
        if values is None:
            if not self._axis_warned:
                self._axis_warned = True
                logger.warning(
                    "HID直結のスティック補正にSDLの生joystickを使えません。"
                    "全倒しに届かない場合は「再接続」を試してください"
                )
            return state
        lx, ly, rx, ry, _lt, _rt, raw_axes = values
        return replace(
            state,
            thumb_lx=int(lx),
            thumb_ly=int(ly),
            thumb_rx=int(rx),
            thumb_ry=int(ry),
            raw_axes=tuple(raw_axes),
        )

    def read(self, index: int = 0) -> PadState:
        """指定番号を読む。どれも繋がっていなければ未接続を返す。

        未選択の番号はSDL→HID→XInputの順に試す。SDL優先はXInputの
        DLL掴みが先行するとSDLのProCon USB接続が開けなくなる実測のため。
        HIDはSteam起動中のSDL不通時の受け皿（ProCon直読み）。
        一度失敗した口は同じ回では読み直さない（SDLのopen失敗は
        0.5秒バックオフするため、二重に呼ぶと無駄に待つ）。
        """
        if not 0 <= int(index) < MAX_PADS:
            return PadState()
        state = PadState()
        tried: set[str] = set()
        route = self._route.get(int(index))
        if route == "hid" and self.allow_sdl:
            # HID に固定されたまま校正済みの SDL 軸へ戻れないのを防ぐ。
            now = time.monotonic()
            if now >= self._sdl_reprobe_at:
                self._sdl_reprobe_at = now + self.SDL_REPROBE_S
                sdl_state = self._read_sdl(index)
                if sdl_state.connected:
                    self._route[int(index)] = "sdl"
                    return sdl_state
        if route:
            tried.add(route)
            state = self._read_route(route, index)
            if state.connected:
                return state
            self._route.pop(int(index), None)
        for name in self._route_order():
            if name in tried:
                continue
            state = self._read_route(name, index)
            if not state.connected:
                continue
            self._route[int(index)] = name
            if name == "hid" and not self._fallback_logged:
                self._fallback_logged = True
                logger.warning(
                    "HID直結（ProCon）でパッドを読みます"
                    if not self.allow_sdl
                    else "SDLでパッドを読めないため、HID直結（ProCon）へ切り替えました"
                )
            return state
        return state

    def scan(self) -> list[int]:
        """繋がっている番号の一覧。XInput・SDL・HID の和集合。"""
        if not self.allow_sdl and self._hid_present:
            return [0]
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
        if not self.allow_sdl and self._hid_present:
            return {0: "Pro Controller (HID直結)"}
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

    def identities(self) -> dict[int, str]:
        """番号 → 個体識別子。一覧更新で同じ個体を選び続けるための鍵。

        SDL は接続中だけ一意な instance_id。HID直結は列挙した個体の
        パスを使う。XInput は個体を跨いで安定な ID を取れないため
        載せない（番号のまま追従する）。
        """
        if not self.allow_sdl:
            return {}
        try:
            out = {int(k): str(v) for k, v in self.sdl.identities().items()}
        except Exception:
            out = {}
        if 0 not in out:
            try:
                from core.nintendo_hid import NintendoHidReader

                devs = NintendoHidReader.enumerate()
                if devs:
                    raw_path = devs[0].get("path", b"") or b""
                    if isinstance(raw_path, bytes):
                        key = raw_path.hex()
                    else:
                        key = str(raw_path)
                    out[0] = f"hid:{key}" if key else "hid"
            except Exception:
                pass
        return out

    def route_of(self, index: int) -> str:
        return self._route.get(int(index), "")
