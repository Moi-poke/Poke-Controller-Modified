# pico-firmware（新設計・wakeCon参考）

PokeConからSwitchへ入力を届けるPico用ファームウェアの新設計置き場。
PC側とのプロトコル互換は維持しない（破棄可）。

## 位置づけ

- 削除済みの `pico_firmware/`（旧Q/Rキュー機・superseded）とは別物である。
  旧フォルダは未使用のため削除した。名前の綴り（ハイフン）で区別する。
- 参考実装: 別リポジトリ `Moi-poke/pico-wakeCon`（プロトコル参照）。
  現行線の仕様: UART0 115200 8N1＋USB CDC、CRLF、`S <6hex>` 入力（無応答）、
  `N`（応答待ちなし）、`O <4hex> → color ...` 応答、200ms watchdog、
  Mモニタ2行目 `press/ok/ng`（累積・差分評価）。
- 被験環境は有線モード（`usb en=1`、`cid=0`）。有線USB入力は10msタスク駆動。

## 要求（優先順。実測根拠は `docs/superpowers/specs/2026-09-12-live-input-scheduler-design.md` §5）

1. UART受信の割込み化＋リングバッファ（ポーリング＋32B FIFOの溢れ根治）。
   目標: 素シリアル125Hzフルレート連送で `ng +0`（現行: 12ms間隔で `ng +12/20試行`）。
2. 有線USBタスク10msの短縮（可能な範囲で。HIDレポート間隔の安定化・125Hz目標）。
3. BT接続間隔との整合確認（BT使用時）。
4. プロトコルは新設計可。推奨: 行単位テキスト＋CRLF維持、M相当の計数チャネル維持、
   watchdog相当の維持信号（<200ms）。バイナリ化時は固定長＋連番＋CRC。
5. 受入: (a) 125Hz連送 `ng +0`、(b) burst 50ms/100ms×240でM press差分240、
   (c) E4遅延分布の改善。ビルド＋CTest＋フラッシュ＋回帰を毎回。

## 運用

- このフォルダはPython系gate（`task ci`・pre-commit）の対象外である。
  C用CIは別ワークフローで足す（未整備）。
- FW安定後は別リポジトリへの分割を検討する（`finishing-a-development-branch`で裁定）。

## 未確定

- 基板（RP2040 / Pico W / wakeCon基板か）、主経路（有線優先かBT優先か）。
- 詳細引継ぎ: `C:\Users\moilo\AppData\Local\Temp\opencode\handoff-firmware-newrepo.md`
  （別セッションが読む想定。決まり次第ここへ転記する）。
