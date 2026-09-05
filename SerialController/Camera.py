#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Camera.py - カメラ制御.

cv2 でキャプチャし、別スレッド（Camera）または別プロセス（CameraQueue）で
「最新の1枚だけ」を供給する。キューに積み上げないので遅延が溜まらない。

Camera       : threading 版。通常はこちらを使う。
CameraQueue  : multiprocessing + 共有メモリ版。GIL を避けたい場合に使う。
"""

from __future__ import annotations

import datetime
import multiprocessing
import os
import threading
import time
import traceback
from multiprocessing import Value, shared_memory
from multiprocessing.sharedctypes import Synchronized
from typing import Any

import cv2
import numpy as np
from loguru import logger


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

CAPTURE_DIR = "./Captures/"
CAPTURE_SIZE = (1280, 720)
SHARED_MEMORY_NAME = "camera_image"

THREAD_JOIN_TIMEOUT = 0.5  # GUI を止めずに待てる上限(秒)
THREAD_RELEASE_TIMEOUT = 5.0  # 裏で取得スレッドの終了を待つ上限(秒)
PROCESS_JOIN_TIMEOUT = 3.0

# 共有メモリの面数。ワーカーは未使用面へ書いてから face を差し替える。
# N 面あれば読み手は N-1 フレームぶん安全に参照できる（3 で十分）。
FACE_COUNT = 3


def frame_shape(capture_size: tuple = CAPTURE_SIZE) -> tuple:
    """(w, h) から numpy の (h, w, 3) を作る。共有メモリのサイズ計算に使う。"""
    return (int(capture_size[1]), int(capture_size[0]), 3)


def _configure_capture(camera: Any, capture_size: tuple, fps: int = 0) -> None:
    """解像度・FOURCC・FPS を設定し、実際に効いたか読み戻して確認する。

    FOURCC を指定しないと CAP_DSHOW の既定は YUY2（非圧縮）になる。
    1280x720 では USB2.0 の帯域を使い切るため、指定 fps に関係なく
    実測 5〜10fps で頭打ちになるキャプチャボードが多い。MJPG を
    「最初に」指定するのが要点で、後から変えると解像度が既定へ戻る
    ドライバがある。
    """
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, capture_size[0])
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_size[1])
    if fps and int(fps) > 0:
        camera.set(cv2.CAP_PROP_FPS, float(fps))
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 遅延を溜めない

    # set() は効かなくても False を返すだけで例外にならない。
    # 「指定したのに効いていない」を見えるようにするため読み戻す。
    actual_w = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = float(camera.get(cv2.CAP_PROP_FPS))
    code = int(camera.get(cv2.CAP_PROP_FOURCC))
    fourcc = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))

    logger.debug(f"Capture: {actual_w}x{actual_h} {fourcc} {actual_fps:.1f}fps")
    if (actual_w, actual_h) != (int(capture_size[0]), int(capture_size[1])):
        logger.warning(
            f"Capture size {capture_size[0]}x{capture_size[1]} not applied "
            f"(actual {actual_w}x{actual_h})"
        )
    if fourcc != "MJPG":
        logger.warning(
            f"MJPG not applied (actual {fourcc!r}). "
            "帯域が不足し fps が頭打ちになる可能性がある"
        )
    if fps and int(fps) > 0 and 0 < actual_fps and actual_fps + 1.0 < int(fps):
        logger.warning(f"FPS {int(fps)} not applied (actual {actual_fps:.1f})")


# ---------------------------------------------------------------------------
# 画像の保存（Camera / CameraQueue 共通）
# ---------------------------------------------------------------------------


def imwrite(filename: str, img: np.ndarray, params: Any = None) -> bool:
    """日本語を含むパスへも書き込める cv2.imwrite の代替."""
    try:
        ext = os.path.splitext(filename)[1]
        result, encoded = cv2.imencode(ext, img, params if params else [])
        if not result:
            logger.error(f"Image encode failed: {filename}")
            return False

        with open(filename, mode="w+b") as f:
            encoded.tofile(f)
        return True
    except Exception as e:
        logger.error(f"Image Write Error: {e}")
        return False


def _get_save_filespec(filename: str) -> str:
    """保存パスを取得する。絶対パスならそのまま返す。"""
    if os.path.isabs(filename):
        return filename
    return os.path.join(CAPTURE_DIR, filename)


def _build_filename(filename: str | None) -> str:
    """保存名を決める。未指定なら日時、拡張子が無ければ .png を補う。"""
    if not filename:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"{stamp}.png"
    if os.path.splitext(filename)[1]:
        return filename
    return f"{filename}.png"


def _crop_image(image: np.ndarray, crop: Any, crop_ax: list | None) -> np.ndarray:
    """crop 指定に従って切り出す。crop=1 は座標指定、crop=2 は幅高さ指定。"""
    if crop is None:
        return image

    ax = crop_ax if crop_ax else [0, 0, CAPTURE_SIZE[0], CAPTURE_SIZE[1]]
    if crop in (1, "1"):
        # ax = [x1, y1, x2, y2]
        return image[ax[1] : ax[3], ax[0] : ax[2]]
    if crop in (2, "2"):
        # ax = [x, y, w, h]
        return image[ax[1] : ax[1] + ax[3], ax[0] : ax[0] + ax[2]]
    return image


def save_capture(
    image: np.ndarray | None,
    filename: str | None = None,
    crop: Any = None,
    crop_ax: list | None = None,
) -> bool:
    """フレームをファイルへ保存する。成否を返す。"""
    if image is None:
        logger.error("Capture Failed: no frame")
        return False

    image = _crop_image(image, crop, crop_ax)
    save_path = _get_save_filespec(_build_filename(filename))

    save_dir = os.path.dirname(save_path)
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    try:
        if not imwrite(save_path, image):
            logger.error(f"Capture Failed: {save_path}")
            return False
    except cv2.error as e:
        logger.error(f"Capture Failed: {e}")
        return False

    logger.info(f"Capture succeeded: {save_path}")
    return True


# ---------------------------------------------------------------------------
# 最新フレーム保持
# ---------------------------------------------------------------------------


class LatestFrame:
    """最新の1枚だけを保持する箱。

    queue.Queue(maxsize=1) を継承して get/put の意味を変える実装だったが、
    標準の契約（block / timeout）を無視することになり誤用を招くのでやめた。
    用途が「最新1枚の上書き」だけなら Lock + 変数1つで十分かつ速い。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None

    def put(self, frame: np.ndarray | None) -> None:
        """最新フレームを差し替える。None では上書きしない。"""
        if frame is None:
            return
        with self._lock:
            self._frame = frame

    def get(self) -> np.ndarray | None:
        """最新フレームを返す。まだ1枚も来ていなければ None。"""
        with self._lock:
            return self._frame

    def clear(self) -> None:
        with self._lock:
            self._frame = None


