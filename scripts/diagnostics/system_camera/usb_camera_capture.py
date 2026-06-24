"""Capture a still from the arm-mounted USB observation camera (read-only diagnostic).

Opens a live cv2 preview of the USB camera that films the Optomed Aurora's screen so you can
frame the shot. Each SPACE saves the current live frame at the camera's configured stream
resolution (``width``x``height`` from ``system_camera_config.yaml`` -- 2560x1440 on the IFWATER
cam) to ``--out-dir``. The preview keeps streaming the whole time; there is no full-resolution
reopen. NOT the Aurora *fundus* camera (patient retinal images -- that is
``arm101_hand.fundus_camera``); this is the host webcam in ``arm101_hand.system_camera``.

Single-threaded by design: unlike ``usb_camera_probe.py`` (which exercises the daemon-threaded
``WebcamPreview``), this tool reads + shows frames itself. Keys are read from the TERMINAL via a
non-blocking ``msvcrt.kbhit()`` poll -- we can't block on ``getwch`` like the probe does, because
the cv2 window needs ``waitKey`` pumped every loop or it freezes. It shares with the device layer:
``open_capture`` (dshow-quirk handling + stream format) and ``imshow_fit`` (manual letterbox --
KEEPRATIO is Qt-only, a no-op on the Win32 backend; see ``preview.py``).

The full ``opencv-python`` wheel is required for the window (lerobot's ``opencv-python-headless``
has no HighGUI); ``pyproject.toml`` drops the headless pin so the full build is the sole ``cv2``.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_capture.py [--camera N] [--backend auto|dshow]
                                                          [--out-dir DIR]

Keys (focus the TERMINAL, not the window):
  SPACE   save the current live frame to --out-dir as usb_cam_<ts>_<w>x<h>.jpg
  q/ESC   quit (Ctrl+C also works)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import msvcrt
import sys
import time
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.system_camera import imshow_fit, open_capture  # noqa: E402

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_GREEN = (0, 255, 0)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_STALL_S = 3.0  # bail if a streaming camera then stops delivering frames for this long


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting).

    Single-threaded design: we can't block on ``getwch`` like the probe does -- that would freeze
    the cv2 window, which needs ``waitKey`` pumped every loop -- so we poll ``kbhit`` each
    iteration instead. Raises ``KeyboardInterrupt`` on Ctrl+C; swallows arrow / function prefixes.
    """
    if not msvcrt.kbhit():
        return ""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume the 2nd byte, ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _write_image(out_dir: Path, ts: str, img) -> bool:
    """Write ``img`` to ``out_dir`` as ``usb_cam_<ts>_<w>x<h>.jpg`` and report it. The ``<w>x<h>``
    suffix records the capture resolution. Returns True on success."""
    h, w = img.shape[:2]
    path = out_dir / f"usb_cam_{ts}_{w}x{h}.jpg"
    if cv2.imwrite(str(path), img):
        print(f"  saved -> {path}  ({w}x{h})")
        return True
    print(f"  WARNING: could not write {path}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--camera",
        type=int,
        default=None,
        help="USB camera index (default: camera_index from system_camera_config.yaml)",
    )
    ap.add_argument(
        "--backend",
        choices=("auto", "dshow"),
        default=None,
        help="cv2 capture backend (default: backend from system_camera_config.yaml)",
    )
    ap.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "media_outputs" / "camera_captures"),
        help="folder for saved stills (default: <repo>/media_outputs/camera_captures)",
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else cfg.camera_index
    backend = args.backend if args.backend is not None else cfg.backend
    out_dir = Path(args.out_dir)
    title = f"USB cam {camera_index} (capture)"

    # Stream at the configured resolution (cfg.width/height); each SPACE saves that live frame as-is.
    # Focus is locked per cfg.autofocus/cfg.focus (DSHOW-only) so the Aurora screen stays sharp.
    print(f"Opening USB camera index {camera_index} ({backend}) ...")
    cap = open_capture(
        camera_index,
        backend,
        fourcc=cfg.fourcc,
        width=cfg.width,
        height=cfg.height,
        autofocus=cfg.autofocus,
        focus=cfg.focus,
    )
    if not cap.isOpened():
        cap.release()
        print(
            f"ERROR: could not open camera index {camera_index}. "
            "Try another --camera N (the arm cam is usually 1; index 0 is often the built-in "
            "webcam) or --backend dshow.",
            file=sys.stderr,
        )
        return 1

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    print(
        f"Streaming {width}x{height} @ {src_fps:.0f} fps; SPACE saves the current frame.\n"
        "Keys (focus THIS terminal): SPACE = capture, q/ESC = quit."
    )

    # WINDOW_NORMAL + manual letterbox via imshow_fit: keep the aspect ratio ourselves. KEEPRATIO
    # is Qt-only -- a no-op on this wheel's Win32 backend (see preview.py). resizeWindow sets a sane
    # initial size.
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    if width > 0 and height > 0:
        cv2.resizeWindow(title, width, height)
    saved = 0
    started = time.monotonic()
    last_good: float | None = None  # time of the most recent successful grab
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                # MSMF can open the device yet fail every grab when the cam is held by another
                # app or was left invalidated by a prior Ctrl+C'd run. Don't spin forever flooding
                # warnings -- bail with guidance once a grace window passes (then `finally` cleans up).
                now = time.monotonic()
                if last_good is None and now - started > _OPEN_GRACE_S:
                    print(
                        f"\nERROR: camera index {camera_index} opened but delivered no frames in "
                        f"{_OPEN_GRACE_S:.0f}s.\n"
                        "  Likely in use by another app (Optomed Client / Windows Camera / Teams / "
                        "browser) or left invalidated by a previous run.\n"
                        "  Fixes: close other camera users; unplug + replug the USB cam; "
                        "or try --backend dshow.",
                        file=sys.stderr,
                    )
                    return 1
                if last_good is not None and now - last_good > _STALL_S:
                    print(
                        f"\nERROR: camera index {camera_index} stopped delivering frames "
                        f"({_STALL_S:.0f}s stall) -- it may have been unplugged or grabbed by "
                        "another app. Reconnect it and retry.",
                        file=sys.stderr,
                    )
                    return 1
                cv2.waitKey(30)  # transient hiccup -- keep pumping, still honor q/ESC
                if _poll_key() in ("q", "Q", "\x1b"):
                    break
                continue

            last_good = time.monotonic()

            disp = frame
            if saved:  # overlay the saved-count on a COPY so the saved frame stays clean
                disp = frame.copy()
                cv2.putText(
                    disp, f"saved: {saved}", (12, disp.shape[0] - 14), _FONT, 0.7, _GREEN, 2, cv2.LINE_AA
                )
            imshow_fit(title, disp)
            cv2.waitKey(1)  # pump GUI events; required for the window to stay responsive

            key = _poll_key()
            if key in ("q", "Q", "\x1b"):  # q / ESC
                break
            if key == " ":  # SPACE -> save the current live frame at the stream resolution
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                if _write_image(out_dir, ts, frame):
                    saved += 1
    except KeyboardInterrupt:
        print("\n^C")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"Camera closed. {saved} image(s) saved to {out_dir}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
