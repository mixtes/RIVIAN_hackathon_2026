"""Tiny stand-in for the car app: speaks the §8 protocol and prints everything it receives.

    uv run python scripts/smoke_client.py                       # start a drive, wait for result
    uv run python scripts/smoke_client.py --end-after 20        # send session.end after 20 s
    uv run python scripts/smoke_client.py --url ws://10.0.2.2:8765/ws

It sends hello + session.start, prints alerts as they arrive, dismisses latched alerts
(`requires_ack`, e.g. EYES_CLOSED) after --dismiss-after seconds like a driver pressing
Dismiss, and acks the drive.result (unless --no-ack, to test re-delivery). Pair it with the
bridge running e.g. `--scenario demo --fast` or drive it from the bridge CLI
(`start phone_call`, `end`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

from websockets.asyncio.client import connect


def envelope(msg_type: str, **payload: object) -> str:
    return json.dumps({
        "v": 1, "type": msg_type, "msg_id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "payload": payload,
    })


def show(msg: dict) -> None:
    t, pl = msg.get("type"), msg.get("payload", {})
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    if t == "alert":
        ack = "  [needs dismiss]" if pl.get("requires_ack") else ""
        print(f"{stamp}  ALERT {pl['level']:<8} {pl['type']:<14} {pl['duration_so_far_s']:>5}s "
              f"\"{pl['message']}\" id={pl['alert_id'][:8]}{ack}")
    elif t == "alert.clear":
        print(f"{stamp}  CLEAR id={pl['alert_id'][:8]}")
    elif t == "drive.result":
        print(f"{stamp}  RESULT score={pl['score']}")
        print(json.dumps(pl, indent=2))
    else:
        print(f"{stamp}  {t}: {json.dumps(pl)}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--session-id", default=None, help="reuse a session id (test resumption)")
    ap.add_argument("--user-id", default=str(uuid.uuid4()))
    ap.add_argument("--end-after", type=float, default=None,
                    help="send session.end after N seconds (default: wait for the bridge)")
    ap.add_argument("--timeout", type=float, default=300, help="give up after N seconds")
    ap.add_argument("--no-ack", action="store_true", help="don't ack drive.result")
    ap.add_argument("--dismiss-after", type=float, default=2.0,
                    help="dismiss latched alerts after N seconds (default 2; negative = never)")
    args = ap.parse_args()
    sid = args.session_id or str(uuid.uuid4())

    async with connect(args.url) as ws:
        await ws.send(envelope("hello", app_version="smoke-client", car_id=None, protocol=1))
        await ws.send(envelope("session.start", session_id=sid, user_id=args.user_id,
                               display_name="Smoke Test", car_id=None,
                               started_at=datetime.now(UTC).isoformat()))
        print(f"session {sid} started; waiting for alerts / drive.result …")

        async def ender() -> None:
            if args.end_after is not None:
                await asyncio.sleep(args.end_after)
                print("sending session.end")
                await ws.send(envelope("session.end", session_id=sid, reason="MANUAL"))

        end_task = asyncio.create_task(ender())
        dismissing: set[str] = set()
        tasks: set[asyncio.Task[None]] = set()

        async def dismiss_later(alert_id: str) -> None:
            await asyncio.sleep(args.dismiss_after)
            print(f"sending alert.dismiss id={alert_id[:8]}")
            await ws.send(envelope("alert.dismiss", alert_id=alert_id, session_id=sid))

        try:
            async with asyncio.timeout(args.timeout):
                async for raw in ws:
                    msg = json.loads(raw)
                    show(msg)
                    pl = msg.get("payload", {})
                    if (msg["type"] == "alert" and pl.get("requires_ack")
                            and args.dismiss_after >= 0 and pl["alert_id"] not in dismissing):
                        dismissing.add(pl["alert_id"])
                        task = asyncio.create_task(dismiss_later(pl["alert_id"]))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    if msg["type"] == "drive.result" and msg["payload"]["session_id"] == sid:
                        if not args.no_ack:
                            await ws.send(envelope("drive.result.ack", session_id=sid))
                            print("acked")
                        return 0
        except TimeoutError:
            print("timed out waiting for drive.result", file=sys.stderr)
            return 1
        finally:
            end_task.cancel()
            for task in tasks:
                task.cancel()
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