# 旧名との互換（外部から CustomQueue を import している箇所があるため）
CustomQueue = LatestFrame


# ---------------------------------------------------------------------------
# スレッド版（本命）
# ---------------------------------------------------------------------------


class Camera:
    """cv2.VideoCapture を別スレッドで回し、最新フレームを供給する。"""

    def __init__(self, fps: int = 45, capture_size: tuple = CAPTURE_SIZE) -> None:
        self.camera: cv2.VideoCapture | None = None
        self.fps = int(fps)
        self.capture_size = capture_size
        self.capture_dir = CAPTURE_DIR
        self.frame_queue: LatestFrame = LatestFrame()

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._error: str | None = None

    # -- 開閉 ---------------------------------------------------------------

    def openCamera(self, cameraId: int) -> bool:
        """カメラを開いて取得スレッドを起動する。成否を返す。"""
        if self.isOpened():
            logger.debug("Camera is already opened")
            self.destroy()

        self.frame_queue.clear()
        self._stop_event.clear()
        self._error = None

        if os.name == "nt":
            logger.debug("NT OS")
            camera = cv2.VideoCapture(cameraId, cv2.CAP_DSHOW)
        else:
            logger.debug("Not NT OS")
            camera = cv2.VideoCapture(cameraId)

        if not camera.isOpened():
            logger.error(f"Camera ID {cameraId} cannot open.")
            camera.release()
            return False

        _configure_capture(camera, self.capture_size, self.fps)

        with self._lock:
            self.camera = camera

        logger.debug(f"Camera ID {cameraId} opened successfully.")
        self._start_thread()
        return True

    def isOpened(self) -> bool:
        camera = self.camera  # 参照を確保してから判定（destroy との競合対策）
        if camera is None:
            return False
        return bool(camera.isOpened())

    def isRunning(self) -> bool:
        """取得スレッドが生きているか。映像が固まった時の切り分け用。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def getError(self) -> str | None:
        """取得スレッドが異常終了した理由。正常なら None。"""
        return self._error

    def destroy(self) -> bool:
        """取得スレッドを止めてからカメラを解放する。

        read() はカメラ1周期＋ドライバ待ちのぶんブロックするため、
        ここで長く待つと「閉じるボタンが効かない」ように見える。
        待つのは THREAD_JOIN_TIMEOUT までとし、間に合わなければ解放を
        別スレッドへ委ねて呼び出し元（GUI スレッド）は即座に返す。
        戻り値は「その場で解放しきれたか」。
        """
        self._stop_event.set()

        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=THREAD_JOIN_TIMEOUT)
            if thread.is_alive():
                logger.warning(
                    f"Camera thread did not stop within {THREAD_JOIN_TIMEOUT}s. "
                    "Releasing the device in background."
                )
                self._release_later(thread)
                return False

        self._release()
        return True

    def _release_later(self, thread: threading.Thread) -> None:
        """取得スレッドの終了を待ってから解放する見張りを裏で走らせる。

        read() の最中に release() すると cv2 が落ちうるので、待たずに
        解放してはいけない。かといって GUI スレッドで待つと固まるので、
        「待つ役」だけを別スレッドへ切り出す。
        """
        with self._lock:
            camera, self.camera = self.camera, None
        if camera is None:
            return

        def waiter() -> None:
            thread.join(timeout=THREAD_RELEASE_TIMEOUT)
            if thread.is_alive():
                logger.error(
                    "Camera thread is still alive. "
                    "Skip releasing the device to avoid a crash."
                )
                return
            camera.release()
            logger.debug("Camera destroyed (deferred)")

        threading.Thread(target=waiter, name="CameraReleaser", daemon=True).start()

    def _release(self) -> None:
        """デバイスを解放する。取得スレッドが止まっていることが前提。"""
        with self._lock:
            camera, self.camera = self.camera, None
        if camera is not None:
            camera.release()
            logger.debug("Camera destroyed")

    # -- フレーム -----------------------------------------------------------

    def readFrame(self, copy: bool = False) -> np.ndarray | None:
        """最新フレームを返す。書き換えるなら copy=True を指定する。"""
        frame = self.frame_queue.get()
        if frame is None:
            return None
        return frame.copy() if copy else frame

    def saveCapture(
        self,
        filename: str | None = None,
        crop: Any = None,
        crop_ax: list | None = None,
        img: np.ndarray | None = None,
    ) -> bool:
        image = img if img is not None else self.readFrame(copy=True)
        return save_capture(image, filename, crop, crop_ax)

    def setFps(self, fps: int) -> None:
        """取得FPSを変更する。取得スレッドは次の周期から新しい間隔で回る。"""
        self.fps = int(fps)
        logger.info(f"Camera fps set to {self.fps}")

    # -- 取得スレッド -------------------------------------------------------

    def _start_thread(self) -> None:
        self._thread = threading.Thread(
            target=self._update, name="CameraThread", daemon=True
        )
        self._thread.start()
        logger.debug("Camera thread started")

    def _update(self) -> None:
        """最新フレームを取り続ける。失敗フレームは捨てる。

        read() 自体がカメラの周期までブロックするため、経過時間を
        差し引くだけの待ちは「カメラfps > 指定fps」のときしか効かない。
        read の所要時間の移動平均を持ち、平均が interval より短いとき
        （＝カメラのほうが速いとき）だけ待つ。取りこぼしは件数を数えて
        logger.debug に出し、見えるようにする。
        """
        read_avg = 0.0  # read() 所要時間の指数移動平均(秒)
        slow = 0  # 想定(interval)の2倍以上かかった回数
        frames = 0

        while not self._stop_event.is_set():
            started = time.perf_counter()
            # setFps() で変更された値を毎周期反映する
            interval = 1.0 / self.fps if self.fps > 0 else 0.0

            camera = self.camera
            if camera is None:
                break

            # release() との競合を狭めるため、read の直前にもう一度見る
            if self._stop_event.is_set():
                break

            try:
                ret, frame = camera.read()
            except Exception:
                # 黙って抜けると「映像が固まっただけ」に見えて原因に辿り着けない
                self._error = "Camera Read Error"
                logger.error(self._error)
                logger.error(traceback.format_exc())
                self._stop_event.set()
                break

            read_time = time.perf_counter() - started
            if read_avg <= 0.0:
                read_avg = read_time
            else:
                read_avg = read_avg * 0.9 + read_time * 0.1

            frames += 1
            if interval > 0.0 and read_time > interval * 2.0:
                slow += 1
            if frames >= 300:
                if slow:
                    logger.debug(
                        f"Camera read: avg {read_avg * 1000:.1f} ms, "
                        f"slow {slow}/{frames} frames "
                        f"(interval {interval * 1000:.1f} ms)"
                    )
                frames, slow = 0, 0

            if not ret or frame is None:
                # 取得失敗。最新フレームを None で上書きはしない
                if self._stop_event.wait(interval):
                    break
                continue

            self.frame_queue.put(frame)

            # read がカメラ周期ぶん待っている場合、その上さらに待つと
            # 取りこぼす。カメラのほうが速いときだけ差分を待つ。
            if read_avg < interval:
                rest = interval - (time.perf_counter() - started)
                if rest > 0 and self._stop_event.wait(rest):
                    break

        logger.debug("Camera thread stopped")

    # -- 旧APIとの互換 ------------------------------------------------------

    def camera_thread_start(self) -> None:
        if not self.isOpened():
            logger.error("Camera is not opened")
            return
        if self.isRunning():
            return
        self._stop_event.clear()
        self._error = None
        self._start_thread()

    def camera_thread_stop(self) -> None:
        self.destroy()


# ---------------------------------------------------------------------------
# プロセス版（共有メモリ経由）
# ---------------------------------------------------------------------------


class CameraController:
    """別プロセスで動くキャプチャワーカー。共有メモリへ最新フレームを書く。

    共有メモリは FACE_COUNT 面ぶん確保してある。ワーカーは「いま公開して
    いない面」へ書き込んでから公開面の番号(face)を差し替える方式（多面
    バッファ）。読み手は face が指す面を読むだけでよく、Lock もコピーも
    要らない。書き込み中の面を読むことが無いので絵が混ざる(tearing)
    ことも起きない。毎フレーム 2.7MB を copy する案は 30fps で 80MB/s の
    複製になるため採らなかった。
    """

    def __init__(
        self,
        stop_event: Any,
        camera_process_status: Synchronized,
        fps: Synchronized,
        face: Synchronized,
        shm_name: str,
        shape: tuple,
        cameraId: int = 0,
        capture_size: tuple = CAPTURE_SIZE,
    ) -> None:
        self.camera: cv2.VideoCapture | None = None
        self.cameraId = cameraId
        self.fps = fps
        self.capture_size = capture_size
        self.stop_event = stop_event
        self.camera_process_status = camera_process_status
        self.face = face
        self.shape = shape

        self.shared_memory = shared_memory.SharedMemory(name=shm_name)
        # (FACE_COUNT, h, w, 3) の1本の配列として持つ。面は images[i]。
        self.images: np.ndarray | None = np.ndarray(
            (FACE_COUNT, *shape), dtype=np.uint8, buffer=self.shared_memory.buf
        )

    def run(self) -> None:
        """開く → 回す → 必ず片付ける。"""
        try:
            self.openCamera()
            self.processCamera()
        finally:
            self.closeCamera()
            self.closeSharedMemory()

    def openCamera(self) -> None:
        if self.camera is not None and self.camera.isOpened():
            logger.debug("Camera is already opened")
            return

        if os.name == "nt":
            self.camera = cv2.VideoCapture(self.cameraId, cv2.CAP_DSHOW)
        else:
            self.camera = cv2.VideoCapture(self.cameraId)

        if not self.camera.isOpened():
            logger.error(f"Camera ID {self.cameraId} cannot open.")
            self.camera_process_status.value = False
            return

        _configure_capture(self.camera, self.capture_size, self.fps.value)

        logger.debug(f"Camera ID {self.cameraId} opened successfully.")
        self.camera_process_status.value = True

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        """共有メモリの形に合わせる。合わないと代入時に ValueError で即死する。"""
        if frame.shape == self.shape:
            return frame
        return cv2.resize(
            frame, (self.shape[1], self.shape[0]), interpolation=cv2.INTER_AREA
        )

    def processCamera(self) -> None:
        """停止要求は Event で受ける。

        time.sleep は停止フラグを見ないため、停止の反映が最大 interval
        ぶん遅れていた（fps=5 なら 200ms）。Event.wait なら待機中でも
        set() で即座に抜けられ、join のタイムアウトに掛かりにくい。
        """
        while not self.stop_event.is_set():
            started = time.perf_counter()

            try:
                if self.camera is None or not self.camera.isOpened():
                    break
                ret, frame = self.camera.read()
                if ret and frame is not None and self.images is not None:
                    # 公開中でない面へ書いてから公開を切り替える。
                    # 読み手は公開面しか見ないので重なりようがない。
                    nxt = (int(self.face.value) + 1) % FACE_COUNT
                    self.images[nxt] = self._fit(frame)
                    self.face.value = nxt
            except Exception:
                logger.error("Camera Process Error")
                logger.error(traceback.format_exc())
                break

            fps = self.fps.value
            interval = 1.0 / fps if fps > 0 else 0.0
            rest = interval - (time.perf_counter() - started)
            if rest > 0 and self.stop_event.wait(rest):
                break

    def closeCamera(self) -> None:
        self.stop_event.set()
        self.camera_process_status.value = False
        if self.camera is not None:
            self.camera.release()
            self.camera = None

    def closeSharedMemory(self) -> None:
        """buf を参照する ndarray を先に手放す。残すと close() で BufferError。"""
        self.images = None
        try:
            self.shared_memory.close()
        except BufferError:
            logger.warning("shared memory still exported; close skipped")


def _camera_worker(
    stop_event: Any,
    camera_process_status: Synchronized,
    fps: Synchronized,
    face: Synchronized,
    shm_name: str,
    shape: tuple,
    cameraId: int,
    capture_size: tuple,
) -> None:
    """子プロセスの入口。Process(target=クラス) だと生成＝実行で分かりにくい。"""
    CameraController(
        stop_event,
        camera_process_status,
        fps,
        face,
        shm_name,
        shape,
        cameraId,
        capture_size,
    ).run()


class CameraQueue:
    """CameraController を別プロセスで回し、共有メモリ経由で受け取る。"""

    def __init__(
        self,
        fps: int,
        cameraId: int,
        capture_size: tuple = CAPTURE_SIZE,
        shm_name: str = SHARED_MEMORY_NAME,
    ):
        logger.debug("CameraQueue initializing")
        self.cameraId = cameraId
        self.capture_size = capture_size
        self.shape = frame_shape(capture_size)
        self._fps = int(fps)
        self.capture_dir = CAPTURE_DIR
        # 並列起動時に衝突しないよう、共有メモリ名は呼び出し側から変えられる
        self.shm_name = shm_name or SHARED_MEMORY_NAME
        self.camera_process: multiprocessing.Process | None = None
        self.shm: shared_memory.SharedMemory | None = None
        self.images: np.ndarray | None = None

        self.init_shared_memory()
        self.openCamera(self.cameraId)

    def init_shared_memory(self) -> None:
        # dtype を変えても壊れないよう nbytes から求める。FACE_COUNT 面ぶん確保する
        size = int(np.zeros((FACE_COUNT, *self.shape), dtype=np.uint8).nbytes)
        try:
            self.shm = shared_memory.SharedMemory(
                create=True, size=size, name=self.shm_name
            )
        except FileExistsError:
            # 前回の異常終了で残っている場合は掴み直して作り直す
            stale = shared_memory.SharedMemory(name=self.shm_name)
            stale.close()
            stale.unlink()
            self.shm = shared_memory.SharedMemory(
                create=True, size=size, name=self.shm_name
            )

        self.images = np.ndarray(
            (FACE_COUNT, *self.shape), dtype=np.uint8, buffer=self.shm.buf
        )

        self.fps: Synchronized = Value("i", self._fps)
        # いま読んでよい面の番号。ワーカーが書き終えてから差し替える
        self.face: Synchronized = Value("i", 0)
        self.stop_event = multiprocessing.Event()
        self.camera_process_status: Synchronized = Value("b", False)

    @property
    def finish_flag(self) -> bool:
        """旧APIとの互換。停止要求の有無を返す。"""
        return self.stop_event.is_set()

    def openCamera(self, camera_id: int) -> None:
        """ワーカープロセスを作り直して起動する（旧APIに合わせた名前）。"""
        self.cameraId = camera_id
        self._stop_process()
        self.stop_event.clear()

        self.camera_process = multiprocessing.Process(
            target=_camera_worker,
            args=(
                self.stop_event,
                self.camera_process_status,
                self.fps,
                self.face,
                self.shm_name,
                self.shape,
                self.cameraId,
                self.capture_size,
            ),
            name="CameraController",
            daemon=True,
        )
        self.startCamera()

    def startCamera(self) -> None:
        if self.camera_process is None:
            logger.error("Camera Process is not initialized")
            return
        if self.camera_process.is_alive():
            return
        self.camera_process.start()
        logger.debug("Camera process started")

    def isOpened(self) -> bool:
        return bool(self.camera_process_status.value)

    def setFps(self, fps: int) -> None:
        self.fps.value = int(fps)

    def readFrame(self, copy: bool = False) -> np.ndarray | None:
        """公開中の面（最新フレーム）を返す。

        ワーカーは公開面以外へ書いてから face を差し替えるので、ここで
        返したビューが書き換えられることはない（FACE_COUNT 面あるので
        FACE_COUNT-1 フレームぶんの猶予がある）。Lock もコピーも不要。
        フレームを長く持ち回る・書き換えるなら copy=True を指定する。
        """
        if self.shm is None or self.images is None:
            return None
        frame = self.images[int(self.face.value)]
        return frame.copy() if copy else frame

    def saveCapture(
        self,
        filename: str | None = None,
        crop: Any = None,
        crop_ax: list | None = None,
        img: np.ndarray | None = None,
    ) -> bool:
        image = img if img is not None else self.readFrame(copy=True)
        return save_capture(image, filename, crop, crop_ax)

    def _stop_process(self) -> None:
        if self.camera_process is None:
            return
        if self.camera_process.is_alive():
            self.stop_event.set()
            self.camera_process.join(timeout=PROCESS_JOIN_TIMEOUT)
            if self.camera_process.is_alive():
                logger.warning("Camera process did not stop. terminating.")
                self.camera_process.terminate()
                # terminate 後の join を省くと資源を掴んだまま残ることがある
                self.camera_process.join(timeout=PROCESS_JOIN_TIMEOUT)
        self.camera_process = None

    def destroy(self) -> None:
        self._stop_process()

        # 解放済みメモリを指したままのビューを残すとクラッシュする
        self.images = None
        if self.shm is not None:
            try:
                self.shm.close()
            except BufferError:
                logger.warning("shared memory still exported; close skipped")
            try:
                self.shm.unlink()
            except FileNotFoundError:
                pass
            self.shm = None
        logger.debug("Camera destroyed")


if __name__ == "__main__":
    import argparse

    # freeze_support() は「実行の入口となるモジュール」で呼ぶもの。
    # import されただけの Camera.py のトップレベルで呼ぶのは誤り。
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="カメラの動作確認")
    parser.add_argument("--id", type=int, default=0, help="カメラID")
    parser.add_argument("--fps", type=int, default=60, help="取得FPS")
    args = parser.parse_args()

    cam = Camera(args.fps)
    if cam.openCamera(args.id):
        try:
            time.sleep(1.0)
            cam.saveCapture("test")
        finally:
            cam.destroy()
