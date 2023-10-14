#!/usr/bin/python3
import tkinter as tk
import tkinter.ttk as ttk
import os

import PCE_Command as PCEC
import PCE_Dialog as PCED
from logging import getLogger, DEBUG, NullHandler




class Pokecon_Code_Editor:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.title = "Pokecon_Code_Editor:"
		self.ValueNone() 
		# build ui
		self.Body = tk.Toplevel(self.root)
		self.Body.configure(height=900, width=1000)
		# self.Body.maxsize(900, 900)
		# self.Body.minsize(900, 900)
		self.Body.title(self.title)


		

		self.Code_Frame = ttk.Labelframe(self.Body)
		self.Code_Frame.configure(text='Code', width=0)
		

		self.Text_Edit_BOX = tk.Text(self.Code_Frame)
		self.scroll_Y = ttk.Scrollbar(self.Code_Frame, orient='vertical', command=self.Text_Edit_BOX.yview)
		self.scroll_X = ttk.Scrollbar(self.Code_Frame, orient='horizontal', command=self.Text_Edit_BOX.xview)

		self.Text_Edit_BOX["xscrollcommand"] = self.scroll_X.set
		self.Text_Edit_BOX["yscrollcommand"] = self.scroll_Y.set

		self.Text_Edit_BOX.configure(height=10, width=50, font=("游ゴシック", 14,"bold"),undo=True,wrap=tk.NONE)
		self.Text_Edit_BOX.place(anchor="nw", relheight=0.98,relwidth=0.95, relx=0.01, rely=0.01, x=0, y=0)
		self.scroll_Y.place(anchor="nw", relheight=0.98, relwidth=0.03,relx=0.96, rely=0.01, x=0, y=0)
		self.scroll_X.place(anchor="nw", relheight=0.02,relwidth=0.96, relx=0.01, rely=0.97, x=0, y=0) 


		
