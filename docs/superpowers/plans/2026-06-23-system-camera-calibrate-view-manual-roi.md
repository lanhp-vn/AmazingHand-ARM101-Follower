# System-camera manual drag-and-deskew ROI calibration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `calibrate_view.py` Calib 1/3 (auto screen-rect detection + 3-angle preset picker) with a manual drag-then-arrow-rotate ROI step, and delete the now-dead detection code.

**Architecture:** Reuse the existing `_drag_box` (mouse-drag + terminal-confirm) to place an axis-aligned box over the Aurora screen, then a small `_deskew_preview` loop nudges the 5:3 crop's angle with ← / → until its edges sit parallel to the (possibly tilted) screen. `_poll_key` learns to surface the two arrow keys. The RED/CLEAR recalibration pipeline (circle fit → arc bands → red sample → threshold, with the `e` retune) is untouched and re-runs on the freshly captured frames. The auto-detector (`detect_screen_rect` + helpers) and its 4 tests are removed.

**Tech Stack:** Python 3.12, OpenCV (plain `opencv-python`, no contrib), numpy, pydantic, ruamel.yaml, `msvcrt` (Windows console keys), pytest, ruff, mypy.

## Global Constraints

- **Base = the current working tree, NOT HEAD.** This refactor builds on uncommitted WIP (the `detect_screen_rect` single-rect iteration + `_drag_box`). Do NOT `git stash`, `git restore`, `git checkout`, or otherwise discard working-tree changes.
- **Touch ONLY these files:** `scripts/calibration/system_camera/calibrate_view.py`, `src/arm101_hand/system_camera/calibration.py`, `tests/unit/test_system_camera_calibration.py`, `CLAUDE.md`. Leave all other modified files (`ONBOARDING.md`, `hand_config.yaml`, `system_camera_config.yaml`, `usb_camera_capture.py`, `usb_camera_roi_preview.py`, `test_roi.py`) exactly as they are — they are unrelated concerns.
- **IL-2:** never edit anything under `references/`. The deleted detector was an in-tree adaptation, not a vendored import.
- **IL-5:** `system_camera_config.yaml` is written only by the validated ruamel writer (`write_calibration_values`) at bench time; no plan step hand-edits it.
- **No `yaml.load()`** — `yaml.safe_load()` only (already the case).
- **Gates (all must pass):** `uv run ruff format <files>`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest -m 'not hardware'`.
- **Commit format:** Conventional Commits with a `(system_camera)` scope, matching recent history (`feat(system_camera): …`, `docs(system_camera): …`).
- **Branch:** all commits land on `feat/calibrate-view-manual-roi` (already checked out; the spec doc is already committed there).

---

### Task 1: Rewrite `calibrate_view.py` Calib 1/3 → drag + arrow-rotate

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py`
- Modify: `CLAUDE.md` (doc touch-ups for the new flow)

**Interfaces:**
- Consumes (unchanged, already imported): `screen_roi_from_rect(rect, frame_w, frame_h) -> RoiBox`, `roi_from_region`, `imshow_fit`, `open_capture`, `RoiBox`, the existing `_drag_box`, `_open_window`, `_capture_frame`, `_FONT`.
- Produces: a module-level `_option_box_pts(cx, cy, w, h, angle, fw, fh) -> np.ndarray`, a new `_deskew_preview(base, box) -> RoiBox | None`, and a rewritten `_pick_roi(white) -> RoiBox | None` (None = recapture the white frame). `main()`'s capture loop is unchanged.

Ordering note: this task runs **before** Task 2 so the tree stays runnable at each commit — after Task 1, `calibrate_view.py` no longer imports `detect_screen_rect`, while `calibration.py` still defines it (harmless dead code the test still imports); Task 2 then removes both.

- [ ] **Step 1: Drop the `detect_screen_rect` import**

In the `from arm101_hand.system_camera.calibration import (...)` block, delete the line `    detect_screen_rect,`. Keep `screen_roi_from_rect` and every other name. Resulting block:

