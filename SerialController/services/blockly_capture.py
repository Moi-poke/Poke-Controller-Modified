#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""キャプチャ画像からのテンプレート切出し（GUI非依存）。

正規化矩形（配信フレーム相対 0〜1）を受け、サーバ側で元画像に
戻して切り出し、`Template/blockly/` にPNG保存する。拒否規則は
Phase A（services/blockly_templates）の書式検査と同方向にする。
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np
from loguru import logger

BLOCKLY_TEMPLATE_DIR_REL = "Template/blockly"

#: /frame連打時のPNGキャッシュ秒数。1080pのimencodeは50〜100msかかるため、
#: 200msだけ使い回してエンコードを畳む。世代seqを出すカメラでは (seq, 時刻)
#: の両方を鍵にし、clear() 直後の即時要求でも前世代の絵を返さない。
#: 陳腐化は最大200msに有界（表示用の目安であり、/matchの遅延には響かない程度）。
FRAME_CACHE_SEC = 0.2

#: 実画像換算でこの未満の辺を持つ矩形は無効。
MIN_SIDE_PX = 8

FRAME_FAIL_MESSAGE = (
    "カメラから画像を取得できません（未接続 / Disable / 取得スレッド停止）。"
)
NO_CAMERA_MESSAGE = "カメラが割り当てられていません。"


def validate_template_name(name: str) -> str | None:
    """保存名が不正なら理由、正常なら None を返す。"""
    if not name.strip():
        return "テンプレート名を書いてください"
    probe = name.replace("\\", "/")
    if probe.startswith("/") or (len(probe) > 1 and probe[1] == ":"):
        return "絶対パスは使えません（`Template/` からの相対で書いてください）"
    if ".." in PurePosixPath(probe).parts:
        return "`..` は使えません"
    if ":" in name:
        return "`:` は使えません"
    return None


