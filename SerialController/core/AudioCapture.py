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


def to_device(
    chunk_src: np.ndarray, dst_rate: int, frames: int, src_rate: int = AUDIO_RATE
) -> np.ndarray:
    """指定レート波形をデバイスレートへ直し、ちょうど frames 件にする。

    既定の送り元は内部レート（後方互換）。管が原生域を持つ場合は
    src_rate に入力自レートを渡し、原生→出力へ直接直す。
    """
    up, down = _resample_factors(src_rate, dst_rate)
    if up == down:
        out = np.asarray(chunk_src, dtype=np.float32).ravel()
    else:
        out = resample_poly(
            np.asarray(chunk_src, dtype=np.float64).ravel(), up, down
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
# 約139ms（512×12/44100）の遅延と引き換えに欠落を吸収する。
# 6段なら約70msだが余裕が半分になる。既定12のまま置く。
JITTER_CHUNKS = 12


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


# カメラ名との照合で落とす汎用語（これだけでは機種を特定できない）。
_GUESS_STOPWORDS = frozenset(
    {
        "usb",
        "video",
        "camera",
        "audio",
        "device",
        "devices",
        "microphone",
        "speaker",
        "input",
        "output",
        "hd",
        "the",
        "and",
        "or",
    }
)


def _guess_tokens(text: str) -> set[str]:
    """照合用の語集合。小文字化・3文字以上・汎用語除外。"""
    import re

    return {
        token
        for token in re.split(r"[^0-9a-z]+", str(text).lower())
        if len(token) >= 3 and token not in _GUESS_STOPWORDS
    }


def guess_capture_input(camera_name: str, entries: list[tuple[int, str]]) -> int | None:
    """カメラ名から取込口（キャプチャボード音声）の番号を推定する。

    ゲーム音はキャプチャボードからしか取れないため、入力は実質固定。
    映像デバイス名と音声デバイス名の語の重なりで探す。2語以上一致の
    最良を返し、なければ None（既定の入力へ落とす）。
    """
    wanted = _guess_tokens(camera_name)
    if not wanted:
        return None
    best: int | None = None
    best_score = 1
    for index, name in entries:
        score = len(wanted & _guess_tokens(name))
        if score > best_score:
            best_score = score
            best = index
    return best


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
    同名重複の曖昧さを潰す。表示名（"番号: 名前 [est. XXms]"）の
    まま保存された旧設定は番号へ戻す。未知の名前はそのまま渡し、
    open時の成否に任せる（旧設定の後方互換）。
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
    parsed = parse_display(text)
    if parsed is not None:
        return parsed
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
        self._ring_seconds = float(ring_seconds)
        capacity = max(AUDIO_CHUNK, int(self._rate * float(ring_seconds)))
        self._ring = np.zeros(capacity, dtype=np.float32)
        self._pos = 0
        self._filled = 0
        self._total = 0
        # 取り込み時刻の Tap（遅延実測用）。(コールバック時刻, 通算件数)。
        # 参照代入は不可分だが、total との組は Lock 内で読む。
        # 通算は原生域で数え、内部域へは呼び側で換算する（壁時刻を保つ）。
        self._tap: deque[tuple[float, int]] = deque(maxlen=1024)
        self._lock = threading.Lock()
        # 計数は実時間スレッドと表示の両方から触る。短い錠で守る。
        # 参照差し替え自体は不可分だが、+= の取りこぼしを防ぐため錠を入れる。
        self._stats_lock = threading.Lock()
        # _stream / _out_stream は別スレッドからの参照・差し替えが
        # 重なる（benign race）。参照代入は不可分で、古い参照を
        # 読んでも次回に直るだけのため Lock は入れない。特に
        # PortAudio コールバック内では待たせないことが優先。
        self._stream: Any = None
        self._input_name = ""
        # デバイス自レート（48kHz等）と内部レート（44.1k）の差は
        # 呼び側で吸収する。環・管は原生域で持ち、検知・録音の窓は
        # 内部レートへ直して渡す（検知の前提を崩さない）。
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

    def _prepare_ring(self, native_rate: int) -> None:
        """新しい自レートに合わせて環を作り直す（旧機の残りは捨てる）。

        開始前に呼ぶ（実時間コールバックと競合させない）。管の古い
        溜まりも捨てる。計数は通算の診断のため残す。
        """
        rate = max(1, int(native_rate))
        capacity = max(AUDIO_CHUNK, int(rate * self._ring_seconds))
        with self._lock:
            if capacity != self._ring.size:
                self._ring = np.zeros(capacity, dtype=np.float32)
            else:
                self._ring[:] = 0.0
            self._pos = 0
            self._filled = 0
            self._total = 0
            self._tap.clear()
        self._drain_pipe()

    def _drain_pipe(self) -> None:
        """管の古い溜まりを捨てる（開始前の準備、実時間外で呼ぶ）。"""
        try:
            while True:
                self._pipe.get_nowait()
        except queue.Empty:
            pass

    def _snapshot_native(self, count: int) -> np.ndarray | None:
        """直近 count 件（原生域）の複製。無ければ None。錠内は複写だけ。"""
        if count <= 0:
            return None
        with self._lock:
            if self._filled == 0 or self._stream is None:
                return None
            want = min(int(count), self._filled)
            end = self._pos
            start = (end - want) % self._ring.size
            if (start < end) or (self._filled < self._ring.size and start == 0):
                single = self._ring[end - want : end].copy()
                first = None
                second = None
            else:
                first = self._ring[start:].copy()
                second = (
                    self._ring[:end].copy()
                    if end > 0
                    else np.zeros(0, dtype=np.float32)
                )
                single = None
        if single is not None:
            return single
        assert first is not None and second is not None
        return first if second.size == 0 else np.concatenate((first, second))

    def _prefill_pipe(self, chunks: int) -> None:
        """有効化直後の無音緩和に直近を少量だけ前置する。無ければ何もしない。"""
        try:
            count = max(1, int(chunks)) * int(AUDIO_CHUNK)
        except (TypeError, ValueError):
            return
        native = self._snapshot_native(count)
        if native is None or native.size == 0:
            return
        for pos in range(0, native.size, AUDIO_CHUNK):
            piece = np.asarray(native[pos : pos + AUDIO_CHUNK], dtype=np.float32)
            if piece.size == 0:
                break
            try:
                self._pipe.put_nowait(piece)
            except queue.Full:
                break

    def openInput(self, device: str | int | None) -> bool:
        """入力を開く。既に開いていれば閉じてから開き直す。成否を返す。

        デバイス自レートで開き、窓は呼び側で内部レートへ直す。
        48kHz専用機も開ける（検知の前提レートは変えない）。
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
        # 新しい自レートに合わせて環を作り直す（開始前に済ませる）
        self._prepare_ring(rate)
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
        """PortAudioスレッドから呼ばれる。重い処理は置かない。

        実時間スレッドでは環への複写と管への受け渡しだけ行う。
        変換（原生→内部）・記録・結合は呼び側で行う。
        監視OFFでは管へ送らず、幻の欠落を数えない。
        有効化直後は前置が空のため短い無音が出る（古い溜まりより正しい）。
        """
        if status and getattr(status, "input_overflow", False):
            with self._stats_lock:
                self._mon_stats["in_overflow"] += 1
        # overflow以外の状態は記録しない（実時間スレッドを待たせない）
        mono = np.asarray(indata, dtype=np.float32).ravel()
        if mono.size == 0:
            return
        # 借用領域のため1回だけ複製する（環は複写、管は同じ物を渡す）
        buf = mono.copy()
        with self._lock:
            end = self._pos + buf.size
            if end <= self._ring.size:
                self._ring[self._pos : end] = buf
            else:
                first = self._ring.size - self._pos
                self._ring[self._pos :] = buf[:first]
                self._ring[: end - self._ring.size] = buf[first:]
            self._pos = end % self._ring.size
            self._filled = min(self._filled + buf.size, self._ring.size)
            self._total += buf.size
            self._tap.append((time.perf_counter(), self._total))
        # 監視が有効な間だけ送る。溢れたら古い方を捨てる。
        # put_nowait / get_nowait のみで、コールバック内で待たない。
        if self._out_stream is None:
            return
        try:
            self._pipe.put_nowait(buf)
        except queue.Full:
            with self._stats_lock:
                self._mon_stats["drops"] += 1
            try:
                self._pipe.get_nowait()
            except queue.Empty:
                pass
            try:
                self._pipe.put_nowait(buf)
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
        """直近 seconds 秒の複製を返す。未取得・未openは None。

        原生域で切り出し、内部レートへは錠の外で直す（実時間側を待たせない）。
        結合も錠の外で行い、錠内は2断片の複写だけにする。
        """
        want_internal = int(self._rate * float(seconds))
        if want_internal <= 0 or self._stream is None:
            return None
        with self._lock:
            if self._filled == 0:
                return None
            in_rate = int(self._in_rate)
            want_native = min(int(in_rate * float(seconds)), self._filled)
            if want_native <= 0:
                return None
            end = self._pos
            start = (end - want_native) % self._ring.size
            if (start < end) or (self._filled < self._ring.size and start == 0):
                single = self._ring[end - want_native : end].copy()
                first = None
                second = None
            else:
                first = self._ring[start:].copy()
                second = (
                    self._ring[:end].copy()
                    if end > 0
                    else np.zeros(0, dtype=np.float32)
                )
                single = None
        if single is not None:
            native = single
        else:
            assert first is not None and second is not None
            native = first if second.size == 0 else np.concatenate((first, second))
        if in_rate == self._rate:
            out = native
        else:
            # 消費者側で重い変換を行う（実時間スレッドではしない）
            out = to_internal(native, in_rate)
            if out.size > want_internal:
                out = out[-want_internal:]
        return out[-want_internal:] if out.size > want_internal else out

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
        通算・Tapは内部域へ換算して返す（窓と整合させ、壁時刻を保つ）。
        """
        window = self.readWindow(seconds)
        if window is None:
            return None
        with self._lock:
            total_native = int(self._total)
            tap_native = list(self._tap)
            in_rate_native = int(self._in_rate)
        if not tap_native:
            return None
        if in_rate_native == self._rate:
            total = total_native
            tap = tap_native
        else:
            # 原生→内部へ比例で直す（時刻換算が保たれる）
            ratio = float(self._rate) / float(max(1, in_rate_native))
            total = int(round(total_native * ratio))
            tap = [(t, int(round(tot * ratio))) for t, tot in tap_native]
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
        """モニター再生のON/OFF。ON時は出力ストリームを開く。成否を返す。

        有効化直後は前置が空のため短い無音が出る（古い溜まりより正しい）。
        送りの残り・割合は開始前に整え、開始後の差し替え競合を避ける。
        """
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
        # 開始前に整える（実時間スレッドが走る前に済ませる）
        self._drain_pipe()
        self._out_rate = int(rate)
        self._out_carry = np.zeros(0, dtype=np.float32)
        # 直近を少量だけ前置する（無ければ無音で始める）
        try:
            self._prefill_pipe(2)
        except Exception as e:
            logger.debug(f"前置に失敗しました: {e}")
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
            # 入力側の受け渡しが溜め始めるよう、先に立ててから開始する
            self._out_stream = out
            try:
                out.start()
            except Exception:
                self._out_stream = None
                try:
                    out.stop()
                except Exception:
                    pass
                try:
                    out.close()
                except Exception:
                    pass
                raise
        except Exception as e:
            logger.error(f"モニター出力を開けません: {e}")
            return False
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
        """モニターの欠落計数の複製（診断用）。錠で snapshot する。"""
        with self._stats_lock:
            return dict(self._mon_stats)

    def _on_output(self, outdata: Any, frames: int, _time: Any, status: Any) -> None:
        """出力コールバック。pipe から順に書く。尽きたら無音（待たない）。

        pipe・carry は入力自レート域、frames は出力自レート件数。
        持ち越しと合わせて必要ぶん集め、自レートへ直接直して書く。
        実時間スレッドでは待たない（get_nowait のみ）。
        """
        if status and getattr(status, "output_underflow", False):
            with self._stats_lock:
                self._mon_stats["out_underflow"] += 1
        # 割合の参照は開始前に整えてある（benign race。古くても次回に直る）。
        in_rate = int(self._in_rate)
        out_rate = int(self._out_rate)
        # 自レート frames 件を作るのに要る入力自レート件数（+余裕1）。
        need = int(frames * in_rate / max(1, out_rate)) + 1
        pieces = [self._out_carry]
        self._out_carry = np.zeros(0, dtype=np.float32)
        have = int(pieces[0].size)
        try:
            pieces.append(self._pipe.get_nowait())
            have += int(pieces[-1].size)
        except queue.Empty:
            pass
        while have < need:
            try:
                pieces.append(self._pipe.get_nowait())
                have += int(pieces[-1].size)
            except queue.Empty:
                break
        if have < need:
            with self._stats_lock:
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
        native = to_device(window[:need], out_rate, int(frames), in_rate)
        shaped = np.tile(native.reshape(-1, 1), (1, AUDIO_CHANNELS))
        buf = np.asarray(outdata)
        buf[:] = apply_volume(shaped, self._monitor_volume)