```python
from arm101_hand.system_camera.calibration import (  # noqa: E402
    arc_bands_from_circle,
    deskew_crop,
    fit_camera_circle,
    sample_red_band,
    screen_roi_from_rect,
    suggest_coverage_threshold,
    write_calibration_values,
)
```

- [ ] **Step 2: Add the rotate-step constant**

Find the constants block (just below the imports):

```python
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_DETECT = (800, 480)  # the deskewed 5:3 detection reference (ref_w x ref_h)
_REF_W, _REF_H = _DETECT
_MAX_WIN_W, _MAX_WIN_H = 1100, 800  # initial window cap so large frames fit the screen
```

Append one line:

```python
_ROTATE_STEP_DEG = 0.5  # <- / -> step in the deskew-rotate preview (auto-repeats on key-hold)
```

- [ ] **Step 3: Teach `_poll_key` to surface ← / →**

Replace the prefix-swallowing branch in `_poll_key`. Current:

```python
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume the 2nd byte, ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch
```

New:

```python
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> read the scan-code 2nd byte
        return {"K": "LEFT", "M": "RIGHT"}.get(msvcrt.getwch(), "")  # only the arrows we use
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch
```

Also update the `_poll_key` docstring's final sentence — change `arrow / function-key prefixes are swallowed.` to:

```
Left/Right arrows return the literal tokens "LEFT"/"RIGHT" (used by the deskew-rotate loop); other arrow / function keys are still swallowed.
```

- [ ] **Step 4: Replace `_pick_roi` (and its nested `_option_box_pts`) with three definitions**

Delete the entire current `_pick_roi` function (from `def _pick_roi(white: np.ndarray) -> RoiBox | None:` through its final `raise KeyboardInterrupt`, including the nested `_option_box_pts`). Replace with the following module-level `_option_box_pts`, then `_pick_roi`, then `_deskew_preview`:

```python
def _option_box_pts(
    cx: float, cy: float, w: float, h: float, angle: float, fw: int, fh: int
) -> np.ndarray:
    """Polygon (int32 points) of the 5:3 crop box for a screen rect at ``angle``, in frame pixels.

    Draw EXACTLY the box ENTER will commit: screen_roi_from_rect does the 5:3 expansion + ref scaling
    + edge clamp, so deriving the preview from it (mapped back to the frame, rotated by +angle about
    its centre) keeps the green preview faithful to the stored/cropped ROI -- one copy of the
    expansion rule, and a clamp at a frame edge shows in the preview too."""
    box = screen_roi_from_rect(((cx, cy), (w, h), angle), fw, fh)
    bx, by, bw, bh = roi_from_region(box).for_frame(fw, fh)
    center = (bx + bw / 2.0, by + bh / 2.0)
    return cv2.boxPoints((center, (float(bw), float(bh)), angle)).astype(np.int32)


def _pick_roi(white: np.ndarray) -> RoiBox | None:
    """Drag a box over the Aurora screen, then rotate the 5:3 crop to deskew it. None = recapture.

    Replaces the old auto-detect + 3-angle-preset picker: the operator drags an axis-aligned box
    (sized to the screen), then nudges the angle with the arrow keys until the 5:3 crop's edges sit
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

    Draws the 5:3 crop box (green, the exact region ENTER will store) rotated by a live angle over the
    white frame; <- / -> nudge the angle by _ROTATE_STEP_DEG. Keys come from the TERMINAL (see
    _poll_key); the window only displays. The dragged box gives centre + (w, h) at angle 0 --
    screen_roi_from_rect widens the short side to 5:3, so the green box reads WIDER than a ~16:10
    screen; "aligned" means parallel edges + centred, not edge-coincident."""
    fh, fw = base.shape[:2]
    cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
    w, h = float(box[2]), float(box[3])
    angle = 0.0
    title = "Calib 1/3: rotate to deskew (press keys in the TERMINAL)"
    print(
        "\nRotate the 5:3 crop to deskew -- focus THIS terminal:\n"
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
            "green = chosen 5:3 crop (rotate until its edges match the screen)",
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
```

- [ ] **Step 5: Fix the stale `--from-files` recapture message**

In `main()`, the `--from-files` branch has a loop whose message still mentions the removed angle presets / `'m'`. Current:

