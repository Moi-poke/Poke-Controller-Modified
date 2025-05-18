#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# import collections
import multiprocessing
from multiprocessing import shared_memory, Value
import multiprocessing.managers
from multiprocessing.sharedctypes import Synchronized
import queue
import threading
from time import sleep
import traceback
from typing import Any
import cv2
import datetime
import os

# from deprecated import deprecated
import numpy as np  # noqa: F401
from logging import getLogger, DEBUG, NullHandler
from loguru import logger
from icecream import ic
from video_capture_wrapper import VideoCaptureWrapper

# import signal

multiprocessing.freeze_support()


def imwrite(filename: str, img: cv2.Mat, params: Any = None) -> bool:
    _logger = getLogger(__name__)
    _logger.addHandler(NullHandler())
    _logger.setLevel(DEBUG)
    _logger.propagate = True
    try:
        ext = os.path.splitext(filename)[1]
        result, n = cv2.imencode(ext, img, params)

        if result:
            with open(filename, mode="w+b") as f:
                n.tofile(f)
            return True
        else:
            return False
    except Exception as e:
        print(e)
        logger.error(f"Image Write Error: {e}")
        return False


CAPTURE_DIR = "./Captures/"


def _get_save_filespec(filename: str) -> str:
    """
    画像ファイルの保存パスを取得する。

    入力が絶対パスの場合は、`CAPTURE_DIR`につなげずに返す。

    Args:
        filename (str): 保存名／保存パス

    Returns:
        str: _description_
    """
    if os.path.isabs(filename):
        return filename
    else:
        return os.path.join(CAPTURE_DIR, filename)


class CameraController:
    def __init__(
        self,
        finish_flag: Synchronized,
        camera_process_status: Synchronized,
        fps: Synchronized,
        cameraId: int = 0,
        capture_size: tuple = (1280, 720),
        # capture_size: tuple = (1920, 1080),
    ):
        self.camera: cv2.VideoCapture | None = None

        self.cameraId = cameraId
        self.fps = fps
        self.capture_size = capture_size

        self.shared_memory = shared_memory.SharedMemory(name="camera_image")
        self.image: np.ndarray = np.ndarray(
            (capture_size[1], capture_size[0], 3),
            dtype=np.uint8,
            buffer=self.shared_memory.buf,
        )
        ic(self.shared_memory.size)

        self.finish_flag = finish_flag
        self.camera_process_status = camera_process_status

        self.openCamera()
        self.processCamera()

    def openCamera(self) -> None:
        if self.camera and self.camera.isOpened():
            logger.debug("Camera is already opened")
            return

        if os.name == "nt":
            logger.debug("NT OS")
            self.camera = cv2.VideoCapture(self.cameraId, cv2.CAP_DSHOW)  # type: ignore
        else:
            logger.debug("Not NT OS")
            self.camera = cv2.VideoCapture(self.cameraId)  # type: ignore

        if not self.camera.isOpened():
            print(f"Camera ID {self.cameraId} cannot open.")
            logger.error(f"Camera ID {self.cameraId} cannot open.")
            self.camera_process_status.value = False
            return
        else:
            print(f"Camera ID {self.cameraId} opened successfully.")
            logger.debug(f"Camera ID {self.cameraId} opened successfully.")
            self.camera_process_status.value = True

            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_size[0])
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_size[1])

    def isOpened(self) -> bool:
        if not self.camera:
            return False
        else:
            return self.camera.isOpened()

    def processCamera(self) -> None:
        while not self.finish_flag.value:
            try:
                if self.camera is not None and self.camera.isOpened():
                    ret, frame = self.camera.read()
                    if ret:
                        self.image[:] = frame
                sleep(1 / self.fps.value)
            except Exception:
                logger.error("Camera Process Error")
                logger.error(traceback.format_exc())
        self.closeCamera()

    def closeCamera(self) -> None:
        self.finish_flag.value = True
        if self.camera is not None and self.camera.isOpened():
            self.camera.release()
            self.camera = None


class CustomQueue(queue.Queue):
    def __init__(self, maxsize: int = 1):
        super().__init__(maxsize=maxsize)
        self.last_frame: Any = None  # 最新フレーム

    def put(self, frame: Any, block: bool = True, timeout: float | None = None) -> None:
        try:
            if self.full():
                self.get_nowait()  # キューが満杯なら古いフレームを取り出す
        except Exception:
            pass
        super().put(frame, block, timeout)
        self.last_frame = frame

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if not self.empty():
            return super().get(block, timeout)
        return self.last_frame  # キューが空なら最新フレームを返す


