"""Enumerate the system camera's reachable capture modes + measure REAL throughput (read-only).

For each candidate resolution this opens the camera via the device layer's own ``open_capture``
(same backend / FOURCC / focus the demos use), reads back what the driver actually negotiated
(width, height, pixel format), and times frames to get the true delivery rate. Use it to pick a
stream resolution that is both sharp enough for the ROI and fast enough to feel smooth.

Why measure instead of trusting the driver:
  * ``CAP_PROP_FPS`` reads 0 on this unit (IFWATER IMX362, dshow) -- nominal fps is useless here.
  * A requested size that comes back DIFFERENT is not a native mode -- the driver clamped it (often
    to a non-4:3 mode); the table flags those.
  * The pixel format matters more than the resolution: uncompressed YUY2 over USB 2.0 collapses the
    high-res modes to ~2-5 fps, while MJPG sustains ~15 fps at 2592x1944. ``open_capture`` brackets
    the resolution with MJPG so high-res modes stay compressed (see ``_apply_format`` in preview.py).

NOT the Aurora *fundus* camera (patient retinal images -- that is ``arm101_hand.fundus_camera``);
this is the host webcam in ``arm101_hand.system_camera``. No motors, no Aurora link, read-only.

Opens the camera once per candidate (~16 opens), so it takes ~30-60 s. Close any other camera user
(Optomed Client / Windows Camera / a running demo) first, or every open will fail.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_modes.py [--camera N]
                                                  [--backend auto|dshow] [--window-s S]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.system_camera import open_capture  # noqa: E402

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"

# Standard UVC candidates: the 4:3 ladder (this cam is 4:3 native) plus common 16:9 sizes, so the
# table also reveals which off-ratio modes the driver clamps an unsupported request to.
_CANDIDATES: tuple[tuple[int, int], ...] = (
    (320, 240),
    (640, 480),
    (800, 600),
    (1024, 768),
    (1280, 720),
    (1280, 960),
    (1600, 1200),
    (1920, 1080),
    (1920, 1440),
    (2048, 1536),
    (2560, 1440),
    (2560, 1920),
    (2592, 1944),
    (3264, 2448),
    (3840, 2160),
    (4000, 3000),
)


def _fourcc_str(cap: cv2.VideoCapture) -> str:
    """Decode the negotiated CAP_PROP_FOURCC int to its 4-char tag (e.g. 'MJPG', 'YUY2')."""
    v = int(cap.get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip() if v else "????"


def _measure_fps(cap: cv2.VideoCapture, warmup: int, window_s: float) -> float:
    """Real delivery rate: discard ``warmup`` frames (settle), then count frames over ``window_s``."""
    for _ in range(warmup):
        cap.read()
    t0 = time.monotonic()
    got = 0
    while time.monotonic() - t0 < window_s:
        ok, _frame = cap.read()
        if ok:
            got += 1
    dt = time.monotonic() - t0
    return got / dt if dt > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--camera", type=int, default=None, help="USB camera index (default: camera_index from config)"
    )
    ap.add_argument(
        "--backend", choices=("auto", "dshow"), default=None, help="cv2 backend (default: from config)"
    )
    ap.add_argument(
        "--window-s", type=float, default=2.5, help="seconds to count frames per mode (default: 2.5)"
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    idx = args.camera if args.camera is not None else cfg.camera_index
    backend = args.backend if args.backend is not None else cfg.backend
    print(
        f"Enumerating modes for camera {idx} ({backend}), fourcc {cfg.fourcc}, "
        f"focus {cfg.focus} (autofocus {cfg.autofocus}). Close other camera users first.\n"
    )
    header = f"{'requested':>11} | {'negotiated':>11} | {'aspect':>6} | {'fmt':>5} | {'fps':>5} | note"
    print(header)
    print("-" * len(header))
    seen: dict[tuple[int, int, str], float] = {}
    for w, h in _CANDIDATES:
        cap = open_capture(
            idx, backend, fourcc=cfg.fourcc, width=w, height=h, autofocus=cfg.autofocus, focus=cfg.focus
        )
        if not cap.isOpened():
            cap.release()
            print(f"{w}x{h:<7} | {'--':>11} | {'--':>6} | {'--':>5} | {'--':>5} | could not open (busy?)")
            continue
        aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fmt = _fourcc_str(cap)
        fps = _measure_fps(cap, warmup=5, window_s=args.window_s)
        cap.release()
        aspect = f"{aw / ah:.3f}" if ah else "?"
        note = "" if (aw, ah) == (w, h) else "clamped (not native)"
        seen.setdefault((aw, ah, fmt), fps)
        print(f"{w}x{h:<7} | {f'{aw}x{ah}':>11} | {aspect:>6} | {fmt:>5} | {fps:>5.1f} | {note}")
        time.sleep(0.3)  # let the device settle between opens

    print("\nDistinct reachable modes (negotiated -> measured fps):")
    for (aw, ah, fmt), fps in sorted(seen.items(), key=lambda kv: (kv[0][0] * kv[0][1], kv[0])):
        aspect = aw / ah if ah else 0.0
        tag = " (4:3)" if abs(aspect - 4 / 3) < 0.02 else ""
        print(f"  {aw}x{ah:<5} {fmt}  ~{fps:>4.0f} fps{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
