#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import ImageProcPythonCommand
from Commands.Keys import Button, Hat, Direction, Stick
import os
import cv2
import tkinter as tk
from tkinter import ttk
import numpy as np

class SV_Raid(ImageProcPythonCommand):

    NAME = 'SVレイド'

    def __init__(self, cam):
        super().__init__(cam)
        self._threshold = 0.9
        self.ver = "0.6.5"
        self.select_flag = False

        self.language = ""
        self.rom = ""
        self.star = 0
        self.event = False
        self.flag = False
        self.get_flag = False
        self.terastal_flag = False
        self.count = 1
        # self.use_poke = {"normal.png":"fighting.png","fire.png":"water.png","water.png":"electric.png","grass.png":"fire.png",
        #                 "electric.png":"ground.png","ice.png":"fighting.png","fighting.png":"fairy.png","poison.png":"ground.png",
        #                 "ground.png":"water.png","flying.png":"electric.png","psychic.png":"dark.png","bug.png":"fire.png",
        #                 "rock.png":"fighting.png","ghost.png":"dark.png","dragon.png":"fairy.png","dark.png":"fighting.png",
        #                 "steel.png":"fighting.png","fairy.png":"poison.png"}

        self.use_gui_poke = {}

        self.type = ["normal.png","fire.png","water.png","grass.png","electric.png","ice.png","fighting.png","poison.png",
                    "ground.png","flying.png","psychic.png","bug.png","rock.png","ghost.png","dragon.png","dark.png",
                    "steel.png","fairy.png"]

        self.ball_img = ["ball_01.png","ball_02.png","ball_03.png","ball_04.png","ball_05.png","ball_06.png",
                        "ball_07.png","ball_08.png","ball_09.png","ball_10.png","ball_11.png","ball_12.png","ball_13.png"]
        self.ball = ""
        self.type_jp = {"bug.png":"むし","dark.png":"あく","dragon.png":"ドラゴン","electric.png":"でんき","fairy.png":"フェアリー","fighting.png":"かくとう",
                        "fire.png":"ほのお","flying.png":"ひこう","ghost.png":"ゴースト","grass.png":"くさ","ground.png":"じめん","ice.png":"こおり",
                        "normal.png":"ノーマル","poison.png":"どく","psychic.png":"エスパー","rock.png":"いわ","steel.png":"はがね","water.png":"みず"}
        self.type_eng = {}
        for key, value in self.type_jp.items():
            self.type_eng[value] = key
        self.ball_jp = {"ball_01.png":"モンスターボール","ball_02.png":"スーパーボール","ball_03.png":"ハイパーボール",
                        "ball_04.png":"プレミアボール","ball_05.png":"ヒールボール","ball_06.png":"ネットボール",
                        "ball_07.png":"ネストボール","ball_08.png":"ダイブボール","ball_09.png":"ダークボール",
                        "ball_10.png":"タイマーボール","ball_11.png":"クイックボール","ball_12.png":"リピートボール","ball_13.png":"ゴージャスボール"}

        self.win_lose = False
        self.win = 0
        self.lose = 0
        self.log = ""
        self.win_lose_log = {"ノーマル":0,"ほのお":0,"みず":0,"くさ":0,"でんき":0,"こおり":0,"かくとう":0,"どく":0,"じめん":0,"ひこう":0,
                            "エスパー":0,"むし":0,"いわ":0,"ゴースト":0,"ドラゴン":0,"あく":0,"はがね":0,"フェアリー":0}

    def do(self):
        self.wait(1)

        print("--------------------------------------------")
        print(f"ポケモンSV自動レイドプログラムVer.{self.ver}")
        print("--------------------------------------------")
        print("自動レイドを起動します")
        print("")

        print("GUIモードで起動します")
        print("")
        self.window()
        while not self.select_flag:
            self.wait(0.5)
        print(f"使用するボールは {self.ball_jp[self.ball]} です")
        print("")
        log = []

        for i in self.win_lose_log:
            log.append(f"{i}\t\t=>\t{self.type_jp[self.use_gui_poke[self.type_eng[i]]]}")
        print("使用するポケモンは")
        print('\n'.join(log))

        while True:
            self.wait(1)
            self.get_flag = False
            if self.isContainTemplate(f"SV/Raid/mark.png", 0.8):
                print("--------------------------------------------")
                self.press(Button.A,0.1,2.5)
                if self.isContainTemplate(f"SV/Raid/{self.language}/cant_get.png", 0.8):
                    self.press(Button.A,0.1,1)
                if self.isContainTemplate(f"SV/Raid/{self.rom}/raid_battle_{self.language}.png", 0.8) and self.flag == False:
                    print("結晶が見つかりました！")
                    if self.isContainTemplate(f"SV/Raid/{self.rom}/event.png", 0.8, use_gray=False):
                        folder = "event_type"
                        self.event = True
                        if self.event_get.get():
                            self.get_flag = True
                    else:
                        folder = "type"
                        self.event = False
                    self.win_lose = False
                    if self.event:
                        if self.isContainTemplate(f"SV/Raid/{self.rom}/event/star_7.png",0.85):
                            self.star = 7
                            if self.skip_7.get():
                                print(f"星{self.star}レイドをスキップします")
                                self.flag = True
                                self.time()
                                continue
                            else:
                                self.win_lose = True
                        elif self.isContainTemplate(f"SV/Raid/{self.rom}/event/star_3.png",0.85):
                            self.star = 3
                            if self.star_3.get():
                                self.get_flag = True
                        elif self.isContainTemplate(f"SV/Raid/{self.rom}/event/star_4.png",0.85):
                            self.star = 4
                            if self.star_4.get():
                                self.get_flag = True
                        elif self.isContainTemplate(f"SV/Raid/{self.rom}/event/star_5.png",0.85):
                            self.star = 5
                            self.win_lose = True
                            if self.star_5.get():
                                self.get_flag = True
                        else:
                            print("星の認識がうまくいきませんでした")
                            self.get_flag = False
                            self.star = "エラー"
                    else:
                        if self.isContainTemplate(f"SV/Raid/{self.rom}/star/star_3.png",0.85):
                            self.star = 3
                            if self.star_3.get():
                                self.get_flag = True
                        elif self.isContainTemplate(f"SV/Raid/{self.rom}/star/star_4.png",0.85):
                            self.star = 4
                            if self.star_4.get():
                                self.get_flag = True
                        elif self.isContainTemplate(f"SV/Raid/{self.rom}/star/star_5.png",0.85):
                            self.star = 5
                            self.win_lose = True
                            if self.star_5.get():
                                self.get_flag = True
                        elif self.isContainTemplate(f"SV/Raid/{self.rom}/star_6.png", 0.85):
                            self.star = 6
                            if self.ditto.get():
                                if self.isContainTemplate(f"SV/Raid/ditto.png", 0.8):
                                    print("星６メタモンが出現しました！")
                                    print("プログラムを終了します")
                                    self.finish()
                            if self.skip_6.get():
                                print(f"星{self.star}レイドをスキップします")
                                self.flag = True
                                self.time()
                                continue
                            else:
                                self.win_lose = True
                                if self.star_6.get():
                                    self.get_flag = True
                        else:
                            print("星の認識がうまくいきませんでした")
                            self.get_flag = False
                            self.star = "エラー"
                    for i in self.type:
                        if self.isContainTemplate(f"SV/Raid/type/{folder}/{i}",0.9):
                            print(f"レイドバトル {self.count} 匹目")
                            if self.event:
                                print(f"星{self.star}イベントレイド テラスタルタイプ：{self.type_jp[i]}")
                            else:
                                print(f"星{self.star}レイド テラスタルタイプ：{self.type_jp[i]}")
                            print(f"星5~7バトルの勝敗数 勝ち：{self.win}回 負け：{self.lose}")
                            for log in self.win_lose_log:
                                if self.win_lose_log[log] != 0:
                                    print(f"{log} タイプに {self.type_jp[self.use_gui_poke[self.type_eng[log]]]} タイプで {self.win_lose_log[log]} 回負けています")
                            self.select_poke(i)
                            while not self.isContainTemplate(f"SV/Raid/{self.rom}/raid_battle_{self.language}.png", 0.8):
                                self.wait(0.5)
                            self.battle(i)
                            break
                elif self.flag == True:
                    print("ポケモンを変更します")
                    self.time()
                    self.flag = False
                else:
                    print("結晶が見つかりませんでした")
                    self.time()

    def select_poke(self,type):
        self.press(Hat.BTM, 0.1, 0.5)
        self.press(Hat.BTM, 0.1, 0.5)
        self.press(Button.A,0.1,0.5)
        while not self.isContainTemplate(f"SV/Raid/{self.language}/box.png", 0.8):
            self.wait(1)
        print(f"{self.type_jp[self.use_gui_poke[type]]}タイプのポケモンで戦います")
        while not self.isContainTemplate(f"SV/Raid/poke_type/{self.language}/{self.use_gui_poke[type]}", 0.8):
            self.press(Hat.RIGHT, 0.1, 0.3)
        self.press(Button.A,0.1,0.7)
        self.press(Button.A,0.1,1)
        while not self.isContainTemplate(f"SV/Raid/{self.rom}/raid_battle_{self.language}.png", 0.8):
            self.wait(0.5)
        self.wait(1)
        self.press(Hat.TOP, 0.1, 0.5)
        self.press(Button.A,0.1,0.5)
        self.press(Button.A,0.1,1)

    def battle(self,type):
        print("対戦開始")
        self.battle_flag = False
        self.turn_count = 1
        while True:
            if self.isContainTemplate("SV/Raid/fight.png", 0.8):
                self.wait(0.5)
                self.press(Button.A,0.1,0.5)
                self.battle_flag = True
            if self.isContainTemplate(f"SV/Raid/{self.language}/waza_select.png", 0.8):
                self.wait(0.5)
                if self.isContainTemplate(f"SV/Raid/waza_type/{self.use_gui_poke[type]}", 0.8, use_gray=False):
                    if not self.terastal_flag:
                        if self.terastal():
                            self.terastal_flag = True
                            self.press(Button.R,0.1,1)
                    self.press(Button.A,0.1,1)
                    self.press(Button.A,0.1,1)
                    self.turn_count = self.turn_count +1
                else:
                    self.press(Hat.BTM, 0.1, 0.5)
            if self.isContainTemplate(f"SV/Raid/{self.language}/back.png", 0.8):
                self.press(Button.A,0.1,1)
            if self.isContainTemplate(f"SV/Raid/{self.language}/get.png", 0.8):
                print("レイドバトルに勝利しました！")
                self.count = self.count +1
                if self.win_lose:
                    self.win = self.win +1
                self.wait(0.5)
                if self.get_flag:
                    self.press(Button.A,0.1,1)
                    while True:
                        if self.isContainTemplate(f"SV/Raid/ball/{self.language}/{self.ball}", 0.9):
                            print(f"ポケモンを {self.ball_jp[self.ball]} で捕まえます")
                            self.press(Button.A,0.1,1)
                            self.press(Button.A,0.1,1)
                            break
                        else:
                            self.press(Hat.LEFT, 0.1,0.5)
                    while not self.isContainTemplate("SV/Raid/dex.png", 0.8):
                        self.press(Button.A,0.1,1)
                    self.wait(3)
                    self.press(Button.A,0.1,0.5)
                    while not self.isContainTemplate("SV/Raid/mark.png", 0.8):
                        self.press(Button.A,0.1,0.5)
                else:
                    print("ポケモンを捕まえません")
                    self.press(Hat.BTM, 0.1,0.5)
                    self.press(Button.A,0.1,1)
                    while not self.isContainTemplate(f"SV/Raid/{self.language}/next.png", 0.8):
                        self.wait(0.5)
                    self.press(Button.A,0.1,1)
                    while not self.isContainTemplate("SV/Raid/mark.png", 0.8):
                        self.press(Button.A,0.1,0.5)
                self.wait(3)
                self.flag = False
                self.terastal_flag = False
                self.win_lose = False
                break
            if self.battle_flag == True:
                if self.isContainTemplate("SV/Raid/mark.png", 0.85, use_gray=False):
                    print("負けてしまった…")
                    self.log = self.type_jp[type]
                    self.win_lose_log[self.log] = self.win_lose_log[self.log]+1
                    if self.win_lose:
                        self.lose = self.lose +1
                    self.flag = True
                    self.terastal_flag = False
                    break
            if self.star == 7:
                if self.isContainTemplate(f"SV/Raid/{self.language}/next.png", 0.8):
                    while not self.isContainTemplate("SV/Raid/mark.png", 0.8):
                        self.press(Button.A,0.1,0.5)
                        break

    def window(self):
        set_window = tk.Toplevel()
        set_window.geometry("360x560")
        set_window.title("使用ポケモン変更")
        box = ["ノーマル","ほのお","みず","くさ","でんき","こおり","かくとう","どく","じめん",
            "ひこう","エスパー","むし","いわ","ゴースト","ドラゴン","あく","はがね","フェアリー"]
        enemy_text = tk.Label(set_window,text="相手のタイプ")
        use_text = tk.Label(set_window,text="使うポケモン")
        enemy_text.grid(row=1, column=0)
        use_text.grid(row=1, column=1)

        use_rom_box = ["スカーレット","バイオレット"]
        use_rom_text = tk.Label(set_window,text="使用するROM")
        use_rom_text.grid(row=0,column=0,pady=2)
        use_rom  = ttk.Combobox(set_window,values=use_rom_box,width=14)
        use_rom.current(1)
        use_rom.grid(row=0,column=1,pady=2)

        normal_text = tk.Label(set_window,text="ノーマル")
        fire_text = tk.Label(set_window,text="ほのお")
        water_text = tk.Label(set_window,text="みず")
        grass_text = tk.Label(set_window,text="くさ")
        electric_text = tk.Label(set_window,text="でんき")
        ice_text = tk.Label(set_window,text="こおり")
        fighting_text = tk.Label(set_window,text="かくとう")
        poison_text = tk.Label(set_window,text="どく")
        ground_text = tk.Label(set_window,text="じめん")
        flying_text = tk.Label(set_window,text="ひこう")
        psychic_text = tk.Label(set_window,text="エスパー")
        bug_text = tk.Label(set_window,text="むし")
        rock_text = tk.Label(set_window,text="いわ")
        ghost_text = tk.Label(set_window,text="ゴースト")
        dragon_text = tk.Label(set_window,text="ドラゴン")
        dark_text = tk.Label(set_window,text="あく")
        steel_text = tk.Label(set_window,text="はがね")
        fairy_text = tk.Label(set_window,text="フェアリー")

        self.normal = ttk.Combobox(set_window,values=box,width=14)
        self.normal.current(6)
        self.fire = ttk.Combobox(set_window,values=box,width=14)
        self.fire.current(8)
        self.water = ttk.Combobox(set_window,values=box,width=14)
        self.water.current(4)
        self.grass = ttk.Combobox(set_window,values=box,width=14)
        self.grass.current(1)
        self.electric = ttk.Combobox(set_window,values=box,width=14)
        self.electric.current(8)
        self.ice = ttk.Combobox(set_window,values=box,width=14)
        self.ice.current(6)
        self.fighting = ttk.Combobox(set_window,values=box,width=14)
        self.fighting.current(17)
        self.poison = ttk.Combobox(set_window,values=box,width=14)
        self.poison.current(8)
        self.ground = ttk.Combobox(set_window,values=box,width=14)
        self.ground.current(2)
        self.flying = ttk.Combobox(set_window,values=box,width=14)
        self.flying.current(4)
        self.psychic = ttk.Combobox(set_window,values=box,width=14)
        self.psychic.current(15)
        self.bug = ttk.Combobox(set_window,values=box,width=14)
        self.bug.current(1)
        self.rock = ttk.Combobox(set_window,values=box,width=14)
        self.rock.current(6)
        self.ghost = ttk.Combobox(set_window,values=box,width=14)
        self.ghost.current(15)
        self.dragon = ttk.Combobox(set_window,values=box,width=14)
        self.dragon.current(17)
        self.dark = ttk.Combobox(set_window,values=box,width=14)
        self.dark.current(6)
        self.steel = ttk.Combobox(set_window,values=box,width=14)
        self.steel.current(6)
        self.fairy = ttk.Combobox(set_window,values=box,width=14)
        self.fairy.current(7)

        normal_text.grid(row=2, column=0,pady=2)
        self.normal.grid(row=2, column=1,pady=2)
        fire_text.grid(row=3, column=0,pady=2)
        self.fire.grid(row=3, column=1,pady=2)
        water_text.grid(row=4, column=0,pady=2)
        self.water.grid(row=4, column=1,pady=2)
        grass_text.grid(row=5, column=0,pady=2)
        self.grass.grid(row=5, column=1,pady=2)
        electric_text.grid(row=6, column=0,pady=2)
        self.electric.grid(row=6, column=1,pady=2)
        ice_text.grid(row=7, column=0,pady=2)
        self.ice.grid(row=7, column=1,pady=2)
        fighting_text.grid(row=8, column=0,pady=2)
        self.fighting.grid(row=8, column=1,pady=2)
        poison_text.grid(row=9, column=0,pady=2)
        self.poison.grid(row=9, column=1,pady=2)
        ground_text.grid(row=10, column=0,pady=2)
        self.ground.grid(row=10, column=1,pady=2)
        flying_text.grid(row=11, column=0,pady=2)
        self.flying.grid(row=11, column=1,pady=2)
        psychic_text.grid(row=12, column=0,pady=2)
        self.psychic.grid(row=12, column=1,pady=2)
        bug_text.grid(row=13, column=0,pady=2)
        self.bug.grid(row=13, column=1,pady=2)
        rock_text.grid(row=14, column=0,pady=2)
        self.rock.grid(row=14, column=1,pady=2)
        ghost_text.grid(row=15, column=0,pady=2)
        self.ghost.grid(row=15, column=1,pady=2)
        dragon_text.grid(row=16, column=0,pady=2)
        self.dragon.grid(row=16, column=1,pady=2)
        dark_text.grid(row=17, column=0,pady=2)
        self.dark.grid(row=17, column=1,pady=2)
        steel_text.grid(row=18, column=0,pady=2)
        self.steel.grid(row=18, column=1,pady=2)
        fairy_text.grid(row=19, column=0,pady=2)
        self.fairy.grid(row=19, column=1,pady=2)

        ball_text = tk.Label(set_window,text= "使うボール")
        ball_text.grid(row=20,column=0,pady=2)

        balls = ["モンスターボール","スーパーボール","ハイパーボール","プレミアボール","ヒールボール","ネットボール","ネストボール",
                "ダイブボール","ダークボール","タイマーボール","クイックボール","リピートボール","ゴージャスボール"]
        eng_balls = {}
        for key, value in self.ball_jp.items():
            eng_balls[value] = key
        ball_box = ttk.Combobox(set_window,values=balls,width=14)
        ball_box.current(0)
        ball_box.grid(row=20,column=1)

        language = ["日本語","英語"]
        lng_text = tk.Label(set_window,text="使用言語")
        lng_text.grid(row=0,column=3)
        lng_box = ttk.Combobox(set_window,values=language)
        lng_box.current(0)
        lng_box.grid(row=1,column=3)

        # self.star_1 = tk.BooleanVar()
        # star_1 = tk.Checkbutton(set_window,text="星1ポケモンを捕まえる",variable=self.star_1,offvalue=False,onvalue=True)
        # self.star_1.set(False)
        # star_1.grid(row=2,column=3)
        # self.star_2 = tk.BooleanVar()
        # star_2 = tk.Checkbutton(set_window,text="星2ポケモンを捕まえる",variable=self.star_2,offvalue=False,onvalue=True)
        # self.star_2.set(False)
        # star_2.grid(row=3,column=3)

        self.star_3 = tk.BooleanVar()
        star_3 = tk.Checkbutton(set_window,text="星3ポケモンを捕まえる",variable=self.star_3,offvalue=False,onvalue=True)
        self.star_3.set(False)
        star_3.grid(row=4,column=3)
        self.star_4 = tk.BooleanVar()
        star_4 = tk.Checkbutton(set_window,text="星4ポケモンを捕まえる",variable=self.star_4,offvalue=False,onvalue=True)
        self.star_3.set(False)
        star_4.grid(row=5,column=3)
        self.star_5 = tk.BooleanVar()
        star_5 = tk.Checkbutton(set_window,text="星5ポケモンを捕まえる",variable=self.star_5,offvalue=False,onvalue=True)
        self.star_5.set(False)
        star_5.grid(row=6,column=3)
        self.star_6 = tk.BooleanVar()
        star_6 = tk.Checkbutton(set_window,text="星6ポケモンを捕まえる",variable=self.star_6,offvalue=False,onvalue=True)
        self.star_6.set(False)
        star_6.grid(row=7,column=3)
        self.event_get = tk.BooleanVar()
        event_get = tk.Checkbutton(set_window,text="イベントレイドを捕まえる",variable=self.event_get,offvalue=False,onvalue=True)
        self.event_get.set(False)
        event_get.grid(row=8,column=3)

        self.ditto = tk.BooleanVar()
        ditto = tk.Checkbutton(set_window,text="星6メタモンが出たら終了する",variable=self.ditto,offvalue=False,onvalue=True)
        self.ditto.set(False)
        ditto.grid(row=9,column=3)

        self.skip_6 = tk.BooleanVar()
        skip_6 = tk.Checkbutton(set_window,text="星6レイドをスキップする",variable=self.skip_6,offvalue=False,onvalue=True)
        self.skip_6.set(True)
        skip_6.grid(row=10,column=3)

        self.skip_7 = tk.BooleanVar()
        skip_7 = tk.Checkbutton(set_window,text="星7レイドをスキップする",variable=self.skip_7,offvalue=False,onvalue=True)
        self.skip_7.set(True)
        skip_7.grid(row=11,column=3)

        btn = tk.Button(set_window,text="決定",command=lambda:hoge(),width = 20)
        btn.grid(row=21,column=0,columnspan=2,pady=5)

        def hoge():
            if use_rom.get() == "スカーレット":
                self.rom = "S"
            else:
                self.rom = "V"
            self.use_gui_poke = {"normal.png":self.type_eng[self.normal.get()],
                            "fire.png":self.type_eng[self.fire.get()],
                            "water.png":self.type_eng[self.water.get()],
                            "grass.png":self.type_eng[self.grass.get()],
                            "electric.png":self.type_eng[self.electric.get()],
                            "ice.png":self.type_eng[self.ice.get()],
                            "fighting.png":self.type_eng[self.fighting.get()],
                            "poison.png":self.type_eng[self.poison.get()],
                            "ground.png":self.type_eng[self.ground.get()],
                            "flying.png":self.type_eng[self.flying.get()],
                            "psychic.png":self.type_eng[self.psychic.get()],
                            "bug.png":self.type_eng[self.bug.get()],
                            "rock.png":self.type_eng[self.rock.get()],
                            "ghost.png":self.type_eng[self.ghost.get()],
                            "dragon.png":self.type_eng[self.dragon.get()],
                            "dark.png":self.type_eng[self.dark.get()],
                            "steel.png":self.type_eng[self.steel.get()],
                            "fairy.png":self.type_eng[self.fairy.get()]}
            self.ball = eng_balls[ball_box.get()]
            self.select_flag = True
            if lng_box.get() == "日本語":
                self.language = "jp"
            else:
                self.language = "eng"
            set_window.destroy()

    '''
    def waza_select(self,type):
        if self.star != "星3":
            if (self.use_poke[type] == "fighting.png" or self.use_poke[type] == "electric.png"
                or self.use_poke[type] == "water.png" or self.use_poke[type] == "fairy.png"):
                if self.turn_count == 1:
                    if self.isContainTemplate(f"SV/Raid/waza_type/normal.png", 0.8):
                        print("バフを使用します")
                        self.press(Button.A,0.1,1)
                        self.press(Button.A,0.1,1)
                        self.turn_count = self.turn_count +1
                    else:
                        self.press(Hat.TOP, 0.1, 0.5)
                if self.turn_count > 1:
                    if self.isContainTemplate(f"SV/Raid/waza_type/{self.use_poke[type]}", 0.8):
                        self.press(Button.A,0.1,1)
                        self.press(Button.A,0.1,1)
                        self.turn_count = self.turn_count +1
                    else:
                        self.press(Hat.BTM, 0.1, 0.5)

        else:
            if self.isContainTemplate(f"SV/Raid/waza_type/{self.use_poke[type]}", 0.8):
                self.press(Button.A,0.1,1)
                self.press(Button.A,0.1,1)
            else:
                self.press(Hat.BTM, 0.1, 0.5)
    '''
    def time(self):
        if self.flag:
            self.press(Button.B,0.1,1)
        self.press(Button.HOME, 0.1, 0.5)
        self.press(Hat.BTM, 0.1, 0.5)
        self.press(Direction.RIGHT,duration=0.8,wait=0.5)
        self.press(Direction.LEFT,0.05,0.3)
        self.press(Button.A,0.05,0.5)
        self.wait(0.1)
        self.press(Direction.DOWN,duration=1.5,wait=0.3)
        self.press(Button.A,0.05,0.5)
        while not self.isContainTemplate("SV/Auction/time.png", 0.8):
            self.press(Hat.BTM, 0.2, 0.01)
        self.press(Button.A,0.05,0.5)
        self.wait(0.1)
        self.press(Direction.DOWN,duration=0.5,wait=0.3)
        self.wait(0.3)
        self.press(Button.A,0.05,0.5)
        self.press(Button.A,0.05,0.1)
        self.press(Button.A,0.05,0.1)
        self.press(Hat.TOP, 0.05, 0.1)
        self.press(Button.A,0.05,0.1)
        self.press(Button.A,0.05,0.1)
        self.press(Button.A,0.05,0.1)
        self.press(Button.A,0.05,0.5)
        self.press(Button.HOME, 0.1, 1)
        self.press(Button.HOME, 0.1, 2)

    def terastal(self):

        cap = self.camera.readFrame()
        can = cv2.imread(f"./Template/SV/Raid/{self.language}/can.png", cv2.IMREAD_COLOR)
        can_temp = cv2.matchTemplate(cap, can, cv2.TM_CCOEFF_NORMED)
        cant = cv2.imread(f"./Template/SV/Raid/{self.language}/cant.png", cv2.IMREAD_COLOR)
        _, can_val, _, _ = cv2.minMaxLoc(can_temp)
        cant_temp = cv2.matchTemplate(cap, cant, cv2.TM_CCOEFF_NORMED)
        _, cant_val, _, _ = cv2.minMaxLoc(cant_temp)
        if can_val > cant_val and can_val > 0.9:
            return True
        else:
            return False

    def star_count(self,color):
        cap = self.camera.readFrame()
        star = cv2.imread(f"./Template/SV/Raid/{color}.png", cv2.IMREAD_COLOR)
        # if color == "event_star":
        cap = cv2.cvtColor(cap, cv2.COLOR_BGR2GRAY)
        star = cv2.cvtColor(star, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(cap, star, cv2.TM_CCOEFF_NORMED)
        loc = np.where( res >= self._threshold)

        result = 0
        for j in zip(*loc[::-1]):
            result = result +1
        self.star = result