class Camera:
    def __init__(self, fps: int = 45):
        self.camera: VideoCaptureWrapper | cv2.VideoCapture | None = None
        self.fps = int(fps)
        self.capture_size = (1280, 720)
        # self.capture_size = (1920, 1080)
        self.capture_dir = "Captures"
        # self.frame_queue: CustomQueue = CustomQueue()
        self.camera_thread_alive: bool = False
        self.cycle: float = 0.00
        self.read_lock: threading.Lock = threading.Lock()
        self._image_bgr: cv2.Mat | None = None

    def openCamera(self, cameraId: int) -> None:
        # self.frame_queue = CustomQueue()
        if self.camera is not None and self.camera.isOpened():
            logger.debug("Camera is already opened")
            self.destroy()

        if os.name == "nt":
            logger.debug("NT OS")
            self.camera = cv2.VideoCapture(cameraId, cv2.CAP_DSHOW)
            # self.camera = VideoCaptureWrapper(cameraId, cv2.CAP_DSHOW) # マルチプロセスにする場合
        else:
            logger.debug("Not NT OS")
            # self.camera = VideoCaptureWrapper(cameraId) # マルチプロセスにする場合
            self.camera = cv2.VideoCapture(cameraId)

        if not self.camera.isOpened():
            print("Camera ID " + str(cameraId) + " can't open.")
            logger.error(f"Camera ID {cameraId} cannot open.")
            return
        print("Camera ID " + str(cameraId) + " opened successfully")
        logger.debug(f"Camera ID {cameraId} opened successfully.")
        # print(self.camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        # self.camera.set(cv2.CAP_PROP_FPS, 60)
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_size[0])
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_size[1])
        _, self._image_bgr = self.camera.read()
        self.camera_thread_start()

    # self.camera.set(cv2.CAP_PROP_SETTINGS, 0)

    def isOpened(self) -> bool:
        logger.debug("Camera is opened")
        if self.camera is None:
            return False
        if isinstance(self.camera, VideoCaptureWrapper) or isinstance(
            self.camera, cv2.VideoCapture
        ):
            return bool(self.camera.isOpened())
        return False

    def readFrame(self) -> cv2.Mat | None:
        if isinstance(self.camera, VideoCaptureWrapper) or isinstance(
            self.camera, cv2.VideoCapture
        ):
            self.read_lock.acquire()
            if self._image_bgr is None:
                frame = None
            else:
                frame = self._image_bgr.copy()
            self.read_lock.release()
            return frame
        else:
            return None

    def saveCapture(
        self,
        filename: str | None = None,
        crop: list | None = None,
        crop_ax: list | None = None,
        img: cv2.Mat | None = None,
    ) -> None:
        if crop_ax is None:
            crop_ax = [0, 0, self.capture_size[0], self.capture_size[1]]
        else:
            pass
            # print(crop_ax)

        dt_now = datetime.datetime.now()
        if filename is None or filename == "":
            filename = dt_now.strftime("%Y-%m-%d_%H-%M-%S") + ".png"
        else:
            filename = filename + ".png"

        image_bgr = self.readFrame()
        if image_bgr is None:
            return
        if crop is None:
            image = image_bgr
        elif crop == 1 or crop == "1":
            image = image_bgr[crop_ax[1] : crop_ax[3], crop_ax[0] : crop_ax[2]]
        elif crop == 2 or crop == "2":
            image = image_bgr[
                crop_ax[1] : crop_ax[1] + crop_ax[3],
                crop_ax[0] : crop_ax[0] + crop_ax[2],
            ]
        elif img is not None:
            image = img
        else:
            image = image_bgr

        save_path = _get_save_filespec(filename)

        if not os.path.exists(os.path.dirname(save_path)) or not os.path.isdir(
            os.path.dirname(save_path)
        ):
            # 保存先ディレクトリが存在しないか、同名のファイルが存在する場合（existsはファイルとフォルダを区別しない）
            os.makedirs(os.path.dirname(save_path))
            logger.debug("Created Capture folder")

        try:
            imwrite(save_path, image)
            logger.debug(f"Capture succeeded: {save_path}")
            print("capture succeeded: " + save_path)
        except cv2.error as e:
            print("Capture Failed")
            logger.error(f"Capture Failed :{e}")

    def destroy(self) -> None:
        if self.camera is not None and self.camera.isOpened():
            self.camera_thread_stop()
            self.camera.release()
            self.camera = None

            logger.debug("Camera destroyed")

    def camera_thread_start(self) -> None:
        if self.camera is None:
            logger.error("Camera is not opened")
            return
        logger.debug("Camera thread starting")
        self.camera_thread_alive = True
        self.thread = threading.Thread(target=self.camera_update, name="CameraThread")
        self.thread.start()

    def camera_thread_stop(self) -> None:
        if not self.camera_thread_alive:
            logger.error("Camera thread stopped")
            return
        logger.debug("Camera thread stopping")
        self.camera_thread_alive = False
        self.thread.join()
        logger.debug("Camera thread stopped")

    def camera_update(self) -> None:
        if self.camera is None:
            logger.error("Camera is not opened")
            return
        logger.debug("Camera update thread started")
        while self.camera_thread_alive:
            _, frame = self.camera.read()
            self.read_lock.acquire()
            self._image_bgr = frame
            self.read_lock.release()
            sleep(self.cycle)
            if self.camera is None:
                break


