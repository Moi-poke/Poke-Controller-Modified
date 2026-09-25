# Windows 高解像度プレビュークロックブリッジ 設計書

日付: 2026-09-25  
状態: 承認済み  
対象: `Poke-Controller-Modified` ホストアプリ  
範囲: プレビューの描画クロックと、その production 性能 contract

## 1. 結論

Windows では、高解像度 waitable timer を 1 個だけ持つ専用ワーカーを追加する。ワーカーは `QueryPerformanceCounter` と `QueryPerformanceFrequency` を基準に absolute deadline を計算し、`SetWaitableTimer` の単発待機を毎回やり直す。Tk main thread が所有する message only window が通知を受け、`CaptureArea` の描画を main thread 上で 1 回実行する。

**ワーカーは Tk を一切呼び出さない。**Tk、widget、`after`、`CaptureArea`、カメラ読み出し、画像の変換と貼付は、Tk main thread だけで実行する。ワーカーと main thread の間へ渡すのは、変更不能な tick、wake、制御状態だけであり、pending tick は latest only の 1 slot とする。

既存の UI と config が公開する 5、15、30、45、60 FPS はすべて supported production target である。60 FPS 未満は test only ではない。native production mode の target FPS を `F` とすると、main thread dispatch と `PhotoImage.paste` の両方で、次の contract を満たす。

```text
fixed_window_mean_hz >= F
endpoint_complete_p1_hz >= F - 1
fixed_window_mean_hz <= F + 0.1
```

`F=60` では、平均 `60.0 Hz` 以上、P1 `59.0 Hz` 以上という従来の hard contract を維持する。`+0.1 Hz` は有限測定窓の境界差を明示的に許す cap tolerance であり、scheduler の目標 rate を緩める relaxation ではない。

Windows 以外または native init failure 時の既存 `after` fallback は、機能を維持する互換経路とする。performance parity を別途証明しない限り、fallback run は production performance accepted にしない。

## 2. 背景と現在の failure evidence

### 2.1 現行の preview clock

`SerialController/GuiAssets.py` の `CaptureArea.capture()` は、1 tick の後に `self.after()` を予約する。`_next_delay()` は `time.perf_counter()` に基づく absolute deadline を保持するため、処理時間が周期を超えても drift を次周期へ累積しない。

しかし、clock source は Windows と Tk の timer quantization に残る。16.667 ms 周期を `ceil()` した整数 millisecond delay を `after()` に渡しても、requested delay と actual callback 時刻は一致しない。Tcl/Tk main loop の負荷、ほかの event handling、OS scheduler delay は、callback interval の slow tail に現れる。

### 2.2 60 FPS の slow tail

現在の generic Tk `after` 経路では、60 FPS 向けの small な test only internal rate headroom を与えても、endpoint complete P1 は約 56 から 57 Hz にとどまった。長期 drift は抑制できているが、59 Hz の受入線を下回る。

`tests/test_preview_fps_measurement_contract.py` の fixed window、endpoint complete P1、nearest rank contract は構造的に pass している。現在の failure は測定式ではなく、`after()` callback の時刻分布にある。

## 3. 目標と非目標

### 3.1 目標

1. 5、15、30、45、60 FPS をすべて production target として同じ scheduler contract で扱う。
2. native production mode の各 target `F` で、dispatch と paste の平均を `F` 以上、`F+0.1` 以下、P1 を `F-1` 以上にする。
3. native QPC interval と portable `after` interval の両方を configured `F` から算出する。
4. `set_fps()` 変更時に grid を re-anchor し、epoch、generation、pending tick を無効化する。
5. timer wake と control wake を分離し、control event が tick を publish しないようにする。
6. latest only tick の publish、supersede、dispatch、stale drop、pending remainder を数え、accounting の漏れを残さない。
7. `PostMessageW` failure、worker join pending、window class teardown を明示的な recovery path にする。
8. synthetic source、camera sequence、dispatch、paste を別 artifact にし、物理カメラや unique physical frame を synthetic evidence から主張しない。
9. source pins、raw artifact hash、thread ID を含む repeatable な report を残す。

### 3.2 非目標

1. compositor または physical display の scan out 保証しない。
2. synthetic 180 Hz source の結果から、physical camera が 60 unique frames を出したとは主張しない。
3. serial、firmware、wire protocol、camera capture architecture、UI layout を再設計しない。
4. Windows 以外へ native clock を移植しない。
5. `after` fallback の performance parity を、別の証明なしに主張しない。
6. test only 60.01、60.03、60.06 Hz を production acceptance に使わない。
7. queue に tick を貯め、遅延 tick をまとめて描画しない。

