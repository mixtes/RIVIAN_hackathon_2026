"""Protocol + session state machine. Pure: no I/O, time comes from an injectable `Clock`.

Every public method returns the list of messages (envelopes) to send to the car app.
The transport (`server.py`) sends them; tests just inspect them.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from . import protocol as p
from .clock import Clock
from .mapping import map_drive_report, to_wall

log = logging.getLogger("simulated_game_engine.core")

TickMode = Literal["per-action", "engine"]



MAX_ENGINE_VERSION_LEN = 40  # backend drives.engine_version is VARCHAR(40)

class NoActiveSessionError(RuntimeError):
    pass


@dataclass
class Session:
    session_id: str
    user_id: str
    display_name: str | None
    car_id: str | None
    wall_start: datetime
    mono_start: float

    def wall(self, t: float) -> datetime:
        return to_wall(t, wall_start=self.wall_start, mono_start=self.mono_start)


@dataclass
class _AlertState:
    alert_id: str
    last: p.AlertOut | None = None


@dataclass
class _Status:
    session: Session | None
    active_actions: list[str]
    configured_actions: list[str]
    pending_results: list[str]
    alert_ids: dict[str, str] = field(default_factory=dict)


class BridgeCore:
    """Wraps one `SafetySystem`. Only one session at a time (one car)."""

    def __init__(self, system: Any, clock: Clock, *, engine_version: str,
                 tick_mode: TickMode = "per-action", ended_history: int = 500) -> None:
        self.system = system
        self.clock = clock
        # The backend column is VARCHAR(40); a longer value would make every upload fail with 422.
        self.engine_version = engine_version[:MAX_ENGINE_VERSION_LEN]
        self.tick_mode: TickMode = tick_mode
        self.session: Session | None = None
        # action (lower-case) -> monotonic start, for actions the simulator holds active
        self.active_actions: dict[str, float] = {}
        # action (lower-case) -> alert id of the current occurrence
        self._alerts: dict[str, _AlertState] = {}
        # session_id -> drive.result envelope, kept until drive.result.ack (§8.4 rule 3)
        self.pending_results: OrderedDict[str, p.Message] = OrderedDict()
        self._ended: deque[str] = deque(maxlen=ended_history)

    # ---- introspection -------------------------------------------------------------------

    @property
    def configured_actions(self) -> list[str]:
        """Lower-case action keys the engine knows (AlertEngine and/or GameEngine config)."""
        keys = set(self.system.alerts.config) | set(self.system.game.config)
        return sorted(k.lower() for k in keys)

    @property
    def supported_actions(self) -> list[str]:
        return [a.upper() for a in self.configured_actions]

    def is_configured(self, action: str) -> bool:
        return action.strip().lower() in self.configured_actions

    def status(self) -> _Status:
        return _Status(
            session=self.session,
            active_actions=sorted(self.active_actions),
            configured_actions=self.configured_actions,
            pending_results=list(self.pending_results),
            alert_ids={a: s.alert_id for a, s in self._alerts.items()},
        )

    # ---- inbound messages ----------------------------------------------------------------

    def handle_raw(self, raw: str | bytes) -> list[p.Message]:
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            return [self._error(p.BAD_REQUEST, f"invalid JSON: {exc}")]
        if not isinstance(obj, dict):
            return [self._error(p.BAD_REQUEST, "message must be a JSON object")]
        return self.handle(obj)

    def handle(self, obj: dict[str, Any]) -> list[p.Message]:
        try:
            env = p.Envelope.model_validate(obj)
        except ValidationError as exc:
            return [self._error(p.BAD_REQUEST, f"invalid envelope: {_brief(exc)}",
                                obj.get("msg_id"))]
        if env.v != p.PROTOCOL_VERSION:
            return [self._error(p.UNSUPPORTED_VERSION,
                                f"protocol v{env.v} not supported (bridge speaks v1)", env.msg_id)]

        handlers = {
            p.HELLO: (p.HelloIn, self._on_hello),
            p.SESSION_START: (p.SessionStartIn, self._on_session_start),
            p.SESSION_END: (p.SessionEndIn, self._on_session_end),
            p.DRIVE_RESULT_ACK: (p.DriveResultAckIn, self._on_result_ack),
        }
        entry = handlers.get(env.type)
        if entry is None:
            log.warning("ignoring unknown message type %r", env.type)
            return [self._error(p.UNKNOWN_TYPE, f"unknown message type {env.type!r}", env.msg_id)]
        model, handler = entry
        try:
            payload = model.model_validate(env.payload)
        except ValidationError as exc:
            return [self._error(p.BAD_REQUEST, f"invalid {env.type} payload: {_brief(exc)}",
                                env.msg_id)]
        return handler(env, payload)  # type: ignore[operator]

    def _on_hello(self, env: p.Envelope, hello: p.HelloIn) -> list[p.Message]:
        if hello.protocol != p.PROTOCOL_VERSION:
            return [self._error(p.UNSUPPORTED_VERSION,
                                f"protocol {hello.protocol} not supported (bridge speaks 1)",
                                env.msg_id)]
        out = [self._env(p.HELLO, p.HelloOut(
            engine_version=self.engine_version,
            active_session_id=self.session.session_id if self.session else None,
            supported_actions=self.supported_actions,
        ))]
        # §8.4 rule 3: re-deliver every unacked result on (re)connect.
        out.extend(self.pending_results.values())
        return out

    def _on_session_start(self, env: p.Envelope, start: p.SessionStartIn) -> list[p.Message]:
        sid = str(start.session_id)
        if self.session is not None and self.session.session_id == sid:
            # §8.4 rule 1: resumption. Continue the drive; re-send alerts still showing.
            log.info("session %s resumed", sid[:8])
            out = [self._env(p.SESSION_ACK, p.SessionAckOut(session_id=sid))]
            out.extend(self._env(p.ALERT, s.last) for s in self._alerts.values() if s.last)
            return out
        if sid in self.pending_results or sid in self._ended:
            # Never restart a finished drive under the same id (it is the backend drive id).
            out = [self._error(p.SESSION_ENDED, f"session {sid} already ended", env.msg_id)]
            if sid in self.pending_results:
                out.append(self.pending_results[sid])
            return out

        out = []
        if self.session is not None:
            log.info("session.start for %s while %s is active: ending the old one",
                     sid[:8], self.session.session_id[:8])
            out.extend(self._end("DRIVER_SWITCH"))
        t = self.clock.monotonic()
        self.system.start_drive(t)
        self.session = Session(
            session_id=sid, user_id=str(start.user_id), display_name=start.display_name,
            car_id=start.car_id, wall_start=self.clock.wall(), mono_start=t,
        )
        self.active_actions.clear()
        self._alerts.clear()
        log.info("session %s started for %s", sid[:8], start.display_name or start.user_id)
        out.append(self._env(p.SESSION_ACK, p.SessionAckOut(session_id=sid)))
        return out

    def _on_session_end(self, env: p.Envelope, end: p.SessionEndIn) -> list[p.Message]:
        sid = str(end.session_id)
        if self.session is not None and self.session.session_id == sid:
            return self._end(end.reason)
        if sid in self.pending_results:
            return [self.pending_results[sid]]  # idempotent: re-send the same result
        return [self._error(p.NO_ACTIVE_SESSION, f"no active session {sid}", env.msg_id)]

    def _on_result_ack(self, env: p.Envelope, ack: p.DriveResultAckIn) -> list[p.Message]:
        if self.pending_results.pop(str(ack.session_id), None) is not None:
            log.info("drive.result %s acked", str(ack.session_id)[:8])
        return []

    # ---- simulator inputs ------------------------------------------------------------------

    def set_action(self, action: str, active: bool) -> list[p.Message]:
        """A driver-action event: `active=True` started / still going, `False` ended."""
        action = action.strip().lower()
        if self.session is None:
            raise NoActiveSessionError("no active session; ignoring driver event")
        t = self.clock.monotonic()
        if active:
            self.active_actions.setdefault(action, t)
        else:
            self.active_actions.pop(action, None)
        out: list[p.Message] = []
        alert = self.system.on_event(action, active, t)
        if alert is not None:
            out.append(self._alert(alert))
        if not active and action in self._alerts:
            out.append(self._env(p.ALERT_CLEAR,
                                 p.AlertClearOut(alert_id=self._alerts.pop(action).alert_id)))
        return out

    def tick(self) -> list[p.Message]:
        """Called every --tick-ms so alerts escalate while an action continues."""
        if self.session is None:
            return []
        t = self.clock.monotonic()
        if self.tick_mode == "engine":
            # AlertEngine.tick() returns only the most severe alert (engine limitation).
            alerts = [self.system.tick(t)]
        else:
            # Re-report each active action as "still active" (documented engine input), so
            # every action's escalation comes back, not just the most severe one.
            alerts = [self.system.on_event(a, True, t)
                      for a in self.system.alerts.active_actions()]
        return [self._alert(a) for a in alerts if a is not None]

    def end_session(self, reason: str = p.ENGINE_END_REASON) -> list[p.Message]:
        """End the drive from the bridge side (CLI `end`, scenario): spontaneous result."""
        if self.session is None:
            raise NoActiveSessionError("no active session to end")
        return self._end(reason)

    # ---- internals -------------------------------------------------------------------------

    def _end(self, reason: str) -> list[p.Message]:
        session = self.session
        assert session is not None
        t = self.clock.monotonic()
        report = self.system.end_drive(t)
        out = [self._env(p.ALERT_CLEAR, p.AlertClearOut(alert_id=s.alert_id))
               for s in self._alerts.values()]
        self._alerts.clear()
        self.active_actions.clear()
        result = map_drive_report(
            report,
            session_id=session.session_id, user_id=session.user_id,
            wall_start=session.wall_start, mono_start=session.mono_start, end_mono=t,
            engine_version=self.engine_version,
            is_penalized=lambda occ: self.system.game.score_occurrence(occ) > 0,
            end_reason=reason,
        )
        msg = self._env(p.DRIVE_RESULT, result)
        self.pending_results[session.session_id] = msg
        self._ended.append(session.session_id)
        self.session = None
        log.info("session %s ended (%s): score %.2f", session.session_id[:8], reason, report.score)
        out.append(msg)
        return out

    def _alert(self, alert: Any) -> p.Message:
        assert self.session is not None
        action = alert.action.strip().lower()   # bridge keys are lower-case; engine may differ
        state = self._alerts.get(action)
        if state is None:
            state = self._alerts[action] = _AlertState(alert_id=str(uuid.uuid4()))
        level = alert.level.name
        state.last = p.AlertOut(
            alert_id=state.alert_id,
            session_id=self.session.session_id,
            type=alert.action.upper(),
            level=level,
            message=alert.message,
            duration_so_far_s=round(float(alert.duration_so_far), 2),
            started_at=p.iso(self.session.wall(alert.timestamp - alert.duration_so_far)),
            ttl_s=p.DEFAULT_TTL_S.get(level, 10),
        )
        return self._env(p.ALERT, state.last)

    def _env(self, msg_type: str, payload: BaseModel) -> p.Message:
        return p.envelope(msg_type, payload, self.clock.wall())

    def _error(self, code: str, message: str, ref: str | None = None) -> p.Message:
        log.warning("error %s: %s", code, message)
        return self._env(p.ERROR, p.ErrorOut(code=code, message=message, ref_msg_id=ref))


def _brief(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}"
                     for e in exc.errors())
