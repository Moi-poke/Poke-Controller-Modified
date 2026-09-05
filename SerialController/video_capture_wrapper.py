from __future__ import annotations

import multiprocessing
import signal
from multiprocessing import shared_memory
from typing import cast

import cv2
import numpy as np


def _update(
    args: tuple,
    props: dict[int, float],
    shm_name: str,
    shape: tuple[int, int, int],
    ready: multiprocessing.synchronize.Event,
    cancel: multiprocessing.synchronize.Event,
):
    """
    画像を取得してshared memoryを更新する
    """

    signal.signal(signal.SIGINT, signal.SIG_IGN)

    video_capture = cv2.VideoCapture(*args)
    if not video_capture.isOpened():
        raise OSError()

    _set_props(video_capture, props)

    # 共有メモリにアタッチ
    existing_shm = shared_memory.SharedMemory(name=shm_name)
    shm_buffer: np.ndarray = np.ndarray(shape, dtype=np.uint8, buffer=existing_shm.buf)

    try:
        while not cancel.is_set():
            ret, mat = cast("tuple[bool, cv2.Mat]", video_capture.read())
            if not ret:
                continue

            ready.clear()
            np.copyto(
                shm_buffer, np.ndarray(mat.shape, dtype=np.uint8, buffer=mat.data)
            )
            ready.set()

    finally:
        video_capture.release()


def _set_props(video_capture: cv2.VideoCapture, props: dict[int, float]):
    for key, value in props.items():
        try:
            video_capture.set(key, value)
        except Exception:
            pass


def _get_props(video_capture: cv2.VideoCapture) -> dict[int, float]:
    ids = [cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FRAME_WIDTH]
    return cast(
        "dict[int, float]", dict([(prop, video_capture.get(prop)) for prop in ids])
    )


def _get_information(
    args: tuple, in_props: dict[int, float]
) -> tuple[tuple[int, int, int], dict[int, float]]:
    video_capture = cv2.VideoCapture(*args)
    if not video_capture.isOpened():
        raise OSError()

    # 入力されたプロパティを設定してから、現在のプロパティ一覧を取得する
    _set_props(video_capture, in_props)
    out_props = _get_props(video_capture)

    try:
        ret, mat = cast("tuple[bool, cv2.Mat]", video_capture.read())
        if not ret:
            raise OSError()

        return mat.shape, out_props

    finally:
        video_capture.release()


class VideoCaptureWrapper:
    def __init__(self, *args) -> None:
        self.__args = ()
        self.__shape = cast(int, 0), cast(int, 0), cast(int, 0)
        self.__props: dict[int, float] = {}

        self.__shm = None
        self.__ready = multiprocessing.Event()
        self.__cancel = multiprocessing.Event()
        self.__enqueue = multiprocessing.Process()

        self.__released = cast(bool, True)

        if len(args) == 0:
            return

        self.open(*args)

    def open(self, *args):
        if not self.__released:
            raise RuntimeError()

        self.__args = args
        self.__shape, self.__props = _get_information(self.__args, self.__props)

        height, width, channels = self.__shape
        size = height * width * channels

        # 共有メモリを作成
        self.__shm = shared_memory.SharedMemory(create=True, size=size)
        shm_name = self.__shm.name

        self.__ready = multiprocessing.Event()
        self.__cancel = multiprocessing.Event()
        self.__enqueue = multiprocessing.Process(
            target=_update,
            args=(
                self.__args,
                self.__props,
                shm_name,
                self.__shape,
                self.__ready,
                self.__cancel,
            ),
            daemon=True,
        )
        self.__enqueue.start()

        self.__released = cast(bool, False)

    def get(self, propId: int):
        if self.__released:
            raise RuntimeError()

        return self.__props[propId]

    def set(self, propId: int, value: float):
        if self.__released:
            raise RuntimeError()

        self.__props[propId] = value
        self.release()
        self.open(*self.__args)

        return cast(bool, True)

    def read(self):
        if self.__released:
            raise RuntimeError()

        self.__ready.wait()

        # 共有メモリから画像を取得
        shm_buffer = np.ndarray(self.__shape, dtype=np.uint8, buffer=self.__shm.buf)
        return cast(bool, True), shm_buffer.copy()

    def isOpened(self):
        return not self.__released

    def release(self):
        if self.__released:
            return

        self.__cancel.set()
        self.__enqueue.join()

        # 共有メモリのクリーンアップ
        if self.__shm:
            self.__shm.close()
            self.__shm.unlink()

        self.__released = True

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
