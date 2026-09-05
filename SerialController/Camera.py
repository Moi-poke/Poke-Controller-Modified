"""Camera.py - 後方互換の再公開口.

実体は core/Camera.py へ移った。既存のコードは
    from Camera import Camera
と書いたまま変えずに使える。新規のコードは core 側から読むこと。
"""

from core.Camera import (
    CAPTURE_DIR as CAPTURE_DIR,
    CAPTURE_SIZE as CAPTURE_SIZE,
    FACE_COUNT as FACE_COUNT,
    PROCESS_JOIN_TIMEOUT as PROCESS_JOIN_TIMEOUT,
    SHARED_MEMORY_NAME as SHARED_MEMORY_NAME,
    THREAD_JOIN_TIMEOUT as THREAD_JOIN_TIMEOUT,
    THREAD_RELEASE_TIMEOUT as THREAD_RELEASE_TIMEOUT,
    Camera as Camera,
    CameraController as CameraController,
    CameraQueue as CameraQueue,
    CustomQueue as CustomQueue,
    LatestFrame as LatestFrame,
    frame_shape as frame_shape,
    imwrite as imwrite,
    save_capture as save_capture,
)
