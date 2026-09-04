"""Pico 2 W ファームの疎通確認（PC から UART 経由で叩く）。

★本家 Poke-Controller を通さず、素の pyserial だけで Pico の応答を見る道具。
★Switch へ挿したまま実行してよい（PC とは UART で繋がっているため）。

使い方:
    python pico_test.py            # A ボタンを15回押す
    python pico_test.py --port COM4
"""

import argparse
import time

import serial

# ---------------------------------------------------------------------------
# ボタンの値は switch_controller_plus.h の enum Button と同じ。
# ★16進の桁を目で数えると取り違えるので、必ず名前で指定する。
#   （0x0010 は A ではなく L。手で書くと間違えやすい）
# ---------------------------------------------------------------------------
BTN = {
    "Y": 0x0001,
    "B": 0x0002,
    "A": 0x0004,
    "X": 0x0008,
    "L": 0x0010,
    "R": 0x0020,
    "ZL": 0x0040,
    "ZR": 0x0080,
    "MINUS": 0x0100,
    "PLUS": 0x0200,
    "LCLICK": 0x0400,
    "RCLICK": 0x0800,
    "HOME": 0x1000,
    "CAPTURE": 0x2000,
}

HAT_CENTER = 8
STICK_CENTER = 0x80

# ★Pico 側 pico_main.c の #define BAUD_RATE と必ず同じにする。
BAUD_RATE = 115200


def drain(ser, label=""):
    """Pico が返した行をすべて読み捨てる（内容は表示する）。

    ★読まずに送り続けると受信バッファが溜まり続けるので、毎回捨てる。
    ★ERR が返っていれば書式が違うと分かるので、黙って捨てずに表示する。
    """
    data = ser.read_all()
    if not data:
        return
    for line in data.decode("ascii", "replace").splitlines():
        line = line.strip()
        if line:
            print(f"  <- {line}{('  ' + label) if label else ''}")


def send_state(
    ser,
    btn=0,
    hat=HAT_CENTER,
    lx=STICK_CENTER,
    ly=STICK_CENTER,
    rx=STICK_CENTER,
    ry=STICK_CENTER,
):
    """姿勢を1行送る。書式は pico_main.c の parse_state_line と対。

    "S <btn16> <hat> <lx> <ly> <rx> <ry>" をすべて16進で送る。
    """
    line = f"S {btn:04x} {hat:x} {lx:02x} {ly:02x} {rx:02x} {ry:02x}\n"
    ser.write(line.encode("ascii"))
    return line.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--button", default="A", choices=sorted(BTN))
    ap.add_argument("--times", type=int, default=15)
    args = ap.parse_args()

    with serial.Serial(args.port, BAUD_RATE, timeout=1) as ser:
        # 起動直後の READY を拾う。ここで何も来なければ配線か速度が違う。
        time.sleep(0.3)
        drain(ser, "(起動時)")

        # 死活確認。PONG が返れば往復できている。
        ser.write(b"P\n")
        time.sleep(0.2)
        drain(ser, "(P への応答。PONG なら疎通 OK)")

        code = BTN[args.button]
        print(f"-- {args.button} (0x{code:04x}) を {args.times} 回押します --")
        for i in range(args.times):
            print(f"[{i + 1}/{args.times}] -> " + send_state(ser, btn=code))
            time.sleep(0.2)
            send_state(ser)  # 離す（全項目を中立で送り直す）
            time.sleep(0.3)
            drain(ser)

        # 終わったら必ず全解放。押しっぱなしのまま終わらせない。
        ser.write(b"N\n")
        time.sleep(0.2)
        drain(ser, "(N への応答。OK なら中立化できた)")
        print("-- 終了。中立化を送りました --")


if __name__ == "__main__":
    main()
