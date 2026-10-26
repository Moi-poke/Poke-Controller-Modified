# AGENTS.md — Poke-Controller Modified

tkinter desktop app that automates a Switch via serial (Arduino Leonardo / Pico) + capture-board video. Primarily used on Windows, but mac/Linux users exist — serial open and other OS-dependent paths must at least fail gracefully without crashing. Python >=3.12, pinned to 3.12.15 in `.python-version`.

## Setup / run

```cmd
pip install "uv>=0.13.5"
uv sync
.\.venv\Scripts\activate
python .\SerialController\Window.py
```

- [`uv.lock`](uv.lock) + [`pyproject.toml`](pyproject.toml) are the source of truth for deps (`requirements.txt` was deleted; install flow is in README).
- Task runner: `task <name>` (needs `winget install Task.Task` once + `task setup_dev` for hooks). Without it, run the `uv run ...` lines inside [`taskfile.yml`](taskfile.yml) directly. Tasks: `sync` / `lint` / `format` / `typecheck` / `bounds` / `userapi` / `docs` / `test` / `check` (fix mode) / `ci` (no-touch mode) / `app` / `launcher` / `clean`.
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `docs` + `test` must all pass (`task ci`). The tree is fully typed; don't add new type errors. tkinter/`cv2` stub friction is handled with `Any`-boundaries and a few commented `type: ignore`s — prefer those over restructuring.
- Multi-instance: `python .\SerialController\launcher.py` (profile picker) or `python .\SerialController\Window.py --profile <name> [--transport <name>]`. Each profile gets `SerialController/settings.<name>.ini` (auto-created with defaults) and a `pokecon[.profile].lock` file — never commit/remove locks manually.
- Tests in [`tests/`](tests) run via `task test`; CI (`.github/workflows/ci.yml`) runs `task ci` equivalent. Verify with the gate above + `python -m py_compile <file>` and, if possible, a real GUI run. Full verification needs hardware (COM-port microcontroller + capture board), so most changes can't be exercised headless — say so in the summary. `task docs` checks links, `task` names, and `opencode.json` prompt paths in this file and README. Never report a check you did not run as passed; say "not run" and why.

## Architecture ([`SerialController/`](SerialController) is the app; details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md))

