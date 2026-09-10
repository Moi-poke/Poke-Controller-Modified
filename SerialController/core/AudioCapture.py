#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AudioCapture.py - 音声入力の所有と最新区間の供給.

Camera.py の音声版。入力デバイスを1つだけ開き、リングバッファへ
書き続ける。検知（AudioMixin）もモニター再生もこの1本から読む。
デバイスをコマンドごとに開き直すと排他で競合するため、開閉の
所有はここへ寄せる。

sounddevice はトップレベルで import しない。Linux/mac で system
PortAudio が無い環境では import 自体が落ちるため、読めたときだけ
使う（欠如時は False・空リスト＋ログで無効化し、落とさない）。
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from typing import Any

import numpy as np
from core import audio_dsp
from loguru import logger
from scipy.signal import resample_poly

# 録音・検知の基準。audio_dsp.SAMPLE_RATE をそのまま使う。
# 別々の数値で持つと乖離する（検知の前提が崩れる）ため参照で揃える。
# デバイス自レート（48kHz等）で開いた場合も、内部はこのレートへ直して扱う。
AUDIO_RATE = audio_dsp.SAMPLE_RATE
AUDIO_CHANNELS = 1
# 1チャンクの秒数。1024（23ms）から512（12ms）へ縮めた。
# CABLEループバック実測：latency low＋512で片道約105ms・欠落0.2%。
# 256以下は改善が頭打ち（出力側の素遅延が支配的）のため採らない。
AUDIO_CHUNK = 512
# ストリームの遅延指定。'low' で出力186ms→93msを確認（同上実測）。
# 開けない機種では open 失敗として扱われ、従来通り False＋ログに落ちる。
AUDIO_LATENCY = "low"