## 4. Clock、cadence、frame の測定境界

本設計では scheduler clock、measurement clock、camera source を分離する。

1. **QPC scheduler cadence**: `QueryPerformanceCounter` と `QueryPerformanceFrequency` から計算する native worker の deadline cadence である。
2. **Main thread dispatch cadence**: `WNDPROC` が `CaptureArea` の 1 tick を開始する cadence である。Tk main loop の負荷を受ける。
3. **Tk paste cadence**: `PhotoImage.paste()` の入口 timestamp による cadence である。Tk 内部処理後の時刻であり、画面 scan out の時刻ではない。
4. **Camera source cadence**: camera または synthetic source が frame sequence を供給する cadence である。scheduler acceptance とは別 evidence とする。

production acceptance は 2 と 3 だけで判定する。1 は scheduler diagnostics、4 は source supply と frame freshness の観測に使う。

`time.perf_counter_ns()` は E2E の measurement timestamp にだけ使う。`PreviewTick` の QPC 値とは直接変換せず、report でも別 field に保存する。fixed window、raw interval、P1 の acceptance timestamp は `time.perf_counter_ns()` とする。

synthetic source は 180 Hz で供給できるが、dispatch 60 Hz と camera unique frame 60 Hz は別概念である。`camera_reads.jsonl` の unique sequence count を報告するが、synthetic evidence から physical unique frame rate を導かない。

## 5. 承認済みアーキテクチャ

### 5.1 構成と public interface

新規 module `SerialController/ui/preview_clock.py` を追加し、clock facade、thread ownership、Win32 adapter、latest only mailbox、health watchdog、`after` fallback を集める。`CaptureArea` は 1 tick の描画と既存 lifecycle だけを担当する。

```python
class StopResult(StrEnum):
    STOPPED = "stopped"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    schedule: Literal["active", "idle"]


class PreviewClock:
    def __init__(
        self,
        root: Any,
        dispatch_tick: Callable[[], DispatchResult],
        configured_fps: int,
        idle_interval_ms: int,
    ) -> None: ...

    def start(self) -> None: ...
    def stop(self) -> StopResult: ...
    def set_fps(self, fps: int) -> None: ...
    def mode(self) -> Literal["stopped", "high_resolution", "after"]: ...
    def health_snapshot(self) -> Mapping[str, int | str | bool]: ...
```

production interface は `WindowUtils.FPS_VALUES` と同じ 5、15、30、45、60 だけを受け付ける。E2E の 60.01、60.03、60.06 Hz は harness 内部の diagnostic override とし、UI、config、public API へ追加しない。

`dispatch_tick()` は現在の `CaptureArea.capture()` から scheduling を除いた 1 tick 処理である。`active` または `idle` を返し、`CaptureArea` の widget、camera、variable を worker へ渡さない。

### 5.2 Windows native resource

`start()` は Tk main thread 上で次を準備する。

1. `QueryPerformanceFrequency`、`QueryPerformanceCounter` で QPC capability を preliminary check する。
2. `CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE)` で high resolution timer を作る。`TIMER_ALL_ACCESS` は要求しない。
3. `CreateEventW` で stop event と control event を `EVENT_MODIFY_STATE | SYNCHRONIZE` 権限で作成する。health state は同じ owner が lock で保護する。
4. `RegisterClassExW` で process-local な message only window class を登録し、`CreateWindowExW` で `HWND_MESSAGE` を parent とする HWND を作る。
5. class atom、`WNDCLASSEXW`、`WNDPROC` callback、ctypes function object、HWND を Tk main thread が保持する。
6. resource を使う daemon worker thread を 1 個だけ start する。

`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION` を受けない Windows では `CreateWaitableTimerExW` が失敗する。この場合は init failure として cleanup し、既存 `after` だけへ fallback する。ordinary timer への別の attempt は行わない。

Win32 `SetWaitableTimer` に `bManualReset` argument は存在しない。自動リセット型は `CreateWaitableTimerExW` で `CREATE_WAITABLE_TIMER_MANUAL_RESET` を加えないことで固定する。これは `bManualReset=FALSE` と同じ指定である。単発動作は `lPeriod=0`、completion routine は `NULL`、`fResume=FALSE` とする。

worker は `QueryPerformanceFrequency` も起動時に取得し、QPC deadline 計算の authoritative frequency とする。

### 5.3 Tick、wake、health data

worker と main thread の間のデータは frozen dataclass とする。

