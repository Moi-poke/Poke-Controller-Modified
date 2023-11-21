#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import cv2
import datetime
import os
import numpy as np
from PIL import Image, ImageTk
from logging import getLogger, DEBUG, NullHandler
from multiprocessing import Process, shared_memory, Manager, Value, Event, Array
from multiprocessing.managers import SharedMemoryManager
import ctypes


def imwrite(filename, img, params=None):
    _logger = getLogger(__name__)
    _logger.addHandler(NullHandler())
    _logger.setLevel(DEBUG)
    _logger.propagate = True
    try:
        ext = os.path.splitext(filename)[1]
        result, n = cv2.imencode(ext, img, params)

        if result:
            with open(filename, mode='w+b') as f:
                n.tofile(f)
            return True
        else:
            return False
    except Exception as e:
        print(e)
        _logger.error(f"Image Write Error: {e}")
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


class Camera:
    def __init__(self, fps=45, resize_width=640, resize_height=360):
        self.camera = None
        self.capture_size = (1280, 720)
        # self.capture_size = (1920, 1080)
        self.capture_dir = "Captures"
        self.fps = int(fps)
        self.resize_shape = (resize_width, resize_height)

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

    def openCamera(self, cameraId):
        if self.camera is not None and self.camera.isOpened():
            self._logger.debug("Camera is already opened")
            self.destroy()

        if os.name == 'nt':
            self._logger.debug("NT OS")
            # self.camera = cv2.VideoCapture(cameraId, cv2.CAP_DSHOW) # Winでは別プロセス化
            self.camera = VideoCaptureWrapper(width=self.capture_size[0],
                                              height=self.capture_size[1],
                                              camera_id=cameraId)
        else:
            self._logger.debug("Not NT OS")
            self.camera = cv2.VideoCapture(cameraId)
            
        if not self.camera.isOpened():
            print(f"Camera ID {cameraId} cannot open.")
            self._logger.error(f"Camera ID {cameraId} cannot open.")
            return
        
        print("Camera ID " + str(cameraId) + " opened successfully")
        self._logger.debug(f"Camera ID {cameraId} opened successfully.")
        # print(self.camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        # self.camera.set(cv2.CAP_PROP_FPS, 60)
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_size[0])
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_size[1])

    # self.camera.set(cv2.CAP_PROP_SETTINGS, 0)

    def isOpened(self):
        self._logger.debug("Camera is opened")
        return self.camera.isOpened()

    def readFrame(self):
        # s = time.perf_counter()
        # print(type(self.camera))
        if type(self.camera) == cv2.VideoCapture:
            _, self.image_bgr = self.camera.read()
            return self.image_bgr
        else:
            _, self.image_bgr, self.image_rgb = self.camera.read()
            return self.image_bgr, self.image_rgb

    def saveCapture(self, filename=None, crop=None, crop_ax=None, img=None):
        if crop_ax is None:
            crop_ax = [0, 0, 1280, 720]
        else:
            pass
            # print(crop_ax)

        dt_now = datetime.datetime.now()
        if filename is None or filename == "":
            filename = dt_now.strftime('%Y-%m-%d_%H-%M-%S') + ".png"
        else:
            filename = filename + ".png"

        if crop is None:
            image = self.image_bgr
        elif crop == 1 or crop == "1":
            image = self.image_bgr[
                    crop_ax[1]:crop_ax[3],
                    crop_ax[0]:crop_ax[2]
                    ]
        elif crop == 2 or crop == "2":
            image = self.image_bgr[
                    crop_ax[1]:crop_ax[1] + crop_ax[3],
                    crop_ax[0]:crop_ax[0] + crop_ax[2]
                    ]
        elif img is not None:
            image = img
        else:
            image = self.image_bgr

        save_path = _get_save_filespec(filename)

        if not os.path.exists(os.path.dirname(save_path)) or not os.path.isdir(os.path.dirname(save_path)):
            # 保存先ディレクトリが存在しないか、同名のファイルが存在する場合（existsはファイルとフォルダを区別しない）
            os.makedirs(os.path.dirname(save_path))
            self._logger.debug("Created Capture folder")

        try:
            imwrite(save_path, image)
            self._logger.debug(f"Capture succeeded: {save_path}")
            print('capture succeeded: ' + save_path)
        except cv2.error as e:
            print("Capture Failed")
            self._logger.error(f"Capture Failed :{e}")

    def destroy(self):
        if self.camera is not None and self.camera.isOpened():
            self.camera.release()
            self.camera = None
            self._logger.debug("Camera destroyed")


