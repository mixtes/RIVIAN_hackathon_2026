# Simulated Game Engine Specification: Engine Bridge + Driver-Event Simulator

> Status: Draft v1.0 (2026-11-06) · Scope: Hackathon MVP
> Extracted from the car app spec (formerly its §15.3).
> **Wire protocol:** [`../car_frontend/frontend_specification.md`](../car_frontend/frontend_specification.md) §8 is the source of truth for every message, payload, and reliability rule. This document covers only how the bridge implements that protocol.
> Engine library: [`../RivianHackatonEngine/`](../RivianHackatonEngine/) (owned by the engine team, **read-only** for us).

---

## 1. Purpose

A standalone **Python 3.12** project in the top-level folder `simulated_game_engine/`. It has two jobs:
1. **Bridge:** wrap the real engine library (`SafetySystem` from `RivianHackatonEngine/main.py`) and serve the WebSocket protocol (car app spec §8) to the car app.
2. **Simulator:** stand in for the camera/sensor detection layer by generating **driver-action events** (`on_event(action, is_active)`) from a CLI, random auto mode, or scripted scenarios.

Scores and alerts therefore come from the **real** engine logic. When real detection exists later, only the event source changes.

```
 driver-action events            ┌──────────── simulated_game_engine (bridge) ─────────────┐
 (CLI / scenarios now,  ───────▶ │  SafetySystem (RivianHackatonEngine, imported as-is)    │
  camera detection later)        │    ├─ AlertEngine ──▶ alert / alert.clear ──┐           │
                                 │    └─ GameEngine  ──▶ drive.result ─────────┤           │
                                 │                         WebSocket server ws://…:8765/ws │
                                 └─────────────────────────────────────────────┬───────────┘
                                                                               ▼
                                                                         Car app (client)
```

## 2. Rules (from `RULES.md`)
- `RivianHackatonEngine/` is **imported, never modified**. Add its path to `sys.path` (`--engine-path`, default `../RivianHackatonEngine`), because its modules use top-level imports (`from AlertEngine.AlertEngine import …`).
- Anything the bridge needs changed in the engine goes into `requested_changes.md` at the repo root (see §7).

## 3. Stack and invocation
`websockets` (asyncio), `pydantic` for message models, `prompt_toolkit` (or plain `aioconsole`) for the CLI. Run with `python -m simulated_game_engine` (or `uv run`). Options: `--host 0.0.0.0 --port 8765 --engine-path ../RivianHackatonEngine --tick-ms 100`.

