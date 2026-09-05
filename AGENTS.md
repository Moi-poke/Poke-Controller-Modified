# AGENTS.md — Poke-Controller Modified

Windows-only tkinter desktop app that automates a Switch via serial (Arduino Leonardo / Pico) + capture-board video. Python >=3.10, pinned to 3.12.7 in `.python-version`.

## Setup / run

```cmd
pip install uv
uv sync
.\.venv\Scripts\activate
python .\SerialController\Window.py
```

- `uv.lock` + `pyproject.toml` are the source of truth for deps (`requirements.txt` was deleted; install flow is in README).
- Task runner: `task <name>` (needs `winget install Task.Task` once + `task setup_dev` for hooks). Without it, run the `uv run ...` lines inside `taskfile.yml` directly. Tasks: `sync` / `lint` / `format` / `typecheck` / `bounds` / `check` (fix mode) / `ci` (no-touch mode) / `app` / `launcher` / `clean`.
- Gate: `ruff check` + `ruff format --check` + `mypy` + `bounds` must all pass (`task ci`). The tree is fully typed; don't add new type errors. tkinter/`cv2` stub friction is handled with `Any`-boundaries and a few commented `type: ignore`s — prefer those over restructuring.
- Multi-instance: `python .\SerialController\launcher.py` (profile picker) or `python .\SerialController\Window.py --profile <name> [--transport <name>]`. Each profile gets `SerialController/settings.<name>.ini` (auto-created with defaults) and a `pokecon[.profile].lock` file — never commit/remove locks manually.
- No tests, no CI. Verify with the gate above + `python -m py_compile <file>` and, if possible, a real GUI run. Full verification needs hardware (COM-port microcontroller + capture board), so most changes can't be exercised headless — say so in the summary.

## Architecture (`SerialController/` is the app; details in `docs/ARCHITECTURE.md`)

- `Window.py` — entry point, `PokeControllerApp`. Does `os.chdir(BASE_DIR)` at startup; resolve all resource paths from `BASE_DIR`, never from cwd. UI construction is split into `_build_*_frame` helpers; stateless helpers live in `core/` (`CommandTags` / `CommandStats` / `CommandPalette` / `LogPane` / `WindowUtils` / `WindowGeometry`).
- `Commands/PythonCommands/` — user automation scripts, auto-discovered by `CommandLoader` via `Utility.importAllModules`. New command = new `.py` there with a class subclassing `PythonCommand` (inputs only) or `ImageProcPythonCommand` (inputs + OpenCV vision) that sets a non-empty `NAME`. One broken file must not break the rest (loader imports per-module) — keep that behavior. Reload happens live via `CommandLoader.reload()`, so avoid module-level side effects.
- `Commands/McuCommands/` — serial-only scripts subclassing `McuCommand`. `core/Sender.py` owns button/stick state + input log; `core/Transport.py` owns the wire (`TextSerialTransport`, `PicoUartTransport`). Don't mix the two layers.
- `core/Camera.py` — capture thread/process serves only the latest frame (queue of 1, no backlog). Sets MJPG FourCC *before* resolution — order matters for USB2.0 bandwidth.
- `Settings.py` — `GuiSettings` reads/writes `settings*.ini`; profile names are sanitized (`sanitize_profile`, keep in sync with `launcher.py`). Missing keys are backfilled with defaults — add new settings there, not ad hoc.
- Logging is `loguru` (`PokeConLogger.root_logger()` once at startup). GUI log pane is fed via queued redirect (`LogPane`), never write to widgets from worker threads.
- `core/` holds GUI-independent logic (no tkinter, no app-layer imports; enforced by `task bounds`). Moves go there with same filename; the old location keeps an explicit re-export shim (`from core.X import Y as Y`) so existing `from Commands.X import ...` keeps working. New code imports from `core` directly.

## Conventions / gotchas

- Absolute imports as if `SerialController/` is on `sys.path` (e.g. `from Commands.Keys import Button`), never relative imports. 4-space indent. Comments and user-visible strings are Japanese — match that.
- Python floor is 3.10 (`requires-python >=3.10`): `X | None`, builtin generics, and `super()` are fine; `type` statements, `except*`, `Self`/`override` are not. Ruff enforces `I` + `UP006/007/008/035`.
- Windows-only deps: `pythonnet`, `DirectShowLib/`, `timeBeginPeriod` in `PythonCommandBase`. Don't add POSIX-only assumptions; `os.name == "nt"` branches are load-bearing. Linux/mac are NOT supported: legacy darwin/posix branches (serial open, mac camera list) are unmaintained, and the `pythonnet` dependency blocks non-Windows installs outright.
- `Sender` send interval (`Transport.MIN_SEND_INTERVAL`) is derived from baud rate with a 0.02s cap — no flow control on the wire, so don't lower it blindly.
- `export_py.py` was deleted (unused). Root `makefile` is the legacy AVR LUFA firmware; local `pico_firmware/` is superseded — the dedicated firmware is the separate repo `Moi-poke/pico-wakeCon`, which is the protocol reference.
- pico-wakeCon wire compat (verified against its `main` branch, no hardware): UART0 115200 8N1 + USB CDC, CRLF, `S <6 hex>` inputs, `O <4 hex> → color ...` replies, 200ms watchdog (keepalive <200ms required), **no Q/R or N replies** (Sender falls back to legacy path; N is sent without waiting for a reply). Pico UART connections must use 115200, not the 9600 default.
