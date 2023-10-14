#!/usr/bin/python3
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog
from logging import getLogger, DEBUG, NullHandler
import os


	
class PrintDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		Print_Toplevel = tk.Toplevel(self.root)
		Print_Toplevel.configure(height=200, width=200)
		Print_Toplevel.geometry("400x200")
		Print_Toplevel.maxsize(400, 200)
		Print_Toplevel.minsize(400, 200)
		Print_Toplevel.title("PCE_Print")
		Print_Frame = ttk.Frame(Print_Toplevel)
		Print_Frame.configure(height=200, width=400)
		Print_Label1 = ttk.Label(Print_Frame)
		Print_Label1.configure(anchor="center",background="#00ffff",text='print')
		Print_Label1.place(relheight=0.1,relwidth=0.1,relx=0.05,rely=0.05,x=0,y=0)
		Print_Label2 = ttk.Label(Print_Frame)
		Print_Label2.configure(anchor="center", text='printしたい文字列')
		Print_Label2.place(relwidth=0.9, relx=0.05, rely=0.25, x=0, y=0)
		Print_Label3 = ttk.Label(Print_Frame)
		Print_Label3.configure(anchor="center", text='例:print("テスト")')
		Print_Label3.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.4,x=0,y=0)
		Print_Label4 = ttk.Label(Print_Frame)
		Print_Label4.configure(anchor="center",foreground="#ff0000",text='出力結果：テスト')
		Print_Label4.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.55,x=0,y=0)
		Print_Label5 = ttk.Label(Print_Frame)
		Print_Label5.configure(anchor="center",background="#00ffff",text='print')
		Print_Label5.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)

		Print_Entry = ttk.Entry(Print_Frame)
		self.Print_Entry_Value = tk.StringVar()
		Print_Entry.configure(textvariable=self.Print_Entry_Value,justify="center")
		Print_Entry.delete("0", "end")
		Print_Entry.insert(tk.END, "")
		Print_Entry.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)

		Print_Button = ttk.Button(Print_Frame)
		Print_Button.configure(text='OK',command=self.Print_Entry_Get)
		Print_Button.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.82,x=0,y=0)

		Print_Frame.place(anchor="nw", x=0, y=0)
		self.mainwindow = Print_Toplevel

	def Print_Entry_Get(self):
		self.Print_Entry_Value = self.Print_Entry_Value.get()
		txt = (f'print("{self.Print_Entry_Value}")\n		')
		if self.Print_Entry_Value is "":
			self._logger.debug("Close Print Dialog")
			self.mainwindow.destroy()
			self.master.printD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close Print Dialog")
			self.mainwindow.destroy()
			self.master.printD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)


	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close Print Dialog")
		self.mainwindow.destroy()
		self.master.printD = None

	def run(self):
		self.mainwindow.mainloop()



class WhileDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		While_Toplevel = tk.Toplevel(self.root)
		While_Toplevel.configure(height=200, width=200)
		While_Toplevel.geometry("400x200")
		While_Toplevel.maxsize(400, 200)
		While_Toplevel.minsize(400, 200)
		While_Toplevel.title("PCE_While")
		While_Frame = ttk.Frame(While_Toplevel)
		While_Frame.configure(height=200, width=400)
		label10 = ttk.Label(While_Frame)
		label10.configure(anchor="center",background="#00ffff",text='while')
		label10.place(relheight=0.1,relwidth=0.1,relx=0.05,rely=0.05,x=0,y=0)
		label15 = ttk.Label(While_Frame)
		label15.configure(anchor="center", text='繰り返し（回数指定なし）')
		label15.place(relwidth=0.8, relx=0.15, rely=0.05, x=0, y=0)
		label16 = ttk.Label(While_Frame)
		label16.configure(anchor="center",text='例:while True:\n\tif ~~:\n\t\u3000\u3000break')
		label16.place(relheight=0.3,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label17 = ttk.Label(While_Frame)
		label17.configure(anchor="center",foreground="#ff0000",text='注意：breakは必ず入れてください。')
		label17.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.65,x=0,y=0)
		button8 = ttk.Button(While_Frame)
		button8.configure(text='OK',command=self.While_Get)
		button8.place(anchor="nw",relwidth=0.9,relx=0.05,rely=0.82,x=0,y=0)
		While_Frame.place(anchor="nw", x=0, y=0)

		self.mainwindow = While_Toplevel

	def While_Get(self):
		txt = ("while True:\n			\n			break\n		")
		self.text.insert(tk.INSERT, txt)
		self._logger.debug("Close While Dialog")
		self.mainwindow.destroy()
		self.master.WhileD = None
		

	def run(self):
		self.mainwindow.mainloop()

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close While Dialog")
		self.mainwindow.destroy()
		self.master.WhileD = None

	def run(self):
		self.mainwindow.mainloop()

class DefDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		Def_Toplevel = tk.Toplevel(self.root)
		Def_Toplevel.configure(width=200)
		Def_Toplevel.geometry("400x200")
		Def_Toplevel.maxsize(400, 200)
		Def_Toplevel.minsize(400, 200)
		Def_Toplevel.title("PCE_Def")
		Def_Frame = ttk.Frame(Def_Toplevel)
		Def_Frame.configure(height=200, width=400)
		label9 = ttk.Label(Def_Frame)
		label9.configure(anchor="center",background="#00ffff",text='def')
		label9.place(relheight=0.1,relwidth=0.1,relx=0.05,rely=0.05,x=0,y=0)
		label13 = ttk.Label(Def_Frame)
		label13.configure(anchor="center", text='例:def test(self):')
		label13.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label14 = ttk.Label(Def_Frame)
		label14.configure(anchor="center",background="#00ffff",text='def')
		label14.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)

		Def_entry = ttk.Entry(Def_Frame)
		self.Def_Entry_Value = tk.StringVar()
		Def_entry.configure(textvariable=self.Def_Entry_Value, justify="center")
		Def_entry.delete("0", "end")
		Def_entry.insert(tk.END, "")
		Def_entry.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)



		button6 = ttk.Button(Def_Frame)
		button6.configure(text='OK',command=self.Def_Get)
		button6.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.82,x=0,y=0)
		label18 = ttk.Label(Def_Frame)
		label18.configure(anchor="center",foreground="#ff0000",text='注意：処理を必ず入れてください。')
		label18.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.55,x=0,y=0)
		label24 = ttk.Label(Def_Frame)
		label24.configure(anchor="center", text='関数作成')
		label24.place(relwidth=0.8, relx=0.15, rely=0.05, x=0, y=0)
		Def_Frame.place(anchor="nw", x=0, y=0)

		self.mainwindow = Def_Toplevel

	def Def_Get(self):
		self.Def_Entry_Value = self.Def_Entry_Value.get()
		txt = (
			f"def {self.Def_Entry_Value}(self):\n			\n			pass")
		if self.Def_Entry_Value is "":
			self._logger.debug("Close Def Dialog")
			self.mainwindow.destroy()
			self.master.DefD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close Def Dialog")
			self.mainwindow.destroy()
			self.master.DefD = None

	def run(self):
		self.mainwindow.mainloop()

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close Def Dialog")
		self.mainwindow.destroy()
		self.master.DefD = None

	def run(self):
		self.mainwindow.mainloop()

class ForDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		For_Toplevel = tk.Toplevel(self.root)
		For_Toplevel.configure(height=200, width=200)
		For_Toplevel.geometry("400x200")
		For_Toplevel.maxsize(400, 200)
		For_Toplevel.minsize(400, 200)
		For_Toplevel.title("PCE_For")
		For_Frame = ttk.Frame(For_Toplevel)
		For_Frame.configure(height=200, width=400)
		label11 = ttk.Label(For_Frame)
		label11.configure(anchor="center",background="#00ffff",text='for')
		label11.place(relheight=0.1,relwidth=0.1,relx=0.05,rely=0.05,x=0,y=0)
		label19 = ttk.Label(For_Frame)
		label19.configure(anchor="center", text='繰り返し（回数指定あり）')
		label19.place(relwidth=0.8, relx=0.15, rely=0.05, x=0, y=0)
		label20 = ttk.Label(For_Frame)
		label20.configure(anchor="center",text='例:for counter in range(5):\n\tprint(counter)\n\t#0,1,2,3,4を順次出力')
		label20.place(relheight=0.3,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		button9 = ttk.Button(For_Frame)
		button9.configure(text='OK',command=self.For_Entry_Get)
		button9.place(anchor="nw",relwidth=0.9,relx=0.05,rely=0.82,x=0,y=0)

		For_Entry = ttk.Entry(For_Frame)
		self.For_Entry_Value1 = tk.StringVar()
		For_Entry.configure(textvariable=self.For_Entry_Value1,justify='center')
		For_Entry.delete("0", "end")
		For_Entry.insert(tk.END, "count")
		For_Entry.place(anchor="nw",relwidth=0.2,relx=0.275,rely=0.7,x=0,y=0)

		label21 = ttk.Label(For_Frame)
		label21.configure(anchor="center",background="#00ffff",text='引数名')
		label21.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)
		label22 = ttk.Label(For_Frame)
		label22.configure(anchor="center",background="#00ffff",text='回数')
		label22.place(relheight=0.1,relwidth=0.2,relx=0.525,rely=0.7,x=0,y=0)

		For_Entry2 = ttk.Entry(For_Frame)
		self.For_Entry_Value2 = tk.StringVar()
		For_Entry2.configure(textvariable=self.For_Entry_Value2, justify='center')
		For_Entry2.delete("0", "end")
		For_Entry2.insert(tk.END, "")
		For_Entry2.place(anchor="nw",relwidth=0.2,relx=0.75,rely=0.7,x=0,y=0)

		For_Frame.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = For_Toplevel

	def run(self):
		self.mainwindow.mainloop()

	def For_Entry_Get(self):
		For_Entry_Value1 = self.For_Entry_Value1.get()
		For_Entry_Value2 = self.For_Entry_Value2.get()
		txt = (
			f"for {For_Entry_Value1} in range ({For_Entry_Value2}):\n			\n			pass")
		if For_Entry_Value1 is "" or For_Entry_Value2 is "":
			self._logger.debug("Close For Dialog")
			self.mainwindow.destroy()
			self.master.ForD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close For Dialog")
			self.mainwindow.destroy()
			self.master.ForD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close For Dialog")
		self.mainwindow.destroy()
		self.master.ForD = None

class WaitDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		Wait_Toplevel = tk.Toplevel(self.root)
		Wait_Toplevel.configure(height=200, width=200)
		Wait_Toplevel.geometry("400x200")
		Wait_Toplevel.maxsize(400, 200)
		Wait_Toplevel.minsize(400, 200)
		Wait_Toplevel.title("PCE_Wait")
		frame6 = ttk.Frame(Wait_Toplevel)
		frame6.configure(height=200, width=400)
		label25 = ttk.Label(frame6)
		label25.configure(anchor="center",background="#00ffff",text='wait')
		label25.place(relheight=0.1,relwidth=0.1,relx=0.05,rely=0.05,x=0,y=0)
		label26 = ttk.Label(frame6)
		label26.configure(anchor="center", text='待機時間')
		label26.place(relwidth=0.8, relx=0.15, rely=0.05, x=0, y=0)
		label27 = ttk.Label(frame6)
		label27.configure(anchor="center",takefocus=False,text='例:\n\u3000\u3000wait(10) #10秒待機\n\u3000\u3000self.press(Button.A, duration = 0.1, wait = 0.1)\n\u3000\u3000# 10秒待機後、Aボタンを0.1秒押し、0.1秒待機')
		label27.place(relheight=0.4,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label28 = ttk.Label(frame6)
		label28.configure(anchor="center",background="#00ffff",text='wait')
		label28.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)

		Wait_Entry = ttk.Entry(frame6)
		self.Wait_Entry_Value = tk.StringVar()
		Wait_Entry.configure(textvariable=self.Wait_Entry_Value,justify="center")
		Wait_Entry.delete("0", "end")
		Wait_Entry.insert(tk.END, "")
		Wait_Entry.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)

		button10 = ttk.Button(frame6)
		button10.configure(text='OK',command=self.Wait_Entry_Get)
		button10.place(anchor="nw",relwidth=0.9,relx=0.05,rely=0.82,x=0,y=0)
		frame6.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = Wait_Toplevel

	def Wait_Entry_Get(self):
		self.Wait_Entry_Value = self.Wait_Entry_Value.get()
		txt = (f"self.wait({self.Wait_Entry_Value})\n		")
		if self.Wait_Entry_Value is "":
			self._logger.debug("Close Wait Dialog")
			self.mainwindow.destroy()
			self.master.WaitD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close Wait Dialog")
			self.mainwindow.destroy()
			self.master.WaitD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close Wait Dialog")
		self.mainwindow.destroy()
		self.master.WaitD = None

	def run(self):
		self.mainwindow.mainloop()

class TemplateDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		Template_Toplevel = tk.Toplevel(self.root)
		Template_Toplevel.configure(height=200, width=200)
		Template_Toplevel.geometry("600x300")
		Template_Toplevel.maxsize(600, 300)
		Template_Toplevel.minsize(600, 300)
		Template_Toplevel.title("PCE_Template")

		frame7 = ttk.Frame(Template_Toplevel)
		frame7.configure(height=300, width=600)
		label29 = ttk.Label(frame7)
		label29.configure(anchor="center",background="#00ffff",text='isContainTemplate')
		label29.place(relheight=0.1,relwidth=0.2,relx=0.025,rely=0.05,x=0,y=0)
		label30 = ttk.Label(frame7)
		label30.configure(anchor="center", text='画像認識\u3000(座標指定なし)')
		label30.place(relheight=0.1,relwidth=0.75,relx=0.225,rely=0.05,x=0,y=0)
		label31 = ttk.Label(frame7)
		label31.configure(anchor="center",takefocus=True,text='例:\u3000if self.isContainTemplate(template_path="test.png", \n\t\t\tthreshhold = 0.7, use_gray = True, show_value = False,\n\t\t\tshow_position = True)\n\tpress(Button.A, duration = 0.1, wait = 0.1)\n\t# test.pngが画面に表示された場合、Aボタンを0.1秒押し、0.1秒待機\ntemplate_path:探す画像のパス ,  \u3000 threshold:しきい値(0.7以上推奨)\nshow_value:一致度の表示(1に近いほど一致度が高い)\nuse_gray:グレースケール(白黒判定) ,  show_position:認識時の枠表示\n\n')
		label31.place(relheight=0.425,relwidth=0.95,relx=0.025,rely=0.175,x=0,y=0)
		label32 = ttk.Label(frame7)
		label32.configure(anchor="center",background="#00ffff",text='template_path')
		label32.place(relheight=0.1,relwidth=0.225,relx=0.025,rely=0.62,x=0,y=0)
		self.Template_Label = ttk.Label(frame7)
		self.Template_Label.configure(anchor="center",background="#00ffff",text='選択した画像名:')
		self.Template_Label.place(relheight=0.1,relwidth=0.525,relx=0.45,rely=0.62,x=0,y=0)
		label34 = ttk.Label(frame7)
		label34.configure(anchor="center",background="#00ffff",text='threshold')
		label34.place(relheight=0.1,relwidth=0.1,relx=0.025,rely=0.75,x=0,y=0)
		label64 = ttk.Label(frame7)
		label64.configure(anchor="center",background="#00ffff",text='use_gray')
		label64.place(relheight=0.1,relwidth=0.1,relx=0.245,rely=0.75,x=0,y=0)
		label65 = ttk.Label(frame7)
		label65.configure(anchor="center",background="#00ffff",text='show_value')
		label65.place(relheight=0.1,relwidth=0.125,relx=0.465,rely=0.75,x=0,y=0)
		label66 = ttk.Label(frame7)
		label66.configure(anchor="center",background="#00ffff",text='show_position')
		label66.place(relheight=0.1,relwidth=0.135,relx=0.705,rely=0.75,x=0,y=0)


		Template_Reference_Button = ttk.Button(frame7)
		self.Template_Path_Entry_value = ""
		Template_Reference_Button.configure(text="参照",command=self.Template_PNG_Get)
		Template_Reference_Button.place(anchor="nw", relwidth=0.2, relx=0.25, rely=0.63, x=0, y=0)

		

		Template_threshold_Entry = ttk.Entry(frame7)
		self.Template_Threshold_Entry_Value = tk.StringVar()
		Template_threshold_Entry.configure(justify="center",textvariable=self.Template_Threshold_Entry_Value)
		Template_threshold_Entry.delete("0", "end")
		Template_threshold_Entry.insert("0", '0.7')
		Template_threshold_Entry.place(anchor="nw", relwidth=0.1, relx=0.135, rely=0.76, x=0, y=0)

		

		Template_usegray_Entry = ttk.Entry(frame7)
		self.Template_usegray_Entry_Value = tk.StringVar()
		Template_usegray_Entry.configure(justify="center",textvariable=self.Template_usegray_Entry_Value)
		Template_usegray_Entry.delete("0", "end")
		Template_usegray_Entry.insert("0", 'True')
		Template_usegray_Entry.place(anchor="nw", relwidth=0.1, relx=0.355, rely=0.76, x=0, y=0)

		

		Template_showvalue_Entry = ttk.Entry(frame7)
		self.Template_showvalue_Entry_Value = tk.StringVar()
		Template_showvalue_Entry.configure(justify="center",textvariable=self.Template_showvalue_Entry_Value)
		Template_showvalue_Entry.delete("0", "end")
		Template_showvalue_Entry.insert("0", 'False')
		Template_showvalue_Entry.place(anchor="nw", relwidth=0.1, relx=0.595, rely=0.76, x=0, y=0)

		Template_showposition_Entry = ttk.Entry(frame7)
		self.Template_showposition_Entry_value = tk.StringVar()
		Template_showposition_Entry.configure(justify="center",textvariable=self.Template_showposition_Entry_value)
		Template_showposition_Entry.delete("0", "end")
		Template_showposition_Entry.insert("0", 'False')
		Template_showposition_Entry.place(anchor="nw", relwidth=0.125, relx=0.845, rely=0.76, x=0, y=0)

		Template_OK_Button = ttk.Button(frame7)
		Template_OK_Button.configure(text='OK',command=self.Template_Entry_Get)
		Template_OK_Button.place(anchor="nw",relwidth=0.95,relx=0.025,rely=0.87,x=0,y=0)

		frame7.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = Template_Toplevel

	def Template_PNG_Get(self):
		current = os.path.abspath("./Template")
		self.Template_Path_Entry_value = filedialog.askopenfilename(
			filetypes=[("Image file", "*.png")], initialdir=current)
		path = current.replace("\\","/")
		self.Template_Path_Entry_value = self.Template_Path_Entry_value.replace(f"{path}/","")
		self.Template_Label.configure(text=f"選択した画像：{self.Template_Path_Entry_value}")
		self.master.mainwindow.focus_force()
		self.focus_force()
		
		 

	def Template_Entry_Get(self):
		self.Template_Threshold_Entry_Value = self.Template_Threshold_Entry_Value.get()
		self.Template_usegray_Entry_Value = self.Template_usegray_Entry_Value.get()
		self.Template_showvalue_Entry_Value = self.Template_showvalue_Entry_Value.get()
		self.Template_showposition_Entry_value = self.Template_showposition_Entry_value.get()
		txt = (
			f'if self.isContainTemplate(\n		template_path = "{self.Template_Path_Entry_value}",\n		threshold = {self.Template_Threshold_Entry_Value},\n		use_gray = {self.Template_usegray_Entry_Value},\n 		show_value = {self.Template_showvalue_Entry_Value},\n 		show_position = {self.Template_showposition_Entry_value}):\n			')
		if self.Template_Path_Entry_value is "":
			self._logger.debug("Close Template Dialog")
			self.mainwindow.destroy()
			self.master.TemplateD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close Template Dialog")
			self.mainwindow.destroy()
			self.master.TemplateD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close Template Dialog")
		self.mainwindow.destroy()
		self.master.TemplateD = None

	def run(self):
		self.mainwindow.mainloop()

