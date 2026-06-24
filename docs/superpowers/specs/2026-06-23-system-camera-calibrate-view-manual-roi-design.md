# System-camera view calibration — manual drag-and-deskew ROI (KISS rework of Calib 1/3)

**Date:** 2026-06-23
**Status:** Design approved; pending implementation plan.
**Touches:** `scripts/calibration/system_camera/calibrate_view.py`, `src/arm101_hand/system_camera/calibration.py`, `tests/unit/test_system_camera_calibration.py` (+ doc touch-ups).

## Problem

`calibrate_view.py` Calib 1/3 currently auto-detects the Aurora screen rectangle on the WHITE
startup frame (`detect_screen_rect` — a brightness-threshold sweep + clean-quad test + likeness
ranking with a whole-frame fallback), then asks the operator to pick one of three discrete deskew
angles (`1`/`2`/`3` = θ−1 / θ / θ+1°), with `m` to drag a box manually as an escape hatch.

In practice the auto-detection is unnecessary complexity: a manual drag is faster and more reliable
than tuning the detector, and three fixed angle presets are a clumsy substitute for "rotate until it
lines up." The detection path is ~110 lines of CV that exists only to seed a box the operator can
place by hand in two seconds.

## Goal

Replace Calib 1/3 with a direct manual workflow and delete the auto-detection entirely:

1. Capture the WHITE frame (unchanged — crisp screen edges make it the best drag backdrop).
2. **Drag** a box over the screen.
3. **Rotate** the 5:3 crop box with ← / → until its edges sit parallel to the (possibly tilted)
   screen, then `ENTER` to confirm.

Calib 2/3 (RED arcs) and Calib 3/3 (BRIGHT aligned screen) are unchanged, and the existing RED/CLEAR
recalibration pipeline (circle fit → symmetric arc bands → red-band sample → coverage threshold, with
the `e` manual-arc-retune fallback) re-runs on the freshly captured frames exactly as today.

Non-goals: changing the RED/CLEAR derivation, the arc geometry, the config schema, the writer, or the
`--from-files` path's downstream stages. No box position/size nudging after the drag (re-drag instead).

## Approach (chosen: A — drag, then a continuous rotate-preview loop)

Reuse the proven `_drag_box` (mouse-drag in a fixed-scale window, accept/cancel from the TERMINAL —
it already solves the focusless-console keyboard problem) to grab an axis-aligned box, then run a
small preview loop that draws the 5:3-expanded box rotated by a live angle over the white frame.

Rejected alternatives:
- **B — unified live editor** (drag + rotate fused in one mouse+key loop): more fluid, but fuses two
  input modes into one state machine for marginal gain. More code, more to get wrong.
- **C — drag-only, angle always 0**: simplest, but drops the deskew of a tilted screen, which is a
  required capability. Listed only as the floor.

## Detailed design

### `_pick_roi(white)` — rewritten

```
def _pick_roi(white) -> RoiBox | None:   # None => recapture the WHITE frame
    fh, fw = white.shape[:2]
    while True:
        box = _drag_box(white, "Drag a box over the Aurora screen (then rotate to deskew).")
        if box is None:
            return None                  # cancelled drag -> main() recaptures WHITE
        roi = _deskew_preview(white, box)
        if roi is not None:
            return roi                   # ENTER confirmed
        # roi is None => 'r' pressed: loop back, re-drag on the same frame (no re-SPACE)
```

### `_deskew_preview(base, box)` — new

```
def _deskew_preview(base, box) -> RoiBox | None:   # RoiBox on ENTER, None on 'r' (re-drag); raises on 'q'
    fh, fw = base.shape[:2]
    cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
    w, h = float(box[2]), float(box[3])
    angle = 0.0
    title = "Calib 1/3: rotate to deskew (keys in the TERMINAL)"
    _open_window(title, fw, fh)
    print(... "  <- / -> rotate 0.5deg   ENTER confirm   r re-drag   q quit")
    while True:
        disp = base.copy()
        cv2.polylines(disp, [_option_box_pts(cx, cy, w, h, angle, fw, fh)], True, (0, 255, 0), 2)
        # HUD: current angle + key legend
        imshow_fit(title, disp); cv2.waitKey(20)
        key = _poll_key()
        if   key == "LEFT":  angle -= _ROTATE_STEP_DEG   # 0.5
        elif key == "RIGHT": angle += _ROTATE_STEP_DEG
        elif key in ("\r", "\n"):
            cv2.destroyWindow(title)
            return screen_roi_from_rect(((cx, cy), (w, h), angle), fw, fh)
        elif key in ("r", "R"):
            cv2.destroyWindow(title); return None
        elif key in ("q", "Q", "\x1b"):
            cv2.destroyWindow(title); raise KeyboardInterrupt
```