# ===================================================================================================================== #
		self.Code_Frame.place(anchor="nw",relheight=0.78,relwidth=0.4,relx=0.01,rely=0.01,x=0,y=0)

		self.Setting_Frame = ttk.Labelframe(self.Body)
		self.Setting_Frame.configure(height=200, text='Setting', width=200)
		self.PY_While = ttk.Button(self.Setting_Frame)
		self.PY_While.configure(text='while',command=self.OpenWhileDialog)
		self.PY_While.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.025,rely=0.1,x=0,y=0)

		self.PY_For = ttk.Button(self.Setting_Frame)
		self.PY_For.configure(text='for',command=self.OpenForDialog)
		self.PY_For.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.275,rely=0.1,x=0,y=0)
		
		self.PY_IF = ttk.Button(self.Setting_Frame)
		self.PY_IF.configure(text='if', state="disabled")
		self.PY_IF.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.525,rely=0.1,x=0,y=0)
		
		self.PY_def = ttk.Button(self.Setting_Frame)
		self.PY_def.configure(text='def',command=self.OpenDefDialog)
		self.PY_def.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.775,rely=0.1,x=0,y=0)

		self.PY_Print = ttk.Button(self.Setting_Frame)
		self.PY_Print.configure(text='print',command=self.OpenPrintDialog)
		
		self.PY_Print.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.025,rely=0.4,x=0,y=0)

		self.PY_Template = ttk.Button(self.Setting_Frame)
		self.PY_Template.configure(text='画像認識',command=self.OpenTemplateDialog)
		self.PY_Template.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.275,rely=0.4,x=0,y=0)

		self.PY_LINE_txt = ttk.Button(self.Setting_Frame)
		self.PY_LINE_txt.configure(text='LINE_text',command=self.OpenLINEtextDialog)
		self.PY_LINE_txt.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.525,rely=0.4,x=0,y=0)

		self.PY_LINE_Image = ttk.Button(self.Setting_Frame)
		self.PY_LINE_Image.configure(text='LINE_image',command=self.OpenLINEimageDialog)
		self.PY_LINE_Image.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.775,rely=0.4,x=0,y=0)
		
		self.PY_Wait = ttk.Button(self.Setting_Frame)
		self.PY_Wait.configure(text='wait',command=self.OpenWaitDialog)
		self.PY_Wait.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.025,rely=0.7,x=0,y=0)

		self.PY_saveCapture = ttk.Button(self.Setting_Frame)
		self.PY_saveCapture.configure(text='saveCapture', command=self.saveCapture_Button)
		self.PY_saveCapture.place(anchor="nw", relheight=0.2,relwidth=0.2, relx=0.275, rely=0.7, x=0, y=0)

		self.PY_IFAlive = ttk.Button(self.Setting_Frame)
		self.PY_IFAlive.configure(text='checkIfAlive',command=self.checkIfAlive_Button)
		self.PY_IFAlive.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.525,rely=0.7,x=0,y=0)

		self.PY_Finish = ttk.Button(self.Setting_Frame)
		self.PY_Finish.configure(text='Finish',command=self.Finish_Button)
		self.PY_Finish.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.775,rely=0.7,x=0,y=0)

		self.PY_Label_For = ttk.Label(self.Setting_Frame)
		self.PY_Label_For.configure(text='繰り返し回数指定あり')
		self.PY_Label_For.place(anchor="nw",relwidth=0.25,relx=0.275,x=0,y=0)

		self.PY_Label_IF = ttk.Label(self.Setting_Frame)
		self.PY_Label_IF.configure(anchor="center", text='条件分岐')
		self.PY_Label_IF.place(anchor="nw", relwidth=0.2, relx=0.525, x=0, y=0)

		self.PY_Label_Def = ttk.Label(self.Setting_Frame)
		self.PY_Label_Def.configure(anchor="center", text='関数作成')
		self.PY_Label_Def.place(anchor="nw",relwidth=0.2,relx=0.775,x=0,y=0)

		self.PY_Label_While = ttk.Label(self.Setting_Frame)
		self.PY_Label_While.configure(anchor="center", text='繰り返し回数指定なし')
		self.PY_Label_While.place(anchor="nw", relwidth=0.25, relx=0.025, x=0, y=0)


		self.Setting_Frame.place(anchor="nw",relheight=0.25,relwidth=0.57,relx=0.42,rely=0.01,x=0,y=0)
		# ===================================================================================================================== #

		
		self.Controller_Frame = ttk.Labelframe(self.Body)
		self.Controller_Frame.configure(height=200, text='Controller', width=200)

		# self.Check_Text = ttk.Checkbutton(self.Controller_Frame)
		# self.Check_Text.configure(text='Text')
		# self.Check_Text.place(anchor="nw",relheight=0.075,relwidth=0.2,relx=0.1,x=0,y=0)

		# self.Check_Repeat = ttk.Checkbutton(self.Controller_Frame)
		# self.Check_Repeat.configure(text='Repeat')
		# self.Check_Repeat.place(anchor="nw",relheight=0.075,relwidth=0.2,relx=0.3,x=0,y=0)

		# self.Check_Hold = ttk.Checkbutton(self.Controller_Frame)
		# self.Check_Hold.configure(text='Hold')
		# self.Check_Hold.place(anchor="nw",relheight=0.075,relwidth=0.2,relx=0.5,x=0,y=0)

		self.Check_Controller = ttk.Checkbutton(self.Controller_Frame)
		self.Check_Controller_value = tk.BooleanVar(value=True)
		self.Check_Controller.configure(variable=self.Check_Controller_value,text='Controller')
		self.Check_Controller.place(anchor="nw",relheight=0.075,relwidth=0.2,relx=0.7,x=0,y=0)

		self.Button_ZL = ttk.Button(self.Controller_Frame)
		self.Button_ZL.configure(text='ZL', command=lambda: self.PressButton('Button.ZL'))
		self.Button_ZL.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.05,rely=0.1,x=0,y=0)

		self.Button_L = ttk.Button(self.Controller_Frame)
		self.Button_L.configure(text='L', command=lambda: self.PressButton('Button.L'))
		self.Button_L.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.15,rely=0.1,x=0,y=0)

		self.Button_MINUS = ttk.Button(self.Controller_Frame)
		self.Button_MINUS.configure(text='-', command=lambda: self.PressButton('Button.MINUS'))
		self.Button_MINUS.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.35,rely=0.1,x=0,y=0)

		self.Button_PLUS = ttk.Button(self.Controller_Frame)
		self.Button_PLUS.configure(text='+', command=lambda: self.PressButton('Button.PLUS'))
		self.Button_PLUS.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.55,rely=0.1,x=0,y=0)

		self.Button_R = ttk.Button(self.Controller_Frame)
		self.Button_R.configure(text='R', command=lambda: self.PressButton('Button.R'))
		self.Button_R.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.75,rely=0.1,x=0,y=0)

		self.Button_ZR = ttk.Button(self.Controller_Frame)
		self.Button_ZR.configure(text='ZR', command=lambda: self.PressButton('Button.ZR'))
		self.Button_ZR.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.85,rely=0.1,x=0,y=0)

		self.LEFT_Stick_UP_LEFT = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_UP_LEFT.configure(text='↖', command=lambda: self.PressButton('Direction.UP_LEFT'))
		self.LEFT_Stick_UP_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.1,rely=0.2,x=0,y=0)

		self.LEFT_Stick_UP = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_UP.configure(text='↑', command=lambda: self.PressButton('Direction.UP'))
		self.LEFT_Stick_UP.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.2,rely=0.2,x=0,y=0)

		self.LEFT_Stick_UP_RIGHT = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_UP_RIGHT.configure(text='↗', command=lambda: self.PressButton('Direction.UP_RIGHT'))
		self.LEFT_Stick_UP_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.3,rely=0.2,x=0,y=0)

		self.LEFT_Stick_LEFT = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_LEFT.configure(text='←', command=lambda: self.PressButton('Direction.LEFT'))
		self.LEFT_Stick_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.1,rely=0.3,x=0,y=0)

		self.LEFT_Stick_L3 = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_L3.configure(text='■', command=lambda: self.PressButton('Button.LCLICK'))
		self.LEFT_Stick_L3.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.2,rely=0.3,x=0,y=0)

		self.LEFT_Stick_RIGHT = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_RIGHT.configure(text='→', command=lambda: self.PressButton('Direction.RIGHT'))
		self.LEFT_Stick_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.3,rely=0.3,x=0,y=0)

		self.LEFT_Stick_DOWN_LEFT = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_DOWN_LEFT.configure(text='↙', command=lambda: self.PressButton('Direction.DOWN_LEFT'))
		self.LEFT_Stick_DOWN_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.1,rely=0.4,x=0,y=0)

		self.LEFT_Stick_DOWN = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_DOWN.configure(text='↓',  command=lambda: self.PressButton('Direction.DOWN'))
		self.LEFT_Stick_DOWN.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.2,rely=0.4,x=0,y=0)

		self.LEFT_Stick_DOWN_RIGHT = ttk.Button(self.Controller_Frame)
		self.LEFT_Stick_DOWN_RIGHT.configure(text='↘', command=lambda: self.PressButton('Direction.DOWN_RIGHT'))
		self.LEFT_Stick_DOWN_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.3,rely=0.4,x=0,y=0)

		self.Button_X = ttk.Button(self.Controller_Frame)
		self.Button_X.configure(text='X', command=lambda: self.PressButton('Button.X'))
		self.Button_X.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.7,rely=0.2,x=0,y=0)

		self.Button_Y = ttk.Button(self.Controller_Frame)
		self.Button_Y.configure(text='Y', command=lambda: self.PressButton('Button.Y'))
		self.Button_Y.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.6,rely=0.3,x=0,y=0)

		self.Button_A = ttk.Button(self.Controller_Frame)
		self.Button_A.configure(text='A', command=lambda:self.PressButton('Button.A'))
		self.Button_A.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.8,rely=0.3,x=0,y=0)

		self.Button_B = ttk.Button(self.Controller_Frame)
		self.Button_B.configure(text='B', command=lambda: self.PressButton('Button.B'))
		self.Button_B.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.7,rely=0.4,x=0,y=0)

		self.Button_Hat_LEFT_TOP = ttk.Button(self.Controller_Frame)
		self.Button_Hat_LEFT_TOP.configure(text='↖', command=lambda: self.PressButton('Hat.TOP_LEFT'))
		self.Button_Hat_LEFT_TOP.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.1,rely=0.6,x=0,y=0)

		self.Button_Hat_TOP = ttk.Button(self.Controller_Frame)
		self.Button_Hat_TOP.configure(text='↑', command=lambda: self.PressButton('Hat.TOP'))
		self.Button_Hat_TOP.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.2,rely=0.6,x=0,y=0)

		self.Button_Hat_RIGHT_TOP = ttk.Button(self.Controller_Frame)
		self.Button_Hat_RIGHT_TOP.configure(text='↗', command=lambda: self.PressButton('Hat.TOP_RIGHT'))
		self.Button_Hat_RIGHT_TOP.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.3,rely=0.6,x=0,y=0)

		self.Button_Hat_LEFT = ttk.Button(self.Controller_Frame)
		self.Button_Hat_LEFT.configure(text='←', command=lambda: self.PressButton('Hat.LEFT'))
		self.Button_Hat_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.1,rely=0.7,x=0,y=0)

		self.Button_Hat_RIGHT = ttk.Button(self.Controller_Frame)
		self.Button_Hat_RIGHT.configure(text='→', command=lambda: self.PressButton('Hat.RIGHT'))
		self.Button_Hat_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.3,rely=0.7,x=0,y=0)

		self.Button_Hat_BTM_LEFT = ttk.Button(self.Controller_Frame)
		self.Button_Hat_BTM_LEFT.configure(text='↙', command=lambda: self.PressButton('Hat.BTM_LEFT'))
		self.Button_Hat_BTM_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.1,rely=0.8,x=0,y=0)

		self.Button_Hat_BTM = ttk.Button(self.Controller_Frame)
		self.Button_Hat_BTM.configure(text='↓', command=lambda: self.PressButton('Hat.BTM'))
		self.Button_Hat_BTM.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.2,rely=0.8,x=0,y=0)

		self.Button_Hat_BTM_RIGHT = ttk.Button(self.Controller_Frame)
		self.Button_Hat_BTM_RIGHT.configure(text='↘', command=lambda: self.PressButton('Hat.BTM_RIGHT'))
		self.Button_Hat_BTM_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.3,rely=0.8,x=0,y=0)

		self.RIGHT_Stick_UP_LEFT = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_UP_LEFT.configure(text='↖', command=lambda: self.PressButton('Direction.R_UP_LEFT'))
		self.RIGHT_Stick_UP_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.6,rely=0.6,x=0,y=0)

		self.RIGHT_Stick_UP = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_UP.configure(text='↑', command=lambda: self.PressButton('Direction.R_UP'))
		self.RIGHT_Stick_UP.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.7,rely=0.6,x=0,y=0)

		self.RIGHT_Stick_UP_RIGHT = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_UP_RIGHT.configure(text='↗', command=lambda: self.PressButton('Direction.R_UP_RIGHT'))
		self.RIGHT_Stick_UP_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.8,rely=0.6,x=0,y=0)

		self.RIGHT_Stick_LEFT = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_LEFT.configure(text='←', command=lambda: self.PressButton('Direction.R_LEFT'))
		self.RIGHT_Stick_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.6,rely=0.7,x=0,y=0)

		self.RIGHT_Stick_R3 = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_R3.configure(text='■', command=lambda: self.PressButton('Button.RCLICK'))
		self.RIGHT_Stick_R3.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.7,rely=0.7,x=0,y=0)

		self.RIGHT_Stick_RIGHT = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_RIGHT.configure(text='→', command=lambda: self.PressButton('Direction.R_RIGHT'))
		self.RIGHT_Stick_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.8,rely=0.7,x=0,y=0)

		self.RIGHT_Stick_DOWN_LEFT = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_DOWN_LEFT.configure(text='↙', command=lambda: self.PressButton('Direction.R_DOWN_LEFT'))
		self.RIGHT_Stick_DOWN_LEFT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.6,rely=0.8,x=0,y=0)

		self.RIGHT_Stick_DOWN = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_DOWN.configure(text='↓', command=lambda: self.PressButton('Direction.R_DOWN'))
		self.RIGHT_Stick_DOWN.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.7,rely=0.8,x=0,y=0)

		self.RIGHT_Stick_DOWN_RIGHT = ttk.Button(self.Controller_Frame)
		self.RIGHT_Stick_DOWN_RIGHT.configure(text='↘', command=lambda: self.PressButton('Direction.R_DOWN_RIGHT'))
		self.RIGHT_Stick_DOWN_RIGHT.place(anchor="nw",relheight=0.075,relwidth=0.075,relx=0.8,rely=0.8,x=0,y=0)

		self.Button_CAPTURE = ttk.Button(self.Controller_Frame)
		self.Button_CAPTURE.configure(text='CAP', command=lambda: self.PressButton('Button.CAPTURE'))
		self.Button_CAPTURE.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.35,rely=0.9,x=0,y=0)

		self.Button_HOME = ttk.Button(self.Controller_Frame)
		self.Button_HOME.configure(text='HOME', command=lambda: self.PressButton('Button.HOME'))
		self.Button_HOME.place(anchor="nw",relheight=0.075,relwidth=0.1,relx=0.55,rely=0.9,x=0,y=0)

		

		# ===================================================================================================================== #

		self.Controller_Frame.place(anchor="nw",relheight=0.5175,relwidth=0.57,relx=0.42,rely=0.271,x=0,y=0)

		self.ETC_Frame = ttk.Labelframe(self.Body)
		self.ETC_Frame.configure(height=200, text='etc', width=200)
		# self.ETC_Label_Threshold = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_Threshold.configure(anchor="center", text='threshold')
		# self.ETC_Label_Threshold.place(relheight=0.2, relwidth=0.1, rely=0.0, x=0, y=0)

		# self.ETC_Label_show_gray = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_show_gray.configure(anchor="center", text='show_gray')
		# self.ETC_Label_show_gray.place(relheight=0.2, relwidth=0.1, rely=0.25, x=0, y=0)

		# self.ETC_Label_Position = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_Position.configure(anchor="center", text='position')
		# self.ETC_Label_Position.place(relheight=0.2, relwidth=0.1, rely=0.5, x=0, y=0)

		# self.ETC_Label_KeyBoard = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_KeyBoard.configure(anchor="center", text='KeyBoard')
		# self.ETC_Label_KeyBoard.place(relheight=0.2, relwidth=0.1, rely=0.75, x=0, y=0)

		self.ETC_Label_Duration = ttk.Label(self.ETC_Frame)
		self.ETC_Label_Duration.configure(anchor="center", text='duration')
		self.ETC_Label_Duration.place(relheight=0.2,relwidth=0.1,relx=0.15,rely=0.0,x=0,y=0)

		# self.ETC_Label_Repeat = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_Repeat.configure(anchor="center", text='repeat')
		# self.ETC_Label_Repeat.place(relheight=0.2,relwidth=0.1,relx=0.15,rely=0.25,x=0,y=0)

		self.ETC_Label_Wait = ttk.Label(self.ETC_Frame)
		self.ETC_Label_Wait.configure(anchor="center", text='wait')
		self.ETC_Label_Wait.place(relheight=0.2,relwidth=0.1,relx=0.15,rely=0.5,x=0,y=0)

		# self.ETC_Label_Print = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_Print.configure(anchor="center", text='print')
		# self.ETC_Label_Print.place(relheight=0.2,relwidth=0.05,relx=0.3,rely=0.0,x=0,y=0)

		# self.ETC_Label_X1 = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_X1.configure(anchor="center", text='x1')
		# self.ETC_Label_X1.place(relheight=0.2,relwidth=0.025,relx=0.5,rely=0.0,x=0,y=0)

		# self.ETC_Label_X2 = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_X2.configure(anchor="center", text='x2')
		# self.ETC_Label_X2.place(relheight=0.2,relwidth=0.025,relx=0.5,rely=0.25,x=0,y=0)

		# self.ETC_Label_Y1 = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_Y1.configure(anchor="center", text='y1')
		# self.ETC_Label_Y1.place(relheight=0.2,relwidth=0.025,relx=0.5,rely=0.5,x=0,y=0)

		# self.ETC_Label_Y2 = ttk.Label(self.ETC_Frame)
		# self.ETC_Label_Y2.configure(anchor="center", text='y2')
		# self.ETC_Label_Y2.place(relheight=0.2,relwidth=0.025,relx=0.5,rely=0.75,x=0,y=0)

		# self.ETC_Entry_Threshold = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_Threshold.configure(justify="center")
		# self.ETC_Entry_Threshold.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.1,rely=0.0,x=0,y=0)

		# self.ETC_Check_Show_Gray = ttk.Checkbutton(self.ETC_Frame)
		# self.ETC_Check_Show_Gray.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.1,rely=0.25,x=0,y=0)

		# self.ETC_Check_Postion = ttk.Checkbutton(self.ETC_Frame)
		# self.ETC_Check_Postion.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.1,rely=0.5,x=0,y=0)

		# self.ETC_Button_PushOnce = ttk.Button(self.ETC_Frame)
		# self.ETC_Button_PushOnce.configure(text='初めに一回')
		# self.ETC_Button_PushOnce.place(anchor="nw",relheight=0.2,relwidth=0.1,relx=0.1,rely=0.75,x=0,y=0)

		# self.ETC_Button_KeyBoard_Input = ttk.Button(self.ETC_Frame)
		# self.ETC_Button_KeyBoard_Input.configure(text='入力')
		# self.ETC_Button_KeyBoard_Input.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.2,rely=0.75,x=0,y=0)

		self.ETC_Entry_Duration = ttk.Entry(self.ETC_Frame)
		self.ETC_Entry_Duration_value = tk.StringVar()
		self.ETC_Entry_Duration.configure(justify="center",textvariable=self.ETC_Entry_Duration_value)
		self.ETC_Entry_Duration.delete("0", "end")
		self.ETC_Entry_Duration.insert(tk.END, "0.1")	
		self.ETC_Entry_Duration.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.25,rely=0.0,x=0,y=0)

		# self.ETC_Entry_Repeat = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_Repeat.configure(justify="center")
		# self.ETC_Entry_Repeat.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.25,rely=0.25,x=0,y=0)

		self.ETC_Entry_Wait = ttk.Entry(self.ETC_Frame)
		self.ETC_Entry_Wait_value = tk.StringVar()
		self.ETC_Entry_Wait.configure(justify="center",textvariable=self.ETC_Entry_Wait_value)
		self.ETC_Entry_Wait.delete("0", "end")
		self.ETC_Entry_Wait.insert(tk.END, "0.1")
		self.ETC_Entry_Wait.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.25,rely=0.5,x=0,y=0)

		# self.ETC_Entry_KeyBoard = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_KeyBoard.configure(justify="center")
		# self.ETC_Entry_KeyBoard.place(anchor="nw",relheight=0.2,relwidth=0.25,relx=0.25,rely=0.75,x=0,y=0)

		# self.ETC_Entry_Print = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_Print.configure(justify="center")
		# self.ETC_Entry_Print.place(anchor="nw",relheight=0.2,relwidth=0.15,relx=0.35,rely=0.0,x=0,y=0)

		# self.ETC_Button_Print_Input = ttk.Button(self.ETC_Frame)
		# self.ETC_Button_Print_Input.configure(text='入力')
		# self.ETC_Button_Print_Input.place(anchor="nw",relheight=0.2,relwidth=0.2,relx=0.3,rely=0.25,x=0,y=0)

		# self.ETC_Entry_X1 = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_X1.configure(justify="center")
		# self.ETC_Entry_X1.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.525,rely=0.0,x=0,y=0)

		# self.ETC_Entry_X2 = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_X2.configure(justify="center")
		# self.ETC_Entry_X2.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.525,rely=0.25,x=0,y=0)

		# self.ETC_Entry_Y1 = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_Y1.configure(justify="center")
		# self.ETC_Entry_Y1.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.525,rely=0.5,x=0,y=0)

		# self.ETC_Entry_Y2 = ttk.Entry(self.ETC_Frame)
		# self.ETC_Entry_Y2.configure(justify="center")
		# self.ETC_Entry_Y2.place(anchor="nw",relheight=0.2,relwidth=0.05,relx=0.525,rely=0.75,x=0,y=0)

		self.ETC_Button_CommentOut_Value = ttk.Button(self.ETC_Frame)
		self.ETC_Button_CommentOut_Value.configure(text='コメント', command=self.OpenCommentOutPDialog)
		self.ETC_Button_CommentOut_Value.place(anchor="nw", relheight=0.5, relx=0.5, rely=0.0, x=0, y=0.5)

		self.ETC_Button_Format_Value = ttk.Button(self.ETC_Frame)
		self.ETC_Button_Format_Value.configure(text='format',command=self.Text_Format,state="disabled")
		self.ETC_Button_Format_Value.place(anchor="nw", relheight=0.5, relx=0.5, rely=0.5, x=0, y=0.5)

		self.ETC_Button_Template_Value = ttk.Button(self.ETC_Frame)
		self.ETC_Button_Template_Value.configure(text='座標指定\n＆\n画像認識',command=self.OpenTemplatePDialog)
		self.ETC_Button_Template_Value.place(anchor="nw", relheight=1.0, relx=0.6, rely=0.0, x=0, y=0)

		self.ETC_Button_Edit_PNG = ttk.Button(self.ETC_Frame)
		self.ETC_Button_Edit_PNG.configure(state="disabled", text='画像編集\n（未実装）')
		self.ETC_Button_Edit_PNG.place(anchor="nw", relheight=1, relx=0.7, rely=0.0, x=0, y=0)

		self.ETC_Button_OCR = ttk.Button(self.ETC_Frame)
		self.ETC_Button_OCR.configure(state="disabled", text='文字認識\n（未実装）')
		self.ETC_Button_OCR.place(anchor="nw",relheight=1,relx=0.8,rely=0,x=0,y=0)

		self.ETC_Button_NewFile = ttk.Button(self.ETC_Frame)
		self.ETC_Button_NewFile.configure(text='新規作成', command=self.OpenNewFileDialog)
		self.ETC_Button_NewFile.place(anchor="nw",relheight=0.25,relx=0.9,rely=0.0,x=0,y=0)

		self.ETC_Button_OpenFile = ttk.Button(self.ETC_Frame)
		self.ETC_Button_OpenFile.configure(text='開く',command=self.OpenFile_Button)
		self.ETC_Button_OpenFile.place(anchor="nw", relheight=0.25, relx=0.9, rely=0.25, x=0, y=0)

		self.ETC_Button_SaveFile = ttk.Button(self.ETC_Frame)
		self.ETC_Button_SaveFile.configure(text='保存', command=self.Save)
		self.ETC_Button_SaveFile.place(anchor="nw", relheight=0.25, relx=0.9, rely=0.5, x=0, y=0)

		self.ETC_Button_NewFileSave = ttk.Button(self.ETC_Frame)
		self.ETC_Button_NewFileSave.configure(text='新規保存',command=self.New_SaveFile)
		self.ETC_Button_NewFileSave.place(anchor="nw", relheight=0.25, relx=0.9, rely=0.75, x=0, y=0)

		# self.ETC_Button_Finish = ttk.Button(self.ETC_Frame)
		# self.ETC_Button_Finish.configure(
		# 	text='終了', command=menu.closingCodeDesigner)
		# self.ETC_Button_Finish.place(anchor="nw", relheight=1, relx=0.9, rely=0.0, x=0, y=0)

		self.ETC_Frame.place(anchor="nw",relheight=0.2,relwidth=0.98,relx=0.01,rely=0.79,x=0,y=0)
		# ===================================================================================================================== #

		self.Body.grid_propagate(0)
		self.Menu_Bar = tk.Menu(self.Body, tearoff=0)
		self.Menu_Command = tk.Menu(self.Menu_Bar, tearoff=0)
		self.Menu_Settings = tk.Menu(self.Menu_Bar, tearoff=0)
		self.Menu_Recent = tk.Menu(self.Menu_Bar, tearoff=0)
		self.Menu_Controller = tk.Menu(self.Menu_Bar,tearoff=0)

		

		
		self.Body.config(menu=self.Menu_Bar)
		self.Menu_Bar_some()
		self.SC_BIND()
		# self.mainwindow.bind("Button-1", self.Finish_Button)
		


		# self.Press_Bind()
	# def run(self):
	# 	self.Body.mainloop()


	def Menu_Bar_some(self):
		self.Menu_Bar.add_cascade(label='メニュー',menu=self.Menu_Command)
		self.Menu_Bar.add_cascade(label='Readme')
		self.Menu_Bar.add_cascade(label='Controller')

		self.Menu_Command.add_command(
			label=u"新規作成", command=self.OpenNewFileDialog, accelerator='Ctrl-N')
		self.Menu_Command.add_command(
			label=u"開く", command=self.OpenFile_Button, accelerator='Ctrl-O')
		self.Menu_Command.add_command(label=u"保存",command=self.Save ,accelerator='Ctrl-S')
		self.Menu_Command.add_command(
			label=u"名前を付けて保存", command=self.New_SaveFile, accelerator='Ctrl-Shift-S')
		self.Menu_Command.add_cascade(label=u'最近開いたファイル(dummy)',menu=self.Menu_Recent)  # リストでここ最近開いたものを取得
		self.Menu_Command.add_separator()
		self.Menu_Command.add_cascade(label=u"設定",menu=self.Menu_Settings)
		self.Menu_Command.add_separator()
		self.Menu_Command.add_command(label=u"終了", command=self.destroy, accelerator='Ctrl-Q')
		# self.Menu_Command.add(tk.CASCADE,menu=self.PCEmenu_Command ,label='最近開いたファイル(dummy)') # リストでここ最近開いたものを取得
		self.Menu_Bar_ADD()

	def Menu_Bar_ADD(self):
		self.Menu_Recent.add_command(label='dummy')
		self.Menu_Settings.add_command(label='プロファイル作成(dummy)')
		self.Menu_Settings.add_command(label='プロファイル読み込み(dummy)')

	def SC_BIND(self):
		self.Body.bind("<Control-KeyRelease-q>", self.destroy)
		self.Body.bind("<Return>", "break")
		self.Body.bind("<Shift-Return>", self.testfunc)
		self.Body.bind("<Control-KeyRelease-o>", self.OpenFile_Button)			
		self.Body.bind("<Control-KeyRelease-n>", self.OpenNewFileDialog)
		self.Body.bind("<Control-KeyRelease-q>", self.destroy)
		self.Body.bind("<Control-KeyRelease-s>", self.Save)
		self.Body.bind("<Control-Shift-S>", self.New_SaveFile)

		

		



	def bind(self, event, func):
		self.Body.bind(event, func)

	def protocol(self, event, func):
		self.Body.protocol(event, func)

	def focus_force(self):
		self.Body.focus_force()



	def destroy(self,event=None):
		self.Body.destroy()
		self.master.code_de = None
		

	def ValueGet(self):
		self.ETC_Entry_Duration_value2 = self.ETC_Entry_Duration_value.get()
		self.ETC_Entry_Wait_value2 = self.ETC_Entry_Wait_value.get()



	def saveCapture_Button(self):
		self.TAB_Get()
		self.val = (f'self.saveCapture("")\n{self.tab_val}')
		self.Text_Edit_BOX.insert(tk.INSERT, self.val)

	def checkIfAlive_Button(self):
		self.TAB_Get()
		self.val = (f"self.checkIfAlive()\n{self.tab_val}")
		self.Text_Edit_BOX.insert(tk.INSERT, self.val)
		
	def Finish_Button(self):
		self.TAB_Get()
		self.val = (f"self.finish()\n{self.tab_val}")
		self.Text_Edit_BOX.insert(tk.INSERT, self.val)


	# pressButtonはPCE‗Commandに追加した。
	# インデントの処理の追加がまだ
	# インデント処理はまた今度。　
	# self.Check_ControllerがTrueの時の動作も追加したい。

	def PressButton(self,Button):
		self.TAB_Get()
		self.ValueGet()
		txt = PCEC.PCE_Command.pressButton(Button = Button,duration=self.ETC_Entry_Duration_value2,wait=self.ETC_Entry_Wait_value2,tab=self.tab_val)
		self.Text_Edit_BOX.insert(tk.INSERT, txt)

	def TAB_Get(self):
		txt = self.Text_Edit_BOX.get('insert linestart',"insert lineend")
		Count =txt.count("	")
		# print(Count)
		self.tab_val = "	"*Count
		# self.Text_Edit_BOX.insert(tk.INSERT,txt)
		# return self.tab_val

	def testfunc(self,event):
		pass
	
	def TAB_insert(self,event):
		txt = self.Text_Edit_BOX.get('insert linestart', "insert lineend")
		Count = txt.count("\t")
		# print(Count)
		self.tab_val = "	"*Count
		self.Text_Edit_BOX.insert(tk.INSERT, self.tab_val)

	def OpenPrintDialog(self):
		if self.printD is not None:
			self.printD.focus_force()
			return
		self._logger.debug("Open print dialog")
		Print_Dialog_window = PCED.PrintDialog(self)
		Print_Dialog_window.focus_force()
		Print_Dialog_window.protocol("WM_DELETE_WINDOW", Print_Dialog_window.destroy)
		self.printD = Print_Dialog_window


	def OpenWhileDialog(self):
		if self.WhileD is not None:
			self.WhileD.focus_force()
			return
		self._logger.debug("Open While dialog")
		While_Dialog_window = PCED.WhileDialog(self)
		While_Dialog_window.focus_force()
		While_Dialog_window.protocol("WM_DELETE_WINDOW", While_Dialog_window.destroy)
		self.WhileD = While_Dialog_window



	def OpenDefDialog(self):
		if self.DefD is not None:
			self.DefD.focus_force()
			return
		self._logger.debug("Open Def dialog")
		Def_Dialog_window = PCED.DefDialog(self)
		Def_Dialog_window.focus_force()
		Def_Dialog_window.protocol("WM_DELETE_WINDOW", Def_Dialog_window.destroy)
		self.DefD = Def_Dialog_window


	def OpenForDialog(self):
		if self.ForD is not None:
			self.ForD.focus_force()
			return
		self._logger.debug("Open For dialog")
		For_Dialog_window = PCED.ForDialog(self)
		For_Dialog_window.focus_force()
		For_Dialog_window.protocol("WM_DELETE_WINDOW", For_Dialog_window.destroy)
		self.ForD = For_Dialog_window


	def OpenWaitDialog(self):
		if self.WaitD is not None:
			self.WaitD.focus_force()
			return
		self._logger.debug("Open Wait dialog")
		Wait_Dialog_window = PCED.WaitDialog(self)
		Wait_Dialog_window.focus_force()
		Wait_Dialog_window.protocol("WM_DELETE_WINDOW", Wait_Dialog_window.destroy)
		self.WaitD = Wait_Dialog_window


	def OpenTemplateDialog(self):
		if self.TemplateD is not None:
			self.TemplateD.focus_force()
			return
		self._logger.debug("Open Template dialog")
		Template_Dialog_window = PCED.TemplateDialog(self)
		Template_Dialog_window.focus_force()
		Template_Dialog_window.protocol("WM_DELETE_WINDOW", Template_Dialog_window.destroy)
		self.TemplateD = Template_Dialog_window

		

	def OpenTemplatePDialog(self):
		if self.TemplatePD is not None:
			self.TemplatePD.focus_force()
			return
		self._logger.debug("Open Template dialog")
		TemplateP_Dialog_window = PCED.TemplatePositonDialog(self)
		TemplateP_Dialog_window.focus_force()
		TemplateP_Dialog_window.protocol(
			"WM_DELETE_WINDOW", TemplateP_Dialog_window.destroy)
		self.TemplatePD = TemplateP_Dialog_window


	def OpenLINEtextDialog(self):
		if self.LINEtextD is not None:
			self.LINEtextD.focus_force()
			return
		self._logger.debug("Open LINEtext dialog")
		LINEtextP_Dialog_window = PCED.LINEtextDialog(self)
		LINEtextP_Dialog_window.focus_force()
		LINEtextP_Dialog_window.protocol(
			"WM_DELETE_WINDOW", LINEtextP_Dialog_window.destroy)
		self.LINEtextD = LINEtextP_Dialog_window
		

	def OpenLINEimageDialog(self):
		if self.LINEimageD is not None:
			self.LINEimageD.focus_force()
			return
		self._logger.debug("Open LINEimage dialog")
		LINEimageP_Dialog_window = PCED.LINEimageDialog(self)
		LINEimageP_Dialog_window.focus_force()
		LINEimageP_Dialog_window.protocol(
			"WM_DELETE_WINDOW", LINEimageP_Dialog_window.destroy)
		self.LINEimageD = LINEimageP_Dialog_window


	def OpenCommentOutPDialog(self):
		if self.CommentD is not None:
			self.CommentD.focus_force()
			return
		self._logger.debug("Open Comment dialog")
		Comment_Dialog_window = PCED.CommentDialog(self)
		Comment_Dialog_window.focus_force()
		Comment_Dialog_window.protocol(
			"WM_DELETE_WINDOW", Comment_Dialog_window.destroy)
		self.CommentD = Comment_Dialog_window
		
	def OpenNewFileDialog(self,event=None):
		if self.NewFileD is not None:
			self.NewFileD.focus_force()
			return
		self._logger.debug("Open NewFile dialog")
		NewFile_Dialog_window = PCED.NewFileDialog(self)
		NewFile_Dialog_window.focus_force()
		NewFile_Dialog_window.protocol(
			"WM_DELETE_WINDOW", NewFile_Dialog_window.destroy)
		self.NewFileD = NewFile_Dialog_window
		self.path = None


	def Text_Format(self):
		self.Text_Edit_value  = self.Text_Edit_BOX.get("1.0","end-1c")
		self.Text_Edit_value = self.Text_Edit_value.replace("	", "    ")
		self.Text_Edit_BOX.delete("1.0","end")
		self.Text_Edit_BOX.insert(tk.END,self.Text_Edit_value)

	def new_insert(self):
		txt2 = f"		"
		self.Text_Edit_BOX.tag_configure("lb", background="#DDEEFF")
		self.Text_Edit_BOX.insert("15.0", txt2, "lb")

	def New_SaveFile(self,event=None):
		txt = self.Text_Edit_BOX.get("1.0", 'end-1c')
		title = self.Body.title()
		title = title.replace("Pokecon_Code_Editor:","")
		if title is "" and txt is "":
			return
		elif title is "":
			title = "Untitled"
		
		return_Values = PCEC.PCE_Command.New_SaveFile(txt=txt,title=title)
		if return_Values is None:
			return
		else:
			self.Body.title(f"Pokecon_Code_Editor:{return_Values[0]}")
			self.path = return_Values[1]
			return_Values = None

		

	def OpenFile_Button(self,event=None):
		return_Values = PCEC.PCE_Command.OpenFile(self)
		if return_Values is None:
			return
		else:
			txt = return_Values[0]
			title = return_Values[1]
			self.path = return_Values[2]
			self.Body.title(f"Pokecon_Code_Editor:{title}")
			self.Text_Edit_BOX.delete("1.0", "end")
			self.Text_Edit_BOX.insert(tk.END,txt)
			return_Values = None

	def Save(self,event=None):
		title = self.Body.title()
		title = title.replace("Pokecon_Code_Editor:", "")
		txt = self.Text_Edit_BOX.get("1.0", 'end-1c')
		
		if title is"" and txt is "":
			return
		
		elif title is "" and self.path is None:
			title = "Untitled"			
			return_Values =  PCEC.PCE_Command.New_SaveFile(txt=txt, title=title)
			if return_Values is None:
				return
			else:
				self.Body.title(return_Values[0])
				self.path = return_Values[1]
				return_Values= None

		else:
			if self.path is None:
				return_Values = PCEC.PCE_Command.New_SaveFile(txt=txt, title=title)
				if return_Values is None:
					return
				else:
					self.Body.title(return_Values[0])
					self.path = return_Values[1]
			else:
				return_Values = PCEC.PCE_Command.SaveFile(txt,title,self.path)
				if return_Values is None:
					return
				else:
					self.Body.title(return_Values[0])
					self.path = return_Values[1]

	def ValueNone(self):
		self.listener = None
		self.printD = None
		self.WhileD = None
		self.DefD = None
		self.ForD = None
		self.WaitD = None
		self.TemplateD = None
		self.TemplatePD = None
		self.LINEtextD = None
		self.LINEimageD = None
		self.CommentD = None
		self.NewFileD = None
		self.path = None
		self.Default_Value = "0.1"

		

	


	def OpenCodeDesigner(self):
		self._logger.debug("Open code designer")
		if self.code_de is not None:
			self.code_de.focus_force()
			return
		CD_window = Pokecon_Code_Editor(self.root, self)
		CD_window.protocol("WM_DELETE_WINDOW", self.closingCodeDesigner)
		self.code_de = CD_window

	def closingCodeDesigner(self):
		self._logger.debug("Close Code Desinger window")
		self.code_de.destroy()
		self.code_de = None

	