# カメラの読み込み処理をマルチプロセス化する
# WIP
class CameraQueue:
    # using shared memory
    def __init__(self, fps: int, cameraId: int, capture_size: tuple = (1280, 720)):
        logger.debug("CameraQueue initializing")
        self.cameraId = cameraId
        self.capture_size = capture_size
        self._fps = int(fps)

        self.manager = multiprocessing.Manager()

        self.capture_dir = "Captures"

        self.init_shared_memory()

        self.openCamera(self.cameraId)
        self.startCamera()
        logger.debug("init CameraQueue")

    def init_shared_memory(self) -> None:
        self.shm = shared_memory.SharedMemory(
            create=True, size=int(np.prod((720, 1280, 3))), name="camera_image"
        )
        self.image_bgr: np.ndarray = np.ndarray(
            (720, 1280, 3), dtype=np.uint8, buffer=self.shm.buf
        )

        self.fps: Synchronized = Value("i", self._fps)
        self.finish_flag: Synchronized = Value("b", False)
        self.camera_process_status: Synchronized = Value("b", False)
        self.camera_process: multiprocessing.Process | None = None

    def openCamera(self, camera_id: int) -> None:
        self.cameraId = camera_id

        if self.camera_process is not None and self.camera_process.is_alive():
            logger.debug("Camera is already opened")
            self.finish_flag.value = True
            self.camera_process.join()
            self.camera_process = None

        self.camera_process = multiprocessing.Process(
            target=CameraController,
            args=(
                self.finish_flag,
                self.camera_process_status,
                self.fps,
                self.cameraId,
                self.capture_size,
            ),
            name="CameraController",
        )

    def startCamera(self) -> None:
        if self.camera_process is None:
            logger.error("Camera Process is not initialized")
            return
        self.camera_process.start()

    def isOpened(self) -> bool:
        if self.camera_process_status.value:
            return True
        else:
            return False

    def readFrame(self) -> cv2.Mat | None:
        logger.debug("Reading Frame")
        return self.image_bgr

    def saveCapture(
        self,
        filename: str | None = None,
        crop: list | None = None,
        crop_ax: list | None = None,
        img: cv2.Mat | None = None,
    ) -> None:
        if crop_ax is None:
            crop_ax = [0, 0, 1280, 720]
        else:
            pass
            # print(crop_ax)

        dt_now = datetime.datetime.now()
        if filename is None or filename == "":
            filename = dt_now.strftime("%Y-%m-%d_%H-%M-%S") + ".png"
        else:
            filename = filename + ".png"

        if crop is None:
            image = self.image_bgr
        elif crop == 1 or crop == "1":
            image = self.image_bgr[crop_ax[1] : crop_ax[3], crop_ax[0] : crop_ax[2]]
        elif crop == 2 or crop == "2":
            image = self.image_bgr[
                crop_ax[1] : crop_ax[1] + crop_ax[3],
                crop_ax[0] : crop_ax[0] + crop_ax[2],
            ]
        elif img is not None:
            image = img
        else:
            image = self.image_bgr

        save_path = _get_save_filespec(filename)

        if not os.path.exists(os.path.dirname(save_path)) or not os.path.isdir(
            os.path.dirname(save_path)
        ):
            # 保存先ディレクトリが存在しないか、同名のファイルが存在する場合（existsはファイルとフォルダを区別しない）
            os.makedirs(os.path.dirname(save_path))
            logger.debug("Created Capture folder")

        try:
            imwrite(save_path, image)
            logger.debug(f"Capture succeeded: {save_path}")
            print("capture succeeded: " + save_path)
        except cv2.error as e:
            print("Capture Failed")
            logger.error(f"Capture Failed :{e}")

    def destroy(self) -> None:
        if self.isOpened() and self.camera_process is not None:
            # self.camera_process.terminate()
            self.finish_flag.value = True
            self.camera_process = None
            logger.debug("Camera destroyed")


if __name__ == "__main__":
    c = Camera(60)
    c.openCamera(2)

    # c.finish_flag.value = True
    # if c.camera_process is not None:
    #     c.camera_process.terminate
    # c.shm.close()
    # c.shm.unlink()