```python
@dataclass(frozen=True, slots=True)
class PreviewTick:
    epoch: int
    generation: int
    configured_fps: int
    sequence: int
    deadline_qpc: int
    emitted_qpc: int


@dataclass(frozen=True, slots=True)
class PreviewWake:
    kind: Literal["clock_ready", "tick", "error"]
    epoch: int
    generation: int
    configured_fps: int
    sequence: int = 0
    qpc_frequency_hz: int = 0
    error_name: str = ""


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    worker_thread_native_id: int
    last_sequence: int
    last_wake_qpc: int
    post_failure_count: int
    last_post_error: int
    running: bool
```

`deadline_qpc` と `emitted_qpc` は QPC domain のみを使う。`configured_fps` は production target であり、diagnostic override rate を別 field として上書きしない。`time.perf_counter_ns()` は tick data に入れない。

`PostMessageW` の payload には tick object pointer を渡さない。message は wake vector で、immutable data は lock 越し mailbox から読む。

### 5.4 Worker の操作境界

worker が実行できる操作は、`QueryPerformanceFrequency`、`QueryPerformanceCounter`、`SetWaitableTimer`、`CancelWaitableTimer`、`WaitForMultipleObjects`、immutable data の mailbox 格納、`PostMessageW`、shared health state 更新に限る。

worker は `time.perf_counter_ns()`、Tk、widget、`after`、`CaptureArea`、camera read、image conversion、routine log を実行しない。Tk へ戻す error data は sanitized code と Win32 error 値だけにする。

### 5.5 Main thread dispatch

`WNDPROC` は window を作成した Tk main thread で動作する。`dispatch_in_flight` flag により、同時に 1 個以下の dispatch だけを許可する。再入 message は後続 dispatch 用 wake として記録し、message だけを消費して pending tick を残さない。

`clock_ready` は QPC frequency を report に保存し、`CaptureArea` を dispatch しない。`error` は排他的 `after` fallback または teardown 処理に入れる。tick wake だけが pending slot から dispatch candidate になる。

`WNDPROC` は Win32 callback 境界で例外を捕捉する。通常の dispatch exception は main thread の log に残し、scheduler は継続する。root teardown 中は dispatch gate を閉じる。

Tk の message loop が `HWND_MESSAGE` 宛の custom message を実 E2E で配送しない場合、native architecture の前提不成立として E2E を失敗にする。`after` fallback を同じ run の成功や production acceptance として扱わない。

## 6. Target FPS 別 scheduler contract

### 6.1 Native QPC interval

configured target を `F` とすると、native interval は次で算出する。

```text
interval_qpc = max(1, ceil(qpc_frequency_hz / F))
```

最初の 1 tick は main thread で同期実行する。`DispatchResult` を受けた後、control event を signal し、worker が `anchor_qpc = QueryPerformanceCounter()` を採取する。最初の deadline は `anchor_qpc + interval_qpc` で、開始直後に 0 ms、1 ms callback を連鎖しない。

worker は timer 復帰後に `now_qpc = QueryPerformanceCounter()` を採取する。早期復帰では tick を publish せず同じ deadline を再 arm する。期限到達時だけ tick を 1 個 publish する。

次の deadline は `deadline_qpc + interval_qpc` とする。`next_qpc <= now_qpc` なら、integer arithmetic 1 回で `now_qpc` より後の最初の grid を選ぶ。while loop、0/1 ms callback、連続した catch-up dispatch は行わない。

`SetWaitableTimer` には QPC から 100 ns 単位へ integer ceiling で変換した negative relative due time を渡す。

```text
delta_qpc = max(0, deadline_qpc - now_qpc)
due_100ns = max(1, ceil(delta_qpc * 10_000_000 / qpc_frequency_hz))
lpDueTime.QuadPart = -due_100ns
lPeriod = 0
fResume = FALSE
```

### 6.2 Portable `after` interval

fallback は configured `F` から `interval_s = 1.0 / F` を算出し、現行 `time.perf_counter()` absolute deadline pacing を維持する。native QPC deadline と同じ値として扱わず、並行稼働させない。

fallback は 5、15、30、45、60 の各設定で start、setFps、idle、stop の functional contract を通す。ただし production performance acceptance は native mode だけを対象にする。

### 6.3 `set_fps()` は grid を re-anchor する

`set_fps(F)` は現在の production target が同じなら何もしない。値が変わるときだけ generation を増やし、pending tick、wake outstanding、control state を無効化する。control event を signal し、worker が新しい `F` から interval と QPC anchor を採用する。fallback も次の deadline を現在時刻から `1.0/F` で再設定する。

idle 復帰時も同じ規則で、configured `F` へ grid を re-anchor する。idle 中の 200 ms interval は production target `F` の performance contract には含めない。

