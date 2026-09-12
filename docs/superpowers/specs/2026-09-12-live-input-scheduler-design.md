# Live入力スケジューラ 設計書

合意: Pico live経路を実コントローラーの模倣にする（PC側のみの改修、ファーム不変）。
HID周期≒8ms想定。毎スロット送出＋最低16ms保持。

## 1. 背景・根本原因

現行（`SerialController/core/serial/sender.py` のlive worker、`core/transport/text_serial.py` の`PicoUartTransport`）の問題は4点である。

1. **mailbox容量1の上書きでpress消失** — `putLive` は未送信を無条件上書きする（`sender.py:1557-1561`）。press→releaseが同一スロット窓（最大8ms＋jitter）に落ちるとpressはワイヤに一度も出ない。`duration=0.1`でもworker停滞（画像認識・描画のGIL競合、`ser.write`ブロック最大0.5s）が押下時間を超えれば同じ崩壊が起きる。
2. **単発送信＋疎keepalive** — 無変化時は88ms再送しない（`LIVE_KEEPALIVE_S=0.088`、`sender.py:1497-1500,1672-1683`）。UARTにフロー制御はなく、1行ロスしたら押下ごと消える。実コントローラーは毎レポート繰り返すため単発ロスに強い。
3. **優先起床がpacingを壊す** — releaseは`_live_wake`で即時送出する（`sender.py:1564-1565,1661-1663`）。press直後のreleaseが1ms未満の間隔で出ると、Picoの同一HID周期内でpressが潰れる。
4. **dwell精度がコマンドスレッドのsleep依存** — `press`は`input→wait(duration)→inputEnd`（`core/CommandOperate.py:71-77`）で、`wait`は`_TICK=0.02`刻みの`Event.wait`（同`:68,139-170`）。jitterがそのままdwell誤差になり、ワイヤ側に最低保持がないため誤差が抜けに直結する。

Pico経路では入力ログは未接続（`PicoUartTransport.add_listener -> False`）で、画面表示も20Hz間引き（`_showLiveRow`）のため、「ログに出る」はワイヤ到達の証拠にならない。

## 2. 目標・成功基準

- `duration=0.008`の押下もワイヤ上で最低16ms（2スロット）保持され、Switchのボタン入力テストに届く。
- `duration>=0.016`の押下は要求通り（水増ししない）。
- ワイヤ抜け率 `<1% / 100試行 @16ms`（実線ワイヤロガーで計測）。
- `task ci`（ruff check / format --check / mypy / bounds / userapi / pytest）全緑。既存テスト470 passed / 1 skippedを維持。

## 3. 設計

### 3.1 不変条件

- workerは8ms固定周期で取出しを判断する。新規エッジはdwell門のみで即時送出する。
- 一度送出を開始した状態は、最低24ms（既定、設定可）が経つまで次の状態へ進めない。短いpress-releaseはpress側を24msパルスに延長する。要求より短くは絶対にしない。
- 無変化の再送は24msに間引く。PicoはUARTを10ms周期ポーリング＋32B FIFOで読むため、8ms毎の連送は2行が1窓に落ちてオーバーランする実測（ng約23%）。200ms watchdogは24ms再送で十分賄う。
- ボタン・Hatが変化するエッジは絶対に捨てない。スティックのみの差分は畳んでよい（legacyの`_coalescable`と同じ判定基準）。

### 3.2 LiveScheduler（新設 `core/serial/live_scheduler.py`）

tkinter-freeの純粋ロジック＋自前ロック（`core/`境界適合）。時刻は引数で受け取り、時計を持たない（テストは仮想時刻を通す）。

```python
class LiveScheduler:
    def __init__(self, slot_s: float = 0.008, min_dwell_s: float = 0.016, capacity: int = 32) -> None
    def push(self, snap: dict[str, Any]) -> None   # 末尾とスティックのみ差分なら畳む（merged計数）
    def advance(self, now: float) -> None          # dwell満了かつpendingありなら最古をcurrentへ
    def current(self) -> dict[str, Any] | None     # 毎slot再送する行（副作用なし）
    def clear(self) -> bool                        # pending破棄。破棄物があればTrue
    def pending_empty(self) -> bool
    def stats(self) -> dict[str, int]              # put/merged/dropped の写し
    def set_min_dwell(self, seconds: float) -> None  # 8ms〜64ms以外は無視
    def reset_stats(self) -> None                    # put/merged/droppedを0へ（clearLiveStats用）
```

