import configparser
from datetime import datetime, timezone
from typing import Dict, Optional
import cv2
import io
from os import path

import requests
from PIL import Image
from logging import getLogger, DEBUG, NullHandler

endpoints = {
    "status": "https://notify-api.line.me/api/status",
    "notify": "https://notify-api.line.me/api/notify"
}

class _Token:
    """
    LINE Notifyのトークンを表す
    """
    def __init__(self, token: str) -> None:
        self.__token = token

    def get_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.__token}"}

    def get_status(self, tz=datetime.utcnow().astimezone().tzinfo):
        class _Status():
            """
            トークンの利用状況を表す
            """
            def __init__(self, token: _Token, res: requests.Response, tz: timezone) -> None:
                self.__token = token

                res.raise_for_status()
                self.__status_code = res.status_code
                self.__limit = res.headers["X-RateLimit-Limit"]
                self.__image_limit = res.headers["X-RateLimit-ImageLimit"]
                self.__remaining = res.headers["X-RateLimit-Remaining"]
                self.__image_remaining = res.headers["X-RateLimit-ImageRemaining"]
                self.__reset = datetime.fromtimestamp(int(res.headers["X-RateLimit-Reset"]), tz)

            @property
            def token(self) -> _Token:
                return self.__token
            @property
            def status_code(self) -> int:
                return self.__status_code
            @property
            def limit(self) -> str:
                return self.__limit
            @property
            def image_limit(self) -> str:
                return self.__image_limit
            @property
            def remaining(self) -> str:
                return self.__remaining
            @property
            def image_remaining(self) -> str:
                return self.__image_remaining
            @property
            def reset(self) -> datetime:
                return self.__reset

        res = requests.get(endpoints["status"], headers=self.get_header())
        
        # デフォルトのタイムゾーンをtzinfoから取得できない場合は、utcを用いる
        if tz is None:
            tz = timezone.utc

        return _Status(self, res, tz)
    
class _Message:
    """
    LINE Notifyで送信するメッセージを表す
    """
    def __init__(self, message: str) -> None:
        self.__message = message

    def get_header(self) -> Dict[str, str]:
        return {'Message': f'{self.__message}'}

def _convert_image(image_bgr: cv2.Mat):
    """
    cv2.Matをbytesに変換する
    """
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(image_rgb)
    png = io.BytesIO()  # 空のio.BytesIOオブジェクトを用意
    image.save(png, format='png')  # 空のio.BytesIOオブジェクトにpngファイルとして書き込み
    return png.getvalue()  # io.BytesIOオブジェクトをbytes形式で読みとり

class _Image:
    """
    LINE Notifyで送信する画像を表す
    """
    def __init__(self, mat: cv2.Mat) -> None:
        self.__bytes = _convert_image(mat)

    def get_header(self) -> Dict[str, bytes]:
        return {'imageFile': self.__bytes}

def _get_tokens_from(filename: str) -> Dict[str, _Token]:
    """
    指定されたファイルからトークンの辞書を取得する
    """
    parser = configparser.ConfigParser(comment_prefixes='#', allow_no_value=True)
    parser.read(filename, 'utf-8-sig' if _is_utf8_file_with_bom(filename) else 'utf-8')
    proxy = parser['LINE']
    return {key: _Token(proxy[key]) for key in proxy}

def _is_utf8_file_with_bom(filename) -> bool:
    """
    utf-8 ファイルが BOM ありかどうかを判定する
    """
    with open(filename, encoding='utf-8') as f:
        line_first = f.readline()
    return line_first[0] == '\ufeff'

class Line_Notify:

    def __init__(self, camera=None, token_name='token'):
        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True
        
        self.__camera = camera

        self._logger.debug("Load token file")
        TOKEN_FILE = path.join(path.dirname(__file__), 'line_token.ini')
        self.__tokens = _get_tokens_from(TOKEN_FILE)

    def __str__(self):
        try:
            _ = [token.get_status() for token in self.__tokens.values()]
        except:
            self._logger.error("Invalid token")
            return "LINE Token Check FAILED."
        
        self._logger.info("Valid token")
        return "LINE-Token Check OK!"

    def __send(self, token_key: str, notification_message: str, image: Optional[_Image]=None):
        target = {
            "jpn": "テキスト" if image is None else "テキストと画像",
            "eng": "text" if image is None else "image with text"
        }
        try:
            token = self.__tokens[token_key]
        except KeyError:
            print(f"指定されたトークンは登録されていません: {token_key}")
            self._logger.error(f"Using the wrong token_key: {token_key}")
            return
        message = _Message(notification_message)

        try:
            if image is None:
                res = requests.post(endpoints["notify"], headers=token.get_header(), data=message.get_header())
            else:
                res = requests.post(endpoints["notify"], headers=token.get_header(), params=message.get_header(), files=image.get_header())
            res.raise_for_status()

        except:
            print(f"[LINE]{target['jpn']}の送信に失敗しました。")
            self._logger.error(f"Failed to send {target['eng']}")
            return
        
        print(f"[LINE]{target['jpn']}を送信しました。")
        self._logger.info(f"Send {target['eng']}")

    def send_text(self, notification_message, token_key='token'):
        """
        LINEにテキストを通知する
        """
        self.__send(token_key, notification_message)
        
    def send_text_n_image(self, notification_message, token_key='token'):
        """
        カメラが開いていないときはテキストのみを通知し、
        開いているときはテキストと画像を通知する
        """
        if self.__camera is None:
            print("Camera is not Opened. Send text only.")
            self.send_text(notification_message, token_key)
            return
        
        self.__send(token_key, notification_message, _Image(self.__camera.readFrame()))

    def getRateLimit(self):
        for key, token in self.__tokens.items():
            try:
                status = token.get_status()
            except:
                # self._logger.error(f"Invalid token: {key}")
                continue

            print(f"For: {key}")
            print(f"X-RateLimit-Limit: {status.limit}")
            print(f"X-RateLimit-ImageLimit: {status.image_limit}")
            print(f"X-RateLimit-Remaining: {status.remaining}")
            print(f"X-RateLimit-ImageRemaining: {status.image_remaining}")
            print(f"X-RateLimit-Reset: {status.reset}")
            print()

            self._logger.info(f"LINE API - Limit: {status.limit}")
            self._logger.info(f"LINE API - ImageLimit: {status.image_limit}")
            self._logger.info(f"LINE API - Remaining: {status.remaining}")
            self._logger.info(f"LINE API - ImageRemaining: {status.image_remaining}")
            self._logger.info(f"LINE API - Reset: {status.reset}")

if __name__ == "__main__":
    '''
    status  HTTPステータスコードに準拠した値
       200  成功時
       401  アクセストークンが無効
    '''
    LINE = Line_Notify()
    print(LINE)
    LINE.getRateLimit()