- [`Window.py`](SerialController/Window.py) — entry point, `PokeControllerApp`. Does `os.chdir(BASE_DIR)` at startup; resolve all resource paths from `BASE_DIR`, never from cwd. Owns only assembly (init/settings/exit/title); panels live in [`ui/`](SerialController/ui) (`camera_panel` / `serial_panel` / `command_panel` / `history_panel` / `log_panel` / `audio_panel` / `bcon_panel` / `layout_panel` mixins; `blockly_editor` and `controller_dock` are separate `ui/` modules), execution lifecycle in [`services/command_runner.py`](SerialController/services/command_runner.py), serial ownership in [`services/serial_service.py`](SerialController/services/serial_service.py), audio ownership in [`services/audio_service.py`](SerialController/services/audio_service.py) (services are tkinter-free; [`ui/`](SerialController/ui) must not import `Window`; see [`tools/check_core.py`](tools/check_core.py)). Stateless UI helpers live in [`core/`](SerialController/core) (`CommandTags` / `CommandStats`) and at [`SerialController/`](SerialController) root (`LogPane` / `WindowUtils` / `WindowGeometry`).
- [`Commands/PythonCommands/`](SerialController/Commands/PythonCommands) — user automation scripts, auto-discovered by `CommandLoader` via `Utility.importAllModules`. New command = new `.py` there with a class subclassing `PythonCommand` (inputs only), `ImageProcPythonCommand` (inputs + OpenCV vision), or `AudioPythonCommand` (inputs + audio detection via `waitTone`/`waitSound`) that sets a non-empty `NAME`. One broken file must not break the rest (loader imports per-module) — keep that behavior. Reload happens live via `CommandLoader.reload()`, so avoid module-level side effects. The `Commands.*` import paths (`Keys` / `PythonCommandBase` / `McuCommandBase` / `WakeLink` / `CommandVision` / `CommandAudio`) are frozen public API for these scripts (enforced by `task userapi`); internals live in [`core/`](SerialController/core) (`serial/` for Sender+Arbiter+encoding, `transport/` for base+text_serial+registry).
- [`Commands/McuCommands/`](SerialController/Commands/McuCommands) — serial-only scripts subclassing `McuCommand`. [`core/serial/sender.py`](SerialController/core/serial/sender.py) owns button/stick state + input log; [`core/transport/`](SerialController/core/transport) owns the wire (`TextSerialTransport`, `PicoUartTransport`, `BconTransport`, `BconProcTransport`; built-ins are `legacy_text` / `pico_uart` / `switch-bcon` / `switch-bcon-proc`). Don't mix the two layers.
- [`core/Camera.py`](SerialController/core/Camera.py) — capture thread/process serves only the latest frame (`LatestFrame`: Lock + one snapshot; no queue/backlog). Sets MJPG FourCC *before* resolution — order matters for USB2.0 bandwidth.
- Blockly: [`ui/blockly_editor.py`](SerialController/ui/blockly_editor.py) + [`services/blockly_*`](SerialController/services) + [`core/blockly_validate.py`](SerialController/core/blockly_validate.py) + [`assets/blockly/`](SerialController/assets/blockly) — visual editor; see [`docs/BLOCKLY_EDITOR.md`](docs/BLOCKLY_EDITOR.md).
- BCON: [`BconSetup.py`](SerialController/BconSetup.py) + [`ui/bcon_panel.py`](SerialController/ui/bcon_panel.py) + [`core/transport/bcon*.py`](SerialController/core/transport) — Pico 2 W binary transport; see [`docs/BCON_TRANSPORT.md`](docs/BCON_TRANSPORT.md).
- SDL/HID: [`core/sdl_pad.py`](SerialController/core/sdl_pad.py) / `sdl_sensor.py` / `pad_source.py` / `nintendo_hid.py` / `hidhide.py` — pad and gyro input; see [`docs/HIDHIDE.md`](docs/HIDHIDE.md).
- [`Settings.py`](SerialController/Settings.py) — `GuiSettings` (Tk mirror + file IO) reads/writes `settings*.ini`; profile names are sanitized (`sanitize_profile`, keep in sync with [`launcher.py`](SerialController/launcher.py)). Format knowledge (defaults, backfill, migration) lives in tkinter-free [`config.py`](SerialController/config.py) — add new settings there, not ad hoc. The ini format is frozen for existing user files.
- Logging is `loguru` (`PokeConLogger.root_logger()` once at startup). GUI log pane is fed via queued redirect (`LogPane`; rendering lives in `ui/log_panel.py` / `ui/log_view.py`, with the line model in `core/log_model.py`), never write to widgets from worker threads.
- [`core/`](SerialController/core) holds GUI-independent logic (no tkinter, no app-layer imports; enforced by `task bounds`). [`services/`](SerialController/services) holds procedures using core (also no tkinter, no UI-module imports; same gate). [`core/`](SerialController/core) moves keep the same filename with an explicit re-export shim at the old location (`from core.X import Y as Y`) so existing `from Commands.X import ...` keeps working. New code imports from `core` directly.

## Conventions / gotchas