### 6.4 Latest only tick accounting

pending slot は常に 1 個で、新しい tick が到着すると古い tick を置き換える。session total と measurement window delta の両方で、次を保存する。

```text
worker_tick_published_count
pending_tick_superseded_count
main_dispatch_count
stale_tick_dropped_count
post_failure_count
pending_tick_present_at_window_end
```

測定窓終了時に、次の不一致を acceptance failure とする。

```text
worker_tick_published_count
= main_dispatch_count
+ pending_tick_superseded_count
+ stale_tick_dropped_count
+ pending_tick_present_at_window_end
```

直列 run では control wake と stale timer wake が tick publish しないことを raw wake reason と共に証明する。

### 6.5 Clock wake と control wake の分離

`WaitForMultipleObjects` の対象は stop event、control event、timer event の 3 個とする。複数 signal が同時に返る場合、stop、control、timer の順に処理する。

1. stop が signal なら tick を publish せず終了する。
2. control が signal なら control state を取り出し、QPC anchor と deadline を再設定する。tick は publish しない。
3. timer だけが signal の場合だけ deadline tick を publish する。

各 wake には `wake_reason` を付ける。health watchdog の `after` callback は frame dispatch scheduler ではなく、valid message ごとに再 arm する failure guard とする。

## 7. Lifecycle と main-thread teardown

### 7.1 State machine

| State | 意味 | scheduler |
|---|---|---|
| `STOPPED` | 非活動 | なし |
| `STARTING_NATIVE` | native resource と worker を初期化中 | なし |
| `HIGH_RESOLUTION` | native clock が活動 | worker 1 個 |
| `AFTER` | portable fallback が活動 | after callback 1 個 |
| `STOPPING` | dispatch gate を閉じて teardown 中 | 新規 dispatch なし |
| `TEARDOWN_PENDING` | worker または class cleanup 完了待ち | 新規 start と root destroy を禁止 |
| `FAILED` | native と after の両方を利用できない | なし |

`HIGH_RESOLUTION` と `AFTER` の両方を同時に-active にしない。runtime failure、PostMessage failure、stop failure の後も、teardown が完了するまで別 scheduler を start しない。

### 7.2 Idempotency と generation

1. `start()` は動作中なら何もしない。
2. `stop()` は `STOPPED` なら `StopResult.STOPPED`、teardown が終わらない場合は `StopResult.PENDING` を返す。
3. `set_fps(F)` は同じ値なら何もしない。変更時だけ generation、anchor、pending tick を更新する。
4. start、stop、実 FPS 変更、active/idle 変更、runtime failure ごとに generation を増やす。
5. epoch は PreviewClock instance と start session を識別する。別 instance、停止済み session、停止済み worker の message は stale とする。

### 7.3 `Window.exit()` との統合

現行 `Window.exit()` は preview stop、camera destroy、root destroy の順で呼ぶが、stop が失敗しても例外を握って root destroy へ進む。bridge 導入後は stop result を必ず確認する。

`Window` は外部 entry の `exit()` と内部の `_continue_exit()` を分ける。`exit()` は確認後に `_exit_requested=True`、`_exit_phase="stopping_preview"` を設定し、`_continue_exit()` を呼ぶ。`_continue_exit()` は `_exit_requested` によって拒否しない内部再入可能 procedure とする。

現行 pipeline の preview stop を、child window、runner、serial、camera の停止より前へ移す。確認後、root に登録した対象 `after` をキャンセルした直後に preview stop を開始し、`StopResult.PENDING` の間は残りの teardown phase へ進まない。

`_continue_exit()` は次の順序で停止する。

1. `CaptureArea.stopCapture()` を Tk main thread で一度だけ呼ぶ。
2. `StopResult.PENDING` なら、camera destroy、serial shutdown、root destroy を行わず、`root.after(50, _continue_exit)` で teardown だけを再試行する。
3. `StopResult.STOPPED` 演出後、serial shutdown、settings save、camera destroy、audio shutdown、`root.destroy()` へ進む。
4. `root.after` callback は teardown retry 専用であり、preview tick scheduler ではない。

`TEARDOWN_PENDING` 中は root destroy を禁止する。root が破棄されれば message only window と Tk callback の寿命が保障されないためである。E2E teardown では `Window.exit()` 経路を直接呼び、pending 中に root が生存することを検証する。

### 7.4 Window class と callback の cleanup

window class、HWND、`WNDPROC` は Tk main thread が所有する。stop は次の順で行う。

