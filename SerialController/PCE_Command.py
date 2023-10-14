from logging import getLogger, DEBUG, NullHandler
from tkinter import filedialog
import os


class PCE_Command:
	def __init__(self,master, Button, Duration, Wait,tab):
		self._logger = getLogger(__name__)
		self._logger.addHandler(NullHandler())
		self._logger.setLevel(DEBUG)
		self.Button = Button
		self.Duration = Duration
		self.Wait = Wait
		self.master = master
		self.tab = tab 
		# self.Text = self.master.Text_Box


	def pressButton(Button,duration,wait,tab):
		txt = f"self.press({Button},duration = {duration}, wait = {wait})\n{tab}"
		return txt
	
	def New_SaveFile(txt, title):
		Text_File_Save = filedialog.asksaveasfilename(
			title="名前を付けて保存",  initialfile=title, initialdir="./Commands/PythonCommands", defaultextension=".py", filetypes=[("Python", ".py"), ("Text", ".txt")])
		if Text_File_Save is "":
			return
		else:
			with open(Text_File_Save, mode='w', encoding='utf-8')as f:
				# Text_Edit_value = txt.get("1.0", "end-1c")
				f.write(txt)
				f.close
			path = os.path.dirname(Text_File_Save)
			title = os.path.basename(Text_File_Save)
			return title,path
			

	def SaveFile(txt,title,path):
		path_title = f"{path}/{title}"
		with open(path_title, mode="w", encoding='utf-8')as f:
			f.write(txt)
			f.close
		title = os.path.basename(path_title)
		path = os.path.dirname(path_title)
		return title,path


	def OpenFile(self):
		File_path = filedialog.askopenfilename(
			filetypes=[("Python File", "*.py")], initialdir="./Commands/PythonCommands")
		if File_path is '':
			return
		else:
			with open(File_path, "r", encoding='utf-8')as f:
				txt = f.read()
				f.close()
			title = os.path.basename(File_path)
			path = os.path.dirname(File_path)
			return txt ,title, path
