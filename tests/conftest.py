"""pytest の土台。SerialController を import できるようにする。

アプリ本体は SerialController/ を sys.path に置いて動く前提なので、
テストも同じ置き方にする（パッケージ改名は Phase 2 以降の話）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))
