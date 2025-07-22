import configparser
from typing import Any, Optional
import cv2
import io
import os

import requests
from PIL import Image
from loguru import logger


class Line_Notify:
    def __init__(self, camera: Optional[Any] = None, token_name: str = "token"):
        try:
            self.res: list[requests.Response] | requests.Response | None = None
            self.token_file = configparser.ConfigParser(
                comment_prefixes="#", allow_no_value=True
            )
            self.open_file_with_utf8()
            self.camera = camera
            self.token_list = {
                key: self.token_file["LINE"][key] for key in self.token_file["LINE"]
            }
            self.token_num = len(self.token_list)
            # self.line_notify_token = self.token_file['LINE'][token_name]
            self.headers = [
                {"Authorization": f"Bearer {token}"}
                for key, token in self.token_list.items()
            ]
            self.res = [
                requests.get("https://notify-api.line.me/api/status", headers=head)
                for head in self.headers
            ]
            self.status = [responses.status_code for responses in self.res]
            self.chk_token_json = [responses.json() for responses in self.res]
        except Exception:
            pass

    def open_file_with_utf8(self) -> None:
        """
        utf-8 のファイルを BOM ありかどうかを自動判定して読み込む
        """
        line_token_path = os.path.join(os.path.dirname(__file__), "line_token.ini")
        is_with_bom = self.is_utf8_file_with_bom(line_token_path)

        encoding = "utf-8-sig" if is_with_bom else "utf-8"

        logger.debug("Load token file")
        self.token_file.read(line_token_path, encoding)

    def is_utf8_file_with_bom(self, filename: str) -> bool:
        """
        utf-8 ファイルが BOM ありかどうかを判定する
        """
        line_first = open(filename, encoding="utf-8").readline()
        return line_first[0] == "\ufeff"

    def __str__(self) -> str:
        for stat in self.status:
            if stat == 401:
                logger.error("Invalid token")
                return "LINE Token Check FAILED."
            elif stat == 200:
                logger.info("Valid token")
                return "LINE-Token Check OK!"
            else:
                return (
                    "LINE通知はサービス終了しました。\nDiscord通知を利用してください。"
                )
        return ""

    def send_text(self, notification_message: str, token: str = "token") -> None:
        """
        LINEにテキストを通知する
        """
        line_notify_api = "https://notify-api.line.me/api/notify"
        try:
            headers = {"Authorization": f"Bearer {self.token_list[token]}"}
            data = {"Message": f"{notification_message}"}
            self.res = requests.post(line_notify_api, headers=headers, data=data)
            if self.res.status_code == 200:
                print("[LINE]テキストを送信しました。")
                logger.info("Send text")
            else:
                print("[LINE]テキストの送信に失敗しました。")
                logger.error("Failed to send text")
        except KeyError:
            print("token名が間違っています")
            logger.error("Using the wrong token")

    def send_text_n_image(
        self, notification_message: str, token: str = "token"
    ) -> None:
        """
        カメラが開いていないときはテキストのみを通知し、
        開いているときはテキストと画像を通知する
        """
        try:
            if self.camera is None:
                print("Camera is not Opened. Send text only.")
                self.send_text(notification_message)
                return

            image_bgr = self.camera.readFrame()
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(image_rgb)
            png = io.BytesIO()  # 空のio.BytesIOオブジェクトを用意
            image.save(
                png, format="png"
            )  # 空のio.BytesIOオブジェクトにpngファイルとして書き込み
            b_frame = png.getvalue()  # io.BytesIOオブジェクトをbytes形式で読みとり

            line_notify_api = "https://notify-api.line.me/api/notify"
            headers = {"Authorization": f"Bearer {self.token_list[token]}"}
            data = {"Message": f"{notification_message}"}
            files = {"imageFile": b_frame}
            self.res = requests.post(
                line_notify_api, headers=headers, params=data, files=files
            )
            if self.res.status_code == 200:
                print("[LINE]テキストと画像を送信しました。")
                logger.info("Send image with text")
            else:
                print("[LINE]テキストと画像の送信に失敗しました。")
                logger.error("Failed to send image with text")
        except KeyError:
            print("token名が間違っています")
            logger.error("Using the wrong token")

    def getRateLimit(self) -> None:
        try:
            pass
            # print("LINE通知はサービス終了しました")
            # if isinstance(self.res, requests.Response):
            #     if self.res.status_code == 410:
            #         print("LINE Notify is Out of Service.")
            #         return
            # if self.res is None or not isinstance(self.res, list):
            #     return
            # for i in range(self.token_num):
            #     print(f"For: {list(self.token_list.keys())[i]}")
            #     print("X-RateLimit-Limit: " + self.res[i].headers["X-RateLimit-Limit"])
            #     print(
            #         "X-RateLimit-ImageLimit: "
            #         + self.res[i].headers["X-RateLimit-ImageLimit"]
            #     )
            #     print(
            #         "X-RateLimit-Remaining: "
            #         + self.res[i].headers["X-RateLimit-Remaining"]
            #     )
            #     print(
            #         "X-RateLimit-ImageRemaining: "
            #         + self.res[i].headers["X-RateLimit-ImageRemaining"]
            #     )
            #     import datetime

            #     dt = datetime.datetime.fromtimestamp(
            #         int(self.res[i].headers["X-RateLimit-Reset"]),
            #         datetime.timezone(datetime.timedelta(hours=9)),
            #     )
            #     print("Reset time:", dt, "\n")

            #     logger.info(
            #         f"LINE API - Limit: {self.res[i].headers['X-RateLimit-Limit']}"
            #     )
            #     logger.info(
            #         f"LINE API - Remaining: {self.res[i].headers['X-RateLimit-Remaining']}"
            #     )
            #     logger.info(
            #         f"LINE API - ImageLimit: {self.res[i].headers['X-RateLimit-Limit']}"
            #     )
            #     logger.info(
            #         f"LINE API - ImageRemaining: {self.res[i].headers['X-RateLimit-ImageRemaining']}"
            #     )
            #     logger.info(f"Reset time: {dt}")
        except AttributeError as e:
            logger.error(e)
            pass
        except KeyError as e:
            logger.error(e)
            pass
        finally:
            logger.debug("LINE Notify is Out of Service")


if __name__ == "__main__":
    """
    status  HTTPステータスコードに準拠した値
       200  成功時
       401  アクセストークンが無効
    """
    LINE = Line_Notify()
    print(LINE)
    LINE.getRateLimit()
