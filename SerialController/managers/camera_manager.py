import tkinter as tk
from tkinter import ttk, messagebox
import platform
import subprocess
from loguru import logger
import os

from Camera import Camera
if platform.system() == "Windows":
    import clr
    clr.AddReference(r"..\DirectShowLib\DirectShowLib-2005")
    from DirectShowLib import DsDevice, FilterCategory

class CameraManager:
    def __init__(self, app, preview):
        self.app = app
        self.root = app.root
        self.camera = None
        self.camera_dic = None
        self.preview = preview

    def setup_ui(self, parent):
        self.camera_lf = ttk.Labelframe(parent, text="Camera")
        self.camera_lf.grid(columnspan=3, padx="5", sticky="ew")

        # Row 0
        ttk.Label(self.camera_lf, text="Camera ID:").grid(padx="5", sticky="ew")
        self.app.camera_id = tk.IntVar()
        self.camera_entry = ttk.Entry(self.camera_lf, textvariable=self.app.camera_id)
        self.camera_entry.grid(column=1, padx="5", row=0, sticky="ew")

        self.reloadButton = ttk.Button(self.camera_lf, text="Reload Camera", command=self.openCamera)
        self.reloadButton.grid(column=2, padx="5", row=0, sticky="ew")

        ttk.Separator(self.camera_lf, orient="vertical").grid(column=3, row=0, sticky="ns")

        self.app.is_show_realtime = tk.BooleanVar()
        self.cb1 = ttk.Checkbutton(self.camera_lf, text="Show Realtime", variable=self.app.is_show_realtime)
        self.cb1.grid(column=4, row=0)

        ttk.Separator(self.camera_lf, orient="vertical").grid(column=5, row=0, sticky="ns")

        capture_f = ttk.Frame(self.camera_lf)
        capture_f.grid(column=6, row=0, sticky="ns")

        self.captureButton = ttk.Button(capture_f, text="Capture", command=self.saveCapture)
        self.captureButton.grid(column=0, row=0)

        self.app.open_folder_img = tk.PhotoImage(file="./assets/icons8-OpenDir-16.png")
        self.OpencaptureButton = ttk.Button(capture_f, image=self.app.open_folder_img, command=self.OpenCaptureDir)
        self.OpencaptureButton.grid(column=1, row=0)

        # Row 1
        ttk.Label(self.camera_lf, text="Camera Name:").grid(column=0, padx="5", row=1, sticky="ew")
        self.app.camera_name_fromDLL = tk.StringVar()
        self.Camera_Name = ttk.Combobox(self.camera_lf, state="readonly", textvariable=self.app.camera_name_fromDLL)
        self.Camera_Name.grid(column=1, columnspan=6, padx="5", row=1, sticky="ew")
        self.Camera_Name.bind("<<ComboboxSelected>>", self.set_cameraid)

        # Row 2 (Preview will be placed here from Window.py)
        self.app.frame_1_2 = ttk.Frame(self.camera_lf, relief="groove", width=640, height=360)
        self.app.frame_1_2.grid(column=0, columnspan=7, row=2)

        # Row 3
        camera_f2 = ttk.Frame(self.camera_lf)
        camera_f2.grid(column=0, columnspan=7, row=3, sticky="nsew")

        ttk.Label(camera_f2, text="FPS:").grid(padx="5", sticky="ew")
        self.app.fps = tk.StringVar()
        self.fps_cb = ttk.Combobox(camera_f2, width=5, justify="right", state="readonly", textvariable=self.app.fps, values=[60, 45, 30, 15, 5])
        self.fps_cb.grid(column=1, padx="10", row=0, sticky="ew")
        self.fps_cb.bind("<<ComboboxSelected>>", self.applyFps)

        ttk.Separator(camera_f2, orient="vertical").grid(column=2, row=0, sticky="ns")

        ttk.Label(camera_f2, text="Show Size:").grid(column=3, padx="5", row=0, sticky="ew")
        self.app.show_size = tk.StringVar()
        self.show_size_cb = ttk.Combobox(camera_f2, textvariable=self.app.show_size, state="readonly", values="640x360 1280x720 1920x1080")
        self.show_size_cb.grid(column=4, padx="10", row=0, sticky="ew")
        self.show_size_cb.bind("<<ComboboxSelected>>", self.applyWindowSize)

        return self.camera_lf

    def initialize_camera(self):
        self.camera = Camera(self.app.fps.get())
        if platform.system() != "Linux":
            try:
                self.locateCameraCmbbox()
                self.camera_entry.config(state="disable")
            except Exception as e:
                logger.error(f"An error occurred: {e}")
                self.app.camera_name_fromDLL.set("An error occurred when displaying the camera name.")
                self.Camera_Name.config(state="disable")
        else:
            self.app.camera_name_fromDLL.set("Linux environment. Cannot show Camera name.")
            self.Camera_Name.config(state="disable")

        self.openCamera()

    def openCamera(self):
        if self.camera:
            self.camera.openCamera(self.app.camera_id.get())

    def assignCamera(self, event):
        if platform.system() != "Linux" and self.camera_dic:
            self.app.camera_name_fromDLL.set(self.camera_dic.get(self.app.camera_id.get()))

    def locateCameraCmbbox(self):
        if platform.system() == "Windows":
            captureDevices = DsDevice.GetDevicesOfCat(FilterCategory.VideoInputDevice)
            self.camera_dic = {cam_id: device.Name for cam_id, device in enumerate(captureDevices)}
        elif platform.system() == "Darwin":
            cmd = 'system_profiler SPCameraDataType | grep "^    [^ ]" | sed "s/    //" | sed "s/://" '
            res = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True, text=True)
            cam_list = list(filter(None, res.stdout.split('\n')))
            self.camera_dic = {cam_id: name for cam_id, name in enumerate(cam_list)}
        else:
            return

        if not self.camera_dic:
            logger.debug("No camera devices can be found.")
            return

        self.camera_dic[str(max(self.camera_dic.keys()) + 1)] = "Disable"
        self.Camera_Name["values"] = list(self.camera_dic.values())

        if self.app.camera_id.get() >= len(self.camera_dic):
            logger.debug("Inappropriate camera ID! -> set to 0")
            self.app.camera_id.set(0)

        self.camera_entry.bind("<KeyRelease>", self.assignCamera)
        self.Camera_Name.current(self.app.camera_id.get())

    def saveCapture(self):
        if self.camera:
            self.camera.saveCapture()

    def OpenCaptureDir(self):
        directory = "Captures"
        logger.debug(f"Open folder: '{directory}'")
        if platform.system() == "Windows":
            os.startfile(directory)
        elif platform.system() == "Darwin":
            subprocess.run(['open', directory])

    def set_cameraid(self, event=None):
        if self.camera_dic:
            keys = [k for k, v in self.camera_dic.items() if v == self.Camera_Name.get()]
            if keys:
                self.app.camera_id.set(keys[0])

    def applyFps(self, event=None):
        if self.preview:
            print("changed FPS to: " + self.app.fps.get() + " [fps]")
            self.preview.setFps(self.app.fps.get())

    def applyWindowSize(self, event=None):
        if self.preview:
            width, height = map(int, self.app.show_size.get().split("x"))

            if self.app.show_size_tmp != self.show_size_cb["values"].index(self.show_size_cb.get()):
                ret = messagebox.askokcancel("確認", "この画面サイズに変更しますか？")
                if ret:
                    self.preview.setShowsize(height, width)
                    self.app.show_size_tmp = self.show_size_cb["values"].index(self.show_size_cb.get())
                else:
                    self.show_size_cb.current(self.app.show_size_tmp)
            else:
                self.preview.setShowsize(height, width)

    def destroy(self):
        if self.camera:
            self.camera.destroy()
