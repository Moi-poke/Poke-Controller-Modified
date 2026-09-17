# bcon対応 設計書

合意: wakecon対応はフリーズし、bcon（`C:\pico-bcon`）へネイティブバイナリで全対応する。
Phase 1は同一プロセスに`BconTransport`を追加し120Hz化＋HELLO/STATUS対応を固め、計測で足りなければPhase 2で送信部のみ別プロセス化する（A案・段階移行）。

- 対象: Switch 1 ProCon（Switch 2では互換動作）。有線優先、無線も動くが精度劣化は許容。
- リフレッシュ: 120Hz目標（8.33ms周期）。変化時即送＋定期リフレッシュ。
- 開始はLEN8（u8スティック）。LEN12は後回しだが切替可能な構造にする。
- PC側再起動WDTは持たない。Pico側timeout-neutral（200ms）＋WDT（2s）に対し、PC側は<200ms再送で姿勢保持し、終了・切断時はNEUTRALを送る。
- wakecon凍結ファイル（`WakeSetup.py`・`WakeLink.py`の振る舞い・`pico_uart`）には手を入れない。bcon用は並置で新設する。

## 1. 背景・前提

PokeCon Modifiedの現行対応は2種のみである（`SerialController/core/transport/registry.py:226-239`）。

- `legacy_text`（Leonardo用テキスト行）
- `pico_uart`（pico-wakeCon用full-state S行、`PICO_LIVE_STATE`）

新方式の差し口は整備済みである。`Transport` subclass＋`register_transport(name, factory, description)`で追加し、未知`capability`はlegacy等価で受入、worker要は`LIVE_WORKER_CAPABILITIES`へ加える（`core/transport/base.py:30-40`）。`WakeLink`は`get_raw_serial()/acquire_write_lock()`経由で非シリアル方式でも落とさずフォールバックする（`core/WakeLink.py:27-86`）。

bcon側SSOTは`C:\pico-bcon\spec\protocol_v3.md`＋`src/proto/*`である（`C:\pico-bcon\AGENTS.md:3`）。

- 経路: `PC →(UART)→ Pico 2 W →(USB-HID / Classic BT)→ Switch 1`。Switch 2 BLE入力は対象外。
- PC→Pico: データUART1 GP4/5、既定1Mbps 8N1、フロー制御なし。ログはUART0 GP0/1 @115200で分離。
- フレーム: `[SYNC=0xAB][TYPE 1B][LEN 1B][PAYLOAD N≦32][SEQ 1B][CRC8 1B]`、合計N+5。CRC-8/SMBUS poly 0x07 init 0x00、対象TYPE..SEQ（SYNC除外）、検査値`crc8("123456789")=0xF4`。
- SEQは方向独立mod256。`expect=(last+1)&0xFF`、不一致は欠落イベント1加算。初回は計数しない。
- STATE=`0x01` LEN8（BTN u32-LE VIIPER順22bit＋LX LY RX RY u8、0x80中央）またはLEN12（BTN同一＋4×u16LE 0-4095、中央0x0800）。HATなし、斜めは2bit同時。予約bitは0送信・受信無視。
- HELLO=`0x10` LEN2 `[ver, flags]`／HELLO_ACK=`0x11` LEN4 `[adopted, major, minor, RESULT]`。現コードは`PROTO_VER=0x04`（`src/proto/protocol.h:11`）でv4のみ受付、他はUNSUPPORTED＋`state_accept=false`。NEUTRALはUNSUPPORTED下でも通す。HELLO前既定はSTATE受付（sweep/PoC互換）。
- PING=`0x03`→PONG=`0x21`（受信PINGのSEQエコー）。STATUS=`0x20` LEN7 `[flags, last_seq, err_crc LE16, err_drop LE16, errcode]`。STATUS_REQ=`0x35`で即時STATUS＋PLAYER_INFO付随。
- CONFIG: CAPTURE_START=`0x30`（秒1-60）、BEACON_START=`0x31`、COLOR_SET=`0x32`（RGB×4＝12B）、KEY_DELETE=`0x33`、WIRED_MODE=`0x34`（0/1、Flash保存・約500ms後再起動適用）、STATUS_REQ=`0x35`、BAUD_SET=`0x36`（rate idx 0-4）。
- タイミング: STATE変化時即送＋定期リフレッシュ（既定60Hz、上限1kHz）。timeout-neutralは直近STATEから200ms無受信で全解放。WDTは2sで別機構。USB unmount/suspendで即中立。
- Baud層: 表`0=115200 1=460800 2=921600 3=1000000(既定) 4=2000000`。huntはlast-good先頭＋降順sweep、2連続有効でlock、lockまでTX抑制。BAUD_SETは旧レートでSTATUS-ACK→100ms後双方切替→2s自動復帰、2frame adoptでBCBR永続。BREAKで再hunt。
- 版ずれ注意: 仕様書表題はv3のまま、コードは0x04。PC側はver=4でHELLOを送ること。v3送るとSTATE拒否で無音ハングになる。

