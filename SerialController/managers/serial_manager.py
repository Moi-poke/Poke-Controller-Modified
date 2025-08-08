import tkinter as tk
from tkinter import ttk, messagebox
from loguru import logger

from Commands.Sender import Sender
from Commands.Keys import KeyPress

class SerialManager:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.ser = None
        self.keyPress = None

    def setup_ui(self, parent):
        self.serial_lf = ttk.Labelframe(parent, text="Serial Settings")

        ttk.Label(self.serial_lf, text="COM Port: ").grid(padx="5", sticky="ew")
        self.app.com_port = tk.IntVar()
        self.app.com_port_name = tk.StringVar()
        self.entry2 = ttk.Entry(self.serial_lf, textvariable=self.app.com_port, width=5)
        self.entry2.grid(column=1, padx="5", row=0, sticky="ew")

        ttk.Label(self.serial_lf, text="Baud Rate: ").grid(column=2, padx="5", row=0, sticky="ew")
        self.app.baud_rate = tk.StringVar()
        self.baud_rate_cb = ttk.Combobox(self.serial_lf, width=6, justify="right", state=self.app.baud_rate_state, textvariable=self.app.baud_rate, values=[9600, 4800])
        self.baud_rate_cb.grid(column=3, padx="5", row=0, sticky="ew")
        self.baud_rate_cb.bind("<<ComboboxSelected>>", self.applyBaudRate)

        self.reloadComPort = ttk.Button(self.serial_lf, text="Reload Port", command=self.activateSerial)
        self.reloadComPort.grid(column=4, padx="5", row=0)

        self.disconnectComPort = ttk.Button(self.serial_lf, text="Disconnect Port", command=self.inactivateSerial)
        self.disconnectComPort.grid(column=5, padx="5", row=0)

        ttk.Separator(self.serial_lf, orient="vertical").grid(column=6, row=0, sticky="ns", padx="5")

        self.app.is_show_serial = tk.BooleanVar()
        ttk.Checkbutton(self.serial_lf, text="Show Serial", variable=self.app.is_show_serial).grid(column=7, columnspan=2, padx="5", row=0, sticky="ew")

        return self.serial_lf

    def initialize_serial(self):
        self.ser = Sender(self.app.is_show_serial)
        self.app.preview.ser = self.ser
        self.activateSerial()

    def activateSerial(self):
        if self.app.baud_rate.get() == "4800":
            ret = messagebox.askquestion("確認", "Baud Rateを4800にすると動かなくなる可能性があります。\n変更しますか？")
            if ret != "yes":
                self.baud_rate_cb.set(value=9600)
                return
        if self.ser.isOpened():
            logger.info("Port is already opened and being closed.")
            self.ser.closeSerial()
            self.keyPress = None
            self.activateSerial()
        else:
            if self.ser.openSerial(self.app.com_port.get(), self.app.com_port_name.get(), self.app.baud_rate.get()):
                logger.info(f"COM Port {self.app.com_port.get()} connected successfully")
                self.keyPress = KeyPress(self.ser)
                self.app.keyPress = self.keyPress # Share keyPress with app
                self.app.settings.com_port.set(self.app.com_port.get())
                self.app.settings.baud_rate.set(self.app.baud_rate.get())
                self.app.settings.save()

    def inactivateSerial(self):
        if self.ser.isOpened():
            logger.info("Port is already opened and being closed.")
            self.ser.closeSerial()
            self.keyPress = None
            self.app.keyPress = None

    def applyBaudRate(self, event=None):
        # This was not implemented in the original code
        logger.info("applyBaudRate called but not implemented.")
        pass

    def destroy(self):
        if self.ser and self.ser.isOpened():
            self.ser.closeSerial()
            logger.info("Serial disconnected")
