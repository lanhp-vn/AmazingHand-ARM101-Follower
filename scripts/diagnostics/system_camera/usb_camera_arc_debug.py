"""Arc-detection debug collector for the arm-mounted USB observation camera (read-only diagnostic).

Stages the arm + hand into the grab pose (the ONLY pose where the Aurora-screen ROI geometry is
valid), then shows the live deskewed 4:3 (640x480) screen ROI with the two alignment-arc boxes
drawn -- each RED when ``arc_detector.detect()`` classifies that arc as RED (misaligned) and GREEN
when it reads clear -- plus a raw-classification HUD (per-arc coverage vs the threshold). Use it to
collect frames where the RED/clear classification is WRONG, so ``coverage_threshold`` / ``a_star_min``
/ the arc box geometry can be retuned against real evidence.

This is a DIAGNOSTIC, not a demo: it runs the same staged grab as the demos but makes NO Aurora
connection, presses NO shutter, fires NO trigger, records NO video, and grades nothing. It only
observes the camera and saves stills on demand. The arc boxes + red bands + screen ROI it draws and
records are read verbatim from ``system_camera_config.yaml`` (never written -- IL-5); re-derive them
with ``scripts/calibration/system_camera/calibrate_view.py``.

The arm + hand buses are opened/closed solely by ``run_grab_demo`` (one owner per bus -- IL-4); this
tool opens only the USB camera. All motion is the defined staged sequence; on exit torque is released
in place (or reversed only on explicit 'h' at the prompt) -- no surprise movement.

SPACE prompts (in the TERMINAL) for a short note describing what is WRONG with the detector's call
on this frame, then saves one "case" -- three files sharing a millisecond-timestamp stem ``arc_<ts>``
in ``--out-dir`` (default ``media_outputs/arc_debug/``, git-ignored):
  * ``arc_<ts>_clean.png``      the un-annotated 640x480 ROI -- ground truth, re-feedable to detect()
                                / calibrate_view.py --from-files.
  * ``arc_<ts>_annotated.png``  the ROI with arc boxes + verdict HUD burned in.
  * ``arc_<ts>.json``           the numbers the detector saw: per-arc coverage + verdict, the
                                threshold, the a* cutoff, the exact ROI/arc geometry used, camera
                                metadata, and your ``expected_note`` (Enter at the prompt = empty).

The full ``opencv-python`` wheel is required for the window (lerobot's headless build has no HighGUI;
``pyproject.toml`` drops the headless pin). Focus is locked per ``system_camera_config.yaml``
(``autofocus``/``focus``; DSHOW-only) so the ROI you check is sharp.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py
      [--camera N] [--backend auto|dshow] [--out-dir DIR]

Keys (focus the TERMINAL, not the window):
  SPACE     prompt for a note, then save one case (clean + annotated + sidecar) to --out-dir
  q / ESC   quit the loop -> arm/hand exit prompt (Enter releases in place, 'h' reverses)
  Ctrl+C    same as q
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import msvcrt
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox  # noqa: E402
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    AlignmentState,
    detect,
    imshow_fit,
    open_capture,
    resolution_mismatch_warning,
    roi_from_region,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_OUT_DIR = _REPO_ROOT / "media_outputs" / "arc_debug"
_WINDOW = "Arc detect debug (Optomed view)"
_RED = (0, 0, 255)
_GREEN = (0, 255, 0)
_WHITE = (255, 255, 255)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_STALL_S = 3.0  # bail if a streaming camera then stops delivering frames for this long


def build_arc_case_sidecar(
    alignment: AlignmentState,
    cfg: AutoTriggerConfig,
    screen_roi: RoiBox,
    *,
    camera_index: int,
    backend: str,
    frame_w: int,
    frame_h: int,
    captured_at: _dt.datetime,
    expected_note: str = "",
) -> dict[str, object]:
    """Assemble the JSON-serializable sidecar dict for one saved arc-debug case.

    Pure (no cv2, no I/O): records what ``detect()`` saw -- per-arc coverage + verdict, the
    threshold, the a* cutoff, and the exact ROI/arc geometry that produced the classification --
    plus camera metadata and the operator's ``expected_note`` (what is actually wrong with the
    detector's call, typed at capture time; empty string if skipped). ``captured_at`` is injected
    (not read from the clock) so the function is deterministic + unit-testable.
    """
    return {
        "tool": "usb_camera_arc_debug",
        "captured_at": captured_at.isoformat(),
        "camera": {
            "index": camera_index,
            "backend": backend,
            "frame_w": frame_w,
            "frame_h": frame_h,
        },
        "screen_roi": screen_roi.model_dump(),
        "detection": {
            "left_red": alignment.left_red,
            "left_cov": alignment.left_cov,
            "right_red": alignment.right_red,
            "right_cov": alignment.right_cov,
            "both_red": alignment.both_red,
            "both_clear": alignment.both_clear,
            "coverage_threshold": cfg.coverage_threshold,
        },
        "left_arc": cfg.left_arc.model_dump(),
        "right_arc": cfg.right_arc.model_dump(),
        "a_star_min": cfg.a_star_min,
        "morph_kernel": cfg.morph_kernel,
        "expected_note": expected_note,
    }


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting).

    Single-threaded design: we can't block on ``getwch`` -- that would freeze the cv2 window, which
    needs ``waitKey`` pumped every loop. Raises ``KeyboardInterrupt`` on Ctrl+C; swallows arrow /
    function-key prefixes.
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


def _annotate(clean: np.ndarray, cfg: AutoTriggerConfig, alignment: AlignmentState) -> np.ndarray:
    """Return a copy of the ROI frame with both arc boxes (RED verdict -> red, clear -> green) and a
    raw-classification HUD drawn. This annotated frame is shown live AND saved beside the clean one;
    it carries no UI chrome (counter/help live in the terminal) so each saved case stays clean."""
    disp = clean.copy()
    h, w = disp.shape[:2]
    for region, is_red in ((cfg.left_arc, alignment.left_red), (cfg.right_arc, alignment.right_red)):
        x, y, bw, bh = roi_from_region(region).for_frame(w, h)
        cv2.rectangle(disp, (x, y), (x + bw, y + bh), _RED if is_red else _GREEN, 2)
    t = cfg.coverage_threshold
    lcmp = ">=" if alignment.left_red else "<"
    rcmp = ">=" if alignment.right_red else "<"
    hud = (
        f"L:{'RED' if alignment.left_red else 'clr'} {alignment.left_cov:.3f}{lcmp}{t:.3f}   "
        f"R:{'RED' if alignment.right_red else 'clr'} {alignment.right_cov:.3f}{rcmp}{t:.3f}   "
        f"both_red={alignment.both_red}"
    )
    cv2.putText(disp, hud, (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _WHITE, 1, cv2.LINE_AA)
    return disp


def _save_case(
    out_dir: Path,
    clean: np.ndarray,
    annotated: np.ndarray,
    sidecar: dict[str, object],
    captured_at: _dt.datetime,
) -> bool:
    """Write the three case files (clean PNG + annotated PNG + JSON sidecar) sharing one ``arc_<ts>``
    stem derived from ``captured_at`` (so the filenames match the sidecar's ``captured_at``). Returns
    True only if all three wrote."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = captured_at.strftime("%Y%m%d_%H%M%S_") + f"{captured_at.microsecond // 1000:03d}"
    clean_path = out_dir / f"arc_{stem}_clean.png"
    annot_path = out_dir / f"arc_{stem}_annotated.png"
    json_path = out_dir / f"arc_{stem}.json"
    ok_clean = cv2.imwrite(str(clean_path), clean)
    ok_annot = cv2.imwrite(str(annot_path), annotated)
    json_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    if ok_clean and ok_annot:
        print(f"  saved case arc_{stem}: {clean_path.name}, {annot_path.name}, {json_path.name}")
        return True
    print(f"  WARNING: case arc_{stem} -- image write failed (clean={ok_clean}, annot={ok_annot})")
    return False


def _prompt_note(alignment: AlignmentState) -> str:
    """Block briefly to read an operator note for the just-captured case from the TERMINAL.

    Called right after SPACE: the cv2 window freezes during this ``input()`` -- intended, the arm
    holds grab under torque and the paused frame is the one you are describing. The current verdict
    is echoed so you can record what it SHOULD have been (the goal of the whole tool). Enter with no
    text leaves the note empty (label-later still works). EOF (piped/closed stdin) -> empty note.
    """
    verdict = (
        f"L:{'RED' if alignment.left_red else 'clr'} "
        f"R:{'RED' if alignment.right_red else 'clr'} "
        f"both_red={alignment.both_red}"
    )
    try:
        note = input(f"  note for this case (detector said {verdict}) -- Enter to skip: ")
    except EOFError:
        return ""
    return note.strip()


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
        default=str(_OUT_DIR),
        help="folder for SPACE-saved cases (default: <repo>/media_outputs/arc_debug)",
    )
    args = ap.parse_args()

    scfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else scfg.camera_index
    backend = args.backend if args.backend is not None else scfg.backend
    out_dir = Path(args.out_dir)
    roi = roi_from_region(scfg.screen_roi)
    acfg = scfg.auto_trigger

    def _arc_debug_loop(_ctx: GrabHoldContext) -> None:
        # _ctx is unused: the arm + hand simply hold grab under torque; this hook coordinates no
        # motion. It only opens the USB camera and runs the detection-overlay loop.
        print(f"Opening USB camera index {camera_index} ({backend}) ...")
        cap = open_capture(
            camera_index,
            backend,
            fourcc=scfg.fourcc,
            width=scfg.width,
            height=scfg.height,
            autofocus=scfg.autofocus,
            focus=scfg.focus,
        )
        if not cap.isOpened():
            cap.release()
            print(
                f"ERROR: could not open camera index {camera_index}. Try another --camera N or "
                "--backend dshow. (Arm/hand will still release at the exit prompt.)",
                file=sys.stderr,
            )
            return

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        warn = resolution_mismatch_warning(scfg.width, scfg.height, width, height)
        if warn:
            print(warn, file=sys.stderr)

        cv2.namedWindow(_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(_WINDOW, roi.ref_w, roi.ref_h)
        print(
            f"Live arc overlay: {width}x{height} stream, cropped to a {roi.w}x{roi.h} deskewed ROI "
            f"(angle {roi.angle:+.2f} deg). Boxes: RED verdict=red, clear=green. coverage_threshold="
            f"{acfg.coverage_threshold:.3f}.\n"
            "Keys (focus THIS terminal): SPACE = note + save case, q/ESC = quit."
        )

        started = time.monotonic()
        last_good: float | None = None
        saved = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    now = time.monotonic()
                    if last_good is None and now - started > _OPEN_GRACE_S:
                        print(
                            f"\nERROR: camera index {camera_index} opened but delivered no frames in "
                            f"{_OPEN_GRACE_S:.0f}s. Likely in use by another app or invalidated by a "
                            "previous run -- close other camera users / replug / try --backend dshow.",
                            file=sys.stderr,
                        )
                        break
                    if last_good is not None and now - last_good > _STALL_S:
                        print(
                            f"\nERROR: camera index {camera_index} stopped delivering frames "
                            f"({_STALL_S:.0f}s stall) -- it may have been unplugged or grabbed by "
                            "another app.",
                            file=sys.stderr,
                        )
                        break
                    cv2.waitKey(30)  # transient hiccup -- keep pumping, still honor q/ESC
                    if _poll_key() in ("q", "Q", "\x1b"):
                        break
                    continue

                last_good = time.monotonic()
                # Crop (+ deskew) to the screen ROI and upscale to the reference size -- identical to
                # WebcamPreview, so detect() sees exactly what the demo feeds it. This is the saveable
                # ground truth.
                clean = cv2.resize(roi.crop(frame), (roi.ref_w, roi.ref_h), interpolation=cv2.INTER_LINEAR)
                alignment = detect(clean, acfg)
                annotated = _annotate(clean, acfg, alignment)
                imshow_fit(_WINDOW, annotated)
                cv2.waitKey(1)  # pump GUI events; required for the window to stay responsive

                key = _poll_key()
                if key in ("q", "Q", "\x1b"):  # q / ESC
                    break
                if key == " ":  # SPACE -> note + save one case
                    captured_at = _dt.datetime.now(_dt.UTC)  # stamp at SPACE, before the note pause
                    note = _prompt_note(alignment)
                    sidecar = build_arc_case_sidecar(
                        alignment,
                        acfg,
                        scfg.screen_roi,
                        camera_index=camera_index,
                        backend=backend,
                        frame_w=width,
                        frame_h=height,
                        captured_at=captured_at,
                        expected_note=note,
                    )
                    if _save_case(out_dir, clean, annotated, sidecar, captured_at):
                        saved += 1
        except KeyboardInterrupt:
            print("\n^C")
        finally:
            cap.release()
            cv2.destroyAllWindows()
            print(f"Camera closed. {saved} case(s) saved to {out_dir}.")

    return run_grab_demo(_arc_debug_loop)


if __name__ == "__main__":
    sys.exit(main())