Modified行方言（`compat-design §1`）は流用する。`<buttons-hex> <hat-hex> [<lx> <ly> [<rx> <ry>]]`＋`end`、wire 16bit→VIIPER写像、HAT 0-8→十字bit、LS/RSフラグ配送quirk（LSのみ→LX/LYへwire lx/ly、RSのみ→RX/RYへwire lx/ly、両方→各々、なし→不変）、欄なし行はボタン・HATのみ更新。

現ブランチ`feature/pico-40ms-parity`（live scheduler純化中）には触れない。bcon作業は別線で進める。

## 2. 目標・成功基準

- ネイティブバイナリでSTATE送受信・HELLO(ver=4)・PING/PONG・STATUS監視・CONFIG群が動く。
- 120Hzリフレッシュ（8.33ms）でp99ジッタを計測し、常時2-3ms超ならPhase 2へ進む定量ゲートとする。
- 変換器latency timer=1ms設定の前後でp99ジッタを取る（FTDI既定16msは120Hzの致命傷。CH340/CP2102も緩衝で化ける）。
- 無線のdisc reason=0x05不安定がPico側かPC送信ジッタか切り分けられること（分離前の計測が必須）。
- 有線で押下確認→サンプルscript（A連打相当）完走。M相当の計数に代わりSTATUSのerr_crc/err_drop差分がほぼ0。
- `task ci`（ruff check／format --check／mypy／bounds／userapi／pytest）全緑。`core/`はtkinter・アプリ層import禁止、`services/`はtkinter・UI-module import禁止を維持。

## 3. 設計

### 3.1 不変条件

- 送信は変化時即送＋8.33ms定期リフレッシュ。1フレーム1`write()`。SEQは方向別mod256。
- HELLO_ACK確認前にSTATEを流さない（HELLOなし既定受付に甘えない）。UNSUPPORTEDなら再HELLO→再hunt→BAUD_SET復帰へ回し、無音ハングにしない。
- 予約bitは0送信。GR/GL/C/Headsetは輸送位置なしのため落とす（pack側と同一）。
- スティックY反転・12bit化方針はPC送信ラッパ1箇所に集約。Picoは8bit値をそのままpackする。
- 終了・切断・USB unmount時はNEUTRALを送る。200ms無音でPicoが全解放する前提で、pause保持はリフレッシュで賄う。
- `WakeSetup.py`・`pico_uart`・`WakeLink`の既存振る舞いは変えない。

### 3.2 BconTransport（新設 `core/transport/bcon.py`）

`Transport`を継承し`register_transport("bcon", BconTransport, ...)`でbuiltin登録する。capabilityは新規名（例`BCON_STATE`）とし`LIVE_WORKER_CAPABILITIES`へ加える。未知値扱いにせず明示追加する。

```python
class BconTransport(Transport):
    name = "bcon"
    capability = "BCON_STATE"  # base.LIVE_WORKER_CAPABILITIESへ追加
    def open(self, portNum, portName="", baudrate=1000000, **extra) -> bool: ...
    def close(self) -> None: ...
    def is_open(self) -> bool: ...
    def send_row(self, row: str, measure_perf: bool = True) -> None: ...  # Phase 1は行受付→VIIPER変換→フレーム化
    def flush_pending(self) -> None: ...
    def subscribe_rx(self, func) -> Callable[[], None]: ...
    def wait_rx(self, prefixes, timeout=0.5) -> str | None: ...
    def rx_pump_running(self) -> bool: ...
    def start_rx_pump(self) -> bool: ...
    def stop_rx_pump(self) -> None: ...
```

Phase 1のI/F境界はPhase 2分離形に作る（実装は後、境界は先）。親→子は最新姿勢のみ（queue of 1、backlogなし、`Camera`の最新1枚思想）。子→親はSTATUS/PONG/統計のみ。`BconTransport`内部を「姿勢受付」「フレーム化・SEQ」「送信ループ」「受信パーサ」の4区画に分け、Phase 2は送信ループ区画の移設で済ませる。Phase 1は`Sender`変更最小化のため行受付（`send_row(row: str)`）を維持し、Phase 2で姿勢dict直受付も可にする。`wait_rx`は基底のprefix前方一致ではなくフレームTYPE一致で待つよう上書きする。

マッパはLEN非依存に書く。`ctrl_state_t`相当（buttons u32＋sticks u16域、中央0x0800）を中間表現とし、LEN8は`u8<<4`、LEN12はu16直結で同一保持にする。LEN選択はフラグ一つで切替可能にし、後付け分岐地獄にしない。Modified行→VIIPER写像は`core/serial/encoding`側に寄せるかbcon専用マッパに切り出す。