class TemplatePositonDialog:

	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		Template_Position_Toplevel = tk.Toplevel(self.root)
		Template_Position_Toplevel.configure(height=400, width=600)
		Template_Position_Toplevel.geometry("600x400")
		Template_Position_Toplevel.maxsize(600, 400)
		Template_Position_Toplevel.minsize(600, 400)
		Template_Position_Toplevel.title("PCE_Template_Path")
		frame8 = ttk.Frame(Template_Position_Toplevel)
		frame8.configure(height=400, width=600)
		
		label40 = ttk.Label(frame8)
		label40.configure(anchor="center",background="#00ffff",text='isContainTemplate')
		label40.place(relheight=0.075,relwidth=0.2,relx=0.025,rely=0.05,x=0,y=0)
		label41 = ttk.Label(frame8)
		label41.configure(anchor="center", text='画像認識\u3000(座標指定あり)')
		label41.place(relheight=0.075,relwidth=0.725,relx=0.225,rely=0.05,x=0,y=0)
		label42 = ttk.Label(frame8)
		label42.configure(anchor="center",takefocus=True,text='例:\u3000if isContainTemplate(template_path="test.png", \n\t\t\tthreshhold = 0.7, use_gray = True, \n\t\t\tshow_position = True, crop[100, 150, 200, 300])\n\tpress(Button.A, duration = 0.1, wait = 0.1)\n\t# test.pngがx100-x150,y200-y300に表示された場合、Aボタンを0.1秒押し、0.1秒待機\n\ntemplate_path:探す画像のパス ,  \u3000 threshold:しきい値(0.7以上推奨)\nshow_value:一致度の表示(1に近いほど一致度が高い)\nuse_gray:グレースケール(白黒判定) ,  show_position:認識時の枠表示\ncrop: [x1,x2,y1,y2]の4点の中に表示された画像だけを認識します。')
		label42.place(relheight=0.4,relwidth=0.95,relx=0.025,rely=0.15,x=0,y=0)
		label43 = ttk.Label(frame8)
		label43.configure(anchor="center",background="#00ffff",text='template_path')
		label43.place(relheight=0.075,relwidth=0.225,relx=0.025,rely=0.57,x=0,y=0)
		self.Template_Position_Label = ttk.Label(frame8)
		self.Template_Position_Label.configure(anchor="center", background="#00ffff", text='選択した画像名:')
		self.Template_Position_Label.place(relheight=0.075, relwidth=0.525,relx=0.45, rely=0.57, x=0, y=0)
		label48 = ttk.Label(frame8)
		label48.configure(anchor="center",background="#00ffff",text='x1')
		label48.place(relheight=0.075,relwidth=0.1,relx=0.025,rely=0.8,x=0,y=0)
		label49 = ttk.Label(frame8)
		label49.configure(anchor="center",background="#00ffff",text='x2')
		label49.place(relheight=0.075,relwidth=0.1,relx=0.245,rely=0.8,x=0,y=0)
		label50 = ttk.Label(frame8)
		label50.configure(anchor="center",background="#00ffff",text='y1')
		label50.place(relheight=0.075,relwidth=0.1,relx=0.475,rely=0.8,x=0,y=0)
		label51 = ttk.Label(frame8)
		label51.configure(anchor="center",background="#00ffff",text='y2')
		label51.place(relheight=0.075,relwidth=0.1,relx=0.725,rely=0.8,x=0,y=0)
		label1 = ttk.Label(frame8)
		label1.configure(anchor="center",background="#00ffff",text='threshold')
		label1.place(relheight=0.075,relwidth=0.1,relx=0.025,rely=0.69,x=0,y=0)
		label3 = ttk.Label(frame8)
		label3.configure(anchor="center",background="#00ffff",text='show_value')
		label3.place(relheight=0.075,relwidth=0.125,relx=0.465,rely=0.69,x=0,y=0)
		label4 = ttk.Label(frame8)
		label4.configure(anchor="center",background="#00ffff",text='show_position')
		label4.place(relheight=0.075,relwidth=0.135,relx=0.705,rely=0.69,x=0,y=0)




		label2 = ttk.Label(frame8)
		label2.configure(anchor="center",background="#00ffff",text='use_gray')
		label2.place(relheight=0.075,relwidth=0.1,relx=0.245,rely=0.69,x=0,y=0)


		button15 = ttk.Button(frame8)
		self.Template_Path_Entry_value = ""
		button15.configure(text='参照', command=self.Template_PNG_Get)
		button15.place(anchor="nw",relwidth=0.2,relx=0.25,rely=0.575,x=0,y=0)



		X1_Entry = ttk.Entry(frame8)
		self.X1 = tk.StringVar()
		X1_Entry.configure(justify="center",textvariable=self.X1)
		X1_Entry.delete("0", "end")
		X1_Entry.insert(tk.END,"")
		X1_Entry.place(anchor="nw",relwidth=0.1,relx=0.135,rely=0.81,x=0,y=0)

		

		X2_Entry = ttk.Entry(frame8)
		self.X2 = tk.StringVar()
		X2_Entry.configure(justify="center", textvariable=self.X2)
		X2_Entry.delete("0", "end")
		X2_Entry.insert(tk.END, "")
		X2_Entry.place(anchor="nw",relwidth=0.1,relx=0.355,rely=0.81,x=0,y=0)
		
		Y1_Entry = ttk.Entry(frame8)
		self.Y1 = tk.StringVar()
		Y1_Entry.configure(justify="center", textvariable=self.Y1)
		Y1_Entry.delete("0", "end")
		Y1_Entry.insert(tk.END, "")
		Y1_Entry.place(anchor="nw",relwidth=0.1,relx=0.595,rely=0.81,x=0,y=0)
		
		Y2_Entry = ttk.Entry(frame8)
		self.Y2 = tk.StringVar()
		Y2_Entry.configure(justify="center", textvariable=self.Y2)
		Y2_Entry.delete("0", "end")
		Y2_Entry.insert(tk.END, "")
		Y2_Entry.place(anchor="nw",relwidth=0.1,relx=0.845,rely=0.81,x=0,y=0)
		
		Template_Position_threshold_Entry = ttk.Entry(frame8)
		self.Template_Position_Threshold_Entry_Value = tk.StringVar()
		Template_Position_threshold_Entry.configure(justify="center", textvariable=self.Template_Position_Threshold_Entry_Value)
		Template_Position_threshold_Entry.delete("0", "end")
		Template_Position_threshold_Entry.insert("0", '0.7')
		Template_Position_threshold_Entry.place(anchor="nw", relwidth=0.1, relx=0.135, rely=0.7, x=0, y=0)
		
		

		Template_Position_usegray_Entry = ttk.Entry(frame8)
		self.Template_Position_usegray_Entry_Value = tk.StringVar()
		Template_Position_usegray_Entry.configure(justify="center", textvariable=self.Template_Position_usegray_Entry_Value)
		Template_Position_usegray_Entry.delete("0", "end")
		Template_Position_usegray_Entry.insert("0", 'True')
		Template_Position_usegray_Entry.place(anchor="nw", relwidth=0.1, relx=0.355, rely=0.7, x=0, y=0)
		
		

		Template_Position_showvalue_Entry = ttk.Entry(frame8)
		self.Template_Position_showvalue_Entry_Value = tk.StringVar()
		Template_Position_showvalue_Entry.configure(justify="center", textvariable=self.Template_Position_showvalue_Entry_Value)
		Template_Position_showvalue_Entry.delete("0", "end")
		Template_Position_showvalue_Entry.insert("0", 'False')
		Template_Position_showvalue_Entry.place(anchor="nw", relwidth=0.1, relx=0.595, rely=0.7, x=0, y=0)

		Template_Position_showposition_Entry = ttk.Entry(frame8)
		self.Template_Position_showposition_Entry_value = tk.StringVar()
		Template_Position_showposition_Entry.configure(
			justify="center", textvariable=self.Template_Position_showposition_Entry_value)
		Template_Position_showposition_Entry.delete("0", "end")
		Template_Position_showposition_Entry.insert("0", 'False')
		Template_Position_showposition_Entry.place(anchor="nw", relwidth=0.125, relx=0.845, rely=0.7, x=0, y=0)
		


		button14 = ttk.Button(frame8)
		button14.configure(text='OK',command = self.Template_Position_Entry_Get)
		button14.place(anchor="nw", relwidth=0.95,relx=0.025, rely=0.92, x=0, y=0)

		frame8.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = Template_Position_Toplevel

	def Template_PNG_Get(self):
		current = os.path.abspath("./Template")
		self.Template_Path_Entry_value = filedialog.askopenfilename(filetypes=[("Image file", "*.png")], initialdir=current)
		path = current.replace("\\", "/")
		self.Template_Path_Entry_value = self.Template_Path_Entry_value.replace(f"{path}/", "")
		self.Template_Position_Label.configure(
			text=f"選択した画像：{self.Template_Path_Entry_value}")
		self.focus_force()
		self.master.mainwindow.focus_force()

	def Template_Position_Entry_Get(self):
		self.Template_Position_Threshold_Entry_Value = self.Template_Position_Threshold_Entry_Value.get()
		self.Template_Position_usegray_Entry_Value = self.Template_Position_usegray_Entry_Value.get()
		self.Template_Position_showvalue_Entry_Value = self.Template_Position_showvalue_Entry_Value.get()
		self.Template_Position_showposition_Entry_value = self.Template_Position_showposition_Entry_value.get()
		X1 = self.X1.get()
		X2 = self.X2.get()
		Y1 = self.Y1.get()
		Y2 = self.Y2.get()
		self.master.TAB_Get()
		txt = (
			f'if self.isContainTemplate(template_path = "{self.Template_Path_Entry_value}",threshold = {self.Template_Position_Threshold_Entry_Value},\n		{self.master.tab_val}use_gray = {self.Template_Position_usegray_Entry_Value},show_value = {self.Template_Position_showvalue_Entry_Value},\n 		{self.master.tab_val}show_position = {self.Template_Position_showposition_Entry_value},crop = [{X1},{X2},{Y1},{Y2}]):\n	{self.master.tab_val}')
		if self.Template_Path_Entry_value is "":
			self._logger.debug("Close Template Dialog")
			self.mainwindow.destroy()
			self.master.TemplatePD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close Template Dialog")
			self.mainwindow.destroy()
			self.master.TemplatePD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close Template Dialog")
		self.mainwindow.destroy()
		self.master.TemplatePD = None

	def run(self):
		self.mainwindow.mainloop()

class LINEtextDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		LINEtext_Toplevel = tk.Toplevel(self.root)
		LINEtext_Toplevel.configure(height=200, width=400)
		LINEtext_Toplevel.geometry("400x200")
		LINEtext_Toplevel.maxsize(400, 200)
		LINEtext_Toplevel.minsize(400, 200)
		LINEtext_Toplevel.title("PCE_LINEtext")
		frame9 = ttk.Frame(LINEtext_Toplevel)
		frame9.configure(height=200, width=400)
		label52 = ttk.Label(frame9)
		label52.configure(anchor="center",background="#00ffff",text='LINE_text')
		label52.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.05,x=0,y=0)
		label53 = ttk.Label(frame9)
		label53.configure(anchor="center", text='LINEメッセージ通知')
		label53.place(relwidth=0.7, relx=0.25, rely=0.05, x=0, y=0)  
		label54 = ttk.Label(frame9)
		label54.configure(anchor="center",background="#00ffff",text='LINE_text')
		label54.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)
		label55 = ttk.Label(frame9)
		label55.configure(anchor="center",text='例:self.LINE_text("通知したい内容")')
		label55.place(relheight=0.3,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label56 = ttk.Label(frame9)
		label56.configure(anchor="center",foreground="#ff0000",text='注意:Tokenを取得して、line_token.iniに追加してください。')
		label56.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.55,x=0,y=0)

		entry18 = ttk.Entry(frame9)
		self.LINEtext_Entry_Value = tk.StringVar()
		entry18.configure(justify="center",textvariable=self.LINEtext_Entry_Value)
		entry18.delete("0", "end")
		entry18.insert(tk.END, "")
		entry18.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)

		button16 = ttk.Button(frame9)
		button16.configure(text='OK',command=self.LINEtext_Entry_Get)
		button16.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.82,x=0,y=0)
		frame9.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = LINEtext_Toplevel


	def LINEtext_Entry_Get(self):
		self.LINEtext_Entry_Value = self.LINEtext_Entry_Value.get()
		txt = (f'self.LINE_text("{self.LINEtext_Entry_Value}")\n			')
		if self.LINEtext_Entry_Value is "":
			self._logger.debug("Close LINEimage Dialog")
			self.mainwindow.destroy()
			self.master.LINEtextD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close LINEimage Dialog")
			self.mainwindow.destroy()
			self.master.LINEtextD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close LINEimage Dialog")
		self.mainwindow.destroy()
		self.master.LINEtextD = None

	def run(self):
		self.mainwindow.mainloop()

class LINEimageDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		LINE_image_Toplevel = tk.Toplevel(self.root)
		LINE_image_Toplevel.configure(height=200, width=400)
		LINE_image_Toplevel.geometry("400x200")
		LINE_image_Toplevel.maxsize(400, 200)
		LINE_image_Toplevel.minsize(400, 200)
		LINE_image_Toplevel.title("PCE_LINEimage")
		label57 = ttk.Label(LINE_image_Toplevel)
		label57.configure(anchor="center",background="#00ffff",text='LINE_image')
		label57.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.05,x=0,y=0)
		label59 = ttk.Label(LINE_image_Toplevel)
		label59.configure(anchor="center", text='LINEメッセージ+画像通知')
		label59.place(relwidth=0.7, relx=0.25, rely=0.05, x=0, y=0)
		label61 = ttk.Label(LINE_image_Toplevel)
		label61.configure(anchor="center",text='例:self.LINE_image("通知したい内容")')
		label61.place(relheight=0.3,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label62 = ttk.Label(LINE_image_Toplevel)
		label62.configure(anchor="center",foreground="#ff0000",text='注意:Tokenを取得して、line_token.iniに追加してください。')
		label62.place(relheight=0.1,relwidth=0.9,relx=0.05,rely=0.55,x=0,y=0)
		label63 = ttk.Label(LINE_image_Toplevel)
		label63.configure(anchor="center",background="#00ffff",text='LINE_image')
		label63.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)

		entry19 = ttk.Entry(LINE_image_Toplevel)
		self.LINEimage_Entry_Value = tk.StringVar()
		entry19.configure(justify="center", textvariable=self.LINEimage_Entry_Value)
		entry19.delete("0", "end")
		entry19.insert(tk.END, "")
		entry19.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)

		button17 = ttk.Button(LINE_image_Toplevel)
		button17.configure(text='OK',command=self.LINEimage_Entry_Get)
		button17.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.82,x=0,y=0)

		# Main widget
		self.mainwindow = LINE_image_Toplevel

	def LINEimage_Entry_Get(self):
		self.LINEimage_Entry_Value = self.LINEimage_Entry_Value.get()
		txt = (f'self.LINE_image("{self.LINEimage_Entry_Value}")\n			')
		if self.LINEimage_Entry_Value is "":
			self._logger.debug("Close LINEimage Dialog")
			self.mainwindow.destroy()
			self.master.LINEimageD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close LINEimage Dialog")
			self.mainwindow.destroy()
			self.master.LINEimageD = None
		

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close LINEimage Dialog")
		self.mainwindow.destroy()
		self.master.LINEimageD = None

	def run(self):
		self.mainwindow.mainloop()


class CommentDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		Comment_Toplevel = tk.Toplevel(self.root)
		Comment_Toplevel.configure(height=200, width=200)
		Comment_Toplevel.geometry("400x200")
		Comment_Toplevel.maxsize(400, 200)
		Comment_Toplevel.minsize(400, 200)
		Comment_Toplevel.title("PCE_Comment")
		frame1 = ttk.Frame(Comment_Toplevel)
		frame1.configure(height=200, width=400)
		label5 = ttk.Label(frame1)
		label5.configure(anchor="center", background="#00ffff", text='#')
		label5.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.05,x=0,y=0)
		label6 = ttk.Label(frame1)
		label6.configure(anchor="center", text='コメントアウト追加')
		label6.place(relwidth=0.7, relx=0.25, rely=0.05, x=0, y=0)
		label7 = ttk.Label(frame1)
		label7.configure(anchor="center",text='例：self.press(Button.A)\n        # Aボタン\n        self.press(Button.B)\n        # コメントアウトは実行されません')
		label7.place(relheight=0.3,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label8 = ttk.Label(frame1)
		label8.configure(anchor="center", background="#00ffff", text='#')
		label8.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)

		Comment_Entry = ttk.Entry(frame1)
		self.Comment_Entry_Value = tk.StringVar()
		Comment_Entry.configure(justify="center",textvariable=self.Comment_Entry_Value)
		Comment_Entry.delete("0", "end")
		Comment_Entry.insert(tk.END, "")
		Comment_Entry.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)

		button1 = ttk.Button(frame1)
		button1.configure(text='OK',command=self.Comment_Entry_Get)
		button1.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.82,x=0,y=0)

		frame1.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = Comment_Toplevel

	def Tab_val(self,tab):
		self.tab_value = tab

	def Comment_Entry_Get(self):
		self.Comment_Entry_Value = self.Comment_Entry_Value.get()
		txt = (f'# {self.Comment_Entry_Value}\n		')
		if self.Comment_Entry_Value is "":
			self._logger.debug("Close Comment Dialog")
			self.mainwindow.destroy()
			self.master.CommentD = None
		else:
			self.text.insert(tk.INSERT, txt)
			self._logger.debug("Close Comment Dialog")
			self.mainwindow.destroy()
			self.master.CommentD = None


	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close Comment Dialog")
		self.mainwindow.destroy()
		self.master.CommentD = None

	def run(self):
		self.mainwindow.mainloop()

class NewFileDialog:
	def __init__(self, master, **kw):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.master = master
		self.root = self.master.root
		self.text = self.master.Text_Edit_BOX
		self.Body = self.master.Body
		NewFile_Toplevel = tk.Toplevel(self.root)
		NewFile_Toplevel.configure(height=200, relief="raised", width=400)
		NewFile_Toplevel.geometry("400x200")
		NewFile_Toplevel.maxsize(400, 200)
		NewFile_Toplevel.minsize(400, 200)
		NewFile_Toplevel.title("PCE_NewFile")
		frame3 = ttk.Frame(NewFile_Toplevel)
		frame3.configure(height=200, width=400)
		label35 = ttk.Label(frame3)
		label35.configure(anchor="center",background="#00ffff",text='NewFile')
		label35.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.05,x=0,y=0)
		label36 = ttk.Label(frame3)
		label36.configure(anchor="center", text='新規作成')
		label36.place(relwidth=0.7, relx=0.25, rely=0.05, x=0, y=0)
		label37 = ttk.Label(frame3)
		label37.configure(anchor="center",text='class名を決めてください。\n\nファイルは保存しなければ消えてしまいます。')
		label37.place(relheight=0.3,relwidth=0.9,relx=0.05,rely=0.2,x=0,y=0)
		label38 = ttk.Label(frame3)
		label38.configure(anchor="center",background="#00ffff",text='class')
		label38.place(relheight=0.1,relwidth=0.2,relx=0.05,rely=0.7,x=0,y=0)

		NewFile_Entry = ttk.Entry(frame3)
		self.NewFile_Entry_Value  = tk.StringVar()
		NewFile_Entry.configure(justify="center", textvariable=self.NewFile_Entry_Value)
		NewFile_Entry.delete("0", "end")
		NewFile_Entry.insert(tk.END, "")
		NewFile_Entry.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.7,x=0,y=0)

		button5 = ttk.Button(frame3)
		button5.configure(text='OK',command=self.NeWFile_Entry_Get)
		button5.place(anchor="nw",relwidth=0.7,relx=0.25,rely=0.82,x=0,y=0)
		frame3.place(anchor="nw", x=0, y=0)

		# Main widget
		self.mainwindow = NewFile_Toplevel

	def run(self):
		self.mainwindow.mainloop()

	def NeWFile_Entry_Get(self):
		NewFile_Entry_Value = self.NewFile_Entry_Value.get()
		a = '# !/usr/bin/env python3\n# -*- coding: utf-8 -*-\nfrom Commands.PythonCommandBase import PythonCommand, ImageProcPythonCommand\nfrom Commands.Keys import KeyPress, Button, Hat, Direction, Stick'
		b = f'\n\nclass {NewFile_Entry_Value}(ImageProcPythonCommand): \n	NAME="{NewFile_Entry_Value}"\n\n	def __init__(self, cam): \n		super().__init__(cam)'
		c = f'\n\n	def do(self): \n		\n		# ここに追加してください\n\n		pass'
		txt = (f"{a}{b}{c}")
		if NewFile_Entry_Value is "":
			self._logger.debug("Close NewFile Dialog")
			self.mainwindow.destroy()
			self.master.NewFileD = None
		else:
			self.text.delete("1.0", "end")
			self.text.insert(tk.END, txt)
			self.master.new_insert()
			self.Body.title(f'Pokecon_Code_Editor:{NewFile_Entry_Value}.py')
			self._logger.debug("Close NewFile Dialog")
			self.mainwindow.destroy()
			self.master.NewFileD = None

	def bind(self, event, func):
		self.mainwindow.bind(event, func)

	def protocol(self, event, func):
		self.mainwindow.protocol(event, func)

	def focus_force(self):
		self.mainwindow.focus_force()

	def destroy(self):
		self._logger.debug("Close NewFile Dialog")
		self.mainwindow.destroy()
		self.master.NewFileD = None

