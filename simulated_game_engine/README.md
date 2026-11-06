# simulated_game_engine

A WebSocket **bridge** around the real engine (`SafetySystem` from [`../RivianHackatonEngine/`](../RivianHackatonEngine/)), plus a **driver-event simulator** that stands in for the camera and sensor detection layer.

- The car app connects to `ws://<host>:8765/ws` and speaks the protocol in [`../car_frontend/frontend_specification.md`](../car_frontend/frontend_specification.md) §8.
- Scores and alerts come from the **real** engine code. The bridge imports it unchanged and never modifies it.
- Spec and implementation decisions: [`simulated_game_engine_specification.md`](simulated_game_engine_specification.md) (§8 lists the choices made).

## Install

You need [uv](https://docs.astral.sh/uv/). uv provides Python 3.12 and the dependencies (`websockets`, `pydantic`, `prompt_toolkit`).

```sh
cd simulated_game_engine
uv sync
```

## Run

```sh
cd simulated_game_engine
uv run python -m simulated_game_engine            # interactive CLI, ws://0.0.0.0:8765/ws
uv run sim-engine                                 # same thing, as a script

# headless demo: every session the car starts plays the scripted demo, then ends
uv run python -m simulated_game_engine --no-cli --scenario demo --fast

# headless random behaviour
uv run python -m simulated_game_engine --no-cli --auto --seed 7
```

| Option | Default | |
|---|---|---|
| `--host` / `--port` / `--path` | `0.0.0.0` / `8765` / `/ws` | |
| `--engine-path` | `<repo>/RivianHackatonEngine` (resolved from the package, so it works from any cwd). Also `$SIM_ENGINE_PATH`. | |
| `--tick-ms` | `100` | how often alerts are re-evaluated so they escalate |
| `--tick-mode` | `per-action` | see Known limitations |
| `--no-cli` | off | headless. This is automatic when stdin is not a TTY, for example when run in the background. |
| `--auto` | off | random driver behaviour during sessions |
| `--scenario demo` | – | run the demo on **every** session the car starts |
| `--speed N` / `--fast` | 1 / 10 | speeds up the clean-driving gaps only. Action holds stay real-time, since the engine thresholds are real seconds. |
| `--seed` | random | seed for auto mode |
| `--engine-version` | `rhe/unknown+bridge-0.1.0` (truncated to 40 chars, the backend limit) | reported in `hello` and `drive.result` |
| `-v` | off | log the full JSON of every message |

### Connecting the car app
- **AAOS emulator:** `ws://10.0.2.2:8765/ws` (`10.0.2.2` is the emulator's alias for the host machine).
- Same machine: `ws://127.0.0.1:8765/ws`. A real head unit needs the host's LAN IP (the bridge binds `0.0.0.0`).

### CLI commands

| Command | |
|---|---|
| `status` | client, session, active actions, alert ids, configured actions, unacked results |
| `start <action>` / `stop <action>` | begin or end a continuous action, e.g. `start phone_call` |
| `pulse <action> <seconds>` | start now, stop automatically after N seconds |
| `auto on\|off` | random behaviour: every 15–45 s, one configured action is held for 0.5–7 s |
| `scenario demo [fast\|<speed>]` | about 2 minutes: phone call 4.5 s (VISUAL → AUDIO), eyes off road 5 s (AUDIO → HAPTIC → CRITICAL), a clean stretch, then `end`. It waits for a session if none is active. |
| `scenario stop` | cancel it |
| `end` | end the **whole drive** now. Sends `drive.result` (`extra.end_reason = "ENGINE"`). Takes no arguments: to end one action, use `stop <action>`. |
| `drop` | close the car's connection, to test reconnects |
| `quit` (or Ctrl-D) | stop the bridge |

Actions are the engine's keys (`phone_call`, `eyes_off_road`; case-insensitive). If you use an action the engine doesn't configure, the CLI shows a warning. The event is still forwarded, and the engine ignores it.

### Smoke client (a stand-in for the car app)

```sh
# terminal 1
uv run python -m simulated_game_engine --no-cli --scenario demo --fast
# terminal 2: hello → session.start → prints alerts → drive.result → ack
uv run python scripts/smoke_client.py
uv run python scripts/smoke_client.py --end-after 10     # app-initiated session.end
uv run python scripts/smoke_client.py --no-ack           # leave the result unacked (re-delivery)
```

## Architecture

```
src/simulated_game_engine/
├── engine_loader.py   finds RivianHackatonEngine, puts it on sys.path, loads main.py → SafetySystem
├── clock.py           Clock protocol: SystemClock (monotonic + wall UTC), FakeClock for tests
├── protocol.py        envelope + payload models (pydantic), message/error constants, iso()
├── mapping.py         DriveReport → drive.result (§8.5), monotonic → wall-clock conversion
├── core.py            BridgeCore: protocol + session state machine. Pure: no I/O; every call
│                      returns the messages to send. Alert ids, pending results, resumption.
├── server.py          BridgeServer: websockets transport, single-client policy, ticker task
├── simulator.py       Simulator: start/stop/pulse, auto mode, scenarios (injectable sleep)
├── console.py         interactive prompt_toolkit CLI
└── app.py             argument parsing, wiring, headless mode
```

- **Clock:** the engine gets `time.monotonic()` timestamps. At `session.start` the bridge records `(wall_start, mono_start)`, and each engine time `t` maps to wall clock as `wall_start + (t - mono_start)`.
- **Alert ids:** the bridge keeps `action → alert_id`. The id is new on the occurrence's first alert, reused for every escalation, and sent as `alert.clear` when the action stops. All ids are cleared at session end.
- **Reliability:** `session.start` is idempotent (same id = resume, and the bridge re-sends the alerts still showing). A `drive.result` is kept until `drive.result.ack` and re-sent after `hello` on every connection.
- **One client:** the newest connection wins. The old one is closed with code `4000`. The session survives.

## Tests

```sh
uv run pytest          # 42 tests: core state machine, mapping, simulator, CLI, real WebSocket round trips
uv run ruff check
```

The tests use the real `SafetySystem` with explicit (fake-clock) timestamps. `tests/test_server_ws.py` runs real WebSocket round trips on an ephemeral port: the full flow, reconnect with resumption, re-delivery of an unacked result, newest-client-wins, the 404 path, and error handling.

## Known limitations
- **`AlertEngine.tick()` returns only the most severe alert** when several actions escalate in the same tick. The bridge works around this through the public API: in the default `--tick-mode per-action` it re-reports each active action as "still active", which returns each action's own escalation. `--tick-mode engine` uses `tick()` as-is and loses simultaneous escalations (covered by a test). The fix is requested from the engine team in `requested_changes.md`.
- The engine configures only `phone_call` and `eyes_off_road` so far. Other actions are accepted and ignored.
- The engine has no version string, so `engine_version` reports `unknown` until it exposes `__version__`, which the bridge picks up automatically.
- Unacked drive results live in memory only. Restarting the bridge loses them.
- An alert is sent only on escalation. If an action lasts longer than the alert's `ttl_s` without escalating, the app may hide the banner before `alert.clear`.
- The engine has no "clear" signal. Clears are derived from `on_event(action, is_active=False)`.
