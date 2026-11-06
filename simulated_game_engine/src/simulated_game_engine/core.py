"""Protocol + session state machine. Pure: no I/O, time comes from an injectable `Clock`.

Every public method returns the list of messages (envelopes) to send to the car app.
The transport (`server.py`) sends them; tests just inspect them.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from . import protocol as p
from .clock import Clock
from .mapping import map_drive_report, to_wall

log = logging.getLogger("simulated_game_engine.core")

# Re-send every alert that is still showing this often. The engine only reports escalations,
# and the app drops a (non-latched) alert `ttl_s` (8-15 s) after its last update, so without
# this an ongoing action's warning would disappear while the driver is still distracted.
ALERT_REFRESH_S = 5.0


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
    """An alert the app is (or should be) showing, keyed by the engine's `alert_id`."""
    alert_id: str
    action: str
    mono_start: float               # occurrence start (engine time)
    last: p.AlertOut
    sent_at: float                  # monotonic time `last` was last sent


@dataclass
class _Status:
    session: Session | None
    active_actions: list[str]
    configured_actions: list[str]
    pending_results: list[str]
    alerts: dict[str, str] = field(default_factory=dict)  # alert_id -> "ACTION LEVEL [latched]"


class BridgeCore:
    """Wraps one `SafetySystem`. Only one session at a time (one car).

    Engine contract: RivianHackatonEngine/AlertEngine/CAR_FRONTEND_INTERFACE.md. Every engine
    call returns a list of `Alert` / `AlertClear` updates; the engine owns the `alert_id`s.
    """

    def __init__(self, system: Any, clock: Clock, *, engine_version: str,
                 detected_actions: list[str] | tuple[str, ...] | None = None,
                 ended_history: int = 500, alert_refresh_s: float = ALERT_REFRESH_S) -> None:
        self.system = system
        self.clock = clock
        # The backend column is VARCHAR(40); a longer value would make every upload fail with 422.
        self.engine_version = engine_version[:MAX_ENGINE_VERSION_LEN]
        # What real detection emits (the engine's DETECTED_ACTIONS); reported in hello.
        self.detected_actions = (sorted(a.strip().upper() for a in detected_actions)
                                 if detected_actions else None)
        self.alert_refresh_s = alert_refresh_s
        self.session: Session | None = None
        # action (lower-case) -> monotonic start, for actions the simulator holds active
        self.active_actions: dict[str, float] = {}
        # engine alert_id -> what the app is showing for it (until the engine clears it)
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
        """hello.supported_actions: the detected subset if the engine declares one."""
        return self.detected_actions or [a.upper() for a in self.configured_actions]

    def is_configured(self, action: str) -> bool:
        return action.strip().lower() in self.configured_actions

    def status(self) -> _Status:
        return _Status(
            session=self.session,
            active_actions=sorted(self.active_actions),
            configured_actions=self.configured_actions,
            pending_results=list(self.pending_results),
            alerts={i: f"{s.action} {s.last.level}" + (" latched" if s.last.requires_ack else "")
                    for i, s in self._alerts.items()},
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
            p.ALERT_DISMISS: (p.AlertDismissIn, self._on_alert_dismiss),
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
            out.extend(self._env(p.ALERT, s.last) for s in self._alerts.values())
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

    def _on_alert_dismiss(self, env: p.Envelope, msg: p.AlertDismissIn) -> list[p.Message]:
        # Fire-and-forget from the app, which already removed the alert locally. Never error:
        # a late dismiss (session over, alert already cleared) is normal and harmless.
        if self.session is None or (msg.session_id and msg.session_id != self.session.session_id):
            log.info("alert.dismiss %s ignored: not for the active session", msg.alert_id[:8])
            return []
        return self.dismiss(msg.alert_id)

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
        return self._apply(self.system.on_event(action, active, t))

    def tick(self) -> list[p.Message]:
        """Called every --tick-ms so alerts escalate while an action continues."""
        if self.session is None:
            return []
        t = self.clock.monotonic()
        out = self._apply(self.system.tick_all(t))
        out.extend(self._refresh_alerts(t))
        return out

    def dismiss(self, alert_id: str) -> list[p.Message]:
        """The driver dismissed a latched alert (`alert.dismiss`, or the CLI `dismiss`).

        The engine answers with `AlertClear(reason="dismissed")`, plus a new `Alert` (new id)
        if the action is still going. Non-latched or unknown ids give nothing.
        """
        if self.session is None:
            return []
        return self._apply(self.system.dismiss(alert_id, self.clock.monotonic()))

    def dismiss_all(self) -> list[p.Message]:
        """Dismiss every latched alert (CLI stand-in for the driver's Dismiss button)."""
        out: list[p.Message] = []
        for alert_id in [i for i, s in self._alerts.items() if s.last.requires_ack]:
            out.extend(self.dismiss(alert_id))
        return out

    def _apply(self, updates: list[Any] | None) -> list[p.Message]:
        """Engine `Alert` / `AlertClear` objects -> `alert` / `alert.clear`, in order."""
        out: list[p.Message] = []
        for u in updates or []:
            if hasattr(u, "reason"):                       # AlertClear
                self._alerts.pop(u.alert_id, None)
                log.debug("clear %s %s (%s)", u.action, u.alert_id[:8], u.reason)
                out.append(self._env(p.ALERT_CLEAR, p.AlertClearOut(alert_id=u.alert_id)))
            else:                                          # Alert
                out.append(self._alert(u))
        return out

    def _refresh_alerts(self, t: float) -> list[p.Message]:
        """Keep-alive: re-send the latest alert (same id and level, so the app plays no new
        sound) of every alert still showing, so the app does not expire it after `ttl_s`.
        That includes latched alerts whose action already ended (they stay until dismissed)."""
        out: list[p.Message] = []
        for state in self._alerts.values():
            if t - state.sent_at < self.alert_refresh_s:
                continue
            state.last = state.last.model_copy(
                update={"duration_so_far_s": round(max(0.0, t - state.mono_start), 2)})
            state.sent_at = t
            out.append(self._env(p.ALERT, state.last))
        return out

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
        mono_start = float(alert.timestamp) - float(alert.duration_so_far)
        level = alert.level.name
        out = p.AlertOut(
            alert_id=str(alert.alert_id),
            session_id=self.session.session_id,
            type=alert.action.strip().upper(),
            level=level,
            message=alert.message,
            duration_so_far_s=round(float(alert.duration_so_far), 2),
            started_at=p.iso(self.session.wall(mono_start)),
            ttl_s=p.DEFAULT_TTL_S.get(level, 10),
            requires_ack=bool(getattr(alert, "requires_ack", False)),
        )
        self._alerts[out.alert_id] = _AlertState(
            alert_id=out.alert_id, action=out.type, mono_start=mono_start, last=out,
            sent_at=self.clock.monotonic())
        return self._env(p.ALERT, out)

    def _env(self, msg_type: str, payload: BaseModel) -> p.Message:
        return p.envelope(msg_type, payload, self.clock.wall())

    def _error(self, code: str, message: str, ref: str | None = None) -> p.Message:
        log.warning("error %s: %s", code, message)
        return self._env(p.ERROR, p.ErrorOut(code=code, message=message, ref_msg_id=ref))


def _brief(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}"
                     for e in exc.errors())