```python
            screen_roi = None
            while screen_roi is None:
                screen_roi = _pick_roi(white)
                if screen_roi is None:
                    print(
                        "Recapture not available with --from-files; preview an angle (1/2/3) "
                        "then ENTER, or 'm' to drag manually."
                    )
```

New:

```python
            screen_roi = None
            while screen_roi is None:
                screen_roi = _pick_roi(white)
                if screen_roi is None:
                    print(
                        "Recapture not available with --from-files; drag a box then rotate "
                        "(ENTER to confirm), or q to quit."
                    )
```

- [ ] **Step 6: Update the module docstring Flow + the selectROI note**

In the module docstring, replace Flow step 1. Current:

```
  1. WHITE startup screen -> the single detected screen rect is previewed; pick 1 of 3 deskew angles
     (theta-1 / theta / theta+1 deg, keys 1/2/3, middle = the auto-detected theta), or 'm' to drag a
     box manually (mouse drag, angle 0), or 'r' to recapture. The chosen ROI is a RoiBox at the
     800x480 detection reference carrying that deskew angle.
```

New:

```
  1. WHITE startup screen -> DRAG a box over the screen (mouse), then <- / -> to rotate the 5:3 crop
     until its edges sit parallel to the (possibly tilted) screen; ENTER confirms, 'r' re-drags, 'q'
     quits. The chosen ROI is a RoiBox at the 800x480 detection reference carrying that deskew angle.
```

In the same docstring, the sentence that begins `The 'm'/'e' manual drags are mouse-only...` — change `The 'm'/'e' manual drags` to `The screen-box drag and 'e' arc-retune drags` so it reads:

```
The screen-box drag and 'e' arc-retune drags are mouse-only in the window (mouse events reach a
window without keyboard focus) and are accepted/cancelled from the TERMINAL too -- NOT cv2.selectROI,
whose key-confirm hangs a focusless console window (see _drag_box). Plain opencv-python only (no contrib).
```

