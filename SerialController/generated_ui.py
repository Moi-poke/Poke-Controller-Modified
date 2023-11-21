#!/usr/bin/python3
import tkinter as tk
import tkinter.ttk as ttk
from customtkinter import (
    CTk,
    CTkButton,
    CTkComboBox,
    CTkEntry,
    CTkFont,
    CTkFrame,
    CTkLabel,
    CTkOptionMenu,
    CTkProgressBar,
    CTkSegmentedButton,
    CTkTabview,
    CTkTextbox,
    CTkCanvas,
    set_default_color_theme)


class MainuiApp:
    def __init__(self, master=None):
        # build ui
        self.mainwindow = CTk(None)
        set_default_color_theme("dark-blue")
        self.mainwindow.title("Poke Controller modified v4.0")
        self.panedwindow_master = ttk.Panedwindow(
            self.mainwindow, orient="horizontal")
        self.panedwindow_master.configure(height=540, width=960)
        self.panedwindow_left = ttk.Panedwindow(
            self.panedwindow_master, orient="vertical")
        self.panedwindow_left.configure(height=540, width=640)
        self.frame_canvas = CTkFrame(self.panedwindow_left)
        self.canvas_camera = tk.Canvas(self.frame_canvas)
        self.canvas_camera.configure(
            background="#1c1a1a",
            cursor="crosshair",
            height=360,
            relief="raised",
            takefocus=False,
            width=640)
        self.canvas_camera.pack(anchor="center", expand=True, side="top")
        self.frame_canvas.grid(column=0, row=0, sticky="nsew")
        self.panedwindow_left.add(self.frame_canvas, weight="3")
        self.frame_commands = CTkFrame(self.panedwindow_left)
        self.frame_commands.configure(height=50)
        self.tabview_commands = CTkTabview(self.frame_commands)
        self.tabview_commands.configure(height=150)
        tab_9 = self.tabview_commands.add("Camera")
        self.ctkoptionmenu_2 = CTkOptionMenu(tab_9)
        self.ctkoptionmenu_2.configure(
            dynamic_resizing=True, values=[
                "0", "1", "2", "3"])
        self.ctkoptionmenu_2.grid(column=0, row=0, sticky="ew")
        self.ctkcombobox_7 = CTkComboBox(tab_9)
        self.ctkcombobox_7.grid(column=0, row=1)
        self.ctkcombobox_11 = CTkComboBox(tab_9)
        self.ctkcombobox_11.grid(column=0, row=2)
        tab_9.grid_anchor("center")
        tab_9.rowconfigure(0, uniform=0)
        tab_9.rowconfigure("all", uniform=0, weight=1)
        tab_9.columnconfigure(0, uniform=0)
        tab_9.columnconfigure("all", uniform=0, weight=1)
        tab_10 = self.tabview_commands.add("Serial")
        self.ctkbutton_1 = CTkButton(tab_10)
        self.ctkbutton_1.configure(text='ctkbutton_1')
        self.ctkbutton_1.grid(column=0, row=0, sticky="ew")
        tab_10.grid_propagate(0)
        tab_10.rowconfigure(0, uniform=0, weight=1)
        tab_10.columnconfigure(0, uniform=0)
        tab_11 = self.tabview_commands.add("Control")
        self.ctkprogressbar_1 = CTkProgressBar(tab_11)
        self.ctkprogressbar_1.grid(column=0, row=0, sticky="ew")
        self.button_4 = ttk.Button(tab_11)
        self.button_4.configure(text='button_4')
        self.button_4.grid(column=0, row=1)
        self.button_4.bind("<ButtonPress>", self.openSetSizeWindow, add="")
        tab_11.rowconfigure(0, uniform=0)
        tab_12 = self.tabview_commands.add("Commands")
        self.ctksegmentedbutton_1 = CTkSegmentedButton(tab_12)
        self.seg_v = tk.StringVar()
        self.ctksegmentedbutton_1.configure(
            dynamic_resizing=True, values=[
                "Python", "MCU", "Short cut"], variable=self.seg_v)
        self.ctksegmentedbutton_1.grid(
            column=0, columnspan=2, padx=50, row=0, sticky="new")
        self.ctkcombobox_1 = CTkComboBox(tab_12)
        self.ctkcombobox_1.grid(column=0, row=1, sticky="ew")
        self.ctkbutton_2 = CTkButton(tab_12)
        self.ctkbutton_2.configure(text='ctkbutton_2')
        self.ctkbutton_2.grid(column=1, row=1)
        self.ctkframe_1 = CTkFrame(tab_12)
        self.ctkframe_1.grid(column=0, columnspan=2, row=2, sticky="ew")
        tab_12.rowconfigure(0, uniform=0, weight=1)
        tab_12.rowconfigure(1, weight=1)
        tab_12.columnconfigure(0, weight=1)
        tab_13 = self.tabview_commands.add("Others")
        self.ctklabel_8 = CTkLabel(tab_13)
        self.ctklabel_8.configure(text='ctklabel_8')
        self.ctklabel_8.grid(column=0, row=0, sticky="ew")
        tab_13.rowconfigure(0, uniform=0, weight=1)
        tab_13.columnconfigure(0, uniform=1, weight=1)
        self.tabview_commands.grid(column=0, row=0, sticky="nsew")
        self.frame_commands.grid(column=0, row=0, sticky="sew")
        self.frame_commands.grid_anchor("sw")
        self.frame_commands.rowconfigure(0, weight=1)
        self.frame_commands.columnconfigure(0, weight=1)
        self.panedwindow_left.add(self.frame_commands, weight="1")
        self.panedwindow_left.grid(column=0, row=0, sticky="nsew")
        self.panedwindow_master.add(self.panedwindow_left, weight="3")
        self.frame_texts = CTkFrame(self.panedwindow_master)
        self.tabview_texts = CTkTabview(self.frame_texts)
        tab_1 = self.tabview_texts.add("tab_1")
        self.ctktextbox_1 = CTkTextbox(tab_1)
        self.ctktextbox_1.configure(
            font=CTkFont(
                "ＭＳ ゴシック",
                12,
                None,
                "roman",
                False,
                False),
            state="normal",
            width=280,
            wrap="word")
        _text_ = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. In maximus ac est ut cursus. Aenean quis aliquet leo. Maecenas facilisis urna nulla, in aliquet augue tristique at. Nulla ut ornare metus. Cras cursus porta mauris sed aliquet. Nunc vitae dignissim sem. Donec sit amet lorem sit amet libero commodo accumsan at et lacus. Quisque tristique porta erat, eu mattis lorem sodales vel. Integer sit amet justo felis. Donec vulputate eros ut luctus mollis. Praesent luctus, dolor eu consequat faucibus, dui arcu fermentum erat, sed maximus odio urna ac nisl. Donec nec dui pharetra, hendrerit turpis non, interdum augue. Proin mollis lorem in aliquam mattis.\n\nInteger id nulla ultricies, pellentesque nulla at, laoreet nulla. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Suspendisse non quam auctor, placerat lorem eget, tempor ligula. Morbi condimentum, odio sed pellentesque lobortis, urna augue bibendum risus, eget sagittis orci est id sem. Phasellus luctus lacinia dolor ut malesuada. Vivamus ut lorem vel eros convallis pretium. Suspendisse ornare tincidunt iaculis. Nullam congue molestie nibh, a accumsan felis maximus id. Vivamus interdum magna sed arcu rhoncus pretium. Fusce congue, quam nec laoreet euismod, sapien tellus interdum orci, quis maximus enim eros ac augue. In in viverra nibh, et pretium purus. Phasellus bibendum leo non enim fringilla, et rutrum augue viverra. Cras sit amet tincidunt tellus, a lacinia odio. Integer vel laoreet turpis, non placerat erat.\n\nDuis tincidunt pretium varius. Ut fermentum nisl eget luctus pellentesque. Mauris vulputate hendrerit tincidunt. Ut turpis odio, varius in accumsan at, fermentum eu risus. Sed mattis accumsan vulputate. Etiam dolor lectus, egestas eu mauris vel, condimentum interdum lacus. Vivamus id viverra nisl. Sed dolor dui, porta non fringilla interdum, suscipit id eros. Sed cursus, ligula id placerat pulvinar, eros libero ornare ante, eget malesuada magna sapien a lorem. Integer facilisis purus at est condimentum tincidunt. Aenean mollis ipsum at feugiat fermentum. Nullam eu libero ante. Phasellus id dignissim lorem. Suspendisse blandit turpis orci, vitae scelerisque ligula viverra eu. Maecenas imperdiet mattis arcu a maximus.\n\nPhasellus luctus tortor leo, vel euismod diam tincidunt a. Vivamus in enim id velit posuere gravida eget non ex. Etiam iaculis risus id ex sodales dictum. Nam volutpat nulla eget felis elementum scelerisque. Pellentesque habitant morbi tristique senectus et netus et malesuada fames ac turpis egestas. Ut suscipit placerat lacus, a feugiat lectus. Praesent scelerisque imperdiet nulla ac tincidunt.\n\nProin nec ante cursus, cursus dui pretium, posuere eros. Sed at sapien eu mauris sollicitudin placerat. Curabitur a justo vel ante accumsan facilisis. Nulla aliquam odio sed commodo vehicula. Proin metus odio, aliquet at tincidunt nec, euismod et lorem. Morbi lobortis sapien et auctor auctor. Pellentesque id ultrices magna. Suspendisse placerat odio vulputate dapibus varius. Proin eget aliquet nulla, feugiat volutpat ligula. Aenean et eros enim.'
        self.ctktextbox_1.insert("0.0", _text_)
        self.ctktextbox_1.grid(column=0, row=0, sticky="nsew")
        tab_1.grid_anchor("n")
        tab_1.rowconfigure(0, weight=1)
        tab_1.columnconfigure(0, weight=1)
        self.tabview_texts.grid(column=0, columnspan=3, row=0, sticky="nsew")
        self.fontSizeEntry = CTkEntry(self.frame_texts)
        self.font_size = tk.IntVar()
        self.fontSizeEntry.configure(
            font=CTkFont(
                "ＭＳ ゴシック",
                12,
                None,
                "roman",
                False,
                False),
            justify="right",
            state="disabled",
            textvariable=self.font_size,
            width=50)
        self.fontSizeEntry.grid(column=0, row=1, sticky="e")
        self.increaseFontSizeButton = CTkButton(self.frame_texts)
        self.increaseFontSizeButton.configure(text='▲', width=25)
        self.increaseFontSizeButton.grid(column=1, row=1, sticky="nsew")
        self.decreaseFontSizeButton = CTkButton(self.frame_texts)
        self.decreaseFontSizeButton.configure(text='▼', width=25)
        self.decreaseFontSizeButton.grid(column=2, row=1, sticky="nsew")
        self.frame_texts.grid(column=1, row=0, sticky="nsew")
        self.frame_texts.rowconfigure(0, weight=1)
        self.frame_texts.columnconfigure(0, weight=1)
        self.panedwindow_master.add(self.frame_texts, weight="1")
        self.panedwindow_master.grid(column=0, row=0, sticky="nsew")
        self.mainwindow.grid_anchor("center")
        self.mainwindow.rowconfigure(0, weight=1)
        self.mainwindow.columnconfigure(0, weight=1)

        # Main widget
        self.mainwindow = self.mainwindow
        # Main menu
        _main_menu = self.create_menu_1(self.mainwindow)
        self.mainwindow.configure(menu=_main_menu)

    def run(self):
        self.mainwindow.mainloop()

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

    def openSetSizeWindow(self, event=None):
        pass


if __name__ == "__main__":
    app = MainuiApp()
    app.run()