### 3.3 フレーム化・受信パーサ

送信は`frame_build`相当をPythonに移植する（TYPE/LEN/SEQ/CRC8付与、LEN>32拒否）。受信は蓄積バッファ上で反復する。先頭0xAB整列→LEN検証（既知型は正確長、STATEは8/12のみ受理）→CRC検証。一致で確定・SEQ更新・コールバック、不一致でERR加算＋先頭1B前進（フレーム全体を捨てない）。SYNCはペイロード中に出現し得るため最終判定は必ずCRC。未知型は32B上限で可変スキップ（前方互換）。満杯時は最古1B破棄＋ERR加算。

PC側RXパーサはスライディング再同期を必須とする（PAYLOAD中の0xAB再スキャン込み）。誤同期後の復帰遅延はSTATUSのerrcode監視詰まりに直結するため、hostテストベクタで固める。

### 3.4 送信ループ・計時（レビュー反映）

tkinterの`after()`に送信周期を載せない。Phase 1でも送信ループは独立スレッド＋`time.perf_counter`基準の絶対時刻スケジューリングとし、tkメインループ・GIL競合から切り離す。`ser.write`ブロック（最大WRITE_TIMEOUT）は錠の外で行い、間引き判定・保留取出しだけを錠内で行う（現行`TextSerialTransport.send_row`と同一規律）。

計測項目（Phase 1必須）。

- 変換器latency timer=1ms設定の前後でp99ジッタ（FTDI既定16msは120Hzの致命傷）。
- 送信周期8.33msのp50/p99、SEQ欠番イベント、STATUSのerr_crc/err_drop差分。
- p99ジッタが常時2-3ms超ならPhase 2へ進む定量トリガとする。
- USB 8msポーリング（bInterval 8実機写し）との干渉も見る。到着即report更新で低遅延は確保されるが、PC側周期がUSB周期とビートしないか確認する。

### 3.5 HELLO/BAUD状態機械（無音ハング防止）

開局手順は次とする。ポート開→PING/HELLO(ver=4, flags) burst→HELLO_ACK＋PONG/STATUS待ち→STATE開始。TXはbaud-lockまで抑制する。

失敗時遷移を状態機械として定義しGUIへ可視化する。

- HELLO_ACKタイムアウト→再HELLO（回数上限付き）→再hunt（last-good先頭＋降順sweep、2連続有効でlock）。
- UNSUPPORTED応答→STATE送らずNEUTRAL維持→版表示して停止（送り続けハングにしない）。
- STATUS errcode検出（0x01 LEN不正／0x02 CRC不一致／0x03 SEQ欠番／0x05 overrun／0x06 overflow／0x10-0x1F CONFIG拒否）→直近エラー保持→正常復帰で0へ。CONFIG拒否は`0x10+(TYPE&0x0F)`で判定。
- BAUD_SETはSTATUS-ACK確認→100ms guard→切替→2s自動復帰。adopt確認でBCBR永続。
- BREAK受信時は再huntへ。
- WIRED_MODE切替はFlash保存・約500ms後自発再起動を前提に、再起動中のSTATE送出を止め、復帰後に再HELLOからやり直す。有線中のCAPTURE/BEACON要求は0x10/0x11拒否になるため、先にWIRED_MODE=0＋再起動が必要な旨を表示する。

`WakeLink`の生ser直読みはbconでは使わない。`subscribe_rx/wait_rx/rx_pump_running`経由に寄せる。`get_raw_serial()`は非シリアル同様None相当の扱いとし、従来経路へ落ちてもバイナリをASCII読みしない。

### 3.6 設定系（BconSetup新設・分岐禁止）

`WakeSetup.py`に条件分岐を差し込まない（凍結ファイルの回帰防止）。`BconSetup`として並置し、共通化は動作確認後に抽出する。対象写像は次とする。

- 取込秒数＋CAPTURE_START（秒1-60、範囲外はERRCODE 0x10）
- BEACON_START（未保存時は0x11）
- 状態確認＝STATUS_REQ（即時STATUS＋PLAYER_INFO付随）
- 疎通＝PING→PONG（SEQエコーでRTT一意化）
- W表示/W0/W1＝WIRED_MODE照会・切替（再起動適用、有線起動は無線一式上げない）
- D/M表示切替の相当情報はSTATUS flagsで代替（USB mounted／Switch ready／timeout-neutral／WDT-recovered／UART-overrun／wired／BT-connected／RUMBLE-seen）
- X破棄/K鍵削除＝BEACON無効化相当／KEY_DELETE（Switch側登録解除も案内）
- 色変更＝COLOR_SET（RGB×4＝12B、有線中は再列挙注意、Switchの色キャッシュに注意）

