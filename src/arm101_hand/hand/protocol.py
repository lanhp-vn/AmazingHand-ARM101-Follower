"""SCS0009 bus-protocol timing constants (not operator-configurable knobs).

``SERVO_SYNC_S`` is the minimum inter-write pause that lets the SCS0009 bus
arbitrate cleanly between consecutive goal writes to different servo IDs. It is
a hardware-level constant derived from bench observation; operators should not
need to tune it. Motion dwell times (0.005 s settle, 0.01 s move, etc.) are
intentionally NOT here -- they are application-level timing and live in the
scripts that own them.
"""

from __future__ import annotations

#: Minimum inter-command pause (seconds) for SCS0009 bus arbitration.
SERVO_SYNC_S: float = 0.0002
