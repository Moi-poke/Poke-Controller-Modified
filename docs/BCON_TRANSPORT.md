# BCON通信（接続・設定・計測）

## 前提

`bcon` は Pico 2 W 用のバイナリ通信方式である。テキ
スト行を送る従来方式と違い、姿勢を STATE バイナリ
に組んで送り、応答の STATUS / PONG / ACK を読む。
Pico 側と版を合わせる必要があり、接続には `HELLO`
による握手が必須である。既定 baud は 1Mbps で、Pico
側は認証後に BCBR へ永続保存する。

相手で使い分ける。本家 Leonardo には `legacy_text`
を使う。`switch-bcon` ファームを載せた Pico 2 W に
繋ぐときだけ `switch-bcon` を選ぶ（旧名 `bcon` も
受け付ける）。それ以外では変える必要はない。

## 接続方式の選択と接続

通信方式は `register_transport` の登録簿で管理する。
組み込みは `legacy_text`、`switch-bcon`、
`switch-bcon-proc` の 3
件である。メイン画面の **Transport:** 欄は
`list_transports` の一覧をそのまま候補に並べ、選んだ
名前を `resolve_transport` で確定する。

`switch-bcon-proc` は送出路の別プロセス版である。操作面は
`switch-bcon` と同じで、120Hz送出・受信・会話opを子プロセス
で回し、GUIのGIL・描画負荷から切り離す。送出タイミングの
ばらつき（p99）が小さくなる。子が死んだ呼出しは安全既定値
（False/None）で返し、開き直しで復帰する。

起動時の優先順位は `--transport` が最上位で、次が設
定ファイルの `[Transport] name` である。`--transport`
を付けると設定を書き換えずにその場限りで試せる。名
前が空・未知のときは `legacy_text` に戻る。

接続は二段階である。まずメイン画面の接続操作で線を
開くが、この時点では `HELLO` を送らない。次に
「メニュー」→「コマンド」→「**Bcon設定**」で小窓を
開き、**接続(HELLO→開始)** を押す。押すと `STATE`
送出を止めてから `HELLO` を送り、応答が返って初めて
live 送出を再開する。`HELLO` の前に `STATE` を流さな
い順序が決まりであり、`HELLO` 不通のときは
`NEUTRAL` を保ったまま止まる。

baud の探索・切替は公開 API の `baud_hunt` と
`set_baud_index` が担い、画面上のボタンは無い。

## BconSetup画面の使い方

「メニュー」→「コマンド」→「**Bcon設定**」で開く小窓だ。窓題は
「**Bcon設定**」である。前提としてシリアルを開いておくこと。
冒頭の説明文にも開→HELLO→live開始の順と再HELLOの注意が出る。
操作はボタンを押すだけだ。実行中は全ボタンが無効になる。

- **接続(HELLO→開始)**
  STATEを止めてHELLOを送り、通ったらliveを起こす。
- **取込開始**
  横の取込秒数spinbox(1-60秒、初期値15)で秒数を決め、
  CAPTURE_STARTを送る。範囲外は送らず注意だけ出す。
  取込期限が来たら保存の有無を1行出す（保存時は
  「取込を保存しました。」）。
- **BEACON再生**
  BEACON_STARTを送り、保存済み入力の再生を始める。
  未保存時は取込を促す。
- **状態確認**
  STATUS_REQを送り、flagsやerrcodeとPLAYER_INFOを読む。
  ランプ表示も最新へ引き直す。
- **RUMBLE表示**
  直近の振動出力（L/R）をログに出す。
- **BOOTSEL再起動**
  確認後にBOOTSEL相当を送り、再起動する。再起動後は
  COMが外れるため、繋ぎ直して再HELLOからやり直すこと。
- **baud切替**
  横のbaud欄で速度を選び、実行で切り替える。確認後に
  set_baud_indexを送り、結果と復帰文言を出す。
- **プレイヤーランプ**
  PLAYER_INFOのランプ4灯を画面に示す。点灯=黄・消灯=灰。
- **疎通(PING)**
  PINGを送り、PONGの往復時間とSEQ一致を見る。
- **W表示**
  STATUSの有線flagを見て、現行が有線か無線か示す。
- **W0無線**
  確認後に無線へ切り替え、約500ms後の再起動に備える。
- **W1有線**
  確認後に有線(USB直結・BT停止)へ切り替え、
  約500ms後の再起動に備える。
- **X破棄**
  確認後にKEY_DELETE相当を送り、BEACONとClassic鍵を破棄する。
  Switch側の登録解除も要る。