def _resample_factors(src_rate: int, dst_rate: int) -> tuple[int, int]:
    """resample_poly 用の (up, down)。等速なら (1, 1)。"""
    src, dst = int(src_rate), int(dst_rate)
    if src <= 0 or dst <= 0 or src == dst:
        return (1, 1)
    g = math.gcd(src, dst)
    return (dst // g, src // g)


def to_internal(mono: np.ndarray, src_rate: int) -> np.ndarray:
    """デバイスレート波形を内部レート（44.1k）へ直す。等速は素通し。"""
    up, down = _resample_factors(src_rate, AUDIO_RATE)
    if up == down:
        return np.asarray(mono, dtype=np.float32)
    return resample_poly(np.asarray(mono, dtype=np.float64), up, down).astype(
        np.float32
    )


def to_device(chunk44: np.ndarray, dst_rate: int, frames: int) -> np.ndarray:
    """内部レート波形をデバイスレートへ直し、ちょうど frames 件にする。"""
    up, down = _resample_factors(AUDIO_RATE, dst_rate)
    if up == down:
        out = np.asarray(chunk44, dtype=np.float32).ravel()
    else:
        out = resample_poly(
            np.asarray(chunk44, dtype=np.float64).ravel(), up, down
        ).astype(np.float32)
    if out.size > frames:
        return out[:frames]
    if out.size < frames:
        padded = np.zeros(frames, dtype=np.float32)
        padded[: out.size] = out
        return padded
    return out


def native_rate(sd: Any, want_input: bool, index: int | None) -> int:
    """デバイスの自レート。分からなければ内部レートへ落とす。"""
    try:
        if index is None:
            default = sd.default.device
            index = default[0] if want_input else default[1]
        index = int(index)
        if index < 0:
            return AUDIO_RATE
        rate = int(float(sd.query_devices()[index].get("default_samplerate") or 0))
        if rate > 0:
            return rate
    except Exception:
        pass
    return AUDIO_RATE


# モニターのジッタ吸収段数。入出力は別クロックで回るため、深さ1では
# 位相ずれのたびに無音が入る（実測で出力の約50%が欠落）。12段で
# 約280msの遅延と引き換えに欠落を吸収する。モニター用途の遅延として許容。
JITTER_CHUNKS = 12
# 出力コールバックの待ち上限（秒）。1チャンクの周期より短くする。
MONITOR_GET_TIMEOUT = 0.02


def _import_sounddevice() -> Any | None:
    """sounddevice を読む。無い・壊れている環境では None。"""
    try:
        import sounddevice as sd

        return sd
    except Exception as e:
        logger.debug(f"音声機能は無効です（sounddevice: {e}）")
        return None


def audio_available() -> bool:
    """音声I/Oが使えるか。バックエンドの有無だけを見る。"""
    return _import_sounddevice() is not None


def _device_names(want_input: bool) -> list[str]:
    """入力／出力に出せるデバイス名の一覧。失敗時は空リスト。"""
    return [name for _, name in device_entries(want_input)]


def device_entries(want_input: bool) -> list[tuple[int, str]]:
    """(番号, 名前) の一覧。番号は sounddevice のデバイス番号そのもの。

    同名デバイスが複数ある（USBオーディオの重複登録等）ため、名前だけでは
    一意に選べない。設定には番号を保存し、表示は "番号: 名前" にする。
    """
    sd = _import_sounddevice()
    if sd is None:
        return []
    return _device_entries_sd(sd, want_input)


def _device_entries_sd(sd: Any, want_input: bool) -> list[tuple[int, str]]:
    """指定バックエンドから (番号, 名前) を集める。失敗時は空リスト。"""
    try:
        devices = sd.query_devices()
    except Exception as e:
        logger.warning(f"音声デバイスを列挙できません: {e}")
        return []
    found = []
    for index, dev in enumerate(devices):
        try:
            channels = (
                dev["max_input_channels"] if want_input else dev["max_output_channels"]
            )
            if int(channels or 0) > 0:
                found.append((index, str(dev["name"])))
        except (KeyError, TypeError, ValueError):
            continue
    return found


def format_display(index: int, name: str, est_ms: float = -1.0) -> str:
    """選択欄の表示名。推定遅延が分かれば "番号: 名前 [est. XXms]"。"""
    base = f"{index}: {name}"
    if est_ms is None or est_ms < 0:
        return base
    return f"{base} [est. {int(round(est_ms))}ms]"


def display_entries(entries: list[tuple[int, str]]) -> list[str]:
    """選択欄の表示名（"番号: 名前"）。番号で同名を区別する。"""
    return [format_display(index, name) for index, name in entries]


def parse_display(text: str) -> int | None:
    """表示名から番号を取り出す。形が違えば None。"""
    head, sep, _ = str(text).partition(":")
    if not sep:
        return None
    try:
        return int(head.strip())
    except ValueError:
        return None


def display_for(want_input: bool, spec: str | int | None) -> str:
    """設定値に対応する表示名。見つからなければ設定値そのまま。"""
    if spec is None:
        return ""
    text = str(spec).strip()
    if not text:
        return ""
    for index, name in device_entries(want_input):
        if text == str(index) or text == name:
            return f"{index}: {name}"
    return text


def probe_details(want_input: bool) -> list[tuple[int, str, float]]:
    """開ける (番号, 名前, 推定遅延ms) だけを返す。重いので裏で回すこと。

    自レートで試し開きし、そのときの stream.latency を推定遅延にする。
    48kHz専用機も自レートで開ければ候補に入る。WDM-KS等の非対応は落とす。
    """
    sd = _import_sounddevice()
    if sd is None:
        return []
    found: list[tuple[int, str, float]] = []
    for index, name in _device_entries_sd(sd, want_input):
        try:
            # 運用時と同じ条件で試す（不一致だと「開ける」と出た物が
            # 実際には開けない逆も起きる）。
            rate = native_rate(sd, want_input, index)
            params = {
                "samplerate": rate,
                "channels": AUDIO_CHANNELS,
                "dtype": "float32",
                "blocksize": AUDIO_CHUNK,
                "device": index,
                "latency": AUDIO_LATENCY,
            }
            if want_input:
                stream = sd.InputStream(**params)
            else:
                stream = sd.OutputStream(**params)
            try:
                est_ms: float = round(float(stream.latency) * 1000.0)
            except Exception:
                est_ms = -1.0
            try:
                stream.close()
            except Exception:
                pass
            found.append((index, name, est_ms))
        except Exception as e:
            logger.debug(f"音声デバイスを使えません [{index}]{name}: {e}")
    return found


def probe_openable(want_input: bool) -> list[tuple[int, str]]:
    """実際に開ける (番号, 名前) だけを返す。重いので裏で回すこと。"""
    return [(index, name) for index, name, _ in probe_details(want_input)]


def resolve_device(want_input: bool, spec: str | int | None) -> int | str | None:
    """設定値を open に渡せる形へ直す。空は既定（None）。

    番号はそのまま通す（open時に検証）。名前は番号へ一本化して
    同名重複の曖昧さを潰す。未知の名前はそのまま渡し、open時の
    成否に任せる（旧設定の後方互換）。
    """
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    text = str(spec).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    for index, name in device_entries(want_input):
        if name == text:
            return index
    return text


def list_input_devices() -> list[str]:
    """録音に使えるデバイス名の一覧。"""
    return _device_names(True)


def list_output_devices() -> list[str]:
    """再生に使えるデバイス名の一覧。"""
    return _device_names(False)


def apply_volume(frames: np.ndarray, volume: float) -> np.ndarray:
    """音量を掛けて [-1, 1] に収める純粋関数。"""
    out = frames.astype(np.float64) * float(volume)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


class AudioCapture:
    """入力1ストリームの所有者。直近 RING_SECONDS 秒を配る。"""

    def __init__(
        self,
        rate: int = AUDIO_RATE,
        ring_seconds: float = 5.0,
        input_factory: Any = None,
        output_factory: Any = None,
        jitter_chunks: int = JITTER_CHUNKS,
    ) -> None:
        self._rate = int(rate)
        capacity = max(AUDIO_CHUNK, int(self._rate * float(ring_seconds)))
        self._ring = np.zeros(capacity, dtype=np.float32)
        self._pos = 0
        self._filled = 0
        self._total = 0
        # 取り込み時刻の Tap（遅延実測用）。(コールバック時刻, 通算件数)。
        # 参照代入は不可分だが、total との組は Lock 内で読む。
        self._tap: deque[tuple[float, int]] = deque(maxlen=1024)
        self._lock = threading.Lock()
        # _stream / _out_stream は別スレッドからの参照・差し替えが
        # 重なる（benign race）。参照代入は不可分で、古い参照を
        # 読んでも次回に直るだけのため Lock は入れない。特に
        # PortAudio コールバック内では待たせないことが優先。
        self._stream: Any = None
        self._input_name = ""
        # デバイス自レート（48kHz等）と内部レート（44.1k）の差は
        # リサンプルで吸収する。検知・録音・pipe は内部レートで統一する。
        self._in_rate = self._rate
        self._factory = input_factory
        self._factory_out = output_factory
        self._monitor_volume = 0.8
        self._out_stream: Any = None
        self._out_device = ""
        self._out_rate = self._rate
        self._out_carry: np.ndarray = np.zeros(0, dtype=np.float32)
        self._jitter = max(1, int(jitter_chunks))
        self._pipe: queue.Queue[np.ndarray] = queue.Queue(maxsize=self._jitter)
        self._mon_stats = {
            "silence": 0,
            "drops": 0,
            "in_overflow": 0,
            "out_underflow": 0,
        }

    def openInput(self, device: str | int | None) -> bool:
        """入力を開く。既に開いていれば閉じてから開き直す。成否を返す。

        デバイス自レートで開き、内部レートへ直して扱う。48kHz専用機も
        開けるようになる（検知の前提レートは変えない）。
        """
        self.close()
        # factory 注入時（テスト）は実バックエンドの有無を問わない。
        # PortAudio なしOSでも合成ストリームで検証できるよう gate を抜ける。
        if self._factory is None:
            sd = _import_sounddevice()
            if sd is None:
                logger.debug("音声入力を開けません（バックエンドなし）")
                return False
        else:
            sd = None
        name = "" if device is None else str(device)
        resolved = resolve_device(True, device)
        rate = self._rate
        if self._factory is None and isinstance(resolved, int):
            assert sd is not None  # gate 通過済み
            rate = native_rate(sd, True, resolved)
        elif self._factory is None and resolved is None and sd is not None:
            rate = native_rate(sd, True, None)
        try:
            if self._factory is not None:
                stream = self._factory(
                    samplerate=self._rate,
                    channels=AUDIO_CHANNELS,
                    blocksize=AUDIO_CHUNK,
                    device=resolved,
                    latency=AUDIO_LATENCY,
                    callback=self._on_input,
                )
            else:
                assert sd is not None  # factory なしは gate 通過済み
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=AUDIO_CHANNELS,
                    dtype="float32",
                    blocksize=AUDIO_CHUNK,
                    device=resolved,
                    latency=AUDIO_LATENCY,
                    callback=self._on_input,
                )
            stream.start()
        except Exception as e:
            logger.error(f"音声入力を開けません ({name or '既定'}): {e}")
            return False
        self._stream = stream
        self._in_rate = int(rate)
        self._input_name = name
        if rate != self._rate:
            logger.info(f"音声入力は {rate}Hz で開き、内部は {self._rate}Hz で扱います")
        logger.debug(f"音声入力を開きました: {name or '既定'}")
        return True

    def _on_input(self, indata: Any, frames: int, _time: Any, status: Any) -> None:
        """PortAudioスレッドから呼ばれる。重い処理は置かない。"""
        if status and getattr(status, "input_overflow", False):
            self._mon_stats["in_overflow"] += 1
        elif status:
            logger.debug(f"音声入力の状態: {status}")
        mono = np.asarray(indata, dtype=np.float32).ravel()
        if mono.size == 0:
            return
        # 自レートと内部レートが違えば直してから格納する。
        # ring・pipe は内部レートで統一し、検知側の前提を崩さない。
        frame = to_internal(mono, self._in_rate)
        with self._lock:
            end = self._pos + frame.size
            if end <= self._ring.size:
                self._ring[self._pos : end] = frame
            else:
                first = self._ring.size - self._pos
                self._ring[self._pos :] = frame[:first]
                self._ring[: end - self._ring.size] = frame[first:]
            self._pos = end % self._ring.size
            self._filled = min(self._filled + frame.size, self._ring.size)
            self._total += frame.size
            self._tap.append((time.perf_counter(), self._total))
        # モニターへの受け渡しは常時行う。ONの瞬間に溜まっている分から
        # 鳴り始められる（深さぶんの遅延）。溢れたら古い方を捨てる。
        # put_nowait / get_nowait のみで、コールバック内で待たない。
        try:
            self._pipe.put_nowait(frame.copy())
        except queue.Full:
            self._mon_stats["drops"] += 1
            try:
                self._pipe.get_nowait()
            except queue.Empty:
                pass
            try:
                self._pipe.put_nowait(frame.copy())
            except queue.Full:
                pass

    def isOpened(self) -> bool:
        return self._stream is not None

    def selected_input(self) -> str:
        return self._input_name

    def close(self) -> None:
        self._stop_output()
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception as e:
            logger.warning(f"音声入力の解放で例外: {e}")

    def readWindow(self, seconds: float) -> np.ndarray | None:
        """直近 seconds 秒の複製を返す。未取得・未openは None。"""
        want = int(self._rate * float(seconds))
        if want <= 0 or self._stream is None:
            return None
        with self._lock:
            if self._filled == 0:
                return None
            want = min(want, self._filled)
            end = self._pos
            start = (end - want) % self._ring.size
            if (start < end) or (self._filled < self._ring.size and start == 0):
                out = self._ring[end - want : end].copy()
            else:
                out = np.concatenate((self._ring[start:], self._ring[:end])).copy()
        return out[-want:] if out.size > want else out

    def input_latency(self) -> float:
        """入力遅延の秒数。取れなければ 0。"""
        try:
            stream = self._stream
            if stream is None:
                return 0.0
            return float(stream.latency)
        except Exception:
            return 0.0

    def read_stamped(
        self, seconds: float
    ) -> tuple[np.ndarray, int, list[tuple[float, int]], float, int] | None:
        """(窓の複製, 通算件数, Tap複製, 入力遅延, 内部レート)。

        遅延実測が収録サンプルへ時刻を付けるための口。取れなければ None。
        """
        window = self.readWindow(seconds)
        if window is None:
            return None
        with self._lock:
            total = self._total
            tap = list(self._tap)
        if not tap:
            return None
        return (window, total, tap, self.input_latency(), self._rate)

    def _level(self) -> np.ndarray | None:
        return self.readWindow(0.1)

    def peak(self) -> float:
        window = self._level()
        if window is None or window.size == 0:
            return 0.0
        return float(np.max(np.abs(window)))

    def rms(self) -> float:
        window = self._level()
        if window is None or window.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))

    def record(self, seconds: float) -> np.ndarray:
        """seconds 秒ぶん集めて返す。未openは空配列。"""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        deadline = time.monotonic() + float(seconds)
        while time.monotonic() < deadline:
            time.sleep(0.05)
        window = self.readWindow(seconds)
        if window is None:
            return np.zeros(0, dtype=np.float32)
        return window

    # -- モニター再生 ---------------------------------------------------
    def setMonitorVolume(self, volume: float) -> None:
        try:
            v = float(volume)
        except (TypeError, ValueError):
            return
        self._monitor_volume = min(1.0, max(0.0, v))

    def isMonitorEnabled(self) -> bool:
        return self._out_stream is not None

    def setMonitorEnabled(self, on: bool, device: str | int | None = None) -> bool:
        """モニター再生のON/OFF。ON時は出力ストリームを開く。成否を返す。"""
        if not on:
            self._stop_output()
            return True
        if self._stream is None:
            print("モニターを開始できません: 音声入力が開いていません")
            logger.warning("モニター開始に失敗（入力未open）")
            return False
        # factory 注入時（テスト）は実バックエンドの有無を問わない。
        if self._factory_out is None:
            sd = _import_sounddevice()
            if sd is None:
                logger.debug("モニター出力を開けません（バックエンドなし）")
                return False
        else:
            sd = None
        self._stop_output()
        resolved = resolve_device(False, device)
        rate = self._rate
        if self._factory_out is None and isinstance(resolved, int):
            assert sd is not None  # gate 通過済み
            rate = native_rate(sd, False, resolved)
        elif self._factory_out is None and resolved is None and sd is not None:
            rate = native_rate(sd, False, None)
        try:
            if self._factory_out is not None:
                out = self._factory_out(
                    samplerate=self._rate,
                    channels=AUDIO_CHANNELS,
                    blocksize=AUDIO_CHUNK,
                    device=resolved,
                    latency=AUDIO_LATENCY,
                    callback=self._on_output,
                )
            else:
                assert sd is not None  # factory なしは gate 通過済み
                out = sd.OutputStream(
                    samplerate=rate,
                    channels=AUDIO_CHANNELS,
                    dtype="float32",
                    blocksize=AUDIO_CHUNK,
                    device=resolved,
                    latency=AUDIO_LATENCY,
                    callback=self._on_output,
                )
            out.start()
        except Exception as e:
            logger.error(f"モニター出力を開けません: {e}")
            return False
        self._out_stream = out
        self._out_rate = int(rate)
        self._out_carry = np.zeros(0, dtype=np.float32)
        if rate != self._rate:
            logger.info(
                f"モニター出力は {rate}Hz で開き、内部は {self._rate}Hz で扱います"
            )
        return True

    def _stop_output(self) -> None:
        out, self._out_stream = self._out_stream, None
        if out is None:
            return
        try:
            out.stop()
        except Exception:
            pass
        try:
            out.close()
        except Exception as e:
            logger.warning(f"モニター出力の解放で例外: {e}")

    def getMonitorStats(self) -> dict[str, int]:
        """モニターの欠落計数の複製（診断用）。"""
        return dict(self._mon_stats)

    def _on_output(self, outdata: Any, frames: int, _time: Any, status: Any) -> None:
        """出力コールバック。pipe から順に書く。尽きたら短く待って無音。

        pipe は内部レート（44.1k）の塊、frames は自レートの件数。
        持ち越し（carry）と合わせて必要ぶん集め、自レートへ直して書く。
        """
        if status and getattr(status, "output_underflow", False):
            self._mon_stats["out_underflow"] += 1
        # 自レート frames 件を作るのに要る内部レートの件数（+余裕1）。
        need = int(frames * self._rate / max(1, self._out_rate)) + 1
        pieces = [self._out_carry]
        self._out_carry = np.zeros(0, dtype=np.float32)
        have = pieces[0].size
        try:
            pieces.append(self._pipe.get(timeout=MONITOR_GET_TIMEOUT))
            have += pieces[-1].size
        except queue.Empty:
            pass
        while have < need:
            try:
                pieces.append(self._pipe.get_nowait())
                have += pieces[-1].size
            except queue.Empty:
                break
        if have < need:
            self._mon_stats["silence"] += 1
        window = (
            np.concatenate(pieces)
            if len(pieces) > 1
            else np.asarray(pieces[0], dtype=np.float32)
        )
        if window.size < need:
            padded = np.zeros(need, dtype=np.float32)
            padded[: window.size] = window
            window = padded
        self._out_carry = np.asarray(window[need:], dtype=np.float32).copy()
        native = to_device(window[:need], self._out_rate, frames)
        shaped = np.tile(native.reshape(-1, 1), (1, AUDIO_CHANNELS))
        buf = np.asarray(outdata)
        buf[:] = apply_volume(shaped, self._monitor_volume)
