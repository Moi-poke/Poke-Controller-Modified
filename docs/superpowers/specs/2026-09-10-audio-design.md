# 音声対応（取込・FFT検知・類似音検知・モニター再生） 設計書

- 日付: 2026-09-10
- ブランチ: `feature/audio-capture`（worktree `C:\PokeCon\Poke-Controller-Audio` で作業。`release/v4.0` 上の別セッション作業と非干渉）
- 背景: 現在はキャプチャボードの映像のみ取得でき、音声は扱えない。`listen_shiny.py` が PyAudio 直掴み＋numpy FFT の前例だが、AUXケーブル等の回避策が必須で、アプリ本体の機能ではない。
- 方式: `core/Camera.py` の「単一open・最新だけ配る」常駐パターンを音声に移植。入力1ストリームを取込・検知・モニター再生で共有する。

## 1. 目的

特定の音（色違いの `ｷﾗｰﾝ` 等）を自動化のトリガーにできるようにする。ゲーム音をツールから再生（モニター）できるようにする。機種・配線によらずデバイス選択で切り替えられるようにする。

## 2. スコープ

- 本書: ①取込（デバイス選択＋常駐キャプチャ）②FFT検知（帯域パワー＋デュアルバンド判定）③類似音検知（wavテンプレートとのマッチ）④出力（モニター再生トグル既定OFF）。利用者向けAPI（AudioMixin）＋設定＋GUI＋テスト。
- やらないこと: 音声の録画ファイル化（録音クリップ保存は検知の補助として含む）、ノイズキャンセル・イコライザ等の音質加工、ネットワーク配信、既存 `listen_shiny.py` の削除（共存し、後継APIへの移行例として残す）。

## 3. アーキテクチャ

```
利用者スクリプト (Commands/PythonCommands/)
        │  AudioMixin（waitTone / waitSound / recordClip / 検知コールバック）
        ▼
core/CommandAudio.py ── core/audio_dsp.py（純粋関数: FFT帯域・相互相関）
        │  最新ウィンドウの読出し（スナップショット複製）
        ▼
core/AudioCapture.py …… GUI非依存。入力1ストリーム所有＋リングバッファ
        │  sounddevice（Win wheelにPortAudio同梱想定。欠如時は無効化）
        ▼
services/audio_service.py …… 接続・切替・寿命管理（serial_service対応）
        ▼
ui/audio_panel.py …… 入出力デバイス選択＋モニタートグル＋レベル表示
```

- Camera対応表: `Camera.openCamera/destroy/readFrame` → `AudioCapture.openInput/close/readWindow`。`LatestFrame`（最新1枚）→ リングバッファ（直近N秒。検知は「窓」が必要なため1点ではなく区間を持つ）。
- `core/` は tkinter・アプリ層 import 禁止（`task bounds` 対象）。`services/` も tkinter 禁止。`ui/` から `Window` 本体の import 禁止。`Commands.*` 公開面の追加は `tools/check_user_api.py` の許可集合更新を伴う。

## 4. コンポーネント

- `core/AudioCapture.py`: 入力デバイス列挙（`query_devices` の薄い包み。例外時は空＋ログ）、`openInput(device) -> bool`、`close()`、リングバッファ（44100Hz mono float32、既定5秒＝約0.9MB。`threading.Lock`＋単調書込）、`readWindow(seconds) -> np.ndarray | None`（複製を返す。PortAudioスレッドの配列を外へ漏らさない。`seconds` はリング長以下であること）、`peak()/rms()`（レベルメーター用）、モニター送出（出力ストリーム。入力コールバック内で複製を出力側へ受渡し。キュー深さ1で遅延を溜めない。既定OFF）。デバイス抜去・open失敗は False＋ログで返し、例外で落とさない（AGENTS.md の graceful 方針）。
- `core/audio_dsp.py`: tkinter・sounddevice 非依存の純粋関数。`band_power(x, rate, lo, hi)`（窓関数＋rFFT。listen_shiny の 3100Hz/4200Hz デュアルバンド判定を一般化）、`is_tone(x, rate, bands, thresholds)`、`match_template(x, rate, wav)`（正規化相互相関。`scipy` は既存依存。サンプルレート不一致は `scipy.signal.resample` で吸収）、`load_wav_mono(path)`（wav読込→mono float32化。対応形式は wave モジュール範囲＋後追いで拡張可）。全てヘッドレスでpytest可能。
- `services/audio_service.py`: `AudioCapture` の所有・デバイス切替・終了時の `close()`。`serial_service.py` 対応（接続→切断→再接続の手順だけ。tkinterなし）。
- `ui/audio_panel.py`: `CameraPanelMixin` 対応の `AudioPanelMixin`。入力／出力デバイスの Combobox（列挙失敗時は手入力可の Entry へ-gradeful 縮退）、モニターのトグル（既定OFF。ON時は出力デバイス必須）、レベル表示（`after()` 駆動のポーリング。ワーカースレッドからwidgetを触らない）、テスト録音ボタン（`recordClip` のGUI版。保存先は `AudioClips/`）。
- `core/CommandAudio.py`＋`Commands/CommandAudio.py` シム（`from core.CommandAudio import AudioMixin as AudioMixin` 形式の再公開のみ）: `AudioMixin`。`_initAudio(audio)` で状態受領（VisionMixin の `_initVision` 対応）。待ち系（`wait` / `_deadline`）は継承側（`OperateMixin` 経由）が用意する前提で、型宣言のみ持つ（VisionMixin と同型）。具象クラスは `AudioPythonCommand(PythonCommand, AudioMixin)`（`ImageProcPythonCommand` 対応。画像＋音声併用は多重継承で各自合成）。API は画像認識の書き心地に寄せる:
  - `isTonePresent(freq, level_dbfs, band_hz=200, window_s=1.5) -> bool`（`level_dbfs` はdBFS閾値）
  - `waitTone(..., timeout=10.0, interval=0.2) -> bool`（期限は `_deadline()` で測る。一時停止中の時計進行問題を VisionMixin と同じく避ける）
  - `isSoundPresent(template_wav, threshold=0.8, window_s=3.0) -> bool`
  - `waitSound(..., timeout=10.0, interval=0.2) -> bool`
  - `recordClip(seconds, name) -> str`（保存先パスを返す。`saveFrame` 対応。日時＋ミリ秒で上書き防止）
  - `onSoundDetected(spec, callback, cooldown_s=5.0)`（検知時コールバック。ポーリングループを持たないコマンド用。Discord通知は `self.Discord` 経由の `_notifySound(name)` に寄せる）
  - テンプレートwavの置き場規約: 画像の `Template/<pack>/` に倣い `Template/audio/<pack>/*.wav`。相対解決は `_get_template_filespec` と同規則。