1. `STOPPING` へ移り、dispatch gate を閉じる。
2. generation と pending tick、control state、health callback を無効化する。
3. stop event を signal し、timer を cancel する。
4. 最大 1.0 秒 worker join を待つ。
5. join 完了後、worker が kernel handle を close する。worker は window destroy、class unregister、Tk を触らない。
6. `DestroyWindow(hwnd)` を main thread で呼ぶ。
7. `UnregisterClassW(class_name, h_instance)` を同じ thread で呼ぶ。
8. class unregister 後に `WNDCLASSEXW`、`WNDPROC`、ctypes function object の参照を解放する。
9. `StopResult.STOPPED` を返す。

1.0 秒で終わらない場合は `TEARDOWN_PENDING`、HWND、class、callback、kernel handle を保持し、root destroy と新規 start を禁止する。cleanup exception は best effort で安全に記録し、worker から Tk へ戻さない。

### 7.5 `PostMessageW` failure recovery

worker が `PostMessageW` に失敗した場合、message を busy retry しない。shared health state に `post_failure_count`、`last_post_error`、`running=False` を記録し、timer の再 arm を止める。

Tk main thread は worker start 後、最初の単発 health callback を main loop へ返る前に arm する。以後 healthy message を受け取った時だけ次回を再 arm し、callback は preview frame を描画しない。

```text
health_timeout_ns = max(250ms, 4 * target_period)
```

health callback は `post_failure_count` の増加、worker heartbeat stale、worker thread exit を検出したら、native teardown を完了してから既存 `after` backend だけを start する。health callback 自体が Tk main loop 长时间 block で実行されない場合は、実 E2E の watchdog failure として扱う。

## 8. 排他的 fallback と performance parity

fallback 規則は次のとおりである。

1. `os.name != "nt"` では Win32 import、QPC、class registration、handle creation を試さない。
2. QPC、high resolution timer、window class、HWND、event、thread start のいずれかが init に失敗した場合、作成済みリソースを解放する。
3. その後、既存 absolute deadline `after` backend だけを start する。
4. runtime native failure と PostMessage failure でも、native teardown 後に同じ fallback を選ぶ。
5. fallback 中は native worker、timer、message window、polling を残さない。
6. fallback は draw、save、mouse、setFps、stop の機能 contract を通す。
7. fallback の performance report は `run_class="fallback_functional"`、`functional_accepted=true`、`production_accepted=false` とする。
8. 別途 parity を証明しない限り、fallback で native の mean、P1、cap guard を主張しない。

`SetTimer` と `WM_TIMER`、plain periodic `CreateTimerQueueTimer`、`after(0)`、`after(1)` の preview dispatch polling は fallback にも使わない。

## 9. `SetTimer` と plain timer queue timer が不足する理由

`SetTimer` は整数 millisecond delay の user message timer であり、16.666 ms を直接指定できない。`WM_TIMER` は priority、system load、window event に左右され、整数 delay polling は high resolution clock ではない。

plain periodic `CreateTimerQueueTimer` も period を整数 millisecond で扱う。16 ms は 62.5 Hz、17 ms は 58.8 Hz となり、configured `F` を正確に表せない。callback は worker pool 上で起こり、main thread dispatch、latest only、epoch、generation を提供しない。

## 10. Stale、exception、camera observability

### 10.1 Stale tick

stop 中、stop 後、別 epoch、generation 増加後の tick は dispatch しない。pending slot の置き換えで失われた旧 tick は superseded として数える。stale tick を paste、draw、source supply、acceptance count に入れない。

### 10.2 Exception

`dispatch_tick()` の camera read、image conversion、PhotoImage paste の失敗は現行 `capture()` と同じく error log に残し、scheduler は継続する。`None` frame は disabled image として扱い、camera frame の stale failure とは分ける。

worker infrastructure exception、PostMessage failure、control event failure は health state に保存し、main thread の recovery path で処理する。WndProc callback exception は Win32 stack へ返さない。

### 10.3 Camera sequence の観測

既存 `readFrameWithSeq()`、`frame_seq()`、LatestFrame sequence を使い、main dispatch ごとに次を保存する。

```text
camera_read_count
camera_frame_present_count
camera_none_count
camera_read_error_count
camera_unique_sequence_count
camera_duplicate_sequence_count
camera_sequence_regression_count
camera_last_sequence
post_teardown_camera_read_count
```

同じ `seq` の再読は duplicate であり、通常は failure ではない。`seq < last_observed_seq` を regression、`None` を no-frame、read exception を read error とする。teardown 後に camera read が 1 回でも起これば failure とする。

synthetic E2E では regression、read error、post-teardown read が 0 であることを確認する。duplicate は source と preview cadence の差を診断する evidence であり、unique physical frame の claim には使わない。