- [ ] **Step 7: Format + lint + import smoke**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check scripts/calibration/system_camera/calibrate_view.py
uv run python scripts/calibration/system_camera/calibrate_view.py --help
```

Expected: ruff format reports the file formatted/unchanged; ruff check reports `All checks passed!`; `--help` prints the argparse usage/description and exits 0 (this confirms the module imports cleanly — `msvcrt`, `cv2`, `arm101_hand` — and parses with no syntax error, without opening a camera).

- [ ] **Step 8: CLAUDE.md doc touch-ups**

Read `CLAUDE.md`, then apply three replacements (match the exact current text):

1. §3 src-tree `system_camera/` line — change `view-calibration math (calibration.py = rotated-rect screen detection + 5:3 deskew candidate + camera-circle fit` to `view-calibration math (calibration.py = 5:3 deskew crop + camera-circle fit`.

2. §3 scripts-tree line — change `# calibrate_view: interactive re-derive of deskewed 5:3 screen_roi + circle-based arc bands` to `# calibrate_view: interactive re-derive of deskewed 5:3 screen_roi (manual drag + arrow-key rotate) + circle-based arc bands`.

3. §7 paragraph — change `white picks one of up to 3 deskewed 5:3 ROI candidates, red samples the arc red band` to `the white frame is the drag-and-rotate backdrop for the deskewed 5:3 screen_roi (manual box + arrow-key rotation), red samples the arc red band`.

- [ ] **Step 9: Run the full suite (regression guard) + commit**

```bash
uv run ruff check .
uv run pytest -m 'not hardware'
```

Expected: ruff `All checks passed!`; pytest green (the calibration tests are unaffected — `calibrate_view.py` is a script, not imported by any test). Then commit ONLY Task 1's files:

```bash
git add scripts/calibration/system_camera/calibrate_view.py CLAUDE.md
git commit -m "feat(system_camera): manual drag-and-rotate ROI in calibrate_view

Replace Calib 1/3 auto screen-rect detection + the 3-angle preset picker
with a manual drag then arrow-key (<-/->) rotation of the 5:3 crop. Drop
the detect_screen_rect import; _poll_key now surfaces LEFT/RIGHT. RED/CLEAR
recalibration (circle fit, arc bands, red sample, threshold, 'e' retune) is
unchanged and re-runs on the same captures."
```

---

### Task 2: Delete the dead screen-detection code + its tests

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Modify: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Removes: `detect_screen_rect`, `_is_clean_quad`, `_screen_likeness`, `_norm_rect`, `_corner_cos`, and the constants `_SCREEN_ASPECT_LO`, `_SCREEN_ASPECT_HI`, `_SCREEN_MIN_AREA_FRAC`, `_SCREEN_BORDER_FRAC`, `_SCREEN_TARGET_AREA_FRAC`, `_SCREEN_THRESH_FRACS`, `_QUAD_MAX_COS`.
- Keeps (verify still present + importable): `_Rect`, `screen_roi_from_rect`, `deskew_crop`, `fit_camera_circle`, `arc_bands_from_circle`, `_RED_PRIOR`, `_prior_mask`, `sample_red_band`, `suggest_coverage_threshold`, `_flow_map`, `_roibox_map`, `_hsv_map`, `write_calibration_values`.

- [ ] **Step 1: Remove the 4 detection tests + their imports from the test file**

In `tests/unit/test_system_camera_calibration.py`, delete these four test functions in full: `test_detect_screen_rect_finds_tilted_interior_quad`, `test_detect_screen_rect_nonrect_blob_falls_back_to_blob`, `test_detect_screen_rect_returns_none_on_blank_frame`, `test_detect_screen_rect_deskew_sign_is_correct`.

Then prune the now-unused imports. `detect_screen_rect` is referenced only by the deleted tests; `deskew_crop` is referenced only by `test_detect_screen_rect_deskew_sign_is_correct`. Remove BOTH from the import block so ruff's unused-import check passes. Resulting block:

```python
from arm101_hand.system_camera.calibration import (
    arc_bands_from_circle,
    fit_camera_circle,
    sample_red_band,
    screen_roi_from_rect,
    suggest_coverage_threshold,
    write_calibration_values,
)
```

(`cv2` and `np` stay — the remaining tests use them.)

- [ ] **Step 2: Run the trimmed suite to confirm it fails cleanly on the stale import, THEN that it is green after**

First run to see the deletion is consistent:

```bash
uv run pytest tests/unit/test_system_camera_calibration.py -q
```

Expected at this point: PASS — the 8 remaining tests still import only kept symbols (`detect_screen_rect` still exists in `calibration.py` because Task 2 Step 3 hasn't run yet, but it's no longer imported, which is fine). If instead you see an ImportError, you removed a symbol the remaining tests still use — re-check Step 1.

- [ ] **Step 3: Delete the detector + helpers + constants from `calibration.py`**

Remove the screen-detection block. Concretely:

(a) The seven module constants:

```python
_SCREEN_ASPECT_LO, _SCREEN_ASPECT_HI = 1.2, 2.0  # landscape ~16:10..16:9
_SCREEN_MIN_AREA_FRAC = 0.015  # ignore blobs smaller than 1.5% of the frame (quad test does the rest)
_SCREEN_BORDER_FRAC = 0.01  # within 1% of an edge counts as "touching the border"
_SCREEN_TARGET_AREA_FRAC = 0.04  # size score saturates around 4% of the frame
_SCREEN_THRESH_FRACS = (0.4, 0.6, 0.8)  # high cuts between Otsu and 255 to isolate the backlit LCD
_QUAD_MAX_COS = 0.25  # a clean quad's worst corner cosine -- |cos| < this == near-right corners
```

(b) The five functions in full: `_screen_likeness`, `_norm_rect`, `_corner_cos`, `_is_clean_quad`, `detect_screen_rect`.

KEEP the `_Rect = tuple[tuple[float, float], tuple[float, float], float]` type alias (it is the parameter type of `screen_roi_from_rect`). After deletion, `screen_roi_from_rect` should be the first definition following the imports + the `_Rect` alias.

- [ ] **Step 4: Trim the module docstring**

In `calibration.py`'s module docstring, delete the screen-detection paragraph (the one beginning `Screen detection sweeps several HIGH brightness thresholds ...` through `... ``detect_screen_rect`` returns the single best rect (or None). Plain opencv-python only (no contrib); patterns adapted from the OpenCV samples, never imported from references/.`). Replace that whole paragraph with:

```
The ROI is chosen interactively (manual drag + arrow-key deskew) in the calibrate_view shell; this
module only normalises the chosen rect to a 5:3 deskewed RoiBox, fits the camera circle, derives the
symmetric arc bands, samples the red HSV band, and round-trips the config. Plain opencv-python only
(no contrib).
```

- [ ] **Step 5: Format, lint, type-check, test**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'
```

Expected: ruff `All checks passed!` (no unused imports/names left by the deletion); `mypy src` clean (`Success: no issues found`); pytest green with the 8 remaining calibration tests (`test_screen_roi_from_rect_is_5_3_at_800x480_ref_with_angle`, `test_fit_camera_circle_*`, `test_arc_bands_*`, `test_sample_red_band_*`, `test_suggest_coverage_threshold_*`, `test_write_calibration_*`) and no `test_detect_screen_rect_*`.

- [ ] **Step 6: Commit Task 2's files**

```bash
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "refactor(system_camera): drop dead screen-rect auto-detection

detect_screen_rect + its _is_clean_quad/_screen_likeness/_norm_rect/
_corner_cos helpers and the _SCREEN_*/_QUAD_MAX_COS constants are unused now
that Calib 1/3 places the ROI by manual drag + rotate. Remove them, the 4
test_detect_screen_rect_* tests, and trim the module docstring. screen_roi_from_rect,
the circle fit, arc bands, red sampling, threshold, and the writer are unchanged."
```

---

## Manual bench verification (operator-run, after both tasks)

Requires the arm-mounted USB camera + the Aurora screen (hardware; not part of the automated gates):

1. `uv run python scripts/calibration/system_camera/calibrate_view.py`
2. Calib 1/3: confirm the box drags, ← / → rotates the green 5:3 box live, the angle HUD updates, `r` re-drags, `ENTER` advances to Calib 2/3.
3. Complete Calib 2/3 (RED) + 3/3 (BRIGHT); confirm the confirm panel still labels both arcs (RED panel both RED, BRIGHT panel both clear) and `y` writes.
4. `uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py` — confirm the written `screen_roi` (with its angle) deskews the Aurora screen upright in the ROI zoom.

---

## Self-review

**Spec coverage:**
- Manual drag + arrow-rotate Calib 1/3 → Task 1 Steps 2–6. ✓
- `_poll_key` surfaces ← / → → Task 1 Step 3. ✓
- Drop `detect_screen_rect` import + auto-detection → Task 1 Step 1 (import), Task 2 Step 3 (defs). ✓
- RED/CLEAR pipeline unchanged + re-runs → untouched in both tasks (verified by Task 2's kept-symbol list + the 8 surviving tests). ✓
- Delete the 4 `test_detect_screen_rect_*` tests → Task 2 Step 1. ✓
- `test_screen_roi_from_rect_is_5_3_at_800x480_ref_with_angle` still covers the ENTER output contract → kept (Task 2 Interfaces). ✓
- Doc touch-ups (both docstrings + CLAUDE.md §3/§7) → Task 1 Steps 3/6/8, Task 2 Step 4. ✓
- `--from-files` stale message → Task 1 Step 5 (caught beyond the spec; the new flow has no presets/`m`). ✓

**Placeholder scan:** none — every code/edit step shows the exact text.

**Type consistency:** `_option_box_pts(cx, cy, w, h, angle, fw, fh) -> np.ndarray`, `_pick_roi(white) -> RoiBox | None`, `_deskew_preview(base, box) -> RoiBox | None`, `_poll_key() -> str` (now also returns `"LEFT"`/`"RIGHT"`). `screen_roi_from_rect(((cx, cy), (w, h), angle), fw, fh)` matches the `_Rect = ((cx,cy),(w,h),angle)` signature used by both `_option_box_pts` and `_deskew_preview`. Consistent across tasks.