- 設定: `[Audio]` 新設（`input_device` / `output_device` / `monitor_enabled=False` / `monitor_volume=0.8`）。既定・補正は `config.py`（`default_sections`＋`complete_missing` に追記。`monitor_volume` は0.0〜1.0に収める）。`GuiSettings` は鏡＋IOのみ。既存iniは補正で読める（書式凍結維持）。
- 依存: `sounddevice` を `pyproject.toml` へ追加（`uv.lock` 更新）。`pyaudio` は利用者スクリプト互換のため `check_user_api` 許可維持（本体は使わない）。Linux/mac は system PortAudio 欠如時に import 失敗→音声機能全体を無効化＋案内ログ（クラッシュさせない）。

## 5. データフロー

- 取込: PortAudio入力コールバック → リングバッファへ追記（Lock内はmemcpyのみ）→ 検知・メーターは `readWindow()` の複製を読む。
- 検知: コマンドの `waitTone/waitSound` → `readWindow()` → `audio_dsp` 純粋関数 → 命中で True（`waitTemplate` 対応）。期限切れは False（例外にしない）。
- モニター: ON時のみ入力コールバックの複製を出力ストリームへ（深さ1キュー。溢れたら捨てる＝遅延を溜めない。Camera の queue-of-1 対応）。
- 終了: `exit` 時に service 経由で `close()`（Camera の `destroy` 対応。join は短時間＋切上げ）。

## 6. エラー処理

- デバイスなし・列挙失敗: 空リスト＋ログ。GUIは縮退表示、コマンド側は `RuntimeError("音声デバイスが開いていません")` で言い切る（`_readFrameOrRaise` 対応）。
- open失敗・抜去: False＋ログ。既存の検知ループは False を返して打ち切れる（無限再試行しない）。
- PortAudio/sounddevice欠如: import guard で無効化。設定・GUIは「音声なし」表示。
- 出力デバイス未選択でモニターON: ONにしない＋本人向けメッセージ（`saveCapture` の print方針対応）。
- サンプルレート不一致（テンプレートwav）: resampleで吸収。吸収不能な形式は理由付き例外。

## 7. テスト・成功基準

- 自動（実機不要）: `audio_dsp` の合成波テスト（既知正弦波の帯域検知、無音時の非検知、テンプレート自己相関≒1・無相関≒0、resample一致）、`AudioCapture` の fake ストリームによるリングバッファ・readWindow複製性・close後挙動、`audio_service` の状態遷移、config の `[Audio]` 補正。Gate全緑（ruff＋format＋mypy＋bounds＋userapi＋pytest）。
- 手動（実機）: USBオーディオ選択→レベルメーター振れ→モニターONで遅延体感→色違い音相当のテストトーンで `waitTone` 命中→抜去時の縮退表示。
- 成功基準: 自動Gate緑＋手動の 取込→検知→再生 の一巡確認。

## 8. 影響範囲・制約

- 触るファイル: `core/AudioCapture.py`・`core/audio_dsp.py`・`core/CommandAudio.py`（新規）、`services/audio_service.py`（新規）、`ui/audio_panel.py`（新規）、`Commands/*` シム（再公開のみ）、`config.py`・`Settings.py`（`[Audio]`分）、`Window.py`（組立のみ）、`tools/check_user_api.py`（`ALLOWED_COMMANDS_SUBS` へ `CommandAudio` 追加＋`THIRD_PARTY` へ `sounddevice` 追加）、`pyproject.toml`・`uv.lock`、`tests/test_audio_*.py`。
- 触らないもの: 送信経路（serial/transport）、画像認識、既存コマンドの挙動、`settings*.ini` の既存項目。
- 規約: `SerialController/` 起点の絶対import、相対import禁止。コメント・利用者文面は日本語。4スペース。資源パスは `BASE_DIR`/`APP_DIR` 起点。ワーカースレッドからtkinterを触らない。Python>=3.12。
- 運用: 本ブランチでは音声以外の変更をしない。`release/v4.0` へのマージ方針・時期は別途判断（Blockly作業と独立にレビュー可能な粒度を保つ）。

## 9. 決定記録

- 常駐単一open（A案）。オンデマンドopenの競合・二重管理を避ける。
- 基盤は sounddevice（numpy直結・duplex向き）。pyaudioは互換維持。
- モニター既定OFF（ハウリング・遅延・無音環境の混乱回避）。
- APIは Vision対応＋イベント駆動（`waitTone/waitSound`＋`onSoundDetected`＋Discord連携）。
- 録音44.1kHz mono float32、リング5秒。テンプレは `Template/audio/`。
- 仕様書承認後に `writing-plans` で実装計画化する。
