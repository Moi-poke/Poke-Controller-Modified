import pathlib
import tkinter as tk
import tkinter.ttk as ttk
import pygubu
from pygubu.widgets.scrollbarhelper import ScrollbarHelper

PROJECT_PATH = pathlib.Path(__file__).parent
PROJECT_UI = PROJECT_PATH / "MainUI.ui"


class AppBase:
    def __init__(self, master=None):
        # build ui
        self.PokeController = ttk.Frame(master)
        self.labelframe_camera = ttk.Labelframe(self.PokeController)
        self.label_camera_id = ttk.Label(self.labelframe_camera)
        self.label_camera_id.configure(anchor='center', text='Camera ID :')
        self.label_camera_id.grid(padx=5, sticky='e')
        self.entry_camera = ttk.Entry(self.labelframe_camera)
        self.camera_id = tk.IntVar(value=0)
        self.entry_camera.configure(state='normal', textvariable=self.camera_id, validate='key')
        self.entry_camera.grid(column=1, padx=5, row=0, sticky='ew')
        _validatecmd = (self.entry_camera.register(self.int_only), '%S')
        self.entry_camera.configure(validatecommand=_validatecmd)
        self.button_reload = ttk.Button(self.labelframe_camera)
        self.button_reload.configure(text='Reload Camera')
        self.button_reload.grid(column=2, padx=5, row=0, sticky='ew')
        self.button_reload.configure(command=self.open_camera)
        self.separator_1 = ttk.Separator(self.labelframe_camera)
        self.separator_1.configure(orient='vertical')
        self.separator_1.grid(column=3, row=0, sticky='ns')
        self.checkbutton_show_realtime = ttk.Checkbutton(self.labelframe_camera)
        self.is_show_realtime = tk.BooleanVar(value=True)
        self.checkbutton_show_realtime.configure(offvalue='False', onvalue=True, text='Show Realtime',
                                                 variable=self.is_show_realtime)
        self.checkbutton_show_realtime.grid(column=4, row=0)
        self.checkbutton_show_realtime.configure(command=self.toggle_show_realtime)
        self.separator_2 = ttk.Separator(self.labelframe_camera)
        self.separator_2.configure(orient='vertical')
        self.separator_2.grid(column=5, row=0, sticky='ns')
        self.button_capture = ttk.Button(self.labelframe_camera)
        self.button_capture.configure(text='Capture')
        self.button_capture.grid(column=6, row=0)
        self.button_capture.configure(command=self.save_capture)
        self.button_open_capture_folder = ttk.Button(self.labelframe_camera)
        self.img_icons8OpenDir16 = tk.PhotoImage(file='./assets/icons8-OpenDir-16.png')
        self.button_open_capture_folder.configure(image=self.img_icons8OpenDir16, width=10)
        self.button_open_capture_folder.grid(column=7, row=0, sticky='w')
        self.button_open_capture_folder.configure(command=self.open_capture)
        self.label_camera_name = ttk.Label(self.labelframe_camera)
        self.label_camera_name.configure(anchor='e', text='Camera Name :')
        self.label_camera_name.grid(column=0, padx=5, row=1, sticky='e')
        self.combobox_camera_name = ttk.Combobox(self.labelframe_camera)
        self.camera_name_from_dll = tk.StringVar(value='')
        self.combobox_camera_name.configure(state='readonly',
                                            textvariable=self.camera_name_from_dll)
        self.camera_name_from_dll.set("Can't get camera names.")
        self.combobox_camera_name.grid(column=1, columnspan=5, padx=5, row=1, sticky='ew')
        self.combobox_camera_name.bind('<<ComboboxSelected>>', self.set_camera_id)
        self.label_fps = ttk.Label(self.labelframe_camera)
        self.label_fps.configure(text='FPS:')
        self.label_fps.grid(column=6, padx=5, row=1, sticky='e')
        self.combobox_fps = ttk.Combobox(self.labelframe_camera)
        self.fps = tk.IntVar(value=30)
        self.combobox_fps.configure(justify='right', state='readonly', textvariable=self.fps, width=5)
        self.combobox_fps.grid(column=7, padx=10, row=1, sticky='ew')
        self.combobox_fps.bind('<<ComboboxSelected>>', self.apply_fps)
        self.frame_preview = ttk.Frame(self.labelframe_camera)
        self.frame_preview.configure(height='360', relief='groove', width='640')
        self.frame_preview.grid(column=0, columnspan=9, padx=3, pady=3, row=2, sticky='nsew')
        self.labelframe_camera.configure(height='200', text='Camera', width='200')
        self.labelframe_camera.grid(column=0, row=0)
        self.frame_camera = ttk.Frame(self.PokeController)
        self.label_communication_port = ttk.Label(self.frame_camera)
        self.label_communication_port.configure(text='COM Port: ')
        self.label_communication_port.grid(column=0, padx=10, row=0, sticky='ew')
        self.spinbox_communication_port = ttk.Spinbox(self.frame_camera)
        self.communication_port = tk.IntVar(value=0)
        self.communication_port_name = tk.StringVar(value="")
        self.spinbox_communication_port.configure(from_=0, increment=1, state='readonly',
                                                  textvariable=self.communication_port)
        self.spinbox_communication_port.configure(to=2147483647, width=5)
        self.spinbox_communication_port.grid(column=1, row=0, sticky='ew')
        _validatecmd = (self.spinbox_communication_port.register(self.int_only), '%S')
        self.spinbox_communication_port.configure(validatecommand=_validatecmd)
        # self.spinbox_communication_port.configure(command=self.connect_com_port)
        self.button_reload_communication_port = ttk.Button(self.frame_camera)
        self.button_reload_communication_port.configure(text='Reload Port')
        self.button_reload_communication_port.grid(column=2, row=0)
        self.button_reload_communication_port.configure(command=self.connect_communication_port)
        self.separator_3 = ttk.Separator(self.frame_camera)
        self.separator_3.configure(orient='vertical')
        self.separator_3.grid(column=3, padx=5, row=0, sticky='ns')
        self.checkbutton_show_serial = ttk.Checkbutton(self.frame_camera)
        self.is_show_serial = tk.BooleanVar(value=False)
        self.checkbutton_show_serial.configure(offvalue='False', onvalue=True, text='Show Serial',
                                               variable=self.is_show_serial)
        self.checkbutton_show_serial.grid(column=4, columnspan=1, padx=5, row=0, sticky='ew')
        self.checkbutton_show_serial.configure(command=self.toggle_show_serial)
        self.separator_4 = ttk.Separator(self.frame_camera)
        self.separator_4.configure(orient='vertical')
        self.separator_4.grid(column=5, padx=5, row=0, sticky='ns')
        self.label_show_size = ttk.Label(self.frame_camera)
        self.label_show_size.configure(text='Show Size:')
        self.label_show_size.grid(column=6, padx=5, row=0, sticky='ew')
        self.combobox_show_size = ttk.Combobox(self.frame_camera)
        self.show_size = tk.StringVar(value='')
        self.combobox_show_size.configure(state='readonly', textvariable=self.show_size)
        self.combobox_show_size.grid(column=7, padx=10, row=0, sticky='ew')
        self.combobox_show_size.bind('<<ComboboxSelected>>', self.apply_window_size)
        self.separator_5 = ttk.Separator(self.frame_camera)
        self.separator_5.configure(orient='vertical')
        self.separator_5.grid(column=8, row=0, sticky='ns')
        self.button_clear_log = ttk.Button(self.frame_camera)
        self.button_clear_log.configure(text='Clear Log')
        self.button_clear_log.grid(column=9, padx=5, row=0, sticky='ew')
        self.button_clear_log.configure(command=self.clear_log)
        self.frame_camera.grid(column=0, ipadx=2, ipady=2, row=1, sticky='ew')
        self.labelframe_command = ttk.Labelframe(self.PokeController)
        self.notebook_python_command = ttk.Notebook(self.labelframe_command)
        self.frane_python_command = ttk.Frame(self.notebook_python_command)
        self.combobox_python_command = ttk.Combobox(self.frane_python_command)
        self.python_command_name = tk.StringVar(value='')
        self.combobox_python_command.configure(state='readonly', textvariable=self.python_command_name)
        self.combobox_python_command.grid(column=0, columnspan=1, row=0, sticky='ew')
        self.button_open_python_command = ttk.Button(self.frane_python_command)
        self.button_open_python_command.configure(image=self.img_icons8OpenDir16)
        self.button_open_python_command.grid(column=1, row=0, sticky='nsew')
        self.button_open_python_command.configure(command=self.open_python_commands)
        self.frane_python_command.configure(width='400')
        self.frane_python_command.pack(expand=True, fill='both', side='top')
        self.frane_python_command.columnconfigure(0, uniform="1", weight=1)
        self.notebook_python_command.add(self.frane_python_command, padding=5, sticky='ew', text='Python Command')
        self.frame_mcu_command = ttk.Frame(self.notebook_python_command)
        self.combobox_mcu_command = ttk.Combobox(self.frame_mcu_command)
        self.mcu_command_name = tk.StringVar(value='')
        self.combobox_mcu_command.configure(state='readonly', textvariable=self.mcu_command_name)
        self.combobox_mcu_command.grid(column=0, columnspan=1, row=0, sticky='ew')
        self.button_open_mcu_command = ttk.Button(self.frame_mcu_command)
        self.button_open_mcu_command.configure(image=self.img_icons8OpenDir16, text=' ')
        self.button_open_mcu_command.grid(column=1, row=0, sticky='nsew')
        self.button_open_mcu_command.configure(command=self.open_mcu_commands)
        self.frame_mcu_command.pack(expand=True, fill='both', side='top')
        self.frame_mcu_command.columnconfigure(0, uniform="1", weight=1)
        self.notebook_python_command.add(self.frame_mcu_command, padding=5, sticky='ew', text='MCU Command')
        self.notebook_python_command.grid(column=0, columnspan=4, padx=5, pady=5, row=0, rowspan=2,
                                          sticky='ew')
        self.button_reload_command = ttk.Button(self.labelframe_command)
        self.button_reload_command.configure(text='Reload')
        self.button_reload_command.grid(column=4, padx=2, pady=5, row=1, sticky='ew')
        self.button_reload_command.configure(command=self.reload_commands)
        self.button_start_command = ttk.Button(self.labelframe_command)
        self.button_start_command.configure(text='Start')
        self.button_start_command.grid(column=5, padx=2, pady=5, row=1, sticky='ew')
        self.button_start_command.configure(command=self.start_assigned_command)
        self.button_pause_command = ttk.Button(self.labelframe_command)
        self.button_pause_command.configure(state='disabled', text='Pause_wip')
        self.button_pause_command.grid(column=6, padx=2, pady=5, row=1, sticky='ew')
        self.button_pause_command.configure(command=self.pause_running_script)
        self.labelframe_command.configure(height='200', text='Command')
        self.labelframe_command.grid(column=0, row=2, sticky='ew')
        self.labelframe_command.columnconfigure(0, uniform="1", weight=1)
        self.scrollbar_log_area = ScrollbarHelper(self.PokeController, scrolltype='both')
        self.text_log_area = tk.Text(self.scrollbar_log_area.container)
        self.text_log_area.configure(blockcursor=True, height=10, insertunfocussed='none', maxundo=0)
        self.text_log_area.configure(relief='flat', state='disabled', undo=False, width=50)
        self.text_log_area.configure(wrap='word')
        self.text_log_area.pack(expand=True, fill='both', side='top')
        self.scrollbar_log_area.add_child(self.text_log_area)
        self.scrollbar_log_area.configure(borderwidth=1, padding=1, relief='sunken', usemousewheel=True)
        self.scrollbar_log_area.grid(column=3, padx=5, pady=5, row=0, rowspan=3, sticky='nsew')
        self.PokeController.configure(height='720', padding=5, relief='flat', width='1280')
        self.PokeController.pack(expand=True, fill='both', side='top')
        self.PokeController.grid_anchor('nw')
        self.PokeController.columnconfigure(3, uniform="1", weight=1)

        # Main widget
        self.main_window = self.PokeController

    def run(self):
        self.main_window.mainloop()

    def int_only(self, S):
        pass

    def open_camera(self):
        pass

    def toggle_show_realtime(self):
        pass

    def open_capture(self):
        pass

    def save_capture(self):
        pass

    def set_camera_id(self, event=None):
        pass

    def apply_fps(self, event=None):
        pass

    def connect_com_port(self):
        pass

    def connect_communication_port(self):
        pass

    def toggle_show_serial(self):
        pass

    def apply_window_size(self, event=None):
        pass

    def clear_log(self):
        pass

    def open_python_commands(self):
        pass

    def open_mcu_commands(self):
        pass

    def reload_commands(self):
        pass

    def start_assigned_command(self):
        pass

    def pause_running_script(self):
        pass


if __name__ == '__main__':
    root = tk.Tk()
    app = AppBase(root)
    app.run()
