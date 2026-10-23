# AGENTS.md — Poke-Controller Modified

tkinter desktop app that automates a Switch via serial (Arduino Leonardo / Pico) + capture-board video. Primarily used on Windows, but mac/Linux users exist — serial open and other OS-dependent paths must at least fail gracefully without crashing. Python >=3.12, pinned to 3.12.10 in `.python-version`.

## Setup / run

```cmd
pip install uv
uv sync
.\.venv\Scripts\activate
python .\SerialController\Window.py
```

- [`uv.lock`](uv.lock) + [`pyproject.toml`](pyproject.toml) are the source of truth for deps (`requirements.txt` was deleted; install flow is in README).
- Task runner: `task <name>` (needs `winget install Task.Task` once + `task setup_dev` for hooks). Without it, run the `uv run ...` lines inside [`taskfile.yml`](taskfile.yml) directly. Tasks: `sync` / `lint` / `format` / `typecheck` / `bounds` / `userapi` / `test` / `check` (fix mode) / `ci` (no-touch mode) / `app` / `launcher` / `clean`.
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `test` must all pass (`task ci`). The tree is fully typed; don't add new type errors. tkinter/`cv2` stub friction is handled with `Any`-boundaries and a few commented `type: ignore`s — prefer those over restructuring.
- Multi-instance: `python .\SerialController\launcher.py` (profile picker) or `python .\SerialController\Window.py --profile <name> [--transport <name>]`. Each profile gets `SerialController/settings.<name>.ini` (auto-created with defaults) and a `pokecon[.profile].lock` file — never commit/remove locks manually.
- Tests in [`tests/`](tests) run via `task test`; CI (`.github/workflows/ci.yml`) runs `task ci` equivalent. Verify with the gate above + `python -m py_compile <file>` and, if possible, a real GUI run. Full verification needs hardware (COM-port microcontroller + capture board), so most changes can't be exercised headless — say so in the summary.

## Architecture ([`SerialController/`](SerialController) is the app; details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md))

- [`Window.py`](SerialController/Window.py) — entry point, `PokeControllerApp`. Does `os.chdir(BASE_DIR)` at startup; resolve all resource paths from `BASE_DIR`, never from cwd. Owns only assembly (init/settings/exit/title); panels live in [`ui/`](SerialController/ui) (`camera_panel` / `serial_panel` / `command_panel` / `log_panel` / `audio_panel` mixins), execution lifecycle in [`services/command_runner.py`](SerialController/services/command_runner.py), serial ownership in [`services/serial_service.py`](SerialController/services/serial_service.py), audio ownership in [`services/audio_service.py`](SerialController/services/audio_service.py) (services are tkinter-free; [`ui/`](SerialController/ui) must not import `Window`; see [`tools/check_core.py`](tools/check_core.py)). Stateless UI helpers live in [`core/`](SerialController/core) (`CommandTags` / `CommandStats`) and at [`SerialController/`](SerialController) root (`LogPane` / `WindowUtils` / `WindowGeometry`).
- [`Commands/PythonCommands/`](SerialController/Commands/PythonCommands) — user automation scripts, auto-discovered by `CommandLoader` via `Utility.importAllModules`. New command = new `.py` there with a class subclassing `PythonCommand` (inputs only), `ImageProcPythonCommand` (inputs + OpenCV vision), or `AudioPythonCommand` (inputs + audio detection via `waitTone`/`waitSound`) that sets a non-empty `NAME`. One broken file must not break the rest (loader imports per-module) — keep that behavior. Reload happens live via `CommandLoader.reload()`, so avoid module-level side effects. The `Commands.*` import paths (`Keys` / `PythonCommandBase` / `McuCommandBase` / `WakeLink` / `CommandVision` / `CommandAudio`) are frozen public API for these scripts (enforced by `task userapi`); internals live in [`core/`](SerialController/core) (`serial/` for Sender+Arbiter+encoding, `transport/` for base+text_serial+registry).
- [`Commands/McuCommands/`](SerialController/Commands/McuCommands) — serial-only scripts subclassing `McuCommand`. [`core/serial/sender.py`](SerialController/core/serial/sender.py) owns button/stick state + input log; [`core/transport/`](SerialController/core/transport) owns the wire (`TextSerialTransport`, `PicoUartTransport`). Don't mix the two layers.
- [`core/Camera.py`](SerialController/core/Camera.py) — capture thread/process serves only the latest frame (queue of 1, no backlog). Sets MJPG FourCC *before* resolution — order matters for USB2.0 bandwidth.
- [`Settings.py`](SerialController/Settings.py) — `GuiSettings` (Tk mirror + file IO) reads/writes `settings*.ini`; profile names are sanitized (`sanitize_profile`, keep in sync with [`launcher.py`](SerialController/launcher.py)). Format knowledge (defaults, backfill, migration) lives in tkinter-free [`config.py`](SerialController/config.py) — add new settings there, not ad hoc. The ini format is frozen for existing user files.
- Logging is `loguru` (`PokeConLogger.root_logger()` once at startup). GUI log pane is fed via queued redirect (`LogPane`), never write to widgets from worker threads.
- [`core/`](SerialController/core) holds GUI-independent logic (no tkinter, no app-layer imports; enforced by `task bounds`). [`services/`](SerialController/services) holds procedures using core (also no tkinter, no UI-module imports; same gate). [`core/`](SerialController/core) moves keep the same filename with an explicit re-export shim at the old location (`from core.X import Y as Y`) so existing `from Commands.X import ...` keeps working. New code imports from `core` directly.

