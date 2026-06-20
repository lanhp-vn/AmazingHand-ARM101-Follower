"""Auto/manual arc-triggered Aurora capture WITH inline diabetic-retinopathy grading.

This is the AUTO-trigger variant of ``grab_trigger_capture_analysis.py`` (which fires only on
SPACE). It watches the Optomed Aurora's on-screen alignment arcs in the live USB preview: when
they turn GREEN (correct working distance) and stay green, it auto-fires the SAME capture cycle
SPACE would -- no keypress needed -- then HOLDS (green is ignored) until you grade the shot
with 'g' and press 'n' to advance to the next patient. The trigger starts in MANUAL (SPACE-only);
'm' toggles into AUTO (an explicit arm) and back. Each patient turn accumulates one or more captured fundus
images (each pops up RAW as it lands), and pressing 'g' grades every shot of the turn
with the RETFound ViT-L/16 ``DRGrader``, writes a ``<stem>.dr.json`` sidecar per shot
(identical to ``arm101-dr-grade``) to ``media_outputs/fundus_analysis/``, and shows ONE
combined results panel beside the most-severe shot in the "last capture" popup.

Research/educational use only -- NOT a medical device, not for clinical diagnosis. That
disclaimer ships in every sidecar and on the on-screen panel.

On startup a live preview window opens for the arm-mounted USB camera (it points at the
Aurora's screen). The feed is cropped to a fixed ROI that zooms onto just the Aurora's
screen (``AURORA_SCREEN_ROI``) -- press 'r' anytime to start/stop recording that zoomed
feed to a clip. The preview is best-effort: if the camera will not open, the demo
continues without it (grading + sidecars still run; only the popup is skipped).

The DR-grading model (~1.2 GB) loads once at startup. If the slim weights are missing the
demo STILL captures + pops up images; the panel just reads "grading unavailable". Grading
can be forced on/off with ``--grade`` / ``--no-grade``; otherwise it follows
``inline_grading`` in ``src/arm101_hand/data/fundus_analysis_config.yaml``. The expected
shots per patient is ``captures_per_patient`` in that same file (a soft target -- 'g'
analyzes whenever you choose).

Runs the staged arm+hand grab (see ``arm101_hand.scripts.grab_common``); once both
devices hold ``grab`` under torque, each SPACE runs ONE capture cycle:
  press the index onto the Aurora's shutter -> hold -> release ->
  diff the camera filelist -> pull the new image(s) + metadata into ``media_outputs/fundus_images/``.

PREREQUISITES (set on the camera; the API cannot):
  * Capture mode = STILL imaging  (a held press in VIDEO mode records a clip!)
  * Quick imaging = ON            (else each capture waits in an on-device preview)
  * Optomed Client CLOSED         (the Pictor API allows one client connection)
  * A study/patient selected      (new images land in the current study folder)
  * Slim weights exported once    (uv run python scripts/fundus_analysis/export_weights.py)

Controls (torque ON the whole time; focus the TERMINAL, not the preview window):
  m           toggle MANUAL <-> AUTO trigger mode (AUTO is the explicit arm; starts MANUAL)
  SPACE       (MANUAL only) fire one capture cycle -- ignored in AUTO (capture is automatic)
  g           analyze the current patient turn (grade every shot, show the combined panel)
  n           next patient: re-arm AUTO to watch for green again (grade the held shot with 'g' first)
  r           start / stop recording the USB preview
  [ / ]       shrink / grow the press depth (applies to the NEXT press)
  q / Ctrl+C  stop and go to the exit prompt

Usage:
  uv run python scripts/demos/grab_auto_trigger_analysis.py [--grade | --no-grade]
"""

from __future__ import annotations