## 11. E2E first の検証計画

### 11.1 実装前の RED

production code より先に `tests/test_preview_fps_e2e.py` と `tests/preview_fps_support.py` を拡張する。real Tk、synthetic 180 Hz source、1280 x 720、10 秒 warmup、60 秒 measurement を基盤にする。60 FPS の current after baseline では P1 約 56 から 57 Hz の RED を保存する。

### 11.2 Parameterized production acceptance

configured `F` は 5、15、30、45、60 のいずれかとする。native production run は、dispatch と paste の両方で次の acceptance を満たす。

```text
F >= 5
target_mean_min_hz = F
target_mean_max_hz = F + 0.1
target_p1_min_hz = F - 1
cap_tolerance_hz = 0.1
cap_tolerance_semantics = "finite_window_boundary_not_rate_relaxation"
```

`mean=F+0.1` 以下は cap guard であり、平均の下限を `F-0.1` へ緩めない。`F=60` の下限と P1 は、平均 `60.0 Hz` 以上、P1 `59.0 Hz` 以上のまま維持する。

source supply は scheduler acceptance とは別で、synthetic 180 Hz の source rate を paste rate と同一視しない。

### 11.3 Fixed window と actual overshoot

primary mean は nominal half-open window の entry count を使う。

```text
window = [measurement_start_ns, measurement_start_ns + configured_measurement_s * NS_PER_S)
mean_hz = entry_count * NS_PER_S / configured_measurement_s
```

`configured_measurement_s=60.0` の 60 秒窓で、mean は configured `F` 以上 `F+0.1` 以下。実 end timestamp は診断値であり、mean の分母にはしない。

harness の actual measurement overshoot は `max(0, actual_end_ns - nominal_end_ns)` で記録し、次の bound を超えると `measurement_overshoot_ok=false`、`production_accepted=false` とする。

```text
measurement_overshoot_limit_ns = max(2 * target_period_ns, 50_000_000)
```

この bound は evidence collection の停止遅延を制限するもので、scheduler の rate contract を緩めるものではない。overshoot を通過した run でも、fixed window count は必ず nominal window で算出する。

### 11.4 Endpoint-only P1

P1 は測定窓内で連続する 2 endpoint が両方収まる interarrival interval だけを使う。partial interval、境界をまたぐ interval、補間値を加えない。

```text
frequency_hz = NS_PER_S / interval_ns
rank = max(1, ceil(0.01 * interval_count))
p1_hz = sorted(frequencies)[rank - 1]
```

P1 は target `F-1` 以上を対象とする。60 FPS では 59 Hz 以上の条件を維持する。平均と P1 は別 artifact とし、平均が contract でも P1 が落ちれば production accepted にしない。

### 11.5 Gap contract

`gap_threshold_ns = 25_000_000` とする。25 ms 以上の paste gap、dispatch gap、worker tick gap、control wake gap を raw artifact に列挙する。gap は worker の queue 短縮で消さず、開始、終了、前後 sequence、recovery、帰属 reason を残す。

gap 件数を無条件に zero にする contract は作らない。P1 と gap forensics を併用し、1 件だけ生じた gap も evidence に残す。

### 11.6 Matrix

| Scope | 5 | 15 | 30 | 45 | 60 | 60.01/60.03/60.06 |
|---|---:|---:|---:|---:|---:|---:|
| deterministic contract | 必須 | 必須 | 必須 | 必須 | 必須 | diagnostic |
| real Tk native gate | optional | 必須 | optional | optional | 必須 | diagnostic only |
| `after` functional | 必須 | 必須 | 必須 | 必須 | 必須 | 必須 |
| production performance | native のみ | native のみ | native のみ | native のみ | native のみ | 禁止 |

default full gated real Tk run は 60 FPS と lower target の 15 FPS を含む。5、30、45 FPS は opt-in extended real Tk run、5 FPS は deterministic、idle、lifecycle を含む。diagnostic rates は 60.01、60.03、60.06 を参考 run として残せるが、`diagnostic_only=true`、`production_accepted=false`、`accepted=false` とする。

portable `after` は全 target で functional gate を通すが、performance parity を別 run で証明しない限り `production_accepted=false` とする。

### 11.7 Report と artifact

report は少なくとも次を持つ。

```text
configured_fps
diagnostic_rate_hz
run_class
diagnostic_only
clock_mode
target_mean_min_hz
target_mean_max_hz
target_p1_min_hz
cap_tolerance_hz
cap_tolerance_semantics
measurement_overshoot_ns
measurement_overshoot_limit_ns
measurement_overshoot_ok
production_accepted
functional_accepted
test_passed
accepted
camera_read_count
camera_unique_sequence_count
camera_sequence_regression_count
post_teardown_camera_read_count
thread_ids
```

