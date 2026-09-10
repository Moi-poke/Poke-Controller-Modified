"""Vision saveFrame が日本語安全な保存を使う検証。

cv2.imwrite は非 ASCII で静かに失敗する。Camera 側の imwrite
（imencode 経由）を使い回せば、同じ場所へ書けること。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from core.CommandVision import VisionMixin


class _FakeCam:
    """最新絵を返すだけの撮り口。"""

    def __init__(self, img: Any) -> None:
        self._img = img

    def readFrame(self, copy: bool = False) -> Any:
        _ = copy
        return self._img


def test_saveframe_survives_imwrite_failure(tmp_path: Path, monkeypatch: Any) -> None:
    """cv2.imwrite が死んでも保存できる（安全側へ使い回す）。"""
    # 非 ASCII での沈黙失敗を確実に再現するため、必ず False にする。
    monkeypatch.setattr(cv2, "imwrite", lambda *a: False)

    img = np.zeros((10, 10, 3), dtype=np.uint8)
    mix = VisionMixin()
    mix._initVision(_FakeCam(img))

    folder = str(tmp_path / "日本語フォルダ")
    out = mix.saveFrame(name="テスト", folder=folder)
    assert out != ""
    assert os.path.isfile(out)


def test_saveframe_normal_still_writes(tmp_path: Path) -> None:
    """通常時も日時つき png を残す。"""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    mix = VisionMixin()
    mix._initVision(_FakeCam(img))

    folder = str(tmp_path / "Debug")
    out = mix.saveFrame(name="frame", folder=folder)
    assert out != ""
    assert os.path.isfile(out)