## Conventions / gotchas

- Absolute imports as if [`SerialController/`](SerialController) is on `sys.path` (e.g. `from Commands.Keys import Button`), never relative imports. 4-space indent. Comments and user-visible strings are Japanese — match that.
- Python floor is 3.12 (`requires-python >=3.12`): `X | None`, builtin generics, `super()`, `type` statements, `except*`, and `Self` are fine. Ruff enforces `I` + `UP006/007/008/035`.
- Python version policy: floor = oldest version with >1 year of EOL runway left (review each October); pin = latest patch of a stable minor. Next review Oct 2027 (consider 3.13; 3.14+ needs pythonnet finals + Tk 9 maturity, free-threading is out until further notice).
- Windows-only deps: `pythonnet`, [`DirectShowLib/`](DirectShowLib), `timeBeginPeriod` in `PythonCommandBase`. Don't add POSIX-only assumptions; `os.name == "nt"` branches are load-bearing. mac/Linux users exist: `Transport.open` posix/Darwin branches and other OS-dependent paths must at least fail gracefully without crashing (return False + log, never raise on bad baud/port). `chmod 0o600` in `Settings` is posix-only guarded.
- New connection method (beyond legacy/Pico): add a `Transport` subclass + `register_transport(name, factory, description)`. Unknown `capability` names are accepted (treated as legacy-equivalent, sync `send_row`, no live worker); worker-backed methods add their name to `Transport.LIVE_WORKER_CAPABILITIES`. `open/openSerial` accept `**extra` for future params (VID/PID, IP, etc.). `WakeLink` uses `get_raw_serial()/acquire_write_lock()` so non-serial transports degrade to fallback instead of crashing. Logging policy: file (`logger`) for almost everything, GUI (`print` → LogPane) only for critical user-actionable failures (open failure, write timeout, SerialException, resolve/create fallback).
- `Sender` send interval (`Transport.MIN_SEND_INTERVAL`) is derived from baud rate with a 0.02s cap — no flow control on the wire, so don't lower it blindly.
- `export_py.py` was deleted (unused). Root `makefile` is the legacy AVR LUFA firmware; local `pico_firmware/` is superseded — the dedicated firmware is the separate repo `Moi-poke/pico-wakeCon`, which is the protocol reference.
- pico-wakeCon wire compat (verified against its `main` branch, no hardware): UART0 115200 8N1 + USB CDC, CRLF, `S <6 hex>` inputs, `O <4 hex> → color ...` replies, 200ms watchdog (keepalive <200ms required), `N` is sent without waiting for a reply. Pico UART connections must use 115200, not the 9600 default.

