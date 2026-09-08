# AGENTS.md — Poke-Controller Modified

tkinter desktop app that automates a Switch via serial (Arduino Leonardo / Pico) + capture-board video. Primarily used on Windows, but mac/Linux users exist — serial open and other OS-dependent paths must at least fail gracefully without crashing. Python >=3.12, pinned to 3.12.10 in `.python-version`.

## Setup / run

```cmd
pip install uv
uv sync
.\.venv\Scripts\activate
python .\SerialController\Window.py
```

- `uv.lock` + `pyproject.toml` are the source of truth for deps (`requirements.txt` was deleted; install flow is in README).
- Task runner: `task <name>` (needs `winget install Task.Task` once + `task setup_dev` for hooks). Without it, run the `uv run ...` lines inside `taskfile.yml` directly. Tasks: `sync` / `lint` / `format` / `typecheck` / `bounds` / `userapi` / `test` / `check` (fix mode) / `ci` (no-touch mode) / `app` / `launcher` / `clean`.
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` + `userapi` + `test` must all pass (`task ci`). The tree is fully typed; don't add new type errors. tkinter/`cv2` stub friction is handled with `Any`-boundaries and a few commented `type: ignore`s — prefer those over restructuring.
- Multi-instance: `python .\SerialController\launcher.py` (profile picker) or `python .\SerialController\Window.py --profile <name> [--transport <name>]`. Each profile gets `SerialController/settings.<name>.ini` (auto-created with defaults) and a `pokecon[.profile].lock` file — never commit/remove locks manually.
- Tests in `tests/` run via `task test`; CI (`.github/workflows/ci.yml`) runs `task ci` equivalent. Verify with the gate above + `python -m py_compile <file>` and, if possible, a real GUI run. Full verification needs hardware (COM-port microcontroller + capture board), so most changes can't be exercised headless — say so in the summary.

## Architecture (`SerialController/` is the app; details in `docs/ARCHITECTURE.md`)

- `Window.py` — entry point, `PokeControllerApp`. Does `os.chdir(BASE_DIR)` at startup; resolve all resource paths from `BASE_DIR`, never from cwd. Owns only assembly (init/settings/exit/title); panels live in `ui/` (`camera_panel` / `serial_panel` / `command_panel` / `log_panel` mixins), execution lifecycle in `services/command_runner.py`, serial ownership in `services/serial_service.py` (services are tkinter-free; `ui/` must not import `Window`; see `tools/check_core.py`). Stateless UI helpers live in `core/` (`CommandTags` / `CommandStats` / `LogPane` / `WindowUtils` / `WindowGeometry`).
- `Commands/PythonCommands/` — user automation scripts, auto-discovered by `CommandLoader` via `Utility.importAllModules`. New command = new `.py` there with a class subclassing `PythonCommand` (inputs only) or `ImageProcPythonCommand` (inputs + OpenCV vision) that sets a non-empty `NAME`. One broken file must not break the rest (loader imports per-module) — keep that behavior. Reload happens live via `CommandLoader.reload()`, so avoid module-level side effects. The `Commands.*` import paths (`Keys` / `PythonCommandBase` / `McuCommandBase` / `WakeLink` / `CommandVision`) are frozen public API for these scripts (enforced by `task userapi`); internals live in `core/` (`serial/` for Sender+Arbiter+encoding, `transport/` for base+text_serial+registry).
- `Commands/McuCommands/` — serial-only scripts subclassing `McuCommand`. `core/serial/sender.py` owns button/stick state + input log; `core/transport/` owns the wire (`TextSerialTransport`, `PicoUartTransport`). Don't mix the two layers.
- `core/Camera.py` — capture thread/process serves only the latest frame (queue of 1, no backlog). Sets MJPG FourCC *before* resolution — order matters for USB2.0 bandwidth.
- `Settings.py` — `GuiSettings` (Tk mirror + file IO) reads/writes `settings*.ini`; profile names are sanitized (`sanitize_profile`, keep in sync with `launcher.py`). Format knowledge (defaults, backfill, migration) lives in tkinter-free `config.py` — add new settings there, not ad hoc. The ini format is frozen for existing user files.
- Logging is `loguru` (`PokeConLogger.root_logger()` once at startup). GUI log pane is fed via queued redirect (`LogPane`), never write to widgets from worker threads.
- `core/` holds GUI-independent logic (no tkinter, no app-layer imports; enforced by `task bounds`). `services/` holds procedures using core (also no tkinter, no UI-module imports; same gate). `core/` moves keep the same filename with an explicit re-export shim at the old location (`from core.X import Y as Y`) so existing `from Commands.X import ...` keeps working. New code imports from `core` directly.

## Conventions / gotchas

- Absolute imports as if `SerialController/` is on `sys.path` (e.g. `from Commands.Keys import Button`), never relative imports. 4-space indent. Comments and user-visible strings are Japanese — match that.
- Python floor is 3.12 (`requires-python >=3.12`): `X | None`, builtin generics, `super()`, `type` statements, `except*`, and `Self` are fine. Ruff enforces `I` + `UP006/007/008/035`.
- Python version policy: floor = oldest version with >1 year of EOL runway left (review each October); pin = latest patch of a stable minor. Next review Oct 2027 (consider 3.13; 3.14+ needs pythonnet finals + Tk 9 maturity, free-threading is out until further notice).
- Windows-only deps: `pythonnet`, `DirectShowLib/`, `timeBeginPeriod` in `PythonCommandBase`. Don't add POSIX-only assumptions; `os.name == "nt"` branches are load-bearing. mac/Linux users exist: `Transport.open` posix/Darwin branches and other OS-dependent paths must at least fail gracefully without crashing (return False + log, never raise on bad baud/port). `chmod 0o600` in `Settings` is posix-only guarded.
- New connection method (beyond legacy/Pico): add a `Transport` subclass + `register_transport(name, factory, description)`. Unknown `capability` names are accepted (treated as legacy-equivalent, sync `send_row`, no live worker); worker-backed methods add their name to `Transport.LIVE_WORKER_CAPABILITIES`. `open/openSerial` accept `**extra` for future params (VID/PID, IP, etc.). `WakeLink` uses `get_raw_serial()/acquire_write_lock()` so non-serial transports degrade to fallback instead of crashing. Logging policy: file (`logger`) for almost everything, GUI (`print` → LogPane) only for critical user-actionable failures (open failure, write timeout, SerialException, resolve/create fallback).
- `Sender` send interval (`Transport.MIN_SEND_INTERVAL`) is derived from baud rate with a 0.02s cap — no flow control on the wire, so don't lower it blindly.
- `export_py.py` was deleted (unused). Root `makefile` is the legacy AVR LUFA firmware; local `pico_firmware/` is superseded — the dedicated firmware is the separate repo `Moi-poke/pico-wakeCon`, which is the protocol reference.
- pico-wakeCon wire compat (verified against its `main` branch, no hardware): UART0 115200 8N1 + USB CDC, CRLF, `S <6 hex>` inputs, `O <4 hex> → color ...` replies, 200ms watchdog (keepalive <200ms required), `N` is sent without waiting for a reply. Pico UART connections must use 115200, not the 9600 default.
