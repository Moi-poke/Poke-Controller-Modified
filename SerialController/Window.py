import queue

import cv2
import os
import sys
from logging import LogRecord, StreamHandler, getLogger, DEBUG, NullHandler, Handler, INFO
import subprocess
import platform
import pathlib
import tkinter as tk
import tkinter.ttk as ttk
from tkinter.messagebox import askyesno, askokcancel
import pygubu

import Settings
from assets.gui_base import AppBase
import Utility as util
from Camera import Camera
from CommandLoader import CommandLoader
from Commands import McuCommandBase, PythonCommandBase, Sender
import PokeConLogger
from Commands.Keys import KeyPress
from GuiAssets import CaptureArea, ControllerGUI
from Keyboard import SwitchKeyboardController
from Menubar import PokeController_Menubar as Menu

PROJECT_PATH = pathlib.Path(__file__).parent
PROJECT_UI = PROJECT_PATH / "assets/MainUI.ui"
PLATFORM = platform.system()
PYTHON_COMMAND_DIR = "Commands/PythonCommands"
MCU_COMMAND_DIR = "Commands/McuCommands"

NAME = "Poke-Controller Modified"
VERSION = f"v4.0.4 beta, python{platform.python_version()}"  # based on 1.0-beta3


class PokeConApp(AppBase):
    def __init__(self, master=None, log_q=None):
        self.master = master
        """GUIの生成"""
        # Loggerの設定
        super().__init__(master)
        self.logger = getLogger(__name__)
        self.logger.addHandler(NullHandler())
        # self.logger.setLevel(INFO)
        self.logger.propagate = True
        # 標準出力をログにリダイレクト・pythonのバージョン表示
        # print(f"python version is {platform.python_version()}")
        self.logger.info(f"python version is {platform.python_version()}")
        self.text_log_area.configure(font='TkFixedFont')
        self.text_log_area.tag_config('DEBUG', foreground='gray')
        self.text_log_area.tag_config('INFO', foreground='black')
        self.text_log_area.tag_config('WARNING', background='yellow')
        self.text_log_area.tag_config('ERROR', foreground='red', background='yellow')
        self.text_log_area.tag_config('CRITICAL', foreground='red', background='yellow', underline=1)
        # 変数の設定
        self.root = self.main_window.master

        self.combobox_fps.config(values=[60, 45, 30, 15, 5])
        self.combobox_show_size.config(values=["640x360", "1280x720", "1920x1080"])

        self.settings = None
        self.keyPress = None
        self.keyboard = None
        self.py_cur_command = None
        self.mcu_cur_command = None
        self.mcu_classes = None
        self.py_classes = None
        self.mcu_loader = None
        self.py_loader = None
        self.cur_command = None
        self.controller = None
        self.isShowInput = False

        # 標準出力をログにリダイレクト
        sys.stdout = StdoutRedirector(self.text_log_area)
        print(f"python version is {platform.python_version()}")
        # load settings file
        self.load_settings()
        # 各tk変数に設定値をセット(コピペ簡単のため)
        try:
            if self.settings is not None:
                self.is_show_realtime.set(self.settings.is_show_realtime.get())
                self.is_show_serial.set(self.settings.is_show_serial.get())
                # self.is_use_keyboard.set(self.settings.is_use_keyboard.get())
                self.fps.set(self.settings.fps.get())
                self.show_size.set(self.settings.show_size.get())
                self.communication_port.set(self.settings.com_port.get())
                self.communication_port_name.set(self.settings.com_port_name.get())
                self.camera_id.set(self.settings.camera_id.get())
        except:
            self.logger.exception("設定ファイルに不具合があります。"
                                  "settings.iniを削除して再起動してください。")
        # Open up a camera
        self.camera = Camera(self.fps.get())
        self.open_camera()
        # activate serial communication
        self.ser = Sender.Sender(self.is_show_serial)
        self.activate_serial()
        self.activate_keyboard()
        self.preview = CaptureArea(self.camera,
                                   self.fps.get(),
                                   self.is_show_realtime,
                                   self.ser,
                                   self.keyPress,
                                   self.frame_preview,
                                   *list(map(int, self.show_size.get().split("x"))),
                                   )
        self.preview.config(cursor='crosshair')
        self.preview.pack()
        self.load_commands()

        self.show_size_tmp = self.combobox_show_size['values'].index(self.combobox_show_size.get())
        self.root.bind('<Key-F5>', self.reload_command_with_f5)
        self.logger.debug("Bind F5 key to reload commands")
        self.root.bind('<Key-F6>', self.start_command_with_f6)
        self.logger.debug("Bind F6 key to execute commands")
        self.root.bind('<Key-Escape>', self.stop_command_with_esc)
        self.logger.debug("Bind Escape key to stop commands")

        self.menu = Menu(self)
        self.preview.set_menu(self.menu)
        self.root.config(menu=self.menu)
        self.is_use_keyboard = self.menu.is_use_keyboard
        self.is_use_L_stick_mouse = self.menu.is_use_L_stick_mouse
        self.is_use_R_stick_mouse = self.menu.is_use_R_stick_mouse
        if self.keyPress is not None:
            self.isShowInput = self.menu.isShowInput.get()
            self.keyPress.isShowInput = self.isShowInput
        if PLATFORM != 'Linux':
            try:
                self.set_camera_name()
                self.entry_camera.config(state='disable')
            except:
                # Locate an entry instead whenever dll is not imported successfully
                self.camera_name_from_dll.set("Can't get camera names.")
                self.logger.warning("Can't get camera names.")
                self.combobox_camera_name.config(state='disable')
        elif PLATFORM == 'Linux':
            self.camera_name_from_dll.set("Linux environment. Cannot get Camera name.")
            self.combobox_camera_name.config(state='disable')
            self.menu.is_use_keyboard.config(state='disable')
            return
        else:
            self.camera_name_from_dll.set("Unknown environment. Cannot get Camera name.")
            self.combobox_camera_name.config(state='disable')

        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.preview.startCapture()

    def run(self):
        self.logger.debug("Start Poke-Controller")
        self.main_window.mainloop()

    def int_only(self, S):
        if not S.isnumeric():
            return False
        return True

    def open_camera(self):
        self.camera.openCamera(self.camera_id.get())

    def toggle_show_realtime(self):
        pass

    def save_capture(self):
        self.camera.saveCapture()

    def set_camera_id(self, event=None):
        keys = [k for k, v in self.camera_dic.items() if v == self.camera_name_from_dll.get()]
        if keys:
            ret = keys[0]
        else:
            ret = None
        self.camera_id.set(ret)

    def apply_fps(self, event=None) -> None:
        # print(f'changed FPS to: {self.fps.get()} [fps]')
        self.logger.info(f'changed FPS to: {self.fps.get()} [fps]')
        print(f'changed FPS to: {self.fps.get()} [fps]')
        self.preview.setFps(self.fps.get())

    def connect_com_port(self):
        self.activate_serial()

    def connect_communication_port(self):
        self.activate_serial()

    def toggle_show_serial(self):
        pass

    def apply_window_size(self, event=None) -> None:
        width, height = map(int, self.show_size.get().split("x"))
        self.preview.setShowsize(height, width)
        if self.show_size_tmp != self.combobox_show_size['values'].index(self.combobox_show_size.get()):
            ret = askokcancel('確認', "この画面サイズに変更しますか？")
        else:
            return

        if ret:
            self.show_size_tmp = self.combobox_show_size['values'].index(self.combobox_show_size.get())
        else:
            self.combobox_show_size.current(self.show_size_tmp)
            width_bef, height_bef = map(int, self.show_size.get().split("x"))
            self.preview.setShowsize(height_bef, width_bef)
            # self.show_size_tmp = self.show_size_cb['values'].index(self.show_size_cb.get())
        pass

    def clear_log(self) -> None:
        self.text_log_area.configure(state='normal')
        self.text_log_area.delete("1.0", "end")
        self.text_log_area.configure(state='disabled')
        self.logger.debug("Clear log")

    def open_python_commands(self) -> None:
        directory = os.path.join("Commands", "PythonCommands")
        self.logger.debug(f'Open folder: \'{directory}\'')
        if PLATFORM == 'Windows':
            subprocess.call(f'explorer "{directory}"')
        elif PLATFORM == 'Darwin':
            command = f'open "{directory}"'
            subprocess.run(command, shell=True)

    def open_mcu_commands(self) -> None:
        directory = os.path.join("Commands", "McuCommands")
        self.logger.debug(f'Open folder: \'{directory}\'')
        if PLATFORM == 'Windows':
            subprocess.call(f'explorer "{directory}"')
        elif PLATFORM == 'Darwin':
            command = f'open "{directory}"'
            subprocess.run(command, shell=True)

    def open_capture(self) -> None:
        directory = "Captures"
        self.logger.debug(f'Open folder: \'{directory}\'')
        if PLATFORM == 'Windows':
            subprocess.call(f'explorer "{directory}"')
        elif PLATFORM == 'Darwin':
            command = f'open "{directory}"'
            subprocess.run(command, shell=True)

    def reload_commands(self) -> None:
        # 表示しているタブを読み取って、どのコマンドを表示しているか取得、リロード後もそれが選択されるようにする
        oldval_mcu = self.combobox_mcu_command.get()
        oldval_py = self.combobox_python_command.get()

        self.py_classes = self.py_loader.reload()
        self.mcu_classes = self.mcu_loader.reload()

        # Restore the command selecting state if possible
        self.set_command_items()
        if oldval_mcu in self.combobox_mcu_command['values']:
            self.combobox_mcu_command.set(oldval_mcu)
        if oldval_py in self.combobox_python_command['values']:
            self.combobox_python_command.set(oldval_py)

        ret = self.assign_command()
        if not ret:
            self.logger.error(f"Failed assign command")

        # print('Finished reloading command modules.')
        self.logger.info("Reloaded commands.")
        print("Reloaded commands.")
        self.root.focus_set()

    def start_assigned_command(self) -> None:
        if self.cur_command is None:
            # print('No commands have been assigned yet.')
            self.logger.info('No commands have been assigned yet.')

        # set and init selected command
        ret = self.assign_command()
        if not ret:
            self.logger.error(f"Failed assign command")

        # print(self.button_start_command["text"] + ' ' + self.cur_command.NAME)
        self.logger.info(f"-- {self.button_start_command['text']}  {self.cur_command.NAME} --")
        self.cur_command.start(self.ser, self.stop_play_post)

        self.button_start_command["text"] = "Stop"
        self.button_start_command["command"] = self.stop_assigned_command
        self.button_reload_command["state"] = "disabled"
        self.root.focus_set()

    def stop_assigned_command(self) -> None:
        # print(self.button_start_command["text"] + ' ' + self.cur_command.NAME)
        self.logger.info(self.button_start_command["text"] + ' ' + self.cur_command.NAME)
        print(self.button_start_command["text"] + ' ' + self.cur_command.NAME)
        self.button_start_command["state"] = "disabled"
        self.cur_command.end(self.ser)

    def stop_play_post(self) -> None:
        self.button_start_command["text"] = "Start"
        self.button_start_command["command"] = self.start_assigned_command
        self.button_start_command["state"] = "normal"
        self.button_reload_command["state"] = "normal"

    def pause_running_script(self) -> None:
        pass

    def assign_camera(self, event) -> None:
        if PLATFORM != "Linux":
            self.camera_name_from_dll.set(self.camera_dic[self.camera_id.get()])

    def load_commands(self) -> None:
        self.py_loader = CommandLoader(util.ospath(PYTHON_COMMAND_DIR),
                                       PythonCommandBase.PythonCommand)  # コマンドの読み込み
        self.mcu_loader = CommandLoader(util.ospath(MCU_COMMAND_DIR), McuCommandBase.McuCommand)
        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()
        self.set_command_items()
        ret = self.assign_command()
        if not ret:
            self.logger.error(f"Failed assign command")

    def set_command_items(self) -> None:
        self.combobox_python_command['values'] = [c.NAME for c in self.py_classes]
        self.combobox_python_command.current(0)
        self.combobox_mcu_command['values'] = [c.NAME for c in self.mcu_classes]
        self.combobox_mcu_command.current(0)

    def assign_command(self) -> bool:
        # 選択されているコマンドを取得する
        self.mcu_cur_command = self.mcu_classes[
            self.combobox_mcu_command.current()]()  # MCUコマンドについて

        # pythonコマンドは画像認識を使うかどうかで分岐
        cmd_class = self.py_classes[self.combobox_python_command.current()]
        if issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
            try:  # 画像認識の際に認識位置を表示する引数追加。互換性のため従来のはexceptに。
                self.py_cur_command = cmd_class(self.camera, self.preview)
            except TypeError:
                self.py_cur_command = cmd_class(self.camera)
            except Exception as e:
                self.logger.error(f"Error: {e}")
                return False
                # self.py_cur_command = cmd_class(self.camera)
        else:
            self.py_cur_command = cmd_class()

        if self.notebook_python_command.index(
                self.notebook_python_command.select()) == 0:
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command
        return True

    def load_settings(self) -> None:
        self.settings = Settings.GuiSettings()
        self.settings.load()

    def set_camera_name(self) -> bool:
        if PLATFORM == 'Windows':
            import clr
            clr.AddReference(r"..\DirectShowLib\DirectShowLib-2005")
            from DirectShowLib import DsDevice, FilterCategory

            # Get names of detected camera devices
            captureDevices = DsDevice.GetDevicesOfCat(FilterCategory.VideoInputDevice)
            self.camera_dic = {cam_id: device.Name for cam_id, device in enumerate(captureDevices)}

            self.camera_dic[str(max(list(self.camera_dic.keys())) + 1)] = 'Disable'
            self.combobox_camera_name['values'] = [device for device in self.camera_dic.values()]
            self.logger.debug(f"Camera list: {[device for device in self.camera_dic.values()]}")
            dev_num = len(self.camera_dic)
            return True

        elif PLATFORM == "Darwin":
            cmd = 'system_profiler SPCameraDataType | grep "^    [^ ]" | sed "s/    //" | sed "s/://" '
            res = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
            # 出力結果の加工
            ret = res.stdout.decode('utf-8')
            cam_list = list(filter(lambda a: a != "", ret.split('\n')))
            self.camera_dic = {cam_id: camera_name for cam_id, camera_name in enumerate(cam_list)}
            dev_num = len(self.combobox_camera_name['values'])
            self.camera_dic[str(max(list(self.camera_dic.keys())) + 1)] = 'Disable'
            self.combobox_camera_name['values'] = [device for device in self.camera_dic.values()]
            return True
        else:
            return False
        # if self.camera_id.get() > dev_num - 1:
        #     print('Inappropriate camera ID! -> set to 0')
        #     self.logger.debug('Inappropriate camera ID! -> set to 0')
        #     self.camera_id.set(0)
        #     if dev_num == 0:
        #         print('No camera devices can be found.')
        #         self.logger.debug('No camera devices can be found.')
        # #
        # self.entry_camera").bind('<KeyRelease>', self.assign_camera)
        # self.combobox_camera_name.current(self.camera_id.get())

    def activate_serial(self) -> None:
        if self.ser.isOpened():
            # print('Port is already opened and being closed.')
            self.logger.info('Port is already opened and being closed.')
            print('Port is already opened and being closed.')
            self.ser.closeSerial()
            self.keyPress = None
            self.activate_serial()
        else:
            if self.ser.openSerial(self.communication_port.get(), self.communication_port_name.get()):
                # print('COM Port ' + str(self.communication_port.get()) + ' connected successfully')
                self.logger.info('COM Port ' + str(self.communication_port.get()) + ' connected successfully')
                print('COM Port ' + str(self.communication_port.get()) + ' connected successfully')
                self.keyPress = KeyPress(self.ser)
                try:
                    if self.menu is not None:
                        self.isShowInput = self.menu.isShowInput.get()
                        self.keyPress.isShowInput = self.isShowInput
                except:
                    self.logger.exception("Wrong COM Port")

    def activate_keyboard(self) -> None:
        if self.settings.is_use_keyboard:
            # if False:
            # enable Keyboard as controller
            if self.keyboard is None:
                self.keyboard = SwitchKeyboardController(self.keyPress)
                self.keyboard.listen()

            # bind focus
            if PLATFORM != 'Linux':
                self.root.bind("<FocusIn>", self.on_focus_in_controller)
                self.root.bind("<FocusOut>", self.on_focus_out_controller)

        else:
            if PLATFORM != 'Linux':  # NOTE: Idk why but self.keyboard.stop() makes crash on Linux
                if self.keyboard is not None:
                    # stop listening to keyboard events
                    self.keyboard.stop()
                    self.keyboard = None

                self.root.bind("<FocusIn>", lambda _: None)
                self.root.bind("<FocusOut>", lambda _: None)

    def on_focus_in_controller(self, event) -> None:
        # enable Keyboard as controller
        if event.widget == self.root and self.keyboard is None:
            self.keyboard = SwitchKeyboardController(self.keyPress)
            self.keyboard.listen()

    def on_focus_out_controller(self, event) -> None:
        # stop listening to keyboard events
        if event.widget == self.root and self.keyboard is not None:
            self.keyboard.stop()
            self.keyboard = None

    def createControllerWindow(self) -> None:
        if self.controller is not None:
            self.controller.focus_force()
            return

        window = ControllerGUI(self.root, self.ser)
        window.protocol("WM_DELETE_WINDOW", self.closingController)
        self.controller = window

    def reload_command_with_f5(self, *event) -> None:
        self.reload_commands()

    def start_command_with_f6(self, *event) -> None:
        if self.button_start_command["text"] == "Stop":
            # print("Command is now working!")
            self.logger.debug("Command is now working!")
        elif self.button_start_command["text"] == "Start":
            self.start_assigned_command()

    def stop_command_with_esc(self, *event) -> None:
        if self.button_start_command["text"] == "Stop":
            self.stop_assigned_command()

    def exit(self) -> None:
        ret = askyesno('確認', 'Poke Controllerを終了しますか？')
        if ret:
            if self.ser.isOpened():
                self.ser.closeSerial()
                # print("Serial disconnected")
                self.logger.info("Serial disconnected")

            # stop listening to keyboard events
            if not self.keyboard is None:
                self.keyboard.stop()
                self.keyboard = None

            # save settings
            self.settings.is_show_realtime.set(self.is_show_realtime.get())
            self.settings.is_show_serial.set(self.is_show_serial.get())
            # self.settings.is_use_keyboard.set(self.is_use_keyboard.get())
            self.settings.fps.set(self.fps.get())
            self.settings.show_size.set(self.show_size.get())
            self.settings.com_port.set(self.communication_port.get())
            self.settings.is_use_keyboard.set(self.is_use_keyboard.get())
            self.settings.isShowInput.set(self.isShowInput)
            self.settings.is_use_L_stick_mouse.set(self.is_use_L_stick_mouse.get())
            self.settings.is_use_R_stick_mouse.set(self.is_use_R_stick_mouse.get())

            self.settings.save()

            self.camera.destroy()
            cv2.destroyAllWindows()
            self.logger.debug("Stop Poke Controller")
            self.root.destroy()
            self.logger.debug("Stopped")

    def closingController(self) -> None:
        self.controller.destroy()
        self.controller = None

    # def poll_print_queue(self) -> None:
    #     while True:
    #         try:
    #             s = self.log_queue.get(block=False)
    #         except queue.Empty:
    #             break
    #         else:
    #             self.update_text(s)
    #     self.scrollbar_log_area.after(100, self.poll_print_queue)

    # def update_text(self, rec: LogRecord) -> None:
    #     msg = self.queue_handler.format(rec)
    #     self.text_log_area.configure(state='normal')
    #     self.text_log_area.insert('end', msg + '\n', rec.levelname)
    #     self.text_log_area.see('end')
    #     # self.text_space.update_idletasks()
    #     self.text_log_area.configure(state='disabled')


class StdoutRedirector(object):
    """
    標準出力をtextウィジェットにリダイレクトするクラス
    重いので止めました →# update_idletasks()で出力のたびに随時更新(従来はfor loopのときなどにまとめて出力されることがあった)
    """

    def __init__(self, text_widget, log_queue: queue.Queue = None):
        self.text_space = text_widget
        # self.print_queue = log_queue

    def write(self, string: any):
        self.text_space.configure(state='normal')
        self.text_space.insert('end', string)
        self.text_space.see('end')
        # self.text_space.update_idletasks()
        self.text_space.configure(state='disabled')
        # self.print_queue.put(string)

    def flush(self):
        pass


if __name__ == '__main__':
    root = tk.Tk()
    root.title(f"{NAME} {VERSION}")

    logger = PokeConLogger.root_logger()

    app = PokeConApp(root)
    try:
        app.run()
    except Exception as e:
        logger.exception(e)
