"""The byte CVEngine sends to the AlertEngine / GameEngine for every analyzed frame, and the flicker filter
that smooths each bit. Contract: RivianHackatonEngine/AlertEngine/CV_ENGINE_INTERFACE.md (sections 2 to 4).

Bit layout, most significant bit first; a higher bit is a higher priority:
  0x80 fell asleep / unresponsive: eyes closed (eyes_closed.py) or slumped posture (slump.py)
  0x40 looked off the road  (head turned away, see eyes_off_road.py)
  0x20 hands off the steering wheel
  0x10 distracted by mobile phone (not wired into the live output yet)
  0x0F reserved, always sent as 0
A bit is a STATE ("the condition is present in this frame"), lightly smoothed here; no duration hold is applied:
the engines own every duration threshold. 0 never proves absence: when nothing can be observed the bit is 0.
"""

ASLEEP, OFF_ROAD, HANDS_OFF, PHONE = 0x80, 0x40, 0x20, 0x10
RESERVED = 0x0F


def format_record(timestamp, flags, occupants=None):
    """The line CVEngine streams for one analyzed frame: 'timestamp,flags,occupants' (seconds, integer 0..255, integer
    count of people in the car). Without `occupants` the older two-field line 'timestamp,flags' is produced."""
    if occupants is None:
        return f'{timestamp:.3f},{int(flags) & 0xFF}'
    return f'{timestamp:.3f},{int(flags) & 0xFF},{max(int(occupants), 0)}'


def pack_flags(asleep=False, off_road=False, hands_off=False, phone=False):
    """-> int 0..255. The reserved low nibble is always 0."""
    return ((ASLEEP if asleep else 0) | (OFF_ROAD if off_road else 0)
            | (HANDS_OFF if hands_off else 0) | (PHONE if phone else 0))


class FlickerFilter:
    """Debounces one bit (target 0.3 to 0.5 s, symmetric so durations are not biased).

    update(t, raw) with raw in {1, 0, None}; None means 'could not observe'. Returns the filtered 0/1.
      - the output switches to `raw` only after raw has disagreed with it for on_s (0 -> 1) or off_s (1 -> 0)
      - None neither confirms nor cancels a pending change, but if the bit stays unobservable for more than
        unknown_s the output falls to 0 (a bit is never held on without observations) and pending changes reset
    Uses only samples up to t (causal). Call reset() between unrelated clips and after a seek.
    """

    def __init__(self, on_s=0.3, off_s=0.3, unknown_s=0.5):
        self.on_s, self.off_s, self.unknown_s = on_s, off_s, unknown_s
        self.reset()

    def reset(self):
        self.out = 0
        self._pending = None
        self._unknown_since = None

    def update(self, t, raw):
        if raw is None:
            if self._unknown_since is None:
                self._unknown_since = t
            if t - self._unknown_since > self.unknown_s:
                self.out, self._pending = 0, None
            return self.out
        self._unknown_since = None
        raw = 1 if raw else 0
        if raw == self.out:
            self._pending = None
            return self.out
        if self._pending is None:
            self._pending = t
        if t - self._pending >= (self.on_s if raw else self.off_s):
            self.out, self._pending = raw, None
        return self.out
