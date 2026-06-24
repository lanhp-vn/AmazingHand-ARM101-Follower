"""Interactive view-calibration for the arm-mounted USB observation camera.

Re-derives the Aurora SCREEN ROI (a deskewed 4:3 / 640x480 crop carrying a rotation angle), the two
manually-placed alignment-ARC boxes, and the RED HSV band(s) + coverage threshold (auto-swept against
a RED + CLEAR reference panel), then writes them into src/arm101_hand/data/system_camera_config.yaml.
Run after any RESOLUTION or LIGHTING change (the fixed-fraction ROI + colour bands drift otherwise).

Detection is RED-ONLY: each arc is RED (misaligned) or not-red (aligned); no green is sampled (the
bright/aligned screen reads greenish overall, so green coverage is unreliable).

Flow (inside run_grab_demo -- the arm+hand stage the grab so the camera views the Aurora screen):
  1. WHITE startup screen -> drag a box + <-/-> rotate to deskew -> screen_roi (full frame).
  2-3. Freeze a RED-arc ROI -> drag the LEFT arc box, then the RIGHT (left box shown).
  4. Capture a RED panel (both arcs red) and a CLEAR/aligned panel.
  5. Auto-sweep the red HSV band + coverage threshold against both panels (report + best-effort).
  6-7. Confirm: 'y' write, 't' test loop (label frames 1=both-red/2=both-clear -> re-sweep), 'r'
       redo arcs, 'q' quit. Loop until satisfied.
  8. Write screen_roi + arc boxes + a_star_min + threshold to system_camera_config.yaml.
Action keys are read from the TERMINAL (the cv2 window often has no keyboard focus from a console).

Usage:
  uv run python scripts/calibration/system_camera/calibrate_view.py [--camera N] [--backend auto|dshow]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import msvcrt
import sys
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox  # noqa: E402
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    imshow_fit,
    open_capture,
    resolution_mismatch_warning,
    roi_from_region,
)
from arm101_hand.system_camera.arc_detector import (  # noqa: E402
    AlignmentState,
    arc_redness_mask,
    detect,
)
from arm101_hand.system_camera.calibration import (  # noqa: E402
    ArcCase,
    SweepResult,
    describe_case,
    deskew_crop,
    screen_roi_from_rect,
    sweep_red_detection,
    write_calibration_values,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_CALIB_CASE_DIR = _REPO_ROOT / "media_outputs" / "arc_calib"
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_DETECT = (640, 480)  # the deskewed 4:3 detection reference (ref_w x ref_h)
_REF_W, _REF_H = _DETECT
_MAX_WIN_W, _MAX_WIN_H = 1100, 800  # initial window cap so large frames fit the screen
_ROTATE_STEP_DEG = 0.5  # <- / -> step in the deskew-rotate preview (auto-repeats on key-hold)


def _open_window(title: str, frame_w: int, frame_h: int) -> None:
    """Create a resizable window sized to fit the screen (never upscales). MUST precede imshow_fit:
    on the Win32 backend getWindowImageRect raises a NULL-window error for a never-created window."""
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    scale = min(_MAX_WIN_W / frame_w, _MAX_WIN_H / frame_h, 1.0)
    cv2.resizeWindow(title, max(1, round(frame_w * scale)), max(1, round(frame_h * scale)))


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL ('' if none waiting).

    The cv2 window needs ``waitKey`` pumped every loop to stay responsive, so action keys are read
    from the CONSOLE instead (via msvcrt) -- this works regardless of which window has OS focus, so
    the operator presses keys in the same terminal they launched from (a cv2 window often does NOT
    get keyboard focus when launched from a console). Mirrors usb_camera_capture.py. Ctrl+C raises
    KeyboardInterrupt; Left/Right arrows return the literal tokens "LEFT"/"RIGHT" (used by the
    deskew-rotate loop); other arrow / function keys are still swallowed.
    """
    if not msvcrt.kbhit():
        return ""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> read the scan-code 2nd byte
        return {"K": "LEFT", "M": "RIGHT"}.get(msvcrt.getwch(), "")  # only the arrows we use
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _capture_frame(cap, title: str, overlay: RoiBox | None) -> np.ndarray | None:
    """Stream until SPACE (return the frame) or q/ESC (return None). Draws ``overlay`` if given.

    Destroys its own window before returning (try/finally), like every other helper here, so only ONE
    preview window is ever open at a time -- otherwise this startup window lingers under the later
    drag/rotate/arc/confirm windows for the whole session.

    Keys are read from the TERMINAL (see :func:`_poll_key`) while ``waitKey`` only pumps the window,
    so the operator presses SPACE/q in the console regardless of which window has OS focus."""
    print(f"\n{title}\n  Focus THIS terminal: SPACE = capture this frame, q = quit.")
    opened = False
    overlay_roi = roi_from_region(overlay) if overlay is not None else None
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                cv2.waitKey(30)  # keep the window pumped during camera warm-up / hiccups
                if _poll_key() in ("q", "Q", "\x1b"):
                    return None
                continue
            if not opened:  # create the window once we know the frame size (imshow_fit needs it to exist)
                _open_window(title, frame.shape[1], frame.shape[0])
                opened = True
            disp = frame.copy()
            if overlay_roi is not None:
                x, y, w, h = overlay_roi.for_frame(frame.shape[1], frame.shape[0])
                cv2.rectangle(disp, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(disp, title, (12, 28), _FONT, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
            imshow_fit(title, disp)
            cv2.waitKey(1)  # render only; action keys come from the terminal
            key = _poll_key()
            if key == " ":
                return frame
            if key in ("q", "Q", "\x1b"):
                return None
    finally:
        if opened:
            cv2.destroyWindow(title)


def _pick_arcs(roi_frame: np.ndarray) -> tuple[RoiBox, RoiBox] | None:
    """Drag the LEFT arc box, then the RIGHT one with the confirmed left box shown, on the deskewed
    ROI frame (already at the 640x480 ref, so drag pixels are ref pixels). Returns (left, right)
    RoiBoxes, or None if either drag is cancelled."""
    left = _drag_box(roi_frame, "Drag the LEFT arc box, then SPACE/ENTER.")
    if left is None:
        return None
    right = _drag_box(roi_frame, "Drag the RIGHT arc box (left box shown), then SPACE/ENTER.", keep_box=left)
    if right is None:
        return None
    return (
        RoiBox(x=left[0], y=left[1], w=left[2], h=left[3], ref_w=_REF_W, ref_h=_REF_H),
        RoiBox(x=right[0], y=right[1], w=right[2], h=right[3], ref_w=_REF_W, ref_h=_REF_H),
    )


def _capture_roi(cap, screen_roi: RoiBox, title: str) -> np.ndarray | None:
    """Stream the deskewed ROI (screen_roi crop -> 640x480) until SPACE (return the frozen ROI) or
    q/ESC (None). Action keys come from the TERMINAL (see _poll_key)."""
    print(f"\n{title}\n  Focus THIS terminal: SPACE = capture, q = quit.")
    opened = False
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            cv2.waitKey(30)
            if _poll_key() in ("q", "Q", "\x1b"):
                return None
            continue
        roi_img = deskew_crop(frame, screen_roi, out=_DETECT)
        if not opened:
            _open_window(title, _REF_W, _REF_H)
            opened = True
        disp = roi_img.copy()
        cv2.putText(disp, title, (10, 24), _FONT, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
        imshow_fit(title, disp)
        cv2.waitKey(1)
        key = _poll_key()
        if key == " ":
            cv2.destroyWindow(title)
            return roi_img
        if key in ("q", "Q", "\x1b"):
            cv2.destroyWindow(title)
            return None


def _drag_box(
    base: np.ndarray, prompt: str, *, keep_box: tuple[int, int, int, int] | None = None
) -> tuple[int, int, int, int] | None:
    """Mouse-drag a rectangle on a window; ACCEPT/CANCEL from the TERMINAL. Returns ``(x, y, w, h)``
    in ``base`` pixels, or None if cancelled.

    Replaces ``cv2.selectROI``, which is unusable in this console-launched flow: ``selectROI`` confirms
    via SPACE/ENTER/c read through the cv2 window's OWN ``waitKey``, but a console-launched cv2 window
    usually has no keyboard focus on Windows -- so those keys never arrive, and because ``selectROI``
    blocks while holding the GIL even Ctrl+C can't break in (it hangs). Here the drag is captured with
    a mouse callback (mouse events reach the window regardless of focus) and the accept/cancel keys
    come from the TERMINAL via :func:`_poll_key`, exactly like every other key in this script.

    The frame is shown in a FIXED-size AUTOSIZE window (scaled down to fit the screen, never upscaled)
    so the mouse->frame mapping is a single uniform scale -- no letterbox padding to invert.
    """
    fh, fw = base.shape[:2]
    scale = min(_MAX_WIN_W / fw, _MAX_WIN_H / fh, 1.0)
    disp_w, disp_h = max(1, round(fw * scale)), max(1, round(fh * scale))
    shown = cv2.resize(
        base, (disp_w, disp_h), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    )
    win = "Drag the box (mouse) -- accept/cancel in the TERMINAL"
    st = {"x0": 0, "y0": 0, "x1": 0, "y1": 0, "drag": False, "box": False}

    def _on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        x, y = max(0, min(x, disp_w - 1)), max(0, min(y, disp_h - 1))  # clamp to the shown image
        if event == cv2.EVENT_LBUTTONDOWN:
            st.update(x0=x, y0=y, x1=x, y1=y, drag=True, box=True)
        elif event == cv2.EVENT_MOUSEMOVE and st["drag"]:
            st.update(x1=x, y1=y)
        elif event == cv2.EVENT_LBUTTONUP and st["drag"]:
            st.update(x1=x, y1=y, drag=False)

    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(win, _on_mouse)
    print(
        f"  {prompt}\n"
        "  Drag a box with the LEFT mouse button, then in THIS terminal: "
        "SPACE/ENTER = accept, c = cancel."
    )
    try:
        while True:
            disp = shown.copy()
            if keep_box is not None:  # an already-confirmed box, drawn thin so the new drag references it
                kx, ky, kw, kh = (round(v * scale) for v in keep_box)
                cv2.rectangle(disp, (kx, ky), (kx + kw, ky + kh), (0, 200, 0), 1)
            if st["box"]:
                cv2.rectangle(disp, (st["x0"], st["y0"]), (st["x1"], st["y1"]), (0, 255, 0), 2)
            cv2.imshow(win, disp)
            cv2.waitKey(1)  # render only; action keys come from the terminal
            key = _poll_key()
            if key in (" ", "\r", "\n") and st["box"]:
                x = round(min(st["x0"], st["x1"]) / scale)
                y = round(min(st["y0"], st["y1"]) / scale)
                w = round(abs(st["x1"] - st["x0"]) / scale)
                h = round(abs(st["y1"] - st["y0"]) / scale)
                if w > 0 and h > 0:
                    return x, y, w, h
            elif key in ("c", "C", "q", "Q", "\x1b"):
                return None
    finally:
        cv2.destroyWindow(win)


def _option_box_pts(cx: float, cy: float, w: float, h: float, angle: float, fw: int, fh: int) -> np.ndarray:
    """Polygon (int32 points) of the 4:3 crop box for a screen rect at ``angle``, in frame pixels.

    Draw EXACTLY the box ENTER will commit: screen_roi_from_rect does the 4:3 expansion + ref scaling
    + edge clamp, so deriving the preview from it (mapped back to the frame, rotated by +angle about
    its centre) keeps the green preview faithful to the stored/cropped ROI -- one copy of the
    expansion rule, and a clamp at a frame edge shows in the preview too."""
    box = screen_roi_from_rect(((cx, cy), (w, h), angle), fw, fh)
    bx, by, bw, bh = roi_from_region(box).for_frame(fw, fh)
    center = (bx + bw / 2.0, by + bh / 2.0)
    return cv2.boxPoints((center, (float(bw), float(bh)), angle)).astype(np.int32)


def _pick_roi(white: np.ndarray) -> RoiBox | None:
    """Drag a box over the Aurora screen, then rotate the 4:3 crop to deskew it. None = recapture.

    Replaces the old auto-detect + 3-angle-preset picker: the operator drags an axis-aligned box
    (sized to the screen), then nudges the angle with the arrow keys until the 4:3 crop's edges sit
    parallel to the (possibly tilted) screen. A cancelled drag returns None so main() recaptures the
    white frame; 'r' in the rotate step re-drags on the same frame (no re-SPACE)."""
    while True:
        box = _drag_box(white, "Drag a box over the Aurora screen (then rotate to deskew).")
        if box is None:
            return None  # cancelled drag -> main() recaptures the white frame
        roi = _deskew_preview(white, box)
        if roi is not None:
            return roi  # ENTER confirmed
        # roi is None -> 'r' pressed: loop back and re-drag on the same frame


def _deskew_preview(base: np.ndarray, box: tuple[int, int, int, int]) -> RoiBox | None:
    """Rotate-to-deskew preview for a dragged screen box. Returns the confirmed RoiBox (ENTER), or
    None to re-drag ('r'); raises KeyboardInterrupt on 'q'.

    Draws the 4:3 crop box (green, the exact region ENTER will store) rotated by a live angle over the
    white frame; <- / -> nudge the angle by _ROTATE_STEP_DEG. Keys come from the TERMINAL (see
    _poll_key); the window only displays. The dragged box gives centre + (w, h) at angle 0 --
    screen_roi_from_rect widens the short side to 4:3, so the green box reads WIDER than a ~16:10
    screen; "aligned" means parallel edges + centred, not edge-coincident."""
    fh, fw = base.shape[:2]
    cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
    w, h = float(box[2]), float(box[3])
    angle = 0.0
    title = "Calib 1/3: rotate to deskew (press keys in the TERMINAL)"
    print(
        "\nRotate the 4:3 crop to deskew -- focus THIS terminal:\n"
        "  <- / -> = rotate 0.5deg   ENTER = confirm   r = re-drag   q = quit"
    )
    _open_window(title, fw, fh)
    while True:
        disp = base.copy()
        cv2.polylines(disp, [_option_box_pts(cx, cy, w, h, angle, fw, fh)], True, (0, 255, 0), 2)
        cv2.putText(
            disp,
            f"angle={angle:+.1f}deg  [<-/-> rotate  ENTER confirm  r re-drag  q quit]",
            (12, 28),
            _FONT,
            0.6,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            disp,
            "green = chosen 4:3 crop (rotate until its edges match the screen)",
            (12, 52),
            _FONT,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        imshow_fit(title, disp)
        cv2.waitKey(20)  # render only; action keys come from the terminal
        key = _poll_key()
        if key == "LEFT":
            angle -= _ROTATE_STEP_DEG
        elif key == "RIGHT":
            angle += _ROTATE_STEP_DEG
        elif key in ("\r", "\n"):
            cv2.destroyWindow(title)
            return screen_roi_from_rect(((cx, cy), (w, h), angle), fw, fh)
        elif key in ("r", "R"):
            cv2.destroyWindow(title)
            return None
        elif key in ("q", "Q", "\x1b"):
            cv2.destroyWindow(title)
            raise KeyboardInterrupt


def _tint_mask(crop: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    out = crop.copy()
    out[mask > 0] = color
    return cv2.addWeighted(crop, 0.5, out, 0.5, 0.0)


def _annotate_live(roi_frame: np.ndarray, cfg: AutoTriggerConfig, state: AlignmentState) -> np.ndarray:
    """ROI copy with both arc boxes (RED verdict -> red, clear -> green) + a coverage HUD."""
    disp = roi_frame.copy()
    h, w = disp.shape[:2]
    for region, is_red in ((cfg.left_arc, state.left_red), (cfg.right_arc, state.right_red)):
        x, y, bw, bh = roi_from_region(region).for_frame(w, h)
        cv2.rectangle(disp, (x, y), (x + bw, y + bh), (0, 0, 255) if is_red else (0, 255, 0), 2)
    t = cfg.coverage_threshold
    hud = (
        f"L:{'RED' if state.left_red else 'clr'} {state.left_cov:.3f}   "
        f"R:{'RED' if state.right_red else 'clr'} {state.right_cov:.3f}   thr={t:.3f}"
    )
    cv2.putText(disp, hud, (10, h - 12), _FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return disp


def _prompt_expected(state: AlignmentState) -> str | None:
    """Read the on-screen ground truth from the TERMINAL: '1' both arcs RED, '2' both arcs CLEAR,
    'c'/q to cancel. Returns 'red'/'clear'/None. Window stays pumped via waitKey."""
    print(
        f"  detector said L:{'RED' if state.left_red else 'clr'} R:{'RED' if state.right_red else 'clr'}. "
        "Ground truth -> 1 = both RED, 2 = both CLEAR, c = cancel"
    )
    while True:
        cv2.waitKey(20)
        key = _poll_key()
        if key == "1":
            return "red"
        if key == "2":
            return "clear"
        if key in ("c", "C", "q", "Q", "\x1b"):
            return None


def _save_calib_case(
    out_dir: Path,
    roi_img: np.ndarray,
    cfg: AutoTriggerConfig,
    state: AlignmentState,
    expected: str,
    comment: str,
    camera_index: int,
    backend: str,
) -> None:
    """Save clean + annotated PNGs + a JSON sidecar (ground truth, detector output, generated comment,
    geometry, bands, threshold) under media_outputs/arc_calib/ for the audit trail."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now()
    stem = ts.strftime("%Y%m%d_%H%M%S_") + f"{ts.microsecond // 1000:03d}"
    cv2.imwrite(str(out_dir / f"calib_{stem}_clean.png"), roi_img)
    cv2.imwrite(str(out_dir / f"calib_{stem}_annotated.png"), _annotate_live(roi_img, cfg, state))
    sidecar = {
        "tool": "calibrate_view",
        "captured_at": ts.isoformat(),
        "expected": expected,
        "comment": comment,
        "detection": {
            "left_red": state.left_red,
            "left_cov": state.left_cov,
            "right_red": state.right_red,
            "right_cov": state.right_cov,
            "coverage_threshold": cfg.coverage_threshold,
        },
        "left_arc": cfg.left_arc.model_dump(),
        "right_arc": cfg.right_arc.model_dump(),
        "a_star_min": cfg.a_star_min,
        "morph_kernel": cfg.morph_kernel,
        "camera": {"index": camera_index, "backend": backend},
    }
    (out_dir / f"calib_{stem}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    print(f"  saved calib_{stem} ({comment})")


def _report_sweep(result: SweepResult, cases: list[ArcCase]) -> None:
    """Print the sweep outcome: threshold, separability over the FITTED (clean) cases, any clean case
    still wrong, and the transitional 'red' cases excluded from tuning (one arc read clear)."""
    n_excluded = len(result.excluded_transitional)
    n_fit = len(cases) - n_excluded
    ok = n_fit - len(result.unsatisfied)
    print(
        f"\nSweep -> threshold={result.coverage_threshold:.4f}  separable={result.separable}  "
        f"{ok}/{n_fit} fitted cases correct ({n_excluded} excluded as transitional)."
    )
    for i in result.unsatisfied:
        dl, dr = result.case_detections[i]
        print(f"  case {i} STILL WRONG: {describe_case(cases[i].expected, dl, dr)}")
    for i in result.excluded_transitional:
        dl, dr = result.case_detections[i]
        print(
            f"  case {i} EXCLUDED (transitional): {describe_case(cases[i].expected, dl, dr)} "
            "-- one arc read clear; relabel as clear or recapture as a clean both-red frame."
        )


def _confirm(red_ref: np.ndarray, clear_ref: np.ndarray, cfg: AutoTriggerConfig) -> str:
    """Two deskewed panels (RED + CLEAR) with arc boxes + tinted red masks + real-detector labels.
    Returns the pressed action: 'y' write, 't' test loop + re-sweep, 'r' redo arcs, 'q' quit."""
    title = "Calib confirm (press keys in the TERMINAL)"
    la, ra = roi_from_region(cfg.left_arc), roi_from_region(cfg.right_arc)
    print(
        "\nConfirm calibration -- focus THIS terminal:\n"
        "  y = write config   t = test loop (label cases + re-sweep)   r = redo arcs   q = quit (no write)"
    )
    _open_window(title, 2 * _REF_W, _REF_H)
    try:
        while True:
            panels = []
            for label, frame in (("RED frame", red_ref), ("CLEAR frame", clear_ref)):
                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                panel = _tint_mask(frame, arc_redness_mask(lab, cfg.a_star_min), (0, 0, 255))
                for arc in (la, ra):
                    ax, ay, aw, ah = arc.for_frame(_REF_W, _REF_H)
                    cv2.rectangle(panel, (ax, ay), (ax + aw, ay + ah), (255, 255, 0), 1)
                state = detect(frame, cfg)
                cv2.putText(
                    panel,
                    f"{label}: L={'RED' if state.left_red else 'clear'} R={'RED' if state.right_red else 'clear'}",
                    (8, 24),
                    _FONT,
                    0.55,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
                panels.append(panel)
            gate = detect(red_ref, cfg).both_red and detect(clear_ref, cfg).both_clear
            composite = np.hstack(panels)
            cv2.putText(
                composite,
                f"red->both RED and clear->both clear: {gate}",
                (8, _REF_H - 12),
                _FONT,
                0.6,
                (0, 255, 0) if gate else (0, 0, 255),
                2,
                cv2.LINE_AA,
            )
            imshow_fit(title, composite)
            cv2.waitKey(20)
            key = _poll_key()
            if key in ("y", "Y", "t", "T", "r", "R", "q", "Q", "\x1b"):
                return "q" if key in ("q", "Q", "\x1b") else key.lower()
    finally:
        cv2.destroyWindow(title)


def _test_loop(
    cap, screen_roi: RoiBox, cfg: AutoTriggerConfig, camera_index: int, backend: str
) -> list[ArcCase] | None:
    """Live ROI + arc HUD using cfg; SPACE freezes a frame -> tag 1/2 -> ArcCase + saved case files.
    Labelling a case loops straight back to live capture (no note prompt). 'd' = done (return
    collected cases), 'q' = abort (None)."""
    print(
        "\nTest loop -- focus THIS terminal:\n"
        "  SPACE = freeze + label a case   d = done (re-sweep)   q = abort"
    )
    title = "Calib test loop (Optomed ROI)"
    _open_window(title, _REF_W, _REF_H)
    new_cases: list[ArcCase] = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                cv2.waitKey(30)
                key = _poll_key()
                if key in ("q", "Q", "\x1b"):
                    return None
                if key in ("d", "D"):
                    return new_cases
                continue
            roi_img = deskew_crop(frame, screen_roi, out=_DETECT)
            state = detect(roi_img, cfg)
            imshow_fit(title, _annotate_live(roi_img, cfg, state))
            cv2.waitKey(1)
            key = _poll_key()
            if key in ("d", "D"):
                return new_cases
            if key in ("q", "Q", "\x1b"):
                return None
            if key == " ":
                expected = _prompt_expected(state)
                if expected is None:
                    continue
                comment = describe_case(expected, state.left_red, state.right_red)
                print(f"  -> {comment}")
                _save_calib_case(
                    _CALIB_CASE_DIR,
                    roi_img,
                    cfg,
                    state,
                    expected,
                    comment,
                    camera_index,
                    backend,
                )
                new_cases.append(ArcCase(frame=roi_img, expected=expected))
    finally:
        cv2.destroyWindow(title)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="USB camera index (default: config)")
    ap.add_argument(
        "--backend", choices=("auto", "dshow"), default=None, help="cv2 backend (default: config)"
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else cfg.camera_index
    backend = args.backend if args.backend is not None else cfg.backend

    def _calibrate_hook(_ctx: GrabHoldContext) -> None:
        # _ctx unused: arm+hand hold grab under torque (the only pose where the Aurora screen is in
        # view); this hook opens the USB camera and runs the calibration. Mirrors usb_camera_arc_debug.
        print(f"Opening USB camera {camera_index} ({backend}) ...")
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
                f"ERROR: could not open camera {camera_index}. Try --camera N / --backend dshow. "
                "(Arm/hand still release at the exit prompt.)",
                file=sys.stderr,
            )
            return
        warn = resolution_mismatch_warning(
            cfg.width,
            cfg.height,
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        if warn:
            print(warn, file=sys.stderr)
        try:
            # Stage 1: WHITE startup screen -> drag + rotate -> screen_roi (full frame)
            screen_roi = None
            while screen_roi is None:
                white = _capture_frame(cap, "Calib 1/3: frame the WHITE startup screen, SPACE", None)
                if white is None:
                    return
                screen_roi = _pick_roi(white)

            # Stage 2-3: arcs -- freeze a RED-arc ROI, drag LEFT then RIGHT
            arcs = None
            while arcs is None:
                roi_red = _capture_roi(cap, screen_roi, "Show RED arcs; SPACE to freeze for arc-box drag")
                if roi_red is None:
                    return
                arcs = _pick_arcs(roi_red)
            left_arc, right_arc = arcs

            # Stage 4: reference panels
            red_panel = _capture_roi(cap, screen_roi, "Capture RED panel (both arcs RED), SPACE")
            if red_panel is None:
                return
            clear_panel = _capture_roi(cap, screen_roi, "Capture CLEAR/aligned panel, SPACE")
            if clear_panel is None:
                return
            cases = [ArcCase(red_panel, "red"), ArcCase(clear_panel, "clear")]

            # Stage 5: sweep
            def _sweep(case_list: list[ArcCase]) -> AutoTriggerConfig:
                result = sweep_red_detection(case_list, left_arc, right_arc)
                _report_sweep(result, case_list)
                return AutoTriggerConfig(
                    left_arc=left_arc,
                    right_arc=right_arc,
                    a_star_min=result.a_star_min,
                    coverage_threshold=result.coverage_threshold,
                )

            try:
                trial = _sweep(cases)
            except ValueError as e:
                print(f"ERROR: {e}. Re-run and show clearly RED, misaligned arcs.", file=sys.stderr)
                return

            # Stage 6-8: confirm / test loop / re-sweep / write
            while True:
                action = _confirm(red_panel, clear_panel, trial)
                if action == "y":
                    write_calibration_values(
                        _CONFIG_PATH,
                        screen_roi=screen_roi,
                        left_arc=left_arc,
                        right_arc=right_arc,
                        a_star_min=trial.a_star_min,
                        coverage_threshold=trial.coverage_threshold,
                    )
                    print(f"Wrote calibration to {_CONFIG_PATH} (backup: {_CONFIG_PATH.name}.bak).")
                    return
                if action == "q":
                    print("Quit without writing.")
                    return
                if action == "r":
                    arcs = None
                    while arcs is None:
                        roi_red = _capture_roi(cap, screen_roi, "Show RED arcs; SPACE to re-drag arc boxes")
                        if roi_red is None:
                            return
                        arcs = _pick_arcs(roi_red)
                    left_arc, right_arc = arcs
                    trial = _sweep(cases)
                if action == "t":
                    new = _test_loop(cap, screen_roi, trial, camera_index, backend)
                    if new is None:
                        print("Test loop aborted; nothing written.")
                        return
                    cases += new
                    trial = _sweep(cases)
        finally:
            cap.release()
            cv2.destroyAllWindows()

    return run_grab_demo(_calibrate_hook)


if __name__ == "__main__":
    sys.exit(main())