おうむ返し注意。PokeConはふだん線を読まないが、bconではSTATUS/PONG/ACKを読む。読むのは応答行ではなく応答フレームであり、liveのSTATE送出と並行してよい。書込みは1フレーム単位で噛み合わせる。

## 4. Phase 2（送信部のみ別プロセス・実装は後）

Phase 1で境界だけ作り、計測トリガ（p99>2-3ms常時）で移設する。親は姿勢生成（GUI・コマンド・キーボード）、子はシリアル所有（送信ループ・受信パーサ・SEQ・HELLO/BAUD状態機械）とする。

- 親→子: 最新姿勢のみ。深さ1（最新1件上書き、backlogなし）。
- 子→親: STATUS/PONG/統計（err_crc/err_drop／SEQ欠番／RTT）のみ。入力ログ・CommandStats・GUI表示はここから還流する。
- 寿命: 起動・終了・NEUTRAL・再接続は子が所有。`pokecon[.profile].lock`の二重起動防止は維持。mac/Linuxは開失敗でFalse＋logし、落とさない（現行`Transport.open`規律）。
- Windows multiprocessing＋tkinter相性はPhase 2着手時に検証する。`core/`・`services/`のtkinter禁止（`task bounds`）を崩さない。

## 5. テスト計画

- host単体（`tests/test_bcon_*.py`）: CRC検査値`0xF4`、STATE例（全中立＋A押下`BTN=0x00000002`）、HELLO行列（ver=4 OK／他 UNSUPPORTED）、PONGエコー、CONFIG拒否（範囲外秒数・未保存再生・範囲外有線値）、LEN12（`0x80==0x800`、他LENはERR_BAD_LEN）、HAT 9通り＋LS/RS quirk 4通り＋`end`→全解放＋不正行棄却、SYNC再同期（PAYLOAD中0xAB・CRC不一致→1B前進・最大1frame損失）、SEQ欠番計数（初回不計数・欠落数でなくイベント数）。
- 結合（実機なし）: 既存`task ci`全緑。`pico_uart`・`legacy_text`の既存テストを壊さない。
- 実機（有線優先）: data-UART COMへ接続→HELLO_ACK確認→仮想コントローラ→Switch入力テスト画面で押下確認→A連打完走→遅延分布。変換器latency timer前後比較、USBポーリング干渉確認、WIRED往復と起動時復元、UART併用時の二重コンソール相当の確認はbconのdata/log分離に読替える。

## 6. リスク・未決

- 仕様v3表題とコード0x04の乖離。v4改訂（Task 9相当）がbcon側で進んだ場合の追従点（PLAYER_INFO仕様追記、RUMBLE送出開始、0x36/0x37の取込み）をPhase 1のどこまで入れるか。本設計は0x36 BAUD_SETまで必須、0x37は対象外とする。
- RUMBLE振幅転送は駐車中（HW振動キャプチャ待ち）。STATUS bit7の有無のみ見て振幅転送はしない。
- PLAYER_INFOはdispatch＋配線済み・HW裏取り待ち。STATUS_REQ付随の受信は実装するがランプ一致の厳密検証はHW確認に委ねる。
- 無線精度劣化は許容だが、disc 0x05系の切断がPico側かPCジッタかの切分け手順を実機手順書に残す。
- `pico-firmware/`（新設計置き場）との関係。本設計はbcon外部FWを前提とし、内蔵FW雛形の変更は含まない。

## 7. 参考

- `C:\pico-bcon\spec\protocol_v3.md`（SSOT）
- `C:\pico-bcon\src\proto\protocol.h`、`protocol.c:31-50,78-155`（TYPE/LEN表・CRC・スライディング・SEQ）
- `C:\pico-bcon\src\proto\dispatch.h`、`dispatch.c`（HELLO門・PONG・CONFIG・outbox）
- `C:\pico-bcon\src\poc_dualcore\poc_send.py`（正準ビルダ・RXスキャナ・HELLO/PING/ladder流儀。量産ラッパではない）
- `C:\pico-bcon\src\main.c:46-103,260-332,640-769,770-912`（ピン・baud・Core1取込・poll_tick・neutral・WDT・WIRED再起動）
- `C:\pico-bcon\docs\superpowers\specs\2026-09-16-pokecon-compat-design.md`（wire→VIIPER §1.3、HAT §1.4、LS/RS quirk §1.5）
- `C:\pico-bcon\docs\wiki\Protocol.md`（v3＋v4差分の早見表）
- PokeCon側: `SerialController/core/transport/base.py:30-40`、`registry.py:226-239`、`text_serial.py:620-682`（PicoUart）、`core/WakeLink.py:27-86`、`WakeSetup.py`
