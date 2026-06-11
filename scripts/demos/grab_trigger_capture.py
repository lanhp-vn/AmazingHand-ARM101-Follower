"""Grab the camera, stream the arm-mounted USB cam, and click the index finger to capture.

On startup a live preview window opens for the arm-mounted USB camera (it points at the
Aurora's screen) -- press 'r' anytime to start/stop recording that feed to a clip. The
preview is best-effort: if the camera will not open, the demo continues without it.

Runs the staged arm+hand grab (see ``arm101_hand.scripts.grab_common``); once both
devices hold ``grab`` under torque, each SPACE runs ONE capture cycle:
  press the index onto the Aurora's shutter -> hold -> release ->
  diff the camera filelist -> pull the new image(s) + metadata into ``media_outputs/fundus_images/``.

After a successful pull, the freshly saved image pops up in a second window (titled
"... -- last capture") and stays there until the next capture replaces it. The popup rides on
the USB preview's cv2 thread, so it only appears when that preview window is available.

PREREQUISITES (set on the camera; the API cannot):
  * Capture mode = STILL imaging  (a held press in VIDEO mode records a clip!)
  * Quick imaging = ON            (else each capture waits in an on-device preview)
  * Optomed Client CLOSED         (the Pictor API allows one client connection)
  * A study/patient selected      (new images land in the current study folder)

Controls (torque ON the whole time; focus the TERMINAL, not the preview window):
  SPACE       fire one capture cycle (press -> hold -> release -> pull)
  r           start / stop recording the USB preview to media_outputs/camera_recordings/
  [ / ]       shrink / grow the press depth (applies to the NEXT press)
  q / Ctrl+C  stop and go to the exit prompt (Enter releases in place, 'h' reverses)

Usage:
  uv run python scripts/demos/grab_trigger_capture.py
"""

from __future__ import annotations

import datetime as _dt
import math
import msvcrt
import sys
import time
from pathlib import Path

from arm101_hand.config import (
    FINGER_SERVO_IDS,
    SystemCameraConfig,
    load_fundus_config,
    load_system_camera_config,
)
from arm101_hand.fundus_camera import (
    CameraError,
    PictorClient,
    classify_capture,
    pull_file,
    save_capture,
    snapshot_filenames,
    wait_for_new_files,
)
from arm101_hand.hand import drive_finger, load_warning, read_finger
from arm101_hand.hand.index_trigger import TriggerState, apply_action, key_to_action, press_base
from arm101_hand.hand.pose_jog import HandJogState, format_hand_status
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo
from arm101_hand.system_camera import WebcamPreview

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _REPO_ROOT / "src" / "arm101_hand" / "data"
_FUNDUS_CONFIG_PATH = _DATA_DIR / "fundus_config.yaml"
_SYSTEM_CAMERA_CONFIG_PATH = _DATA_DIR / "system_camera_config.yaml"
_STATIC_FINGERS = ("middle", "ring", "thumb")


def _read_key() -> str:
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume + ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _scalar(v: object) -> float:
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def _status_line(state: TriggerState, base: int, side: int, others: dict) -> str:
    fingers = {"index": (int(base), int(side)), **others}
    return format_hand_status(HandJogState(active="index", step=state.delta, fingers=fingers))


def _print_prereqs() -> None:
    print(
        "PREREQUISITES (set on the camera): Still mode | Quick imaging ON | "
        "Optomed Client CLOSED | study selected"
    )


def _print_poll(elapsed: float, n_new: int, note: str) -> None:
    """Per-poll progress line during the image-pull wait (counts elapsed seconds)."""
    detail = note if note else f"{n_new} new file(s) seen"
    print(f"    +{elapsed:4.1f}s  {detail}")


def _start_preview(scfg: SystemCameraConfig) -> WebcamPreview | None:
    """Best-effort: open the arm-cam preview window. None if disabled or the cam won't open."""
    if not scfg.enabled:
        return None
    preview = WebcamPreview(
        index=scfg.camera_index,
        window_title=scfg.window_title,
        record_dir=_REPO_ROOT / scfg.record_dir,
        fps=scfg.fps,
        backend=scfg.backend,
    )
    if not preview.start():
        print(
            f"WARNING: USB preview camera index {scfg.camera_index} not available -- "
            "continuing without a preview window.",
            file=sys.stderr,
        )
        return None
    print(
        f"USB preview: camera {scfg.camera_index} {preview.width}x{preview.height}"
        f"@{preview.src_fps:.0f}fps -- 'r' starts/stops recording."
    )
    return preview


