"""SerialController パッケージの起動口。

`python -m SerialController`（リポジトリ直下から）で起動できる。
アプリ本体は SerialController/ を sys.path に置く前提なので、
先に場所を足してから Window.main へ渡す。`python Window.py`
（launcher.py が使う従来経路）もそのまま動く。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Window import main

if __name__ == "__main__":
    main()