`_option_box_pts` is the existing rotation-about-center helper (today nested in `_pick_roi`), lifted
to take explicit `(cx, cy, w, h, angle, fw, fh)`. It calls `screen_roi_from_rect` to do the 5:3
widening + ref scaling + edge clamp, maps the result back to frame pixels, and rotates the polygon by
`+angle` about its center — so the green preview is exactly the crop `ENTER` will store (one copy of
the expansion rule; a clamp at a frame edge shows in the preview too). `_ROTATE_STEP_DEG = 0.5` is a
module constant.

The dragged box supplies center + (w, h) at angle 0; the operator drags a box matching the screen's
size as if untilted, then rotates it onto the tilt. The 5:3 widening means the green box is wider than
a ~16:10 screen — "aligned" means parallel edges + centered, not edge-coincident.

### `_poll_key()` — surface the arrow keys

Today the `\x00`/`\xe0` two-byte prefix is consumed and `""` returned (arrows swallowed). Change to
read the second byte and map the two we use:

```
if ch in ("\x00", "\xe0"):
    code = msvcrt.getwch()
    return {"K": "LEFT", "M": "RIGHT"}.get(code, "")   # other function keys still ignored
```

Backward-compatible: every other caller matches single characters (`" "`, `"q"`, `"\r"`, `"\x1b"`,
`"1"`/`"2"`/`"3"`, `"m"`, `"e"`, `"y"`, …), none of which equal `"LEFT"`/`"RIGHT"`, so no existing key
handling changes.

### Deletions in `calibration.py`

Remove (dead once auto-detection is gone — confirmed sole consumers were `_pick_roi` + the 4 tests):

- Constants: `_SCREEN_ASPECT_LO`, `_SCREEN_ASPECT_HI`, `_SCREEN_MIN_AREA_FRAC`, `_SCREEN_BORDER_FRAC`,
  `_SCREEN_TARGET_AREA_FRAC`, `_SCREEN_THRESH_FRACS`, `_QUAD_MAX_COS`.
- Functions: `_screen_likeness`, `_norm_rect`, `_corner_cos`, `_is_clean_quad`, `detect_screen_rect`.

Keep: the `_Rect` type alias (still the parameter type of `screen_roi_from_rect`), `screen_roi_from_rect`,
`deskew_crop`, `fit_camera_circle`, `arc_bands_from_circle`, `_RED_PRIOR`, `_prior_mask`,
`sample_red_band`, `suggest_coverage_threshold`, and the ruamel writer. Trim the module docstring's
screen-detection paragraph.

### `calibrate_view.py` import + docstring

Drop `detect_screen_rect` from the `calibration` import (keep `screen_roi_from_rect` and the rest).
Update the module docstring's Flow section step 1 to describe drag → rotate → confirm.

## Testing

- Delete `test_detect_screen_rect_finds_tilted_interior_quad`,
  `test_detect_screen_rect_nonrect_blob_falls_back_to_blob`,
  `test_detect_screen_rect_returns_none_on_blank_frame`,
  `test_detect_screen_rect_deskew_sign_is_correct`, and remove `detect_screen_rect` from the import.
- Keep the remaining 8 tests. `test_screen_roi_from_rect_is_5_3_at_800x480_ref_with_angle` already
  pins the exact output contract `ENTER` produces (5:3 box at the 800x480 ref carrying the chosen
  angle), so the math the rotate loop depends on stays covered.
- The new drag/rotate interaction is cv2-HighGUI + msvcrt and is not unit-testable in host scope —
  consistent with the rest of `calibrate_view.py`, which is exercised on the bench.
- Gates: `ruff format`, `ruff check`, `mypy src`, `pytest -m 'not hardware'` all green.
- Bench validation (manual, post-merge): run `calibrate_view.py` live, drag + rotate onto a tilted
  screen, confirm the written `screen_roi.angle` deskews the ROI upright in `usb_camera_roi_preview.py`.

## Documentation

- Flow docstrings in `calibrate_view.py` and `calibration.py` (above).
- CLAUDE.md §3 `calibrate_view` blurb and §7 system-camera paragraph — the stale "white picks one of
  up to 3 deskewed 5:3 ROI candidates" wording becomes "white frame is the drag-and-rotate backdrop."
- `system_camera_config.yaml` header comments still hold (still "re-derived from 3 captures"); no
  change needed beyond the wording above.

## Iron Laws

- **IL-5**: still writes only `system_camera_config.yaml` via the validated ruamel writer (+ `.bak`);
  no runtime code writes it. Unchanged.
- **IL-2**: no `references/` edits; the deleted detector was an in-tree adaptation, not a vendored import.
- **IL-7**: doc touch-ups above keep CLAUDE.md the single source of truth.
- No motor/bus/voltage surface (IL-1/3/4/6 N/A).
