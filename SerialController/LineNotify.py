import configparser
import cv2
import io
import os

import requests
from PIL import Image
from logging import Logger, getLogger, DEBUG, NullHandler

def _get_token_list(filename: str):
    proxy = _open_file_with_utf8(filename)['LINE']
    return {key: proxy[key] for key in proxy}

def _open_file_with_utf8(filename: str):
    """
    utf-8 のファイルを BOM ありかどうかを自動判定して読み込む
    """
    parser = configparser.ConfigParser(comment_prefixes='#', allow_no_value=True)
    parser.read(filename, 'utf-8-sig' if _is_utf8_file_with_bom(filename) else 'utf-8')
    
    return parser

def _is_utf8_file_with_bom(filename):
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

        self.__res = None
        
        self._logger.debug("Load token file")
        
        TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'line_token.ini')

        self.__camera = camera
        self.__token_list = _get_token_list(TOKEN_FILE)
        self.__token_num = len(self.__token_list)
        # self.line_notify_token = self.token_file['LINE'][token_name]
        self.__headers = [{'Authorization': f'Bearer {token}'} for key, token in self.__token_list.items()]
        self.__res = [requests.get('https://notify-api.line.me/api/status', headers=head) for head in self.__headers]
        self.__status = [responses.status_code for responses in self.__res]
        self.__chk_token_json = [responses.json() for responses in self.__res]

    def __str__(self):
        for stat in self.__status:
            if stat == 401:
                self._logger.error("Invalid token")
                return "LINE Token Check FAILED."
            elif stat == 200:
                self._logger.info("Valid token")
                return "LINE-Token Check OK!"

    def send_text(self, notification_message, token='token'):
        """
        LINEにテキストを通知する
        """
        line_notify_api = 'https://notify-api.line.me/api/notify'
        try:
            headers = {'Authorization': f'Bearer {self.__token_list[token]}'}
            data = {'Message': f'{notification_message}'}
            self.__res = requests.post(line_notify_api, headers=headers, data=data)
            if self.__res.status_code == 200:
                print("[LINE]テキストを送信しました。")
                self._logger.info("Send text")
            else:
                print("[LINE]テキストの送信に失敗しました。")
                self._logger.error("Failed to send text")
        except KeyError:
            print('token名が間違っています')
            self._logger.error('Using the wrong token')

    def send_text_n_image(self, notification_message, token='token'):
        """
        カメラが開いていないときはテキストのみを通知し、
        開いているときはテキストと画像を通知する
        """
        try:
            if self.__camera is None:
                print("Camera is not Opened. Send text only.")
                self.send_text(notification_message)
                return

            image_bgr = self.__camera.readFrame()
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(image_rgb)
            png = io.BytesIO()  # 空のio.BytesIOオブジェクトを用意
            image.save(png, format='png')  # 空のio.BytesIOオブジェクトにpngファイルとして書き込み
            b_frame = png.getvalue()  # io.BytesIOオブジェクトをbytes形式で読みとり

            line_notify_api = 'https://notify-api.line.me/api/notify'
            headers = {'Authorization': f'Bearer {self.__token_list[token]}'}
            data = {'Message': f'{notification_message}'}
            files = {'imageFile': b_frame}
            self.__res = requests.post(line_notify_api, headers=headers, params=data, files=files)
            if self.__res.status_code == 200:
                print("[LINE]テキストと画像を送信しました。")
                self._logger.info("Send image with text")
            else:
                print("[LINE]テキストと画像の送信に失敗しました。")
                self._logger.error("Failed to send image with text")
        except KeyError:
            print('token名が間違っています')
            self._logger.error('Using the wrong token')

    def getRateLimit(self):
        try:
            for i in range(self.__token_num):
                print(f'For: {list(self.__token_list.keys())[i]}')
                print('X-RateLimit-Limit: ' + self.__res[i].headers['X-RateLimit-Limit'])
                print('X-RateLimit-ImageLimit: ' + self.__res[i].headers['X-RateLimit-ImageLimit'])
                print('X-RateLimit-Remaining: ' + self.__res[i].headers['X-RateLimit-Remaining'])
                print('X-RateLimit-ImageRemaining: ' + self.__res[i].headers['X-RateLimit-ImageRemaining'])
                import datetime
                dt = datetime.datetime.fromtimestamp(int(self.__res[i].headers['X-RateLimit-Reset']),
                                                     datetime.timezone(datetime.timedelta(hours=9)))
                print('Reset time:', dt, '\n')

                self._logger.info(f"LINE API - Limit: {self.__res[i].headers['X-RateLimit-Limit']}")
                self._logger.info(f"LINE API - Remaining: {self.__res[i].headers['X-RateLimit-Remaining']}")
                self._logger.info(f"LINE API - ImageLimit: {self.__res[i].headers['X-RateLimit-Limit']}")
                self._logger.info(f"LINE API - ImageRemaining: {self.__res[i].headers['X-RateLimit-ImageRemaining']}")
                self._logger.info(f"Reset time: {dt}")
        except AttributeError as e:
            self._logger.error(e)
            pass
        except KeyError as e:
            self._logger.error(e)
            pass


if __name__ == "__main__":
    '''
    status  HTTPステータスコードに準拠した値
       200  成功時
       401  アクセストークンが無効
    '''
    LINE = Line_Notify()
    print(LINE)
    LINE.getRateLimit()
