#!/usr/bin/python3
import sys
import queue
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
from PIL import Image
import cv2
import numpy as np
from multiprocessing import Process, shared_memory, Manager
import os
import random
from customtkinter import (
    CTk,
    CTkButton,
    CTkComboBox,
    CTkFont,
    CTkFrame,
    CTkImage,
    CTkLabel,
    CTkOptionMenu,
    CTkProgressBar,
    CTkSegmentedButton,
    CTkTabview,
    CTkTextbox,
    CTkToplevel,
    CTkEntry,
    set_default_color_theme)
from GuiAssets import CaptureArea, CaptureAreaCustom
from generated_ui import MainuiApp
from src.CTkScrollableDropdown import *
from Camera import Camera
import math

# from functools import wraps
# def stop_watch(func) :
#     @wraps(func)
#     def wrapper(*args, **kargs) :
#         start = time.time()
#         result = func(*args,**kargs)
#         process_time =  time.time() - start
#         print(f"{func.__name__}は{process_time}秒かかりました")
#         return result
#     return wrapper

# Queueからデータを読み取り
def read(q1, q2):
    print('Process to read: {}'.format(os.getpid()))
    while True:
        
        try:
            texts = ""
            s = q1.qsize()
            k = 64
            if s > k:
                n = s // k
            else:
                n = s
            for _ in range(n):
                v = q1.get(True)
                texts += v
            if texts != "":
                q2.put(texts)
                # print('Get {} from queue.'.format(texts))
                texts = ""
                
        except queue.Empty:
            pass
        except EOFError:
            print("Main Process Closed.")
            return
        except Exception:
            return