- Absolute imports as if [`SerialController/`](SerialController) is on `sys.path` (e.g. `from Commands.Keys import Button`), never relative imports. Comments and user-visible strings are Japanese — match that.
- Python floor is 3.12 (`requires-python >=3.12`): `X | None`, builtin generics, `super()`, `type` statements, `except*`, and `Self` are fine. Style and typing are enforced by ruff/mypy configured in [`pyproject.toml`](pyproject.toml) — don't restate those rules here.
- Python version policy: floor = oldest version with >1 year of EOL runway left (review each October); pin = latest patch of a stable minor. Current pin: 3.12.15 (3.12 EOL 2028-10; 3.12.16 is released but not yet available as a uv-managed build). Next review Oct 2027: prefer 3.14 over 3.13 (3.13 is security-only since 2026-10; 3.14 on Windows uses Tcl/Tk 9.0.4, so GUI/hardware verification is required; pythonnet 3.2.1 supports 3.14; free-threading is out until further notice).
- Windows-only deps: `pythonnet`, [`DirectShowLib/`](DirectShowLib), `timeBeginPeriod` in `PythonCommandBase`. Don't add POSIX-only assumptions; `os.name == "nt"` branches are load-bearing. mac/Linux users exist: `Transport.open` posix/Darwin branches and other OS-dependent paths must at least fail gracefully without crashing (return False + log, never raise on bad baud/port). `chmod 0o600` in `Settings` is posix-only guarded.
- New connection method (beyond the built-in `legacy_text` / `pico_uart` / `switch-bcon` / `switch-bcon-proc`): add a `Transport` subclass + `register_transport(name, factory, description)`. Unknown `capability` names are accepted (treated as legacy-equivalent, sync `send_row`, no live worker); worker-backed methods add their name to `Transport.LIVE_WORKER_CAPABILITIES`. `open/openSerial` accept `**extra` for future params (VID/PID, IP, etc.). `WakeLink` uses `get_raw_serial()/acquire_write_lock()` so non-serial transports degrade to fallback instead of crashing. Logging policy: file (`logger`) for almost everything, GUI (`print` → LogPane) only for critical user-actionable failures (open failure, write timeout, SerialException, resolve/create fallback).
- `Sender` send interval (`Transport.MIN_SEND_INTERVAL`) is derived from baud rate with a 0.02s cap — no flow control on the wire, so don't lower it blindly.
- `export_py.py` was deleted (unused). Root `makefile` is the legacy AVR LUFA firmware. The old `pico_firmware/` (underscore) was deleted; current `pico-firmware/` (hyphen) is a new design workspace. The protocol reference is the separate repo `Moi-poke/pico-wakeCon`.
- pico-wakeCon wire compat (verified against its `main` branch, no hardware): UART0 115200 8N1 + USB CDC, CRLF, `S <6 hex>` inputs, `O <4 hex> → color ...` replies, 200ms watchdog (keepalive <200ms required), `N` is sent without waiting for a reply. Pico UART (legacy text) connections must use 115200, not the 9600 default; BCON uses 1 Mbps.
- `settings*.ini` は**アプリ終了時に保存**される。起動中に ini を手編集すると終了時に旧値で上書きされるので、編集はアプリを閉じてから行う。

## Working rules

- This file is the entry point. Keep it to project-wide rules and load-bearing gotchas; put long subsystem detail in `docs/` (say when and why to read it) or in a skill.
- When the same mistake happens twice, fix the harness — this file or a check — not just the current change. If two fixes for the same cause fail, stop and report the cause and what is missing.
- For an independent second pass, use the read-only [`.opencode/agents/project-reviewer.md`](.opencode/agents/project-reviewer.md). It cannot edit or run commands, so treat its findings as hypotheses to verify.
- For real-screen checks, use [`.opencode/skills/gui-probe/SKILL.md`](.opencode/skills/gui-probe/SKILL.md) or the read-only [`.opencode/agents/gui-verifier.md`](.opencode/agents/gui-verifier.md).

## Orca 作業役（worker）

`docs/tasks/Txx.md` 指示書を受けて動く場合だけ、[`docs/agents/orca-worker.md`](docs/agents/orca-worker.md) の追加規律に従う。

## IMU (ジャイロ)

IMU・HID・ジャイロ・0x30/0x40 に触るときは [`docs/IMU.md`](docs/IMU.md) を読む。