## 4. Bridge behavior
- **Protocol:** implements every message in car app spec §8.3 and all reliability rules in §8.4: idempotent `session.start`, `hello` with `active_session_id` and `supported_actions` (= the keys of the engine's configs, upper-cased), session resumption after reconnect, and keeping and re-sending unacked `drive.result` messages.
- **Session:** `session.start` → `SafetySystem.start_drive(t)`. `session.end` (or CLI `end`) → `end_drive(t)` → map `DriveReport` → `drive.result` (mapping table in car app spec §8.5).
- **Clock:** the bridge passes its own `time.monotonic()` values as `timestamp` to the engine. At session start it records `(wall_start, mono_start)` and converts any engine time `t` to wall clock as `wall_start + (t - mono_start)`.
- **Events:** each simulated event calls `on_event(action_lowercase, is_active, t)`. If it returns an `Alert`, the bridge sends `alert`.
- **Ticker:** every `--tick-ms` it calls `tick(t)`, so alerts escalate while an action continues. A returned `Alert` is sent as `alert`.
- **Alert IDs:** the engine has no IDs. The bridge keeps `action → alert_id`: a new UUID when an action starts (the first alert for it), reused for every escalation of that occurrence. When the action ends (`is_active=False`), the bridge sends `alert.clear` for that ID and forgets it. At session end it clears all IDs.
- **Mapping `Alert` → `alert`:** `type = action.upper()`, `level = alert.level.name`, `message = alert.message`, `duration_so_far_s = alert.duration_so_far`, `started_at` = the occurrence start converted to wall clock.
- Actions the engine doesn't configure are still forwarded to `on_event` (the engine ignores them). The CLI prints a warning so the operator knows nothing will happen.

## 5. Simulator inputs (CLI commands)
- `status`: connection, active session, active actions, configured actions.
- `start <action>` / `stop <action>`: begin or end a continuous action (for example `start phone_call`).
- `pulse <action> <seconds>`: start now and stop automatically after N seconds.
- `auto on|off`: random driver behavior during a session. Every 15–45 s it picks a configured action and holds it for 0.5–7 s, so you see escalations and clears.
- `scenario demo`: a scripted ~2-minute drive: a short phone call (VISUAL → AUDIO), eyes off road for 5 s (AUDIO → HAPTIC → CRITICAL), a clean stretch, then `end`.
- `end`: end the drive and send `drive.result`.
- `drop`: close the connection, to test reconnects. `quit`.

## 6. Tests and docs
- **Tests:** pytest for the protocol state machine (resume, re-delivery, idempotency), the `DriveReport` → `drive.result` mapping, alert-ID assignment and clears, and the clock conversion. Tests use the real `SafetySystem` with explicit timestamps.
- **README:** run instructions, including the emulator note: the app connects to `ws://10.0.2.2:8765/ws`.

## 7. Changes to request from the engine team
These go in `requested_changes.md`. The bridge does not make them itself.
1. `AlertEngine.tick()` returns only the single most severe alert when several actions escalate in the same tick, so the others are lost. Ask for it to return all of them (or add a public `evaluate(action, ts)`).
2. Use the canonical action keys for new actions: `eyes_closed`, `yawn`, `hands_off_wheel`, `phone_use`, `eating_drinking`, `smoking`. These map 1:1 to the shared mistake types.
3. Optional: emit an explicit clear signal from `AlertEngine` (their current TODO). The bridge already derives clears from `is_active=False`.
4. Optional: expose an engine version string (`__version__`), so `drive.result.engine_version` is meaningful.

## 8. Implementation decisions (v0.1.0)

Choices made where the protocol (car app spec §8) left room. The car app can rely on them.

| Topic | Decision |
|---|---|
| Run | From `simulated_game_engine/`: `uv run python -m simulated_game_engine` (or `uv run sim-engine`). The package is `src/simulated_game_engine/`. Extra flags: `--no-cli` (also automatic when stdin isn't a TTY), `--auto`, `--scenario demo` (runs on **every** session the car starts), `--speed N` / `--fast`, `--seed`, `--engine-version`, `--tick-mode`, `-v`. The engine path also comes from `$SIM_ENGINE_PATH`; the default is resolved relative to the package, so the cwd doesn't matter. |
| Escalation ticker | Default `--tick-mode per-action`: on each tick, every action in `AlertEngine.active_actions()` is re-reported as `on_event(action, True, t)`. The engine documents this as "still happening", and `GameEngine` ignores repeats, so **every** action's escalation is delivered. This uses only the public API and works around the `tick()` limitation (§7 item 1). `--tick-mode engine` uses `SafetySystem.tick()` as-is. |
| `ttl_s` | Per level, from car app spec §9.1: VISUAL 8, AUDIO 8, HAPTIC 10, CRITICAL 15. |
| Alert re-send on resume | After a resumed `session.start` (same id), the bridge sends `session.ack` and then re-sends the latest `alert` for each still-active occurrence (same `alert_id`). Alerts produced while no client is connected are dropped (fire-and-forget). |
| `session.start` with a new id while a session is active | The active session is ended with reason `DRIVER_SWITCH` (its `drive.result` is sent first), then the new one starts and is acked. |
| `session.start` for a session that already ended | Rejected with `error` `SESSION_ENDED`, and the `drive.result` is re-sent if it is still unacked. A finished drive is never restarted under the same id, because that id is the backend drive id. |
| `session.end` for an ended-but-unacked session | Re-sends the same `drive.result`. For an unknown id: `error` `NO_ACTIVE_SESSION`. |
| Bridge-initiated end | CLI `end` or the scenario's end produces a spontaneous `drive.result` with `extra.end_reason = "ENGINE"`. Otherwise `extra.end_reason` is the reason from `session.end`. |
| `drive.result` re-delivery | The identical envelope (same `msg_id`) is re-sent right after the bridge's `hello` on every connection, until `drive.result.ack`. Pending results live **in memory only**, so a bridge restart loses them. |
| `mistakes[]` | Lists **every** action the GameEngine is configured for, including `count: 0` (durations 0.0). `extra` also carries `engine_drive_duration_s`. |
| Errors sent | `UNSUPPORTED_VERSION` (envelope `v` ≠ 1 or `hello.protocol` ≠ 1), `BAD_REQUEST` (invalid JSON, envelope, or payload), `UNKNOWN_TYPE`, `NO_ACTIVE_SESSION`, `SESSION_ENDED`, `INTERNAL_ERROR`. The connection stays open after an error. |
| Multiple clients | One car. **The newest connection wins.** The previous one is closed with code `4000` ("replaced by a newer connection"). The session belongs to the bridge, so it survives. |
| Paths | Only `/ws` is served. Other paths get HTTP 404. |
| Driver events without a session | Rejected. The CLI prints "no active session". `scenario demo` waits for the car's `session.start`. |
| Scenario speed | `--speed` / `--fast` (×10) shortens only the clean-driving gaps. Action holds stay real-time, because the engine's thresholds are real seconds. At normal speed the demo takes about 120 s; with `--fast`, about 21 s. |
| `engine_version` | `rhe/<engine __version__ or "unknown">+bridge-<bridge version>` (`rhe` = RivianHackatonEngine). It can be overridden with `--engine-version`. Always truncated to **40 characters**, because the backend column is `VARCHAR(40)`. |