class MainWindow(MainuiApp):
    def __init__(self, master=None, width=960, height=540):
        super().__init__(master)
        # additional
        self.tabview_commands._segmented_button.grid(sticky="W")
        self.tabview_texts._segmented_button.grid(sticky="W")
        self.mainwindow.bind("<Configure>", self.resize_func)
        self.mainwindow.geometry(f"{width}x{height}")
        # self.button_1 = CTkButton(self.mainwindow, text="print spam", command=self.print_spam)
        # self.button_1.grid(column=5, row=5)
        
        self.text_size:int = 12
        self.font_size.set(self.text_size)
        self.text_font = CTkFont(family="MS Gothic", size = self.text_size)
        self.ctktextbox_1.configure(font=self.text_font)
        self.increaseFontSizeButton.configure(command = self.incrementFontEntry)
        self.decreaseFontSizeButton.configure(command = self.decrementFontEntry)
        
        self.textsTab = [self.ctktextbox_1]
        self.ctktextbox_1.bind("<Key>", lambda e:"break")
        
        self.ctkbutton_1.configure(command = self.print_spam)
        # self.ctkbutton_1.configure(command = lambda:print("TEST"))
        
        # CTkScrollableDropdown(attach=self.ctkoptionmenu_2, values=[i for i in range(100)])
        
        manager = Manager()
        self.q = manager.Queue()
        self.q2= manager.Queue()
        
        self.canvas_camera.destroy()
        self.camera = Camera(fps=60,
                             resize_width=self.frame_canvas.winfo_width,
                             resize_height=self.frame_canvas.winfo_height)
        self.camera.openCamera(3)
        self.canvas_camera = CaptureAreaCustom(self.camera,
                                   60,
                                   tk.BooleanVar(value=True),
                                   None,
                                   self.frame_canvas,
                                #    *[width, height]
                                   )
        self.canvas_camera.configure(
            background="#1c1a1a",
            cursor="crosshair",
            height=360,
            relief="raised",
            takefocus=False,
            width=640)
        self.canvas_camera.pack(anchor="center", expand=True, side="top")
        
        sys.stdout = self
        # 別スレッドでScrolledTextに対する操作を実行する
        self.queue = queue.Queue()
        self.queue2 = queue.Queue()
        t = threading.Thread(target=self.process_queue)
        t.daemon = True
        t2 = threading.Thread(target=self.process_queue2)
        t2.daemon = True
        
        
        self.assignResizetextwidget()
        self.canvas_camera.startCapture()
        self.pr = Process(target=read, args=(self.q,self.q2),name="PCM print proc")
        self.pr.start()
        t.start()
        # t2.start()
    
    # def on_configure(self, e):
    #     if e.widget == self.mainwindow:
    #         time.sleep(0.015)
    
    def ts(self):
        cnt = 0
        temp_time = time.perf_counter()
        start_time = time.perf_counter()
        self.camera.camera._width = 1920
        self.camera.camera._height = 1080
        self.camera.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.camera.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        while temp_time - start_time <= 10:
            print(f"LOOP: {cnt}")
            # self.q.put(cnt)
            cnt = cnt + 1
            # while time.perf_counter() - temp_time<0.001:
            #     pass
            temp_time = time.perf_counter()
            time.sleep(0.05)
        print("end")
        print(self.camera.camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    def print_spam(self):
        print(s:=time.perf_counter())
        
        self.t1 = threading.Thread(target=self.ts)
        self.t1.daemon = True
        self.t1.start()
        
        print(time.perf_counter()-s)
        # t1 = None
    
    def write(self, text):
        # キューにScrolledTextに対する操作を追加する
        # self.queue.put(lambda: self.textsTab[0].insert("end", text))
        # self.queue.put(text)
        self.q.put(text)
    
    def flush(self):
        ...
            
    def process_queue(self):
        # キューからScrolledTextに対する操作を取り出して実行する
        
        while True:
            try:
                text = self.q2.get(True, timeout=None)
                self.textsTab[0].insert("end", text)
                self.textsTab[0].see("end")
                # time.sleep(0.001)
            except queue.Empty:
                break
            except EOFError:
                return
            except Exception as e:
                print(e)
                return

        # # 100ms後に再度呼び出す
        # self.mainwindow.after(100, self.process_queue)
        
    
    def process_queue2(self):    
        while True:
            try:
                text = self.queue2.get()
                self.textsTab[0].insert("end", text)
                self.textsTab[0].see("end")
                time.sleep(0.001)
            except queue.Empty:
                break
        
        self.mainwindow.after(150, self.process_queue2)
            
    
    def run(self):
        self.mainwindow.mainloop()
        
    def resize_func(self, event):
        # print(event)
        if event.widget == self.canvas_camera:
            # print(f"{self.frame_canvas.winfo_width()},{self.frame_canvas.winfo_height()}")
            w_ = self.frame_canvas.winfo_width()
            h_ = self.frame_canvas.winfo_height()
            if w_*9//16 < h_:
                self.canvas_camera.config(width=w_, height=w_*9//16)
                self.canvas_camera.show_size = [w_, w_*9//16]
            else:
                self.canvas_camera.config(width=h_*16//9, height=h_)
                self.canvas_camera.show_size = [h_*16//9, h_]

    def create_menu_1(self, master):
        self.menu_1 = tk.Menu(master)
        self.menu_1.configure(tearoff=False, title='menu')
        self.submenu_1 = tk.Menu(self.menu_1, tearoff=False)
        self.menu_1.add(tk.CASCADE, menu=self.submenu_1, label='File')
        self.submenu_1.add("checkbutton", label='checkbutton_3')
        self.submenu_1.add("command", label='command_1')
        self.submenu_1.add("checkbutton", label='checkbutton_1')
        self.submenu_1.add("checkbutton", label='checkbutton_2')
        self.submenu_3 = tk.Menu(self.menu_1)
        self.menu_1.add(tk.CASCADE, menu=self.submenu_3, label='Option')
        self.submenu_4 = tk.Menu(self.menu_1, name="help", tearoff=False)
        self.menu_1.add(tk.CASCADE, menu=self.submenu_4, label='Help')
        self.submenu_4.add(
            "command",
            bitmap="info",
            label='help',
            state="disabled")
        return self.menu_1
    
    def openSetSizeWindow(self, event):
        _temp_win = MyLabelEntryWidget("Change Window Size", [["Width", self.mainwindow.winfo_width()], ["Height", self.mainwindow.winfo_height()]], master=self.mainwindow)
        
        # ウィンドウを中央に配置し、フォーカスを設定する
        _temp_win.wait_visibility()
        _temp_win.attributes("-topmost", True)
        x = self.mainwindow.winfo_x() + self.mainwindow.winfo_width()//2 - _temp_win.winfo_width()//2
        y = self.mainwindow.winfo_y() + self.mainwindow.winfo_height()//2 - _temp_win.winfo_height()//2
        _temp_win.geometry(f"+{x}+{y}")
        _temp_win.focus_set()
        
        # ウィンドウを表示して、入力結果を取得する
        _temp_win.wait_window()
        result = _temp_win.result
        
        if result:
            # OKが押された場合、ウィンドウサイズを変更する
            self.mainwindow.geometry(f"{result[0]}x{result[1]}")
            
    def changeWidgetFontSize(self, event = None):
        if event is None:
            self.text_font = CTkFont(family="MS Gothic", size = self.font_size.get())
            self.text_size = self.font_size.get()
        else:
            if event.delta > 0:
                self.text_size += 1
            else:
                self.text_size = max(self.text_size -1 , 1)
            self.text_font = CTkFont(family="MS Gothic", size = self.text_size)            
            self.font_size.set(self.text_size)
        
        for _ in self.textsTab:
            # _.configure(font=self.text_font)
            _.cget("font").configure(size=self.text_size)
            
    def incrementFontEntry(self):
        self.fontSizeEntry.configure(state = "normal")
        new_size = self.font_size.get()+1
        self.font_size.set(new_size)
        self.fontSizeEntry.configure(state = "disable")
        self.changeWidgetFontSize()
        
    def decrementFontEntry(self):
        self.fontSizeEntry.configure(state = "normal")
        new_size = max(self.font_size.get()-1,1)
        self.font_size.set(new_size)
        self.fontSizeEntry.configure(state = "disable")
        self.changeWidgetFontSize()
        
        

    def assignResizetextwidget(self):
        for _ in self.textsTab:
            _.bind("<Control-MouseWheel>", self.changeWidgetFontSize)
        
        
    

            
            
class MyLabelEntryWidget(CTkToplevel):
    def __init__(self, title:str, values:list[list,list], master=None, **kw):
        """_summary_

        Args:
            title (str): title string
            values (list[list,list]): [label string, value int]
            master (_type_, optional): _description_. Defaults to None.
        """
        super(MyLabelEntryWidget, self).__init__(master, **kw)
        self.result = None
        self.label_title = CTkLabel(self)
        self.label_title.configure(
            state="disabled",
            takefocus=False,
            text=title)
        self.label_title.grid(column=0, row=0)
        self.frames = []
        self.labels = []
        self.entrys = []
        for i, v in enumerate(values):
            self.frames.append(CTkFrame(self))
            self.frames[i].configure(fg_color="transparent")
            
            label = CTkLabel(self.frames[i])
            label_value = tk.StringVar(value=v[0])
            self.labels.append({"label": label, "value":label_value})
            self.labels[i]["label"].configure(
                fg_color="transparent",
                textvariable=self.labels[i]["value"])
            self.labels[i]["label"].grid(column=0, row=0)
            
            entry = CTkEntry(self.frames[i])
            entry_value = tk.IntVar(value=v[1])
            self.entrys.append({"entry":entry, "value":entry_value})
            self.entrys[i]["entry"].configure(textvariable=self.entrys[i]["value"])
            self.entrys[i]["entry"].grid(column=1, row=0)
            
            self.frames[i].grid(column=0, row=i+1, sticky="nsew")
            
        
        self.buttons_frame = CTkFrame(self)
        self.buttons_frame.configure(fg_color="transparent")
        self.button_ok = CTkButton(self.buttons_frame, command=self.close_self)
        self.button_ok.configure(text='OK', width=50)
        self.button_ok.grid(column=0, row=0)
        # self.button_cancel = CTkButton(self.buttons_frame)
        # self.button_cancel.configure(text='Cancel', width=50)
        # self.button_cancel.grid(column=1, row=0)
        self.buttons_frame.grid(column=0, pady=10, row=i+2)
        self.configure(takefocus=True)
        self.resizable(False, False)
        self.rowconfigure("all", weight=1)
        self.columnconfigure("all", weight=1)
        
    
    # close_selfメソッドを修正
    def close_self(self):
        # 入力された値を取得する
        result = []
        for entry in self.entrys:
            result.append(entry["value"].get())

        # 値を返してからウィンドウを破棄する
        self.result = result
        self.destroy()


if __name__ == "__main__":
    app = MainWindow(width=1920, height=1080)
    app.run()
