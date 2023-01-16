#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time

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


class AutoRaid(ImageProcPythonCommand):
    NAME = 'SV_Online自動レイド_v.0.4'

    def __init__(self, cam):
        super().__init__(cam)

    def do(self):
        print("-------------------------------")
        print("SV オンライン自動レイド_v.0.4")
        print("Developed by.けんと")
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

        # 事前設定
        # ニックネーム設定：OFF
        # ボックスは自動で送る設定
        # オートセーブ：ON
        # 使用するポケモンをdecide_myPokemonで設定した順番通りにボックスの左上からに配置
        # 1  2  3  4  5  6
        # 7  8  9  10 11 12
        # ...
        # そのボックスを選択した状態で終了
        # それぞれのポケモンの一番上の技が連打したい技か確認(パラボラチャージ、ドレインキッスetc)
        # できれば全員にメトロノームを持たせる
        # 手持ちの1番目のポケモンも一応戦える状態にする
        # ネットワークに接続しテラレイドバトルを開始してスタート(後々ネットワーク切断からも復帰できるようにしたい)

        while True:
            self.wait(0.5)

            # レイドが始まるまでXとAを連打する(レイドに入れたことを示す画像を検知するまでXとAを連打する)
            # 「レイドに参加できませんでした」を検知して処理を行っても良い(Xを押して更新する、方向キーを押す)
            while not self.isContainTemplate('SV_Raid/RaidBattle_JP.png', threshold=0.8, use_gray=True,
                                             show_value=False):
                self.press(Button.A, wait=2.0)
                self.press(Button.X, wait=2.0)
                

            # 捕まえるか否かの判定
            is_capture = self.is_capture_pokemon()
            print("レイド開始", "倒せた場合は捕まえます。" if is_capture else "このポケモンは捕まえません。")

            # レイド参加
            count += 1
            self.camera.saveCapture(filename=f"{start_time_yyyymmddHHMMSS}/{count}")
            print("------------レイド開始-------------")
            print("         ", count, "回目のレイドです")
            print("----------------------------------")

            # レイドポケモンのタイプを判定する
            raidPokemon_type = self.judge_raidPokemon_type()

            # レイドポケモンのタイプに応じて使用するポケモンを決定する
            myPokemon_num = self.decide_myPokemon(raidPokemon_type)

            # 必要に応じて使用するポケモンを変更する
            self.change_pokemon_from_box(myPokemon_num)

            # レイド準備完了
            self.press(Button.A, wait=1.0)
            self.press(Button.A, wait=1.0)  # 入力抜け防止

            # レイドを倒すまでの処理
            # つかまえたが出るまで
            print("倒すまでの処理を実施します")
            turn = 0
            while not self.isContainTemplate('SV_Raid/raid_catch.png', threshold=0.95, use_gray=False,
                                             show_value=False):
                # レイド解散チェック(解散していた場合XA連打の処理に戻る)
                if self.isContainTemplate("SV_Raid/raid_breakup.png", threshold=0.8, use_gray=False, show_value=False):
                    continue
                print("レイドのwhile")
                raid_while_num += 1
                self.wait(2.0)
                if self.isContainTemplate("SV_Raid/raid_keepon_Y.png", threshold=0.8, use_gray=False, show_value=False) \
                        or self.isContainTemplate("SV_Raid/raid_keepon_m.png", threshold=0.8, use_gray=False,
                                                  show_value=False) \
                        or self.isContainTemplate("SV_Raid/raid_keepon_f.png", threshold=0.8, use_gray=False,
                                                  show_value=False):
                    print("レイド継続中")
                    if (raid_while_num < 5):
                        turn -= 1
                    raid_while_num = 0
                    self.wait(0.5)  # 0.5にする
                    self.press(Button.A, wait=1.0)
                    if 3 <= turn and turn <= 6:
                        print("多分テラスタルオーブが溜まりました。テラスタルを発動します。")
                        self.press(Button.R, wait=0.5)
                    self.press(Button.A, wait=1.0)
                    self.press(Button.A, wait=1.0)
                    self.press(Button.A, wait=1.0)
                    self.press(Button.A, wait=1.0)
                    turn += 1

                if self.isContainTemplate('SV_Raid/raid_lose.png', threshold=0.95, use_gray=True, show_value=False):
                    break
            if self.isContainTemplate('SV_Raid/raid_lose.png', threshold=0.9, use_gray=True, show_value=False):
                print("負けたため、次のレイドを行います。")
                lose_counts.append(count)
                self.wait(4.0)
                self.press(Button.A, wait=1.0)
                # self.dayprogress()
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
            # ボール選ぶ処理を入れるならここ
            # つぎへのAボタン認識するまで待機
            while not self.isContainTemplate('SV_Raid/raid_A.png', threshold=0.8, use_gray=False, show_value=False):
                print("結果待ちのwhile")
                self.wait(0.5)

            self.wait(2.0)
            self.press(Button.A, wait=1.0)
            print("------------レイド終了-------------")
            print("周回数 → ", count, "回")
            print("敗北数 → ", len(lose_counts), "回")
            for lose in lose_counts:
                print("敗北画像 → ", lose, ".png")
            print("----------------------------------")

    def is_capture_pokemon(self):
        CWD = os.getcwd()
        POKEMON_DIR = CWD + r"\Template\SV_Raid\capture_pokemons"
        capture_pokemons = glob.glob(os.path.join(POKEMON_DIR, '*.png'))
        for pokemon_image_full_path in capture_pokemons:
            image_name = os.path.basename(pokemon_image_full_path)
            path = f'SV_Raid/capture_pokemons/{image_name}'
            if self.isContainTemplate(path, threshold=0.85, use_gray=True, show_value=False):
                return True
        return False

    def judge_raidPokemon_type(self):
        for x in Type:
            if self.isContainTemplate('SV_Raid/raid_S/raid_' + x.name + '.png', threshold=0.95, use_gray=True,
                                      show_value=False) \
                    or self.isContainTemplate('SV_Raid/raid_6/raid_6_' + x.name + '.png', threshold=0.95, use_gray=True,
                                              show_value=False):
                print(x.value + "のレイドです。")
                return x
        # 合致するものがいなかった場合ドラゴンタイプとして返す(デフォルトはニンフィアを使用する)
        print("合致するタイプがないのでドラゴンタイプとして処理します。")
        return Type.DRAGON

    def decide_myPokemon(self, raidPokemon_type: Type):
        # ニンフィア(1番目)を使うタイプ
        NINFIA = [Type.FIGHTING, Type.DRAGON, Type.DARK]
        # テツノカイナ(2番目を使うタイプ)
        TETUNOKAINA = [Type.NORMAL, Type.ICE, Type.ROCK, Type.STEEL]
        # ハラバリー(3番目を使うタイプ)
        HARABARI = [Type.WATER, Type.FLYING]
        # クエスパトラ(4番目を使うタイプ)
        KUESUPATORA = [Type.POISON]
        # テツノドクガ(5番目を使うタイプ)
        TETUNODOKUGA = [Type.GRASS, Type.BUG]
        # マリルリ(6番目を使うタイプ)
        MARIRURI = [Type.FIRE, Type.GROUND]
        # ハバタクカミ(7番目を使うタイプ)
        HABATAKUKAMI= [Type.PSYCHIC, Type.GHOST] 
        # デカヌチャン(8番目を使うタイプ)
        DEKANUCHAN = [Type.FAIRY]
        # ガブリアス(9番目を使うタイプ)
        GABURIASU = [Type.ELECTRIC]


        if raidPokemon_type in NINFIA:
            print('ニンフィアを使用します。')
            return 1
        elif raidPokemon_type in TETUNOKAINA:
            print('テツノカイナを使用します。')
            return 2
        elif raidPokemon_type in HARABARI:
            print('ハラバリーを使用します。')
            return 3
        elif raidPokemon_type in KUESUPATORA:
            print('クエスパトラを使用します。')
            return 4
        elif raidPokemon_type in TETUNODOKUGA:
            print('テツノドクガを使用します。')
            return 5
        elif raidPokemon_type in MARIRURI:
            print('マリルリを使用します。')
            return 6
        elif raidPokemon_type in HABATAKUKAMI:
            print('ハバタクカミを使用します。')
            return 7
        elif raidPokemon_type in DEKANUCHAN:
            print('デカヌチャンを使用します。')
            return 8
        elif raidPokemon_type in GABURIASU:
            print('ガブリアスを使用します。')
            return 9
        # デフォルトではニンフィア(1番目)を使用する
        else:
            print('ニンフィアを使用します。')
            return 1

    # def change_pokemon(self, num):
    #     self.press(Direction.DOWN, wait=0.5)
    #     self.press(Button.A, wait=3.0)

    #     # ボックス操作
    #     while not self.isContainTemplate('SV_Raid/raid_box.png', threshold=0.9, use_gray=True, show_value=False):
    #         time.sleep(0.5)
    #     print(f"手持ちから{num}匹目を選択。")
    #     self.press(Direction.LEFT, wait=1.0)

    #     for _ in range(1, num):
    #         self.press(Direction.DOWN, wait=1.0)

    #     self.press(Button.A, wait=1)
    #     self.press(Button.A, wait=5.0)
    #     self.press(Direction.UP, wait=1)

    def change_pokemon_from_box(self, num: int):
        time.sleep(1.0)
        self.press(Direction.DOWN, wait=1.0)
        self.press(Button.A, wait=3.0)

        # ボックス操作
        while not self.isContainTemplate('SV_Raid/raid_box.png', threshold=0.9, use_gray=True, show_value=False):
            time.sleep(0.5)
        print(f"ボックスから{num}匹目を選択。")

        if num == 1:
            time.slee(1.0)
        if 2 <= num <= 6:
            for _ in range(1, num):
                self.press(Direction.RIGHT, wait=1.0)
        elif num == 7:
            self.press(Direction.DOWN, wait=1.0)
        elif 8 <= num <= 12:
            self.press(Direction.DOWN, wait=1.0)
            for _ in range(1, num):
                self.press(Direction.RIGHT, wait=1.0)
        else:
            time.sleep(1.0)

        self.press(Button.A, wait=1)
        self.press(Button.A, wait=5.0)
        self.press(Direction.UP, wait=1)