<<<<<<< HEAD
## Orca 作業役（worker）

Orca の統括役から `docs/tasks/Txx.md` 指示書を受け取って動く場合の追加規律（通常開発では無視してよい）。

- 指示書の「編集してよいファイル」以外は変更しない。迷ったら推測で実装せず、Orca の escalation（統括役への質問）で止める。
- 指示書を受け取ったら、まず `/goal` に完了条件を設定する（goal プラグインは `opencode.json` の `plugins` で有効化済み）。
- 完了条件のコマンド（`task ci` 等）がすべて成功するまで完了にしない。実機が必要で検証できない場合は `blocked` にして理由を書く。
- 完了したら変更をコミットし（履歴の書き換え・未コミット作業の破棄は禁止）、`worker_done` を必ず1回送る（`--outcome succeeded|failed`、`--files-modified`、`--report-path` 付き、要約3文＋`path:line` 証拠）。
- 既定エージェント（`build`）のまま作業し、`coordinator` 等へ切り替えない。worktree・ブランチの作成は Orca に任せ、自分では作らない。
=======
## IMU (ジャイロ) path — 2026-10 追加

- PC→Pico は `T_IMU_SAMPLE`(0x04/LEN12, 6×int16-LE = ax,ay,az,gx,gy,gz) を5ms間隔。PCの0x30は1レポートに5ms間隔の3サンプルを持つので、リーダーは3スロットを順に供給し（`read_next()`）、FWが直近3件を保持して0x30のスロット0..2へ古い順に載せる。**0x30の送出周期は15ms固定**（これより速いとコンソールがジャイロを過積分する。`jyapayne/switch-pico` の実装註記）。
- **0x40の引数はIMU形式の選択**（実測: FW 4.51→`02`=圧縮クォータニオン、3.72→`01`=RAW）。FWは引数を`imu_mode`として保持し、1ならRAW 36B、2なら NS-LIB-HID `ns_mode_2_s` 同形の36B（accel y,x,z＋最大成分基準の30bitクォータニオン＋11bit ms）を返す。`3`は未解明として仮定しない。
- 有線0x30のIMUは既定でrole0のみ（`usb_imu_wired_role_ok`）。role1/2で有線ジャイロを試す診断は `JOY_WIRED_IMU_ALL=1`。FW版のA/Bは `JOY_USB_FW_JOY_OLD=1`（Joy役を3.72に）。
- 軸・符号の正味変換 = **FW側の `JOY_IMU_NEG_MODE`（role2のみ）∘ PC側 `[IMU] accel_*/gyro_axes・signs`**。PC側は HID直結でも適用される（`ImuPanel._sample_transform`）。既定は FW=1（加速度+ジャイロ反転）/ PC=恒等。
- `[IMU] gyro_dead_dps` は送出直前のジャイロ各軸に効く（`core.imu_wave.apply_gyro_deadband`）。`gyro_scale` は実測用トリム。**加工後の値を返さないと不感帯が送出に効かない**（過去に実際に踏んだ）。
- HIDリーダー (`core.nintendo_hid`) はプロセス共有・読み取り専用スレッドが最新0x30を保持する。`ImuPanel` は停止時に共有ハンドルを閉じない。読取失敗が続いたら自分で閉じ、パッド/IMU側が再オープンする（UF2書き込みでUSBが再列挙されるため必須）。
- `settings*.ini` は**アプリ終了時に保存**される。起動中に ini を手編集すると終了時に旧値で上書きされるので、編集はアプリを閉じてから行う。
>>>>>>> Moi-poke/ジャイロへの対応
