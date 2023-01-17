#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.PythonCommandBase import PythonCommand, ImageProcPythonCommand
from Commands.Keys import KeyPress, Button, Direction, Stick, Hat
import datetime
import os
import shutil
import glob
import enum


class Type(enum.Enum):
    NORMAL = "ノーマルタイプ"
    FIRE = "ほのおタイプ"
    WATER = "みずタイプ"
    GRASS = "くさタイプ"
    ELECTRIC = "でんきタイプ"
    ICE = "こおりタイプ"
    FIGHTING = "かくとうタイプ"
    POISON = "どくタイプ"
    GROUND = "じめんタイプ"
    FLYING = "ひこうタイプ"
    PSYCHIC = "エスパータイプ"
    BUG = "むしタイプ"
    ROCK = "いわタイプ"
    GHOST = "ゴーストタイプ"
    DRAGON = "ドラゴンタイプ"
    DARK = "あくタイプ"
    STEEL = "はがねタイプ"
    FAIRY = "フェアリータイプ"


class AutoEncount(ImageProcPythonCommand):
    NAME = 'SV_Local自動レイド_v.0.9.1'

    def __init__(self,cam):
        super().__init__(cam)

    def do(self):
        print("-------------------------------")
        print("SV Local自動レイド_v.0.9.1")
        print("Developed by.じゃんきー")
        print("Special Thanks:はんぺんさん、特にさん、minahokuさん")
        print("-------------------------------")
        self.wait(0.5)
        count = 0
        menu_while_num = 0
        raid_while_num = 0
        lose_counts = []
        # 開始時間を取得（画像ファイル名に用いる）
        t_delta = datetime.timedelta(hours=9)
        JST = datetime.timezone(t_delta, 'JST')
        start_time = datetime.datetime.now(JST)
        start_time_yyyymmddHHMMSS = start_time.strftime('%Y%m%d%H%M%S')
        print("Start", " ", start_time)

        #事前設定
        #ニックネーム設定：OFF
        #ボックスは自動で送らない設定
        #オートセーブ：ON
        #初回は巣穴の正面に立って実施。場所がわかってればその場でOK
        #ボール変更の処理は入れてません
        #メタモン表示があったら止める設定もできます。デフォルトはコメントアウトしてます。
        #注意事項！！！
        #ボールがなくなった時の処理は入れてません。ご注意ください。
        #ボールの変更処理も入れてません。A連打でボールを投げます
        

        while True:
            self.wait(0.5)
            #メニュー認識

            while not self.isContainTemplate('SV_suana/menu_R.png', threshold=0.8, use_gray=True, show_value=False):
                menu_while_num += 1
                print("メニューのwhile")
                if menu_while_num % 15 == 0:
                    print("指定時間以上待機しました。Aボタンをクリックします。")
                    self.press(Button.A, wait=1.0)
                # メニュー展開
                self.press(Button.X, wait=1.0)

            print("メニュー画面を認識しました")
            menu_while_num = 0
            self.press(Button.B, wait=2.0)
            self.press(Button.Y, wait=5.0)
            self.press(Button.Y, wait=4.0)
            self.press(Button.A, wait=3.0)
            while not self.isContainTemplate('SV_suana/V_raid.png', threshold=0.7, use_gray=True, show_value=False):
                print("巣穴のwhile")
                print("巣穴がないため日付変更をします")
                self.dayprogress()
                self.wait(4.0) #巣穴沸き待機
                self.press(Button.A, wait=1.5)

            # 捕まえるか否かの判定
            is_capture = self.is_capture_pokemon()
            print("巣穴発見。", "倒せた場合は捕まえます。" if is_capture else "このポケモンは捕まえません。")
            #レイド参加
            count += 1
            self.camera.saveCapture(filename=f"{start_time_yyyymmddHHMMSS}/{count}")
            print("------------レイド開始-------------")
            print("         ",count,"回目のレイドです")
            print("----------------------------------")
            
            if self.isContainTemplate('SV_suana/raid_ground.png', threshold=0.95, use_gray=True, show_value=False):
                print("地面レイドです。対地面タイプポケモンに変更します。")
                self.change_pokemon(2)

            if self.isContainTemplate('SV_suana/raid_grass.png', threshold=0.95, use_gray=True, show_value=False):
                print("草レイドです。対草タイプポケモンに変更します。")
                self.change_pokemon(3)
            
            self.press(Direction.DOWN, wait=1.0)
            self.press(Button.A, wait=1.0)
            self.press(Button.A, wait=1.0) #入力抜け防止
            #つかまえたが出るまで
            print("倒すまでの処理を実施します")
            turn = 0
            while not self.isContainTemplate('SV_suana/raid_catch.png', threshold=0.95, use_gray=False, show_value=False):
                print("レイドのwhile")
                raid_while_num += 1
                self.wait(1.0)
                if self.isContainTemplate("SV_Suana/raid_keepon_Y.png", threshold=0.8, use_gray=False, show_value=False) \
                or self.isContainTemplate("SV_suana/raid_keepon_m.png", threshold=0.8, use_gray=False, show_value=False) \
                or self.isContainTemplate("SV_suana/raid_keepon_f.png", threshold=0.8, use_gray=False, show_value=False):
                    print("レイド継続中")
                    if(raid_while_num < 5):
                        turn -= 1
                    raid_while_num = 0
                    self.wait(0.5)#0.5にする
                    self.press(Button.A, wait=1.0)
                    if 3 <= turn and turn <= 6:
                        print("多分テラスタルオーブが溜まりました。テラスタルを発動します。")
                        self.press(Button.R, wait=0.5)
                    self.press(Button.A, wait=1.0)
                    self.press(Button.A, wait=1.0)
                    self.press(Button.A, wait=1.0)
                    self.press(Button.A, wait=1.0)
                    turn += 1

                if self.isContainTemplate('SV_suana/raid_lose.png', threshold=0.95, use_gray=True, show_value=False):
                    break
            if self.isContainTemplate('SV_suana/raid_lose.png', threshold=0.9, use_gray=True, show_value=False):
                print("負けたため、次のレイドを行います。")
                lose_counts.append(count)
                self.wait(4.0) #巣穴沸き待機
                self.press(Button.A, wait=1.0)
                self.dayprogress()
                self.wait(1.0)
                try: 
                    SCREENSHOT_DIR = r"C:\PokeCon\Poke-Controller-Modified\SerialController\Captures"
                    image_path = os.path.join(SCREENSHOT_DIR, start_time_yyyymmddHHMMSS)
                    print('image_path: ', image_path)
                    src_path = os.path.join(image_path, f"{count}.png")
                    print('src_path: ', src_path)
                    dst_path = os.path.join(image_path, f"lose_{count}.png")
                    print('dst_path: ', dst_path)
                    shutil.copyfile(src_path, dst_path)
                except BaseException as e:
                    print(e)
                    pass
                continue
            print("ポケモンを倒しました。")
            self.wait(2.0)
            if is_capture:
                print("捕獲対象ポケモンなので捕獲します。")
                self.press(Button.A, wait=1.0)
            else:
                print("捕獲せずに処理を進めます。")
                self.press(Direction.DOWN, wait=1.0)
        
            self.press(Button.A, wait=1.0)
            #ボール選ぶ処理を入れるならここ
            #つぎへのAボタン認識するまで待機
            while not self.isContainTemplate('SV_suana/raid_A.png', threshold=0.8, use_gray=False, show_value=False):
                print("結果待ちのwhile")
                self.wait(0.5)

            self.wait(2.0)
            self.press(Button.A, wait=1.0)
            print("------------レイド終了-------------")
            print("周回数 → ",count,"回")
            print("敗北数 → ",len(lose_counts),"回")
            for lose in lose_counts:
                print("敗北画像 → ",lose,".png")
            print("----------------------------------")


    def dayprogress(self):
        print("< 日付を1日進めます >")
        # ホーム画面⇒日時と時刻の画面
        self.press(Button.HOME, wait=1)
        self.press(Direction.DOWN)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Button.A, wait=1.5)  
        self.press(Direction.DOWN, duration=2, wait=0.5)
        self.press(Button.A, wait=0.3)  
        for j in range(20):
            if self.isContainTemplate("SV_Suana/select_date_change_white.png", threshold=0.83, use_gray=False, show_value=False) \
            or self.isContainTemplate("SV_suana/select_date_change_dark.png", threshold=0.83, use_gray=False, show_value=False):
                self.press(Button.A, wait=1.0)
                self.press(Direction.DOWN)
                self.press(Direction.DOWN)
                self.press(Button.A, wait=1.0)
                self.press(Direction.RIGHT)
                self.press(Direction.RIGHT)
                self.press(Direction.UP)
                self.press(Button.A)
                self.press(Button.A)
                self.press(Button.A)
                self.press(Button.A)
                self.wait(0.5)
                self.press(Button.HOME, wait=0.5)
                self.press(Button.HOME, wait=1)
                break
            else:
                self.press(Direction.DOWN, wait=0.13)

    def is_capture_pokemon(self):
        POKEMON_DIR = r"C:\PokeCon\Poke-Controller-Modified\SerialController\Template\SV_Suana\capture_pokemons"
        capture_pokemons = glob.glob(os.path.join(POKEMON_DIR,'*.png'))
        for pokemon_image_full_path in capture_pokemons:
            image_name = os.path.basename(pokemon_image_full_path)
            path = f'SV_suana/capture_pokemons/{image_name}'
            if self.isContainTemplate(path, threshold=0.85, use_gray=True, show_value=False):
                return True
        return False


    def softreset(self):
        self.wait(0.5)
        self.press(Button.HOME, wait=0.5)
        self.wait(0.5)
        self.press(Button.X, wait=0.5)
        self.wait(0.5)
        self.press(Button.A, wait=5.0) 
        self.press(Button.A, wait=5.0) 
        self.press(Button.A, wait=10.0) 
        while not self.isContainTemplate('SV_suana/S_TOP.png', threshold=0.5, use_gray=True, show_value=False):
            self.wait(0.5)
        print("TOP画面を認識しました。")
        self.wait(3.0)
        self.press(Button.A, wait=1.0)

    def change_pokemon(self, num):
            self.press(Direction.UP, wait=0.5)
            self.press(Button.A, wait=5.0)
            # ボックス操作
            print(f"手持ちから{num}匹目を選択。")
            self.press(Direction.LEFT, wait=1.0)

            for _ in range(1, num):
                self.press(Direction.DOWN, wait=1.0)

            self.press(Button.A, wait=0.5)
            self.press(Button.A, wait=2.0)
            self.press(Direction.DOWN, wait=0.5)