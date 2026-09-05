import io
import os
import tkinter as tk
from tkinter import messagebox, simpledialog

import cv2
import requests
import yaml
from Camera import Camera
from PIL import Image
from deprecated import deprecated
from loguru import logger


class Discord_Notify:
    """
    Discordに通知するクラス

    主要機能:
        - Webhook設定の管理
        - カメラ画像のDiscord送信
        - テキストメッセージの送信
        - GUIによるWebhook操作

    Attributes:
        config_file (str): YAML設定ファイルパス
        camera (Camera): カメラオブジェクト（Noneで初期化）
        webhooks (list): Webhook設定リスト
    """

    def __init__(self, config_file: str = "discord.yml", camera: Camera = None):
        """
        Discord_Notifyの初期化

        Args:
            config_file (str): YAML設定ファイルパス（デフォルト: 'discord.yml'）
            camera (Camera): カメラオブジェクト（Noneで初期化）
        """
        if camera is None:
            self.use_camera = False
        else:
            self.camera = camera
            self.use_camera = True
        self.config_file = config_file
        self.webhooks = self.load_config()

    def camera_to_byte(self) -> bytes:
        """
        カメラの画像をbyte形式に変換

        Returns:
            bytes: 画像データ（PNG形式）
        """
        image_bgr = self.camera.readFrame()
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(image_rgb)
        png = io.BytesIO()
        image.save(
            png, format="png"
        )  # 空のio.BytesIOオブジェクトにpngファイルとして書き込み
        b_frame = png.getvalue()  # io.BytesIOオブジェクトをbytes形式で読みとり
        return b_frame

    def load_config(self) -> list:
        """
        YAML設定ファイルからWebhookリストを読み込む

        Returns:
            list: Webhook設定リスト（URLと名前）
        """
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            return list(config.get("webhooks", []))  # Webhooksのリストを返す
        except FileNotFoundError:
            return []  # 設定ファイルが見つからない場合、空のリストを返す

    def save_config(self) -> None:
        """
        Webhook設定リストをYAMLファイルに保存
        """
        config = {"webhooks": self.webhooks}
        with open(self.config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True)

    def get_webhook_url(self, index: int) -> str | None:
        """
        Webhookリストから指定インデックスのURLを取得

        Args:
            index (int): Webhookインデックス

        Returns:
            str | None: URLまたはNone（範囲外の場合）
        """
        if 0 <= index < len(self.webhooks):
            return str(self.webhooks[index]["url"])
        return None

    def add_webhook(self, name: str, url: str) -> None:
        """
        新しいWebhookを追加

        Args:
            name (str): Webhook名
            url (str): WebhookURL
        """
        self.webhooks.append({"name": name, "url": url})
        self.save_config()

    def update_webhook(self, index: int, name: str, url: str) -> None:
        """
        Webhookを更新

        Args:
            index (int): Webhookインデックス
            name (str): 新しいWebhook名
            url (str): 新しいWebhookURL
        """
        if 0 <= index < len(self.webhooks):
            self.webhooks[index] = {"name": name, "url": url}
            self.save_config()

    def delete_webhook(self, index: int) -> None:
        """
        Webhookを削除

        Args:
            index (int): Webhookインデックス
        """
        if 0 <= index < len(self.webhooks):
            del self.webhooks[index]
            self.save_config()

    def send_message(
        self,
        index: int,
        content: str,
        name: str | None = None,
        without_image: bool = False,
    ) -> None:
        """
        Discordにメッセージを送信

        Args:
            index (int): Webhookインデックス
            content (str): 送信内容
            name (str | None): Webhook名（Noneでインデックス指定）
            without_image (bool): キャプチャ画像を送信するか

        Returns:
            None
        """
        if name is not None:
            found_index = -1
            for idx, webhook in enumerate(self.webhooks):
                if webhook["name"] == name:
                    found_index = idx
                    break
            # nameが見つかった場合はそのインデックスを優先
            index = found_index if found_index != -1 else index

        if isinstance(index, int) and 0 <= index < len(self.webhooks):
            url = self.get_webhook_url(index)
            if url is None:
                print("指定された送信元名前が見つかりません。")
                return
            if not without_image:
                files = (
                    {"file": ("image.png", self.camera_to_byte(), "image/png")}
                    if self.use_camera
                    else {}
                )
            else:
                files = {}
            data = {"content": content}
            response = requests.post(url, data=data, files=files)
            status_code = response.status_code
            if 200 <= status_code < 300:
                print(f"{self.webhooks[index]['name']}にメッセージを送信しました。")
            else:
                print(f"エラーが発生しました: {status_code}")
        elif name is not None and found_index == -1:
            print("指定された送信元名前が見つかりません。")
        else:
            print("Webhook URLが選択されていません。")

    @deprecated(reason="Use send_message instead")
    def send_text(self, notification_message: str, token: str = "token") -> None:
        """
        テキストメッセージを送信（非推奨）

        Args:
            notification_message (str): 通知メッセージ
            token (str): WebhookURL（非推奨）

        Returns:
            None
        """
        try:
            self.send_message(0, notification_message)
        except KeyError:
            print("token名が間違っています")
            logger.error("Using the wrong token")

    @deprecated(reason="Use send_message instead")
    def send_text_n_image(
        self, notification_message: str, token: str = "token"
    ) -> None:
        """
        テキストと画像を送信（非推奨）

        Args:
            notification_message (str): 通知メッセージ
            token (str): WebhookURL（非推奨）

        Returns:
            None
        """
        try:
            self.send_message(0, notification_message)
        except KeyError:
            print("token名が間違っています")
            logger.error("Using the wrong token")


