#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import ImageProcPythonCommand
from Commands.Keys import Button, Hat, Direction, Stick
import cv2
import tkinter as tk
from tkinter import ttk

class SV_Raid_2(ImageProcPythonCommand):

    NAME = "SV_自動レイド 安定版"

    def __init__(self, cam):
        super().__init__(cam)

        self.ver = 0.7

        self.select_flag = False
        self.use_poke = {}
        self.ball = ""
        self.get_flag = False
        self.flag = False
        self.count = 1
        self.terastal_flag = False

    def do(self):
        self.wait(1)

        print("--------------------------------------------")
        print(f"ポケモンSV自動レイドプログラムVer.{self.ver}")
        print("")
        print("---------------Special Thanks---------------")
        print("十字キー入力 シリアル通信直打ち版 @こちゃてす様")
        print("")
        print("--------------------------------------------")
        print("自動レイドを起動します")
        print("")

        self.window()
        while not self.select_flag:
            self.wait(0.5)
        ball = self.rename("jp",self.ball)
        print(f"使用するボールは {ball} です")
        print("")
        log = []

        print("使用するポケモンは")
        for k, v in self.use_poke.items():
            n = self.rename("jp",k)
            m = self.rename("jp",v)
            log.append(f"{n}\t\t=>\t{m}")
        print('\n'.join(log))

        if self.get_flag_val.get():
            self.get_flag = True
        else:
            self.get_flag = False

        while True:
            self.wait(1)
            print("--------------------------------------------")
            if self.flag:
                print("ポケモンを変更します")
                self.time()
                self.flag = False
            self.press(Button.A,0.1,1)
            if self.isContainTemplate(f"SV/Raid/raid.png", 0.8) or self.isContainTemplate(f"SV/Raid/raid_2.png", 0.8):
                print("結晶が見つかりました！")
                for i, type in enumerate(self.use_poke):
                    if self.isContainTemplate(f"SV/Raid/type/{type}",0.8):
                        t = self.rename("jp",type)
                        print(f"{self.count}匹目 {t}タイプ")
                        self.select_poke(type)
                        self.battle(type)
                        break
            else:
                print("結晶が見つかりませんでした")
                self.time()

    def field_check(self):
        while not self.isContainTemplate("SV/Raid/menu.png", 0.8):
            self.press(Button.X,0.1,0.1)
            self.press(Button.A,0.1,1)
        self.press(Button.B,0.1,1)

    def Hatpress(self,hat=None,duration=0.12,wait=0.3,repeat=1):
        # こちゃてす様 十字キー入力 シリアル通信直打ち版
        for _ in range(0,repeat):
            if hat == Hat.BTM:
                self.keys.ser.writeRow('0x0000 4 80 80 80 80')
            elif hat == Hat.RIGHT:
                self.keys.ser.writeRow('0x0000 2 80 80 80 80')
            elif hat == Hat.LEFT:
                self.keys.ser.writeRow('0x0000 6 80 80 80 80')
            elif hat == Hat.TOP:
                self.keys.ser.writeRow('0x0000 0 80 80 80 80')
            self.wait(duration)
            self.keys.ser.writeRow('0x0000 8 80 80 80 80')
            self.wait(wait)

        self.keys.ser.writeRow('0x0000 8 80 80 80 80')

    def time(self):
        self.press(Button.HOME, 0.1, 0.5)
        self.Hatpress(Hat.BTM, 0.1, 0.5)
        self.Hatpress(Hat.RIGHT, 0.1, 0.1,5)
        self.press(Button.A,0.1,0.5)
        self.wait(0.1)
        self.Hatpress(Hat.BTM,duration=1.5,wait=0.4)
        self.press(Button.A,0.1,0.5)
        while not self.isContainTemplate("SV/Raid/time.png", 0.8):
            self.Hatpress(Hat.BTM, 0.2, 0.01)
        self.press(Button.A,0.05,0.5)
        self.wait(0.1)
        self.press(Direction.DOWN,duration=0.5,wait=0.3)
        self.wait(0.3)
        self.press(Button.A,0.1,0.5)
        self.press(Button.A,0.1,0.1)
        self.press(Button.A,0.1,0.1)
        self.Hatpress(Hat.TOP, 0.1, 0.1)
        self.press(Button.A,0.1,0.1)
        self.press(Button.A,0.1,0.1)
        self.press(Button.A,0.1,0.1)
        self.press(Button.A,0.1,0.5)
        self.press(Button.HOME, 0.1, 1)
        self.press(Button.HOME, 0.1, 2)

    def select_poke(self,type):
        count = 0
        self.Hatpress(Hat.BTM, 0.1, 0.3)
        self.Hatpress(Hat.BTM, 0.1, 0.3)
        self.press(Button.A,0.1,0.5)
        while not self.isContainTemplate(f"SV/Raid/box.png", 0.8):
            self.wait(1)
        t = self.rename("jp",self.use_poke[type])
        print(f"{t}タイプのポケモンで戦います")
        while not self.isContainTemplate(f"SV/Raid/poke_type/{self.use_poke[type]}", 0.8):
            if count > 5:
                self.Hatpress(Hat.BTM, 0.1, 0.3)
                count = 0
            else:
                self.Hatpress(Hat.RIGHT, 0.1, 0.3)
                count = count +1
        self.press(Button.A,0.1,0.7)
        self.press(Button.A,0.1,1)
        while not self.isContainTemplate("SV/Raid/raid.png", 0.8) or not self.isContainTemplate("SV/Raid/raid_2.png", 0.8):
            self.wait(0.5)
        self.wait(1)
        self.Hatpress(Hat.TOP, 0.1, 0.5)
        self.press(Button.A,0.1,0.5)
        self.press(Button.A,0.1,1)

    def battle(self,type):
        print("対戦開始")
        self.battle_flag = False
        waza_count = 0
        err = 0
        while True:
            if self.isContainTemplate("SV/Raid/fight.png", 0.8):
                self.wait(0.5)
                self.press(Button.A,0.1,0.5)
                self.battle_flag = True
                waza_count = 0
                err = 0
            if self.isContainTemplate("SV/Raid/waza_select.png", 0.8) and not self.isContainTemplate("SV/Raid/emote.png",0.8):
                self.wait(0.5)
                if self.isContainTemplate(f"SV/Raid/waza_type/{self.use_poke[type]}", 0.8):
                    if not self.terastal_flag:
                        if self.terastal():
                            print("テラスタルを使用します")
                            self.terastal_flag = True
                            self.press(Button.R,0.1,1)
                    if waza_count > 1:
                        print("いちゃもん or かなしばり")
                        self.Hatpress(Hat.BTM, 0.1, 0.5)
                        self.press(Button.A,0.1,1)
                        self.press(Button.A,0.1,1)
                        waza_count = 0
                    else:
                        self.press(Button.A,0.1,1)
                        self.press(Button.A,0.1,1)
                        waza_count = waza_count +1
                else:
                    self.Hatpress(Hat.BTM, 0.1, 0.5)
            if self.isContainTemplate(f"SV/Raid/B.png", 0.8):
                if err > 5:
                    self.Hatpress(Hat.TOP, 0.1, 0.5)
                self.press(Button.A,0.1,1)
                err = err + 1
            if self.isContainTemplate(f"SV/Raid/get.png", 0.8):
                print("レイドバトルに勝利しました！")
                self.count = self.count +1
                self.wait(0.5)
                if self.get_flag:
                    self.press(Button.A,0.1,1)
                    while True:
                        if self.isContainTemplate(f"SV/Raid/ball/{self.language}/{self.ball}", 0.9):
                            ball = self.rename("jp",self.ball)
                            print(f"ポケモンを {ball} で捕まえます")
                            self.press(Button.A,0.1,1)
                            self.press(Button.A,0.1,1)
                            break
                        else:
                            self.Hatpress(Hat.LEFT, 0.1,0.5)
                else:
                    print("ポケモンを捕まえません")
                    self.Hatpress(Hat.BTM, 0.1,0.5)
                    self.press(Button.A,0.1,1)
                self.field_check()
                self.flag = False
                self.terastal_flag = False
                break
            if self.battle_flag == True:
                self.press(Button.X,0.1,1)
                if self.isContainTemplate("SV/Raid/menu.png", 0.8):
                    print("負けてしまった…")
                    self.press(Button.B,0.1,1)
                    self.flag = True
                    self.terastal_flag = False
                    break

    def terastal(self):
        cap = self.camera.readFrame()
        can = cv2.imread(f"./Template/SV/Raid/can.png", cv2.IMREAD_COLOR)
        can_temp = cv2.matchTemplate(cap, can, cv2.TM_CCOEFF_NORMED)
        cant = cv2.imread(f"./Template/SV/Raid/cant.png", cv2.IMREAD_COLOR)
        _, can_val, _, _ = cv2.minMaxLoc(can_temp)
        cant_temp = cv2.matchTemplate(cap, cant, cv2.TM_CCOEFF_NORMED)
        _, cant_val, _, _ = cv2.minMaxLoc(cant_temp)
        if can_val > cant_val and can_val > 0.9:
            return True
        else:
            return False

    def window(self):
        set_window = tk.Toplevel()
        set_window.geometry("360x560")
        set_window.title("使用ポケモン変更")

        box = ["ノーマル",
                "ほのお",
                "みず",
                "くさ",
                "でんき",
                "こおり",
                "かくとう",
                "どく",
                "じめん",
                "ひこう",
                "エスパー",
                "むし",
                "いわ",
                "ゴースト",
                "ドラゴン",
                "あく",
                "はがね",
                "フェアリー"
                ]
        enemy_text = tk.Label(set_window,text="相手のタイプ")
        use_text = tk.Label(set_window,text="使うポケモン")
        enemy_text.grid(row=1, column=0)
        use_text.grid(row=1, column=1)

        # use_rom_box = ["スカーレット","バイオレット"]
        # use_rom_text = tk.Label(set_window,text="使用するROM")
        # use_rom_text.grid(row=0,column=0,pady=2)
        # use_rom  = ttk.Combobox(set_window,values=use_rom_box,width=14)
        # use_rom.current(1)
        # use_rom.grid(row=0,column=1,pady=2)

        language = ["日本語","英語"]
        lng_text = tk.Label(set_window,text="使用言語")
        lng_text.grid(row=0,column=0,pady=2)
        lng_box = ttk.Combobox(set_window,values=language)
        lng_box.current(0)
        lng_box.grid(row=0,column=1,pady=2)

        normal_index = 0
        fire_index = 1
        water_index = 2
        grass_index = 3
        electric_index = 4
        ice_index = 5
        fighting_index = 6
        poison_index = 7
        ground_index = 8
        flying_index = 9
        psychic_index = 10
        bug_index = 11
        rock_index = 12
        ghost_index = 13
        dragon_index = 14
        dark_index = 15
        steel_index = 16
        fairy_index = 17

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
        self.normal.current(fighting_index)
        self.fire = ttk.Combobox(set_window,values=box,width=14)
        self.fire.current(water_index)
        self.water = ttk.Combobox(set_window,values=box,width=14)
        self.water.current(electric_index)
        self.grass = ttk.Combobox(set_window,values=box,width=14)
        self.grass.current(fire_index)
        self.electric = ttk.Combobox(set_window,values=box,width=14)
        self.electric.current(ground_index)
        self.ice = ttk.Combobox(set_window,values=box,width=14)
        self.ice.current(fighting_index)
        self.fighting = ttk.Combobox(set_window,values=box,width=14)
        self.fighting.current(fairy_index)
        self.poison = ttk.Combobox(set_window,values=box,width=14)
        self.poison.current(psychic_index)
        self.ground = ttk.Combobox(set_window,values=box,width=14)
        self.ground.current(grass_index)
        self.flying = ttk.Combobox(set_window,values=box,width=14)
        self.flying.current(electric_index)
        self.psychic = ttk.Combobox(set_window,values=box,width=14)
        self.psychic.current(ghost_index)
        self.bug = ttk.Combobox(set_window,values=box,width=14)
        self.bug.current(fire_index)
        self.rock = ttk.Combobox(set_window,values=box,width=14)
        self.rock.current(grass_index)
        self.ghost = ttk.Combobox(set_window,values=box,width=14)
        self.ghost.current(ghost_index)
        self.dragon = ttk.Combobox(set_window,values=box,width=14)
        self.dragon.current(fairy_index)
        self.dark = ttk.Combobox(set_window,values=box,width=14)
        self.dark.current(fairy_index)
        self.steel = ttk.Combobox(set_window,values=box,width=14)
        self.steel.current(fire_index)
        self.fairy = ttk.Combobox(set_window,values=box,width=14)
        self.fairy.current(poison_index)

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

        balls = ["モンスターボール",
                "スーパーボール",
                "ハイパーボール",
                "プレミアボール",
                "ヒールボール",
                "ネットボール",
                "ネストボール",
                "ダイブボール",
                "ダークボール",
                "タイマーボール",
                "クイックボール",
                "リピートボール",
                "ゴージャスボール"]
        ball_box = ttk.Combobox(set_window,values=balls,width=14)
        ball_box.current(0)
        ball_box.grid(row=20,column=1)

        self.get_flag_val = tk.BooleanVar()
        self.get_flag_val.set(False)
        get_box = tk.Checkbutton(set_window,text="ポケモンを捕まえる",variable=self.get_flag_val)
        get_box.grid(row=0,column=3)

        # self.star_1 = tk.BooleanVar()
        # star_1 = tk.Checkbutton(set_window,text="星1ポケモンを捕まえる",variable=self.star_1,offvalue=False,onvalue=True)
        # self.star_1.set(False)
        # star_1.grid(row=2,column=3)
        # self.star_2 = tk.BooleanVar()
        # star_2 = tk.Checkbutton(set_window,text="星2ポケモンを捕まえる",variable=self.star_2,offvalue=False,onvalue=True)
        # self.star_2.set(False)
        # star_2.grid(row=3,column=3)

        # self.star_3 = tk.BooleanVar()
        # star_3 = tk.Checkbutton(set_window,text="星3ポケモンを捕まえる",variable=self.star_3,offvalue=False,onvalue=True)
        # self.star_3.set(False)
        # star_3.grid(row=4,column=3)
        # self.star_4 = tk.BooleanVar()
        # star_4 = tk.Checkbutton(set_window,text="星4ポケモンを捕まえる",variable=self.star_4,offvalue=False,onvalue=True)
        # self.star_3.set(False)
        # star_4.grid(row=5,column=3)
        # self.star_5 = tk.BooleanVar()
        # star_5 = tk.Checkbutton(set_window,text="星5ポケモンを捕まえる",variable=self.star_5,offvalue=False,onvalue=True)
        # self.star_5.set(False)
        # star_5.grid(row=6,column=3)
        # self.star_6 = tk.BooleanVar()
        # star_6 = tk.Checkbutton(set_window,text="星6ポケモンを捕まえる",variable=self.star_6,offvalue=False,onvalue=True)
        # self.star_6.set(False)
        # star_6.grid(row=7,column=3)
        # self.event_get = tk.BooleanVar()
        # event_get = tk.Checkbutton(set_window,text="イベントレイドを捕まえる",variable=self.event_get,offvalue=False,onvalue=True)
        # self.event_get.set(False)
        # event_get.grid(row=8,column=3)

        # self.ditto = tk.BooleanVar()
        # ditto = tk.Checkbutton(set_window,text="星6メタモンが出たら終了する",variable=self.ditto,offvalue=False,onvalue=True)
        # self.ditto.set(False)
        # ditto.grid(row=9,column=3)

        # self.skip_6 = tk.BooleanVar()
        # skip_6 = tk.Checkbutton(set_window,text="星6レイドをスキップする",variable=self.skip_6,offvalue=False,onvalue=True)
        # self.skip_6.set(True)
        # skip_6.grid(row=10,column=3)

        # self.skip_7 = tk.BooleanVar()
        # skip_7 = tk.Checkbutton(set_window,text="星7レイドをスキップする",variable=self.skip_7,offvalue=False,onvalue=True)
        # self.skip_7.set(True)
        # skip_7.grid(row=11,column=3)

        btn = tk.Button(set_window,text="決定",command=lambda:hoge(),width = 20)
        btn.grid(row=21,column=0,columnspan=2,pady=5)

        def hoge():
            self.use_poke = {"normal.png":self.rename("eng",self.normal.get()),
                            "fire.png":self.rename("eng",self.fire.get()),
                            "water.png":self.rename("eng",self.water.get()),
                            "grass.png":self.rename("eng",self.grass.get()),
                            "electric.png":self.rename("eng",self.electric.get()),
                            "ice.png":self.rename("eng",self.ice.get()),
                            "fighting.png":self.rename("eng",self.fighting.get()),
                            "poison.png":self.rename("eng",self.poison.get()),
                            "ground.png":self.rename("eng",self.ground.get()),
                            "flying.png":self.rename("eng",self.flying.get()),
                            "psychic.png":self.rename("eng",self.psychic.get()),
                            "bug.png":self.rename("eng",self.bug.get()),
                            "rock.png":self.rename("eng",self.rock.get()),
                            "ghost.png":self.rename("eng",self.ghost.get()),
                            "dragon.png":self.rename("eng",self.dragon.get()),
                            "dark.png":self.rename("eng",self.dark.get()),
                            "steel.png":self.rename("eng",self.steel.get()),
                            "fairy.png":self.rename("eng",self.fairy.get())
                            }
            self.ball = self.rename("eng",ball_box.get())

            if lng_box.get() == "日本語":
                self.language = "jp"
            else:
                self.language = "eng"
            self.select_flag = True
            set_window.destroy()

    def rename(self,lang,text):
        dic = {"normal.png":"ノーマル",
                "fire.png":"ほのお",
                "water.png":"みず",
                "grass.png":"くさ",
                "electric.png":"でんき",
                "ice.png":"こおり",
                "fighting.png":"かくとう",
                "poison.png":"どく",
                "ground.png":"じめん",
                "flying.png":"ひこう",
                "psychic.png":"エスパー",
                "bug.png":"むし",
                "rock.png":"いわ",
                "ghost.png":"ゴースト",
                "dragon.png":"ドラゴン",
                "dark.png":"あく",
                "steel.png":"はがね",
                "fairy.png":"フェアリー",
                "ball_01.png":"モンスターボール",
                "ball_02.png":"スーパーボール",
                "ball_03.png":"ハイパーボール",
                "ball_04.png":"プレミアボール",
                "ball_05.png":"ヒールボール",
                "ball_06.png":"ネットボール",
                "ball_07.png":"ネストボール",
                "ball_08.png":"ダイブボール",
                "ball_09.png":"ダークボール",
                "ball_10.png":"タイマーボール",
                "ball_11.png":"クイックボール",
                "ball_12.png":"リピートボール",
                "ball_13.png":"ゴージャスボール"
                }
        if lang == "jp":
            return dic[text]
        else:
            for key,value in dic.items():
                if text == value:
                    return key