import argparse
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
from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config
from arm101_hand.fundus_analysis import (
    DRGrader,
    GradedShot,
    compose_summary_panel,
    decode_bgr,
    encode_png,
    weights_sha8,
    write_sidecar,
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
from arm101_hand.system_camera import AURORA_SCREEN_ROI, WebcamPreview, auto_trigger, detect

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


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt ('' if none waiting)."""
    if not msvcrt.kbhit():
        return ""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume the 2nd byte, ignore
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


def _overlay(mode: str, alignment, armed_phase: str) -> tuple[str, tuple[int, int, int]]:
    """Build the on-screen status line + colour from the current mode and alignment."""
    if mode != "AUTO":
        return "MANUAL  (m=auto, SPACE=capture)", (200, 200, 200)
    label = f"AUTO [{armed_phase}]  L:{alignment.left} R:{alignment.right}"
    color = (0, 255, 0) if alignment.ready else (0, 0, 255)
    return label, color


def _print_prereqs() -> None:
    print(
        "PREREQUISITES (set on the camera): Still mode | Quick imaging ON | "
        "Optomed Client CLOSED | study selected"
    )


def _print_poll(elapsed: float, n_new: int, note: str) -> None:
    """Per-poll progress line during the image-pull wait (counts elapsed seconds)."""
    detail = note if note else f"{n_new} new file(s) seen"
    print(f"    +{elapsed:4.1f}s  {detail}")


def _toggle_recording(preview: WebcamPreview | None) -> None:
    """Start/stop USB-preview recording (best-effort) and report it. Shared by the key loop and
    the image-pull wait so 'r' works in both."""
    if preview is None:
        print("  (no USB preview window -- nothing to record)")
        return
    rec_path = preview.toggle_record()
    print(f"  recording -> {rec_path}" if rec_path else "  recording stopped.")


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
        roi=AURORA_SCREEN_ROI,
        fourcc=scfg.fourcc,
        width=scfg.width,
        height=scfg.height,
        autofocus=scfg.autofocus,
        focus=scfg.focus,
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
        f"@{preview.src_fps:.0f}fps, cropped to a {AURORA_SCREEN_ROI.w}x{AURORA_SCREEN_ROI.h} ROI "
        "(zoom of the Aurora screen) -- 'r' starts/stops recording."
    )
    return preview


def _analyze_turn(
    grader: DRGrader | None,
    shots: list[tuple[bytes, str]],
    output_dir: Path,
    preview: WebcamPreview | None,
    unavailable_reason: str,
) -> None:
    """Grade every shot of the patient turn, write a sidecar each, show the combined panel.

    Sidecars are written whenever the grader is available, even with no preview window
    (so inline and batch artifacts stay identical); only the popup is gated on ``preview``.
    """
    graded: list[GradedShot] = []
    for data, name in shots:
        img = decode_bgr(data)
        if img is None:
            print(f"  WARNING: could not decode {name} for grading -- skipping.")
            continue
        result = None
        if grader is not None:
            try:
                result = grader.grade_array(img, source_name=name)
                sidecar = write_sidecar(result, output_dir)
                print(
                    f"  graded {name}: grade {result.grade} ({result.label}), "
                    f"{result.confidence} -> {sidecar.name}"
                )
            except Exception as e:
                print(f"  WARNING: grading failed for {name}: {e}")
        graded.append(GradedShot(image_bgr=img, result=result, source_name=name))
    if not graded:
        print("  nothing to display.")
        return
    if any(s.result is not None for s in graded):
        print("  Research/educational use only. Not a medical device; not for clinical diagnosis.")
    if preview is not None:
        composite = compose_summary_panel(graded, unavailable_reason=unavailable_reason)
        preview.show_still(encode_png(composite))
        print("  (combined results shown in the popup until the next capture)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Robot-triggered Aurora capture with inline DR grading.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--grade", dest="grade", action="store_true", default=None, help="force inline DR grading ON"
    )
    group.add_argument(
        "--no-grade",
        dest="grade",
        action="store_false",
        default=None,
        help="force inline DR grading OFF (capture-only)",
    )
    args = parser.parse_args()

    cfg = load_fundus_config(_FUNDUS_CONFIG_PATH)
    conn, cap = cfg.connection, cfg.capture
    fundus_dir = _REPO_ROOT / cap.fundus_dir

    acfg = load_fundus_analysis_config()
    grading_enabled = args.grade if args.grade is not None else acfg.inline_grading
    analysis_output_dir = _REPO_ROOT / acfg.output_dir
    grader: DRGrader | None = None
    grading_reason = "grading disabled (capture-only)"
    if grading_enabled:
        weights = _REPO_ROOT / acfg.models_dir / acfg.weights_filename
        if not weights.is_file():
            grading_reason = "grading unavailable -- run export_weights.py"
            print(f"WARNING: {grading_reason} (missing {weights})", file=sys.stderr)
        else:
            print("Loading DR-grading model (~a few seconds, ~1.2 GB) ...")
            try:
                grader = DRGrader(acfg, weights_path=weights, model_sha8=weights_sha8(weights))
                print("DR-grading model loaded.")
            except Exception as e:
                grading_reason = f"grading failed to load: {e}"
                print(f"WARNING: {grading_reason}", file=sys.stderr)

    # Live arm-cam preview window (best-effort, independent of the Aurora link). Up before the
    # arm moves so the operator can watch the Optomed screen throughout.
    scfg = load_system_camera_config(_SYSTEM_CAMERA_CONFIG_PATH)
    preview = _start_preview(scfg)

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
    if grading_enabled:
        print(f"Inline DR grading ON (target {acfg.captures_per_patient} shot(s)/patient; 'g' analyzes).")
    else:
        print("Inline DR grading OFF (capture-only).")

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
        turn_shots: list[tuple[bytes, str]] = []

        def _on_poll(elapsed: float, n_new: int, note: str) -> None:
            # Per-poll progress, plus let 'r' stop/start recording while we're blocked waiting for
            # the image to land. Ctrl+C still aborts (as elsewhere); other keys are drained here so
            # they do not queue up and fire after the wait returns.
            _print_poll(elapsed, n_new, note)
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume 2nd byte
                    msvcrt.getwch()
                elif ch == "\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                elif ch in ("r", "R"):
                    _toggle_recording(preview)

        def _fire_capture_cycle() -> None:
            # (1) Baseline filelist BEFORE pressing -- retried past the camera's intermittent
            #     GET_FILELIST CODE_FAIL so a single hiccup doesn't waste this press.
            try:
                camera.ensure_connected()
                before = snapshot_filenames(camera, cap.dcim_root)
            except (CameraError, OSError) as e:
                print(f"  camera not ready: {e}\n  -> retry.")
                return

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
            print(
                f"  released. Waiting up to {cap.new_file_timeout_s:.0f}s for the image to land "
                "('r' still toggles recording) ..."
            )
            new = wait_for_new_files(
                camera,
                before,
                dcim_root=cap.dcim_root,
                timeout_s=cap.new_file_timeout_s,
                poll_s=cap.poll_s,
                stable_polls=cap.stable_polls,
                on_poll=_on_poll,
            )

            if not new:
                print(
                    f"  No new image after {cap.new_file_timeout_s:.0f}s -- press SPACE to try again."
                    "\n  (If it keeps timing out: confirm Still mode + Quick imaging ON + a study"
                    " selected. Any 'filelist error' lines above are the camera being momentarily busy.)"
                )
                return

            captured_at = _dt.datetime.now(_dt.UTC)
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
                    print(f"  could not pull {f.filename} after retries: {e} -- press SPACE to try again.")
                    return
                if not data.startswith(b"\xff\xd8\xff") or len(data) != info.filesize:
                    print(
                        f"  WARNING: {f.filename} failed validation "
                        f"(jpeg={data[:3].hex()} bytes={len(data)}/{info.filesize}) -- NOT saving "
                        "(corrupt / non-JPEG pull).\n  -> press SPACE to capture again."
                    )
                    return
                trigger_no["n"] += 1
                saved = save_capture(
                    info,
                    data,
                    fundus_dir,
                    captured_at=captured_at,
                    trigger_no=trigger_no["n"],
                    camera_serial=serial,
                    camera_sw=status.sw_version,
                    camera_wifi=status.wifi_version,
                )
                print(f"  SUCCESS: saved {saved.name} ({len(data)} bytes).")
                turn_shots.append((data, saved.name))
                if preview is not None:
                    preview.show_still(data)  # raw image while the turn is in progress
                target = acfg.captures_per_patient
                if mode == "AUTO":
                    print(
                        f"  shot {len(turn_shots)} captured (AUTO held) -- "
                        "press 'g' to grade, then 'n' for the next patient."
                    )
                elif len(turn_shots) >= target:
                    print(
                        f"  shot {len(turn_shots)} captured (target {target} reached) -- "
                        "press 'g' to analyze, SPACE for another."
                    )
                else:
                    print(f"  shot {len(turn_shots)}/{target} captured -- SPACE for another, 'g' to analyze.")

        mode = "MANUAL"
        state_auto = auto_trigger.arm()
        armed = True  # AUTO fires only while armed; HELD (False) after a fire until 'n' (next patient)
        last_detect = 0.0
        print(
            "Trigger: m=auto/manual, SPACE=capture(MANUAL), g=analyze, n=next patient, r=record, [/]=depth, q=exit"
        )
        _print_prereqs()
        print("  " + _status_line(state, out_base, side, others))

        try:
            while True:
                now = time.monotonic()
                key = _poll_key()
                if key in ("m", "M"):
                    mode = "AUTO" if mode == "MANUAL" else "MANUAL"
                    if mode == "AUTO":
                        state_auto = auto_trigger.arm()
                        armed = True
                    print(f"  mode -> {mode}")
                elif key in ("r", "R"):
                    _toggle_recording(preview)
                elif key in ("g", "G"):  # analyze the current patient turn
                    if not grading_enabled:
                        print("  grading disabled (capture-only) -- nothing to analyze.")
                    elif not turn_shots:
                        if mode == "AUTO":
                            print("  no shots captured yet -- align the Aurora to green; it auto-captures.")
                        else:
                            print("  no shots captured yet -- press SPACE to capture first.")
                    else:
                        print(f"  analyzing {len(turn_shots)} shot(s) ...")
                        _analyze_turn(grader, turn_shots, analysis_output_dir, preview, grading_reason)
                        turn_shots = []
                        print("  patient turn complete -- press 'n' for the next patient.")
                elif key in ("n", "N"):  # advance to the next patient (re-arm AUTO)
                    if turn_shots and grading_enabled:
                        print(
                            f"  {len(turn_shots)} shot(s) not yet analyzed -- "
                            "press 'g' to grade first, or 'q' to exit."
                        )
                    else:
                        if turn_shots:  # capture-only: shots are already saved to disk, nothing to grade
                            print(f"  ({len(turn_shots)} shot(s) saved; grading off) -- next patient.")
                        turn_shots = []
                        state_auto = auto_trigger.arm()
                        armed = True
                        if mode == "AUTO":
                            print("  next patient -- AUTO re-armed, watching for green.")
                        else:
                            print("  next patient -- ready (press 'm' for AUTO).")
                else:
                    action = key_to_action(key) if key else None
                    if action == "quit":
                        break
                    if action == "fire":
                        if mode == "AUTO":
                            print(
                                "  SPACE is disabled in AUTO (capture is automatic) -- "
                                "press 'n' for the next patient, or 'm' for MANUAL."
                            )
                        else:
                            _fire_capture_cycle()
                    elif action is not None:
                        state = apply_action(state, action)
                        base_now, side_now = read_finger(c, "index", index_block)
                        print("  " + _status_line(state, base_now, side_now, others))

                if (
                    mode == "AUTO"
                    and armed
                    and preview is not None
                    and now - last_detect >= scfg.auto_trigger.detect_interval_s
                ):
                    last_detect = now
                    frame = preview.latest_frame()
                    if frame is not None:
                        alignment = detect(frame, scfg.auto_trigger)
                        state_auto, fire = auto_trigger.update(state_auto, alignment, now, scfg.auto_trigger)
                        text, color = _overlay(mode, alignment, state_auto.phase)
                        preview.set_status_text(text, color)
                        if fire:
                            print("  AUTO: arcs green + stable -> firing capture")
                            _fire_capture_cycle()
                            armed = False  # HOLD: ignore green until the operator presses 'n'
                            preview.set_status_text(
                                "AUTO captured -- 'g' to grade, 'n' for next patient", (0, 255, 255)
                            )
                elif mode == "AUTO" and preview is None:
                    print("  AUTO unavailable: no USB preview window -- staying MANUAL.")
                    mode = "MANUAL"

                time.sleep(0.01)  # ~100 Hz poll; keeps the CPU sane (loop no longer blocks on a key)
        except KeyboardInterrupt:
            print("\n^C -- leaving trigger mode")
            if turn_shots:
                print(f"  ({len(turn_shots)} shot(s) captured but not analyzed)")

    try:
        return run_grab_demo(_trigger_loop)
    finally:
        camera.close()
        if preview is not None:
            preview.stop()
        print("Camera connection closed.")


if __name__ == "__main__":
    sys.exit(main())