# GUIの設定と操作
class WebhookGUI:
    def __init__(self, parent: tk.Tk, webhook: Discord_Notify):
        """
        GUIコンポーネントの初期化

        Args:
            parent (tk.Tk): 親ウィンドウ
            webhook (Discord_Notify): Discord通知オブジェクト
        """
        self.root = parent
        self.webhook = webhook

        self.window: tk.Toplevel = tk.Toplevel(self.root)

        self.window.geometry("200x300")
        self.window.resizable(False, False)  # リサイズ不可に設定
        # 親ウィンドウの最小化、最大化ボタンを無効化
        # self.window.overrideredirect(True)  # 装飾（タイトルバー）を完全に消す

        self.window.title("Discord設定")

        # Webhook選択のラベルとドロップダウンメニュー
        self.label_url = tk.Label(self.window, text="Webhook選択:")
        self.label_url.pack(pady=10)

        self.webhook_options = (
            [webhook["name"] for webhook in self.webhook.webhooks]
            if self.webhook.webhooks
            else [""]
        )
        self.selected_webhook = tk.StringVar(self.window)
        self.selected_webhook.set(
            self.webhook_options[0] if self.webhook_options else ""
        )  # 初期値の設定

        self.dropdown = tk.OptionMenu(
            self.window, self.selected_webhook, *self.webhook_options
        )
        self.dropdown.pack(pady=10)

        # メッセージ送信ボタン
        self.send_button = tk.Button(
            self.window, text="テスト送信", command=self.send_webhook
        )
        self.send_button.pack(pady=10)

        # Webhook追加、更新、削除ボタン
        self.add_button = tk.Button(
            self.window, text="Webhook追加", command=self.add_webhook
        )
        self.add_button.pack(pady=10)

        self.update_button = tk.Button(
            self.window, text="Webhook更新", command=self.update_webhook
        )
        self.update_button.pack(pady=10)

        self.delete_button = tk.Button(
            self.window, text="Webhook削除", command=self.delete_webhook
        )
        self.delete_button.pack(pady=10)

    def send_webhook(self) -> None:
        """
        Webhookを選択してメッセージ送信

        Returns:
            None
        """
        selected_index = self.webhook_options.index(self.selected_webhook.get())
        content = "こんにちは、Discord！"
        self.webhook.send_message(selected_index, content)
        messagebox.showinfo("成功", "メッセージが送信されました！")

    def add_webhook(self) -> None:
        """
        新しいWebhookを追加
        """
        name = simpledialog.askstring(
            "Webhookの名前", "Webhookの名前を入力してください:"
        )
        url = simpledialog.askstring("WebhookのURL", "WebhookのURLを入力してください:")
        if name and url:
            self.webhook.add_webhook(name, url)
            self.refresh_dropdown()

    def update_webhook(self) -> None:
        """
        Webhookを更新
        """
        selected_index = self.webhook_options.index(self.selected_webhook.get())
        name = simpledialog.askstring("Webhookの名前", "新しい名前を入力してください:")
        url = simpledialog.askstring("WebhookのURL", "新しいURLを入力してください:")
        if name and url:
            self.webhook.update_webhook(selected_index, name, url)
            self.refresh_dropdown()

    def delete_webhook(self) -> None:
        """
        Webhookを削除
        """
        selected_index = self.webhook_options.index(self.selected_webhook.get())

        # 削除確認ダイアログを表示
        result = messagebox.askyesno("確認", "本当に削除しますか?")

        if result:  # ユーザーが「はい」を選んだ場合
            self.webhook.delete_webhook(selected_index)
            self.refresh_dropdown()
            messagebox.showinfo("削除完了", "Webhookが削除されました。")
        else:
            messagebox.showinfo("キャンセル", "削除がキャンセルされました。")

    def refresh_dropdown(self) -> None:
        """
        ドロップダウンメニューを更新
        """
        self.webhook_options = (
            [webhook["name"] for webhook in self.webhook.webhooks]
            if self.webhook.webhooks
            else []
        )
        self.selected_webhook.set(
            self.webhook_options[0] if self.webhook_options else ""
        )
        self.dropdown["menu"].delete(0, "end")
        for option in self.webhook_options:
            self.dropdown["menu"].add_command(
                label=option, command=tk._setit(self.selected_webhook, option)
            )


if __name__ == "__main__":
    os.chdir("./SerialController")
    Webhook = Discord_Notify()

    root = tk.Tk()
    gui = WebhookGUI(root, Webhook)

    root.mainloop()