def parse_rect(rect: object) -> tuple[float, float, float, float]:
    """`{x, y, width, height}`（0〜1）を検査して4要素にする。おかしければ ValueError。"""
    if not isinstance(rect, dict):
        raise ValueError("矩形は {x, y, width, height} で送ってください")
    try:
        x = float(rect["x"])
        y = float(rect["y"])
        w = float(rect["width"])
        h = float(rect["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "矩形は {x, y, width, height} の数値で送ってください"
        ) from None
    if any(not math.isfinite(v) for v in (x, y, w, h)):
        raise ValueError("矩形に数値でない値があります")
    if w <= 0 or h <= 0:
        raise ValueError("矩形の幅または高さが0以下です")
    if x >= 1 or y >= 1 or x + w <= 0 or y + h <= 0:
        raise ValueError("矩形が画像の外を指しています")
    return (x, y, w, h)


def rect_to_pixels(
    x: float, y: float, w: float, h: float, width: int, height: int
) -> tuple[int, int, int, int]:
    """正規化矩形を画素へ戻す。はみ出しは画像内に丸める。JSの Math.round 寄せにする。"""
    x1 = min(width, max(0, int(math.floor(x * width + 0.5))))
    y1 = min(height, max(0, int(math.floor(y * height + 0.5))))
    x2 = min(width, max(0, int(math.floor((x + w) * width + 0.5))))
    y2 = min(height, max(0, int(math.floor((y + h) * height + 0.5))))
    if (x2 - x1) < MIN_SIDE_PX or (y2 - y1) < MIN_SIDE_PX:
        raise ValueError("矩形が小さすぎます（実画像で8px未満の辺があります）")
    return (x1, y1, x2, y2)


@dataclass
class CaptureResult:
    """切出し保存の結果。statusは saved / failed。"""

    status: str
    message: str
    rel: str = ""


def crop_png(png: bytes, rect: object) -> bytes:
    """PNGをデコード→矩形で切出し→PNGで返す。おかしければ ValueError。"""
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("画像を読み込めませんでした（取り直してください）")
    height, width = int(img.shape[0]), int(img.shape[1])
    x, y, w, h = parse_rect(rect)
    x1, y1, x2, y2 = rect_to_pixels(x, y, w, h, width, height)
    ok, buf = cv2.imencode(".png", img[y1:y2, x1:x2])
    if not ok:
        raise ValueError("画像の切出しに失敗しました（取り直してください）")
    return bytes(buf)


def _next_free(path: Path) -> Path:
    """重複時は `_2`・`_3` を挿した空き名を返す。"""
    if not path.exists():
        return path
    for i in range(2, 10000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(f"空き名がありません: {path}")


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """一時ファイル経由で書く。書けたらのみ置き換える。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_bcapture_")
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_template(
    app_dir: str | Path, name: str, png: bytes, rect: object
) -> CaptureResult:
    """切出し1枚を `Template/blockly/<名>.png` に保存する。検証に落ちたら何も書かない。"""
    stem = name.strip()
    reason = validate_template_name(stem)
    if reason is not None:
        return CaptureResult(status="failed", message=f"保存できません: {reason}")
    # 配置は `Template/blockly/<名>.png` に固定する。`/`・`\` を許すと
    # 配下に小部屋ができる。照合側（match）は配布規約のため相対を許すが、
    # 切出しの保存は素名だけにする。
    if "/" in stem or "\\" in stem:
        return CaptureResult(
            status="failed",
            message="保存できません: 名に `/`・`\\` は使えません"
            "（`Template/blockly/<名>.png` の固定配置のため）",
        )
    base = stem if stem.lower().endswith(".png") else stem + ".png"
    try:
        dest = _next_free(
            Path(app_dir)
            / PurePosixPath(BLOCKLY_TEMPLATE_DIR_REL).as_posix()
            / PurePosixPath(base).as_posix()
        )
    except (OSError, FileExistsError) as e:
        logger.warning(f"テンプレ保存に失敗: {e}")
        return CaptureResult(status="failed", message=f"保存できません: {e}")
    try:
        data = crop_png(png, rect)
    except ValueError as e:
        return CaptureResult(status="failed", message=f"保存できません: {e}")
    try:
        _atomic_write_bytes(dest, data)
    except OSError as e:
        logger.warning(f"テンプレ保存に失敗: {e}")
        return CaptureResult(status="failed", message=f"保存できません: {e}")
    try:
        from core.CommandVision import clear_template_cache

        # CPU側だけ捨てれば足りる。GPU側は VisionMixin の実体ごとの
        # 持ち物で、走り終えたら実体ごと捨てる。編集器は実行中を開けず、
        # 保存は空き時間だけに行い、次に走る実体は新しい物を持つ。
        # 加えて重複は `_next_free` で別名に逃がすため、ある道を上書きして
        # 古い画像を使い続ける形にもならない。通常の1台運用では、ここで
        # CPU側を捨てれば古い絵は残らない。実行時に差し替える別経路では
        # `clearTemplateCaches()`（CPU＋GPU）を使うこと。
        clear_template_cache()
    except Exception as e:
        logger.warning(f"テンプレ cache 無効化に失敗: {e}")
    rel = dest.relative_to(Path(app_dir) / "Template").as_posix()
    logger.info(f"テンプレ保存: Template/{rel}")
    return CaptureResult(
        status="saved", message=f"保存しました: Template/{rel}", rel=rel
    )


def build_get_frame(
    camera_getter: Callable[[], Any],
) -> Callable[[], bytes | None]:
    """最新フレーム1枚をPNG化する関数を作る。撮れなければ None を返す。

    /frameの連打で毎回imencodeすると重いため、直近のPNGを約200msだけ
    使い回す。世代seqを出すカメラでは (seq, 時刻) の両方を鍵にする。
    clear() で seq が進むため、clear 直後の即時要求でも前世代のPNGを
    返さない。seq を出さない旧カメラでは従来どおり時刻基準だけにする。
    陳腐化は最大FRAME_CACHE_SECに有界。失敗時はNoneを返し、
    古い絵で誤魔化さない（別closureの死活とは無関係）。
    """

    _lock = threading.Lock()
    _cached_png: bytes | None = None
    _cached_at: float = 0.0
    _cached_seq: int | None = None

    def get_frame() -> bytes | None:
        nonlocal _cached_png, _cached_at, _cached_seq
        now = time.monotonic()
        try:
            camera = camera_getter()
        except Exception:
            return None
        read = getattr(camera, "readFrame", None)
        read_seq = getattr(camera, "readFrameWithSeq", None)
        if not callable(read) and not callable(read_seq):
            return None
        seq: int | None = None
        has_seq = False
        frame: Any = None
        seq_getter = getattr(camera, "frame_seq", None)
        if callable(seq_getter):
            # frame 本体の複写なしに世代だけ見る。clear() で進む。
            try:
                seq = int(seq_getter())
                has_seq = True
            except Exception:
                has_seq = False
        elif callable(read_seq):
            # frame_seq は無いが世代つき読みがあるカメラ。frame と
            # まとめて取る（読むこと自体は安く、重いのは符号化のため）。
            # 読めない一瞬は時刻基準に落とし、新鮮な絵があればそちらで凌ぐ。
            try:
                frame, seq_val = read_seq()
                try:
                    seq = int(seq_val)
                    has_seq = True
                except Exception:
                    has_seq = False
            except Exception:
                frame = None
                has_seq = False
        with _lock:
            if _cached_png is not None and (now - _cached_at) < FRAME_CACHE_SEC:
                if not has_seq or seq == _cached_seq:
                    return _cached_png
        if frame is None:
            if not callable(read):
                return None
            try:
                frame = read(copy=True)
            except Exception:
                return None
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        try:
            ok, buf = cv2.imencode(".png", frame)
        except Exception:
            return None
        if not ok:
            return None
        png = bytes(buf)
        with _lock:
            _cached_png = png
            _cached_at = time.monotonic()
            _cached_seq = seq if has_seq else None
        return png

    return get_frame
