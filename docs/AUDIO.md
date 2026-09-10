# 音声対応（取込・検知・モニター再生）

キャプチャボードの音声を取り込み、特定の音を自動化のトリガーにしたり、
ゲーム音をPCから再生（モニター）したりできるようにする。

## 前提

- ゲーム音はキャプチャボードからしか取れない。PCのマイク等ではSwitchの
  音は取れない（配線上の制約）。
- 開けないデバイスがある。WDM-KS系は PortAudio の制約で開けない。
  48kHz専用機は本体が自レートで開いて内部44.1kHzへ直す。
- 旧スクリプト `Commands/PythonCommands/listen_shiny.py`（PyAudio直掴み・
  AUXケーブル前提）もそのまま動く。新規は下記の本体機能を使うこと。

## 画面の使い方（Audio欄）

- **Input**: 取込口。空なら起動時にキャプチャボードを自動選択する
  （カメラ名から推定）。手で選び直すこともできる。候補は試し開きで
  「開ける物だけ」に絞られる（起動直後は全件→数秒後に確定）。
- **Output**: モニター再生の出力先。表示の `[est. XXms]` は申告遅延の目安。
- **Monitor**: チェックでゲーム音をPCから再生する。既定OFF。
  約120ms遅れて聞こえる（実測。機器で変わる）。
- **Level**: 直近の入力レベル。振れていれば取り込めている。
- **Test Rec 3s**: 3秒録って `AudioClips/` へ保存する。疎通確認用。
- **遅延計測**: Aボタンを3回押してSwitchを鳴らし、押下→検知の実遅延を
  測る。操作確認画面など「押すと音が出る画面」で使うこと。
  シリアル未接続では測れない。

## コマンド開発者向け

`AudioPythonCommand` を継承すると音声APIが使える
（`ImageProcPythonCommand` の音声版。両方要る場合は多重継承）。

- `waitTone(bands, thresholds, window_s, timeout)` —
  指定帯域がすべて閾値を超えるまで待つ。例は
  `Commands/PythonCommands/listen_shiny2.py`（平常時を測って
  自動較正する書き方）。
- `waitSound(template_wav, threshold, ...)` —
  登録音（`Template/audio/<pack>/*.wav`）との一致待ち。
- `recordClip(seconds, name)` — 直近の録音を `AudioClips/` へ保存する。
- `onSoundDetected(...)` / `watchSounds(...)` — 検知時コールバック駆動。

しきい値は機種・音量で変わるため、絶対値の決め打ちではなく
listen_shiny2.py のように平常時比で決めること。
利用者スクリプトの公開面（`task userapi`）の範囲内で書くこと。
`band_power` / `is_tone` は `Commands.CommandAudio` から読める。

## 制限・既知事項

- モニター再生は約120ms遅れる。用途は確認用であり実況用ではない。
- 申告遅延（est）は機器の自己申告であり、実測とずれる場合がある。
  出力選びの目安にし、迷ったら遅延計測の実測と聞き比べで決めること。
- 録音・検知の内部レートは44.1kHz monoに統一している。
- 音量が小さい入力では検知の感度が落ちる。まずLevel表示で確認すること。
