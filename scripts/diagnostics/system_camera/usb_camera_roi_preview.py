"""Preview the fixed ROI zoom of the arm-mounted USB observation camera (read-only diagnostic).

Validates the region of interest that zooms the live preview + recording onto just the Optomed
Aurora's screen. The arm + hand + camera geometry is fixed, so the screen always lands in the same
place -- ``_ROI`` defaults to the canonical ``AURORA_SCREEN_ROI`` (exactly what
``scripts/demos/grab_trigger_capture.py`` records). Run this, eyeball the framing; to re-tune,
replace ``_ROI`` with a literal (see the note by it), then promote the value into AURORA_SCREEN_ROI.

NOT the Aurora *fundus* camera (patient retinal images -- that is ``arm101_hand.fundus_camera``);
this is the host webcam in ``arm101_hand.system_camera``. No motors, no Aurora link.

Two windows open (both letterboxed, never stretched on resize):
  * "<title> -- ROI zoom"   the ROI crop upscaled to 640x480: the zoomed feed the demo will show.
  * "<title> -- full frame" the full camera frame with the ROI rectangle drawn, so you can see
                            exactly what is being cropped against the whole scene.

Press SPACE to save the framed shot: the ROI zoom (640x480) plus the native-resolution ROI crop
taken straight from the live frame -- at the configured 2592x1944 stream that crop is ~794x595 real
pixels, as sharp as the operating path gets, with no device reopen and no preview freeze. Both files
share a timestamp; the ``<w>x<h>`` suffix tells them apart.

Single-threaded by design (like ``usb_camera_capture.py``): this tool reads + shows frames itself
and polls the TERMINAL for keys via ``msvcrt.kbhit`` -- it can't block on ``getwch`` because the
cv2 windows need ``waitKey`` pumped every loop or they freeze. Shares ``open_capture`` and the
``Roi`` crop helper with the device layer, so the crop math is identical to the demo's.

The full ``opencv-python`` wheel is required for the windows (lerobot's ``opencv-python-headless``
has no HighGUI); ``pyproject.toml`` drops the headless pin so the full build is the sole ``cv2``.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py [--camera N]
                                                       [--backend auto|dshow] [--out-dir DIR]

Keys (focus the TERMINAL, not the windows):
  SPACE   save TWO paired images to --out-dir: the ROI zoom roi_<ts>_640x480.jpg
          + the native-res ROI crop roi_<ts>_<w>x<h>.jpg (same <ts>), cropped from the live frame
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
from arm101_hand.system_camera import (  # noqa: E402
    AURORA_SCREEN_ROI,
    imshow_fit,
    open_capture,
    resolution_mismatch_warning,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"

# ROI to preview. Defaults to the canonical AURORA_SCREEN_ROI (exactly what the demo records), so
# this tool and the demo never drift. To experiment with a different framing, replace this with a
# literal -- e.g. ``from arm101_hand.system_camera import Roi`` then
# ``_ROI = Roi(x=120, y=0, w=320, h=240)`` -- and once validated, promote the value into
# AURORA_SCREEN_ROI in src/arm101_hand/system_camera/roi.py.
_ROI = AURORA_SCREEN_ROI

_ZOOM_SIZE = (640, 480)  # upscale target for the "ROI zoom" window (matches the reference frame)
_GREEN = (0, 255, 0)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_STALL_S = 3.0  # bail if a streaming camera then stops delivering frames for this long


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting).

    Single-threaded design: we can't block on ``getwch`` -- that would freeze the cv2 windows,
    which need ``waitKey`` pumped every loop -- so we poll ``kbhit`` each iteration instead.
    Raises ``KeyboardInterrupt`` on Ctrl+C; swallows arrow / function-key prefixes.
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
    """Write ``img`` to ``out_dir`` as ``roi_<ts>_<w>x<h>.jpg`` and report it. The shared ``ts``
    pairs the two files SPACE saves (the ROI zoom + the full-res ROI crop); the ``<w>x<h>`` suffix
    tells them apart. (The crop is a numpy view, which imwrite accepts.) Returns True on success."""
    h, w = img.shape[:2]
    path = out_dir / f"roi_{ts}_{w}x{h}.jpg"
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
        help="folder for SPACE-saved ROI stills (default: <repo>/media_outputs/camera_captures)",
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else cfg.camera_index
    backend = args.backend if args.backend is not None else cfg.backend
    out_dir = Path(args.out_dir)
    title = f"USB cam {camera_index} (ROI)"
    zoom_title = f"{title} -- ROI zoom"
    full_title = f"{title} -- full frame"

    # Focus is locked per cfg.autofocus/cfg.focus (DSHOW-only) so the ROI you're checking is sharp.
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
    clamp_warn = resolution_mismatch_warning(cfg.width, cfg.height, width, height)
    if clamp_warn:
        print(clamp_warn, file=sys.stderr)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    rx, ry, rw, rh = _ROI.for_frame(width, height)
    # Read back what the device actually accepted -- the requested focus only proves we ASKED.
    # On DSHOW a working VCM reports CAP_PROP_FOCUS near the requested value + AUTOFOCUS prop 2
    # (manual); on MSMF the calls are accepted but the lens never moves (focus stays ~0).
    focus_req = "untouched (autofocus)" if cfg.focus is None else str(cfg.focus)
    focus_actual = cap.get(cv2.CAP_PROP_FOCUS)
    af_actual = cap.get(cv2.CAP_PROP_AUTOFOCUS)
    print(
        f"Window open: {width}x{height} @ {src_fps:.0f} fps (camera-reported).\n"
        f"Focus: autofocus {'off' if not cfg.autofocus else 'on'}, requested {focus_req} -- "
        f"camera reports focus={focus_actual:.0f}, autofocus_prop={af_actual:g}.\n"
        f"ROI (this frame): x={rx} y={ry} w={rw} h={rh}  (constant {_ROI.w}x{_ROI.h} @ "
        f"{_ROI.ref_w}x{_ROI.ref_h}).\n"
        "Keys (focus THIS terminal): SPACE = save ROI zoom + full-res ROI crop, q/ESC = quit."
    )
    if (width, height) != (_ROI.ref_w, _ROI.ref_h):
        print(
            f"  NOTE: live frame {width}x{height} differs from the ROI reference "
            f"{_ROI.ref_w}x{_ROI.ref_h} -- the ROI was rescaled to fit.",
            file=sys.stderr,
        )

    # WINDOW_NORMAL + manual letterbox via imshow_fit: keep the aspect ratio ourselves. KEEPRATIO
    # is Qt-only -- a no-op on this wheel's Win32 backend, where imshow stretches on resize (see
    # preview.py). resizeWindow sets a sane initial size for each window.
    cv2.namedWindow(zoom_title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(zoom_title, *_ZOOM_SIZE)
    cv2.namedWindow(full_title, cv2.WINDOW_NORMAL)
    if width > 0 and height > 0:
        cv2.resizeWindow(full_title, width, height)

    started = time.monotonic()
    last_good: float | None = None  # time of the most recent successful grab
    saved = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                # MSMF can open the device yet fail every grab when the cam is held by another app
                # or was left invalidated by a prior Ctrl+C'd run. Don't spin forever flooding
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

            # ROI zoom: crop to the region, then upscale to the reference size -- this is exactly
            # the zoomed feed the demo's preview + recording will produce.
            zoom = cv2.resize(_ROI.crop(frame), _ZOOM_SIZE, interpolation=cv2.INTER_LINEAR)
            imshow_fit(zoom_title, zoom)

            # Full frame with the ROI rectangle drawn (on a copy so the crop above stays clean).
            x, y, w, h = _ROI.for_frame(frame.shape[1], frame.shape[0])
            overlay = frame.copy()
            cv2.rectangle(overlay, (x, y), (x + w, y + h), _GREEN, 2)
            imshow_fit(full_title, overlay)

            cv2.waitKey(1)  # pump GUI events; required for the windows to stay responsive
            key = _poll_key()
            if key in ("q", "Q", "\x1b"):  # q / ESC
                break
            if key == " ":  # SPACE -> save the ROI zoom + the native-res ROI crop from the live frame
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # shared stem pairs both files
                if _write_image(out_dir, ts, zoom):  # the 640x480 ROI zoom shown above
                    saved += 1
                # Native-res ROI crop straight from the live frame: the stream is already 5 MP, so
                # this is as sharp as the operating path gets -- no device reopen, no preview freeze.
                if _write_image(out_dir, ts, _ROI.crop(frame)):  # ~794x595 at a 2592x1944 stream
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