- **K鍵削除**
  確認後にKEY_DELETEを送り、Pico側のClassic鍵を全削除する。
  Switch側の登録解除も要る。
- **色変更**
  色(RRGGBB)4欄(本体・本体2・左・右)からCOLOR_SETを送る。
  不正時は送らず注意だけ出す。

下部のログ欄に送受信と結果が出る。拒否時はerrcodeの意味も添える。
W切替後は再起動を待ち、再HELLOからやり直すこと。色は再接続後に
反映される。

## 計測と不具合対応

`tools/bcon_jitter.py` は 120Hz送出の揺らぎを量る道具だ。
開→(HELLO)→中立120Hz→要約＋CSV→閉じる、の順に動く。
任意の秒だけ中立を送り、送信間隔の分布と差分を見る。

引数は `--port` が必須で、data-UARTの口を指す(例
`COM9`)。`--baud` は速度で既定が `1000000`、`--secs`
は計測秒数で既定が `10.0` だ。`--latency-note` は変換器
設定の覚書で、FTDI既定16msが120Hzの致命傷になるため、
`FTDI timer=1ms` のような前後比較用メモをCSV先頭へ残す。
`--csv` はCSVの出先で、未指定だと時刻つき自動名になる。
`--no-hello` はHELLOを飛ばすもので、Picoの居ないloopback
(TXをRXへ折返した配線)で送出だけ量るときに使う。

出力は1行要約
`p50=X.XXms p99=Y.YYms sent=N seq_gap=M err_crc=+0 err_drop=+0`
と `csv=...` だ。`p50` と `p99` は送信間隔の百分位で、
`sent` はSTATE送出数、`seq_gap` はSEQ欠番数、`err_crc` と
`err_drop` は前後STATUS差分(符号つき、`+0` が正常)だ。CSV
は `idx`・`t_s`・`delta_ms` の3列で、先頭の `#` 行に実行
条件(`port`・`baud`・`secs`・`latency_note`・`no_hello`・
`samples`)と要約行が残る。`#` 行つきのためpandasでは
`comment='#'` で読む。

不具合時はログ文言を頼りに切り分ける。

- HELLO不通:「HELLOが通らない。送出だけ量る。」や
  「bconのHELLO_ACKが来なかった(○回再送・○s)。再huntへ
  回す(baud_hunt)。」が出る。配線・電源・baudを疑い、
  `baud_hunt` へ回す。
- 版不一致:「bconの版が合わない(adopted=...v...RESULT=...)。
  PCはver=4で送っている。NEUTRALを保ち止める(送り続けな
  い)。」やDOWNGRADED相当の文言が出る。PCはver=4専用の
  ため、NEUTRALを1発保ち止まる。送り続けハングにしない。
- 有線切替後の無応答: W0/W1切替はFlash保存・約500ms後自発
  再起動する。「再起動中のSTATE送出は止め、復帰後に再HELLO
  からやり直すこと。」に従い、送出を止めて再HELLOから
  やり直す。
- STATUS異常:「bconのSTATUS異常を検出した(errcode=0x..
  ...)。正常復帰で0へ戻る。」が出る。**状態確認** で直近
  STATUS(`flags`・`last_seq`・`err_crc`・`err_drop`・
  `errcode`)を読み直し、0への復帰を見る。`err_crc`・
  `err_drop` が増え続ける場合、配線とbaud設定を疑う。

## 制限・既知事項

- PCはver=4専用だ。DOWNGRADED・UNSUPPORTED・版違いは
  NEUTRAL維持で止まり、自動追従しない。
- TXをRXへ折返したloopbackでは自STATEがRXへ戻りSEQ連続性
  (`seq_gap`)も見られる。一方HELLO応答がないため
  `--no-hello` で送出だけ量る。
- **W0無線**・**W1有線**の切替は約500ms後に再起動する。再起動中の
  STATE送出は止め、復帰後に再HELLOからやり直すこと。
- 有線中の取込・BEACON要求は `0x10` / `0x11` で拒否される。
  撮り直し・再生は**W0無線**＋再起動が必要だ。
- `STATUS` の `errcode` は直近エラーを保持し、正常復帰で0へ
  戻る。送出直後の値を読むことで今回分の可否を見る。
- 計測秒数 `secs` は1.0から120.0秒の範囲に丸める。中断時は
  「中断した。ここまでの分で集計する。」と出し、途中
  までの分で集計する。
- Windowsでは待ち粒度を1msへ上げて量る。既定15.6msのまま
  では8.33ms刻みが延び、計測値を壊す。