`accepted` は production run の場合だけ `production_accepted` と同値にし、diagnostic と fallback では false とする。`test_passed` は diagnostic invariant や functional fallback の結果を別 field で表す。

raw artifact は次を保持する。

| Artifact | 内容 |
|---|---|
| `report.json` | target、threshold、clock mode、status、cadence、gap、camera、health、teardown |
| `wakes.jsonl` | QPC tick、wake reason、control event、stale drop、PostMessage result |
| `clock_pairs.jsonl` | QPC と `time.perf_counter_ns()` の paired sample |
| `intervals.jsonl` | paste fixed window、raw interval、P1 |
| `dispatch_intervals.jsonl` | main dispatch fixed window、endpoint interval、P1 |
| `camera_reads.jsonl` | seq、frame presence、duplicate、regression、read error |
| `health.jsonl` | worker heartbeat、PostMessage failure、health callback result |
| `teardown.json` | Window.exit phase、stop result、root alive、join、class cleanup |
| `thread_ids.json` | main、preview worker、window owner、camera thread の native ID |
| `source_pins.json` | 後述 source file path、bytes、SHA-256 |
| `artifact_hashes.json` | raw artifact path、bytes、SHA-256 |

source pins には少なくとも次を含める。

```text
SerialController/ui/preview_clock.py
SerialController/GuiAssets.py
SerialController/Window.py
SerialController/ui/camera_panel.py
SerialController/config.py
SerialController/WindowUtils.py
tests/preview_fps_support.py
tests/test_preview_fps_e2e.py
tests/test_preview_fps_measurement_contract.py
tests/test_preview_fps_internal_rate_contract.py
```

hash は run 開始時と終了時に同じ file set で記録する。source file の変更、artifact の欠落、thread ID 不一致があれば `accepted=false` とする。

### 11.8 Isolation test の範囲

isolation test は主検証にしない。E2E で再現しにくい Win32 failure だけを対象にする。

1. QPC capability、high resolution timer、PostMessage failure の failure injection。
2. configured `F` から QPC interval、100 ns due、after interval、deadline skip、anchor の deterministic conversion。
3. 5/15/30/45/60 の parameterized threshold、cap、fixed window、P1 contract。
4. Window.exit pending 中の root survival、StopResult、class teardown。
5. latest only accounting、stale tick、control wake separation、camera sequence regression。

initial E2E RED を取得するまでは production code を変更しない。unit test を後から追加して主検証にはしない。

## 12. Scope と実装境界

### 12.1 変更対象

1. `SerialController/ui/preview_clock.py`: QPC clock、control event、latest only、health watchdog、fallback、target FPS、state machine。
2. `SerialController/GuiAssets.py`: `CaptureArea` の 1 tick dispatch、`set_fps` re-anchor、camera sequence observability、lifecycle 委譲。
3. `SerialController/Window.py`: `exit()` と `_continue_exit()` の split、StopResult 待ち、root destroy guard。
4. `SerialController/ui/camera_panel.py`: existing `FPS_VALUES` を使う target 変更の evidence と確認。候補値や config schema は変更しない。
5. `tests/preview_fps_support.py`: matrix、report、hash、thread、health、camera、teardown instrumentation。
6. `tests/test_preview_fps_e2e.py`: production native 60/15、extended matrix、fallback、diagnostic run。
7. `tests/test_preview_fps_measurement_contract.py`: 全 target の fixed window、P1、cap、overshoot contract。
8. `tests/test_preview_fps_internal_rate_contract.py`: diagnostic rate の production accepted 禁止。

### 12.2 変更しない対象

1. `SerialController/core/Camera.py` の source thread、LatestFrame、sequence semantics。
2. `SerialController/core/serial/`、`core/transport/`、`services/`。
3. camera、serial、command、audio panel の layout と既存 event contract。
4. firmware、wire protocol、`specs/`、generated file、installer、build artifact。
5. image filter、buffer reuse、disabled image、`saveCapture` の意味。
6. `CameraQueue` の queue 方針。
7. UI/config の production target 値と format。5、15、30、45、60 を変更しない。
8. 物理カメラ、compositor、monitor の scan out guarantee。

## 13. リスクと未解決事項