def main() -> int:
    cfg = load_fundus_config(_FUNDUS_CONFIG_PATH)
    conn, cap = cfg.connection, cfg.capture
    fundus_dir = _REPO_ROOT / cap.fundus_dir

    # Live arm-cam preview window (best-effort, independent of the Aurora link). Up before the
    # arm moves so the operator can watch the Optomed screen throughout.
    preview = _start_preview(load_system_camera_config(_SYSTEM_CAMERA_CONFIG_PATH))

    camera = PictorClient(
        host=conn.host,
        discovery_port=conn.discovery_port,
        message_port=conn.message_port,
        discover_timeout_s=conn.discover_timeout_s,
        connect_timeout_s=conn.connect_timeout_s,
        io_timeout_s=conn.io_timeout_s,
    )
    _print_prereqs()
    print("Connecting to camera ...")
    try:
        camera.ensure_connected()
        status = camera.get_status()
    except CameraError as e:
        print(f"ERROR: camera not ready: {e}", file=sys.stderr)
        if preview is not None:
            preview.stop()
        return 1
    serial = camera.info.serial if camera.info else "?"
    print(f"Camera ready: serial={serial} sw={status.sw_version} wifi={status.wifi_version}")

    trigger_no = {"n": 0}  # mutable counter shared with the hook

    def _trigger_loop(ctx: GrabHoldContext) -> None:
        c = ctx.hand
        calib = ctx.hand_calib
        tuning = ctx.hand_cfg.tuning
        index_block = calib.fingers["index"]
        lim = index_block.limits
        id1, id2 = FINGER_SERVO_IDS["index"]
        wait_kw = {
            "tolerance_rad": math.radians(tuning.pose_margin_deg),
            "timeout_s": tuning.pose_timeout_s,
            "poll_s": tuning.pose_poll_s,
        }
        out_base, side = read_finger(c, "index", index_block)
        others = {name: read_finger(c, name, calib.fingers[name]) for name in _STATIC_FINGERS}
        state = TriggerState(out_base=out_base, side=side)

        print("Trigger capture: SPACE = capture, r = record, [ / ] = press depth, q = exit")
        _print_prereqs()
        print("  " + _status_line(state, out_base, side, others))

        try:
            while True:
                key = _read_key()
                if key in ("r", "R"):  # toggle USB-preview recording (separate from the capture cycle)
                    if preview is None:
                        print("  (no USB preview window -- nothing to record)")
                    else:
                        rec_path = preview.toggle_record()
                        print(f"  recording -> {rec_path}" if rec_path else "  recording stopped.")
                    continue
                action = key_to_action(key)
                if action is None:
                    continue
                if action == "quit":
                    break
                if action != "fire":
                    state = apply_action(state, action)
                    base_now, side_now = read_finger(c, "index", index_block)
                    print("  " + _status_line(state, base_now, side_now, others))
                    continue

                # ---- fire one capture cycle ----
                # (1) Baseline filelist BEFORE pressing -- retried past the camera's intermittent
                #     GET_FILELIST CODE_FAIL so a single hiccup doesn't waste this press.
                try:
                    camera.ensure_connected()
                    before = snapshot_filenames(camera, cap.dcim_root)
                except (CameraError, OSError) as e:
                    print(f"  camera not ready: {e}\n  -> press SPACE to try again.")
                    continue

                # (2) Trigger: press -> hold the shutter for a fixed dwell -> release. This hold
                #     is separate from the image-pull wait in step (3), which starts after release.
                tgt = press_base(state, lim.base_min, lim.base_max)
                print(f"  pressing (base {out_base} -> {tgt}); holding shutter {cap.hold_seconds:.0f}s ...")
                drive_finger(c, "index", index_block, tgt, side, tuning.speeds.close, **wait_kw)
                load1, load2 = (
                    int(_scalar(c.read_present_load(id1))),
                    int(_scalar(c.read_present_load(id2))),
                )
                warn = load_warning(load1, load2)
                if warn:
                    print("  " + warn)
                time.sleep(cap.hold_seconds)  # shutter hold dwell -- NOT the image-pull timeout
                drive_finger(c, "index", index_block, out_base, side, tuning.speeds.open, **wait_kw)

                # (3) Pull wait: starts now (press released). Generous + counted, and tolerant of
                #     the camera's intermittent filelist error -- it keeps polling, so a good poll
                #     within the window still catches the capture.
                print(f"  released. Waiting up to {cap.new_file_timeout_s:.0f}s for the image to land ...")
                new = wait_for_new_files(
                    camera,
                    before,
                    dcim_root=cap.dcim_root,
                    timeout_s=cap.new_file_timeout_s,
                    poll_s=cap.poll_s,
                    stable_polls=cap.stable_polls,
                    on_poll=_print_poll,
                )

                if not new:
                    print(
                        f"  No new image after {cap.new_file_timeout_s:.0f}s -- press SPACE to try again."
                        "\n  (If it keeps timing out: confirm Still mode + Quick imaging ON + a study"
                        " selected. Any 'filelist error' lines above are the camera being momentarily busy.)"
                    )
                    continue

                now = _dt.datetime.now(_dt.UTC)
                for f in new:
                    kind = classify_capture(f)
                    if kind != "still":
                        print(
                            f"  WARNING: {f.filename} looks like {kind!r}, not a still "
                            "(is the camera in VIDEO mode?) -- saving anyway."
                        )
                    try:
                        info, data = pull_file(camera, f.filename)
                    except (CameraError, OSError) as e:
                        print(
                            f"  could not pull {f.filename} after retries: {e} -- press SPACE to try again."
                        )
                        continue
                    if not data.startswith(b"\xff\xd8\xff") or len(data) != info.filesize:
                        print(
                            f"  WARNING: {f.filename} failed validation "
                            f"(jpeg={data[:3].hex()} bytes={len(data)}/{info.filesize}) -- NOT saving "
                            "(corrupt / non-JPEG pull).\n  -> press SPACE to capture again."
                        )
                        continue
                    trigger_no["n"] += 1
                    saved = save_capture(
                        info,
                        data,
                        fundus_dir,
                        captured_at=now,
                        trigger_no=trigger_no["n"],
                        camera_serial=serial,
                        camera_sw=status.sw_version,
                        camera_wifi=status.wifi_version,
                    )
                    print(f"  SUCCESS: saved {saved.name} ({len(data)} bytes). Ready for next trigger.")
                    if preview is not None:
                        preview.show_still(data)  # pop up the image; stays until the next capture
                        print("  (last capture shown in its popup window until the next trigger)")
        except KeyboardInterrupt:
            print("\n^C -- leaving trigger mode")

    try:
        return run_grab_demo(_trigger_loop)
    finally:
        camera.close()
        if preview is not None:
            preview.stop()
        print("Camera connection closed.")


if __name__ == "__main__":
    sys.exit(main())