- `push`: `pending`が空でなければ末尾と比較し、btnとhatが等しければ（スティックのみ差分）末尾を置換して`merged+=1`。そうでなければ追加。満杯（32件）なら最古を捨てて`dropped+=1`（黙って捨てず計数する）。`put+=1`。
- `advance(now)`: `pending`があり、かつ（`current`が無いか、`now - current_sent_at >= min_dwell_s`）なら最古を`current`へ移し`current_sent_at = now`。
- `capacity=32`は滞留上限 `32×16ms=512ms` の意味である。通常の操作頻度（press毎200ms前後）では1〜2件に収まる。マウス mortality のような高頻度スティックは畳まれるため溜まらない。
- `priority`引数は`Sender.putLive`の互換のため残すが、スケジューラ内では区別しない（dwell門が優先起床の速射を既に防ぐ）。

### 3.3 workerループ変更（`sender.py:1642-1699`）

スロット毎の処理を次に置き換える。

```python
snap = None  # mailbox takeLive をやめる
now = time.perf_counter()
self._live_sched.advance(now)
out = self._live_sched.current()
if out is None:
    continue
```

- keepalive分岐（`1672-1683`）は削除し、無変化時も`out`を毎回送る。
- 優先起床（`_live_wake`）は残す。起床時にdwell未満なら何も送らず再び待つため、速射にならない。release遅延が最大8ms縮む。
- `_showLiveRow`はそのまま（表示は20Hz間引き＋解放即時、repeatは非表示）。引数名`is_keepalive`は`is_repeat`へ改名する。
- `_live_box`・`takeLive`の中身はスケジューラへ移す。`takeLive`メソッド自体は残し、互換のため`advance+pop`相当の振る舞いにする（利用者は`sender.py`内部の`_liveLoop`のみ）。

### 3.4 タイミング例（slot=8ms, dwell=16ms）

- 8ms押下（t=0 press, t=8 release）: slot0でpress送出開始、releaseはpending待機、slot2（t=16）でrelease送出。ワイヤdwell=16ms。
- 100ms押下: pressを毎slot再送し続け、releaseは次slotで即送出。水増しなし。
- pressRep連打（16ms間隔）: 各エッジが16msずつ送出され、全体が伸びない（dwellと間隔が一致するため）。

### 3.5 設定

- `config.py`の`Transport`既定へ`live_min_dwell_ms: 16`を追加。`complete_missing`で8〜64以外は`"16"`へ戻す（既存iniはbackfillのみ、書式凍結を守る）。
- `Settings.py`に`live_min_dwell_ms`のtk変数を追加し、保存辞書へ含める。
- `SenderSpec`へ`live_min_dwell_ms: int`を追加し、`serial_panel._sender_spec`から渡す。`build_sender`で`sender.setLiveMinDwell(ms)`を呼ぶ（`setArbitration`と同列）。
- `Sender.setLiveMinDwell(ms)`: 8〜64以外は無視（現状維持）。生成済みスケジューラと保存値の両方へ反映する。

### 3.6 統計

`getLiveStats`のキーは保つ。意味のみ更新する。

- `put`: 受理した申告数（不変）。
- `replaced`: スティック畳み込み（merged）の数。従来の上書き消失は起きない。
- `sent`: 送出行数（repeat含む）。
- `keepalive`: 無変化repeat数。
- `priority`: 優先申告数（不変）。
- `dropped`: 追加。キュー溢れ破棄数（0であるべき）。
- `last_revision` / `inversions`: 不変。

### 3.7 終了時

`closeSerial`の手順（中立優先送出→drain→worker停止→close）は維持する。`CLOSE_DRAIN_S`は0.05→0.10へ延ばす（中立がdwell待ち最大16msのため）。`discardLive`は`clear()`へ、`waitLiveDrained`は`pending_empty()`待ちへ読み替える。

## 4. 互換制約

- `Commands.*`公開API（`Keys`/`PythonCommandBase`/`McuCommandBase`/`WakeLink`/`CommandVision`/`CommandAudio`）不変（`task userapi`）。
- `core/`はtkinter・アプリ層import禁止、`services/`はtkinter・UI禁止（`task bounds`）。
- `Transport`抽象・`putLive/takeLive/discardLive/waitLiveDrained/getLiveStats`の名前・引数不変。
- legacy_text経路・`McuCommand`・調停・音声／画像系に触れない。
- コミットは明示指示があるときのみ。

## 5. 検証

- ヘッドレス: スケジューラ単体テスト（仮想時刻で延長・非水増し・順序・溢れ）、FakeSerial＋本物workerのdwell分布テスト。
- 実機: 新規`tools/pico_wire_log.py`（時刻付きS行記録）で`duration ∈ {8,16,32,100}ms ×100回`sweep→ワイヤdwellヒストグラム＋未送出率。その後Switch本体入力テスト画面で目視計数し、ワイヤOK／表示NGを分離する。

## 6. 対象外

- ファーム（`Moi-poke/pico-wakeCon`）変更、Q/R時刻付きキューの復活。
- legacy（Leonardo）経路のpacing変更。
- GUIへのdwell設定欄の追加（値はini＋既定のみ。画面欄は別途）。