def _cam_reader(id:Value,
                ready:Event,
                cancel:Event,
                array:np.ndarray,
                shm_bgr_name:str,
                shm_rgb_name:str,
                set_prop:dict[int:float],
                get_event:Event,
                get_prop:dict|None):   
    
             
    
    def _get(id:int):
        get_prop[id] = video_capture.get(id)
        get_event.set()
        pass
    
    video_capture = cv2.VideoCapture(id, cv2.CAP_DSHOW)
    # video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    # video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    vshm_bgr = shared_memory.SharedMemory(name=shm_bgr_name)
    vshm_rgb = shared_memory.SharedMemory(name=shm_rgb_name)
    mat = np.ndarray(shape=array.shape, dtype=array.dtype, buffer=vshm_bgr.buf)
    mat2 = np.ndarray(shape=array.shape, dtype=array.dtype, buffer=vshm_rgb.buf)
    
    if not video_capture.isOpened():
        raise IOError()
    
    _set_props(video_capture, set_prop)
    
    try:
        while not cancel.is_set():
            if not get_event.is_set():
                _get(id)
            ret, mat = video_capture.read()
            if not ret:
                continue
            cv2.cvtColor(mat, cv2.COLOR_BGR2RGB, mat2)
            
            ready.clear()
            ready.set()
    except Exception as e:
        # print("----error----",e)
        return
    finally:
        video_capture.release()

def _set_props(video_capture:cv2.VideoCapture, props:dict[int:float]):
    for k, v in props.items():
        try:
            print(k, v, video_capture.set(k, v))
            video_capture.set(k, v)
            print("CLEAR")
        except:
            print(f"Error while setting property.")
            return     
    

class VideoCaptureWrapper:    
    def __init__(self,
                 width:int=1280, height:int=720,
                 resize_shape:list[int,int]=[640, 360],
                 camera_id:int=0, *args) -> None:
        
        self._width = width
        self._height = height
        self._camera_number = camera_id
        self.__released = True
        
        self.makeshm()
        # self.__shape = [0, 0, 0]
        self.__ready = self._manager.Event()
        self.__cancel = self._manager.Event()
        
        

        self.read_proc = None # 読み込みプロセスNone初期化
        
        self.open()
        
        # self.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        # self.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        
    def makeshm(self):
        self._manager = Manager()
        self.shm_manager = SharedMemoryManager()
        self.shm_manager.start()
        
        # bgr格納用
        self.shm_buffer_bgr = self.shm_manager.SharedMemory(size=1920*1080*3*8)
        self.buffer_bgr = np.ndarray(shape=(self._height,self._width,3),
                                 dtype=np.uint8,
                                 buffer=self.shm_buffer_bgr.buf)
        
        # rgb格納用
        self.shm_buffer_rgb = self.shm_manager.SharedMemory(size=1920*1080*3*8)
        self.buffer_rgb = np.ndarray(shape=(self._height,self._width,3),
                                 dtype=np.uint8,
                                 buffer=self.shm_buffer_rgb.buf)
        
        self.set_prop = self._manager.dict()
        print(self.set_prop)
        self.get_prop = self._manager.dict()
        print(self.get_prop)
        self.get_event = self._manager.Event()
        # True: no request
        # False: wait get
        self.get_event.set() 
    
    def resetshm(self):
        self.__ready.clear()
        self.__cancel.clear()
        self.shm_buffer_bgr.close()
        self.shm_buffer_rgb.close()
        
        # bgr格納用
        self.shm_buffer_bgr = self.shm_manager.SharedMemory(size=1920*1080*3*8)
        self.buffer_bgr = np.ndarray(shape=(self._height,self._width,3),
                                 dtype=np.uint8,
                                 buffer=self.shm_buffer_bgr.buf)
        
        # rgb格納用
        self.shm_buffer_rgb = self.shm_manager.SharedMemory(size=1920*1080*3*8)
        self.buffer_rgb = np.ndarray(shape=(self._height,self._width,3),
                                 dtype=np.uint8,
                                 buffer=self.shm_buffer_rgb.buf)
        
        
    def open(self):
        self.resetshm()
        
        self.read_proc = Process(target=_cam_reader, args=(self._camera_number,
                                                           self.__ready,
                                                           self.__cancel,
                                                           self.buffer_bgr,
                                                           self.shm_buffer_bgr.name,
                                                           self.shm_buffer_rgb.name,
                                                           self.set_prop,
                                                           self.get_event,
                                                           self.get_prop,
                                                           ),
                                 daemon=True, name="PCM camera proc")
        self.read_proc.start()
        self.__released = False
    
    def release(self):
        if self.__released:
            return
        
        self.__cancel.set()
        self.read_proc.join()
        self.__released = True
        self.read_proc = None
    
    def isOpened(self):
        return not self.__released
    
    def set(self, key, value):

        self.set_prop[key] = value
        self.release()
        self.open()
        
    def get(self, id):
        self.get_prop[id] = None
        self.get_event.clear()
        self.get_event.wait()
        print(self.get_prop)      
    
    def read(self):
        
        if self.__released:
            return False, np.zeros_like(self.buffer_bgr), np.zeros_like(self.buffer_rgb)
            # raise RuntimeError()
        self.__ready.wait(timeout=None)
        return True, self.buffer_bgr.copy(), self.buffer_rgb.copy()
    
    def __del__(self):
        try:
            self.release()
            self._manager.shutdown()
            self.shm_manager.shutdown()
        except Exception as e:
            print(e)
            return