| リスク | 影響 | 対応 |
|---|---|---|
| high resolution timer も OS scheduler に従う | target `F` の P1 が `F-1` を下回る | 60/15 real Tk を gate、P1 と gap を保存 |
| Tk main loop の長時間 block | worker tick は進むが main dispatch が遅れる | latest only、health、dispatch raw evidence で分離 |
| `HWND_MESSAGE` が Tk loop で配送されない | native clock が成立しない | message delivery precondition 失敗で redesign。fallback を green 扱いしない |
| `PostMessageW` failure | tick が main thread へ届かない | shared health、one-shot health callback、排他的 fallback |
| Window.exit と teardown pending | root destroy 後 callback、crash | `StopResult.PENDING` 中 root destroy 禁止、internal retry 専用 |
| QPC と measurement timestamp の混同 | acceptance が誤る | QPC scheduler、perf measurement、clock pair を分離 |
| actual measurement overshoot | 測定窓の収め誤差と cap guard の解釈が曖昧になる | overshoot limit、nominal fixed window、end timestamp を別記録 |
| camera duplicate/stale frame | unique frame と paste cadence を混同 | sequence artifact、regression 0、unique count は診断、physical claim なし |
| fallback の performance 不足 | native parity を誤認 | functional-only、性能 acceptance は native のみ |
| OS power plan、antivirus、machine load | P1 の再現性が変動 | environment、hash、thread、raw interval を保存 |
| physical camera 不在 | synthetic の external validity は不明 | physical unique frame と scan out は未実施として残す |

## 14. 実装上の注意

1. UI/config の 5、15、30、45、60 を変更しない。production target と diagnostic rate を分離する。
2. native interval は configured `F` から作る。diagnostic rate を使う場合は `configured_fps` を上書きしない。
3. `set_fps()` は同じ値を no-op、異なる値だけ generation と grid re-anchor とする。
4. control event は tick を publish しない。timer event だけが tick を作る。
5. latest only slot の counter equation を report で必ず一致させる。
6. `PostMessageW` 失敗を busy retry しない。health state と main-thread recovery を使う。
7. `Window.exit()` は StopResult が `PENDING` なら root destroy をしない。internal retry は `_continue_exit()` だけで行う。
8. health `after` callback と teardown retry `after` callback は preview scheduler ではない。別 id で保持し、stop 後に残さない。
9. `time.perf_counter_ns()` は worker、deadline、SetWaitableTimer へ入れない。
10. `after` fallback は `1.0/F` interval を使い、native と並行稼働させない。
11. camera `readFrameWithSeq()` の seq は frame observability に使い、synthetic 180 Hz を physical unique frame の根拠にしない。
12. report は configured target、threshold、cap、clock mode、diagnostic/production status を持たない場合 fail とする。
13. source pins と raw artifact は path、bytes、SHA-256 を残す。thread ID 不一致で accepted にしない。
14. 60.01、60.03、60.06 は diagnostic only とし、`production_accepted=true` を禁止する。
15. 60 FPS と 15 FPS の real Tk gate を default とし、5、30、45 は parameterized contract と必要に応じた extended run とする。
16. 物理カメラ、物理 display、unique physical frame の claim を追加しない。
17. commit は明示指示がないため行わない。

## 15. 完了条件と未実施事項

本設計の完了は次を全て満たすこととする。

1. 5、15、30、45、60 の production target が同一の configured-F scheduler contract を使う。
2. native production run の dispatch と paste が target `F` について mean `F` 以上 `F+0.1` 以下、endpoint complete P1 `F-1` 以上を満たす。
3. 60 FPS の平均 `60.0 Hz` 以上、P1 `59.0 Hz` 以上が維持される。
4. diagnostic rate と fallback は `production_accepted=true` を返さない。
5. native QPC と `after` の両方で interval が configured `F` から算出され、`set_fps()` が grid を re-anchor する。
6. control wake、timer wake、latest only accounting、stale tick、PostMessage failure、health recovery が evidence を持つ。
7. Window.exit が StopResult pending 中 root destroy をせず、native teardown 後に camera、root を停止する。
8. fixed window、endpoint P1、cap tolerance、actual overshoot、gap、camera sequence、source hash、artifact hash、thread ID の evidence が残る。
9. real Tk native gate は 60 と 15 を含む。全 5 値は deterministic contract を含む。
10. synthetic source と physical camera evidence が分離される。
11. physical unique 60 frame、compositor、physical display の検証は現在未実施であり、完了条件には含めない。
12. ユーザーからの明示的な commit 指示がないため、この design file は uncommitted のまま残す。

設計上は「configured production FPS を target として、native clock が hard cadence を満たし、fallback は機能を保つ」ことを定める。QPC scheduler、main dispatch、paste、camera source、physical presentation の clock と evidence は混同せず、production accepted と diagnostic の結果を明示的に分離する。
