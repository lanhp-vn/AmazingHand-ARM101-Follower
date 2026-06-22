# System-Camera View Calibration — Design

**Date:** 2026-06-22
**Status:** Approved (pending implementation plan)
**Scope:** A single calibration script + a pure detection module + a small config/schema change. One subsystem; one spec.

---

## 1. Problem

The arm-mounted USB observation camera (IFWATER IMX362, films the Optomed Aurora screen) feeds two computer-vision features:

1. **Screen ROI zoom** — `AURORA_SCREEN_ROI` (a `Roi` constant in `src/arm101_hand/system_camera/roi.py`) crops every preview/recording frame down to just the Aurora screen.
2. **Arc auto-trigger** — `arc_detector.detect()` classifies the Aurora's two on-screen alignment arcs RED/GREEN by HSV coverage inside arc sub-regions of that ROI. The arc regions + red/green HSV bands live in `src/arm101_hand/data/system_camera_config.yaml` (`auto_trigger` block).

These were hand-tuned once, at one resolution and one lighting condition. Two things break them:

- **Resolution change.** The ROI is stored as a fraction of a reference frame and rescaled by `Roi.for_frame`. But changing the capture mode on this sensor can shift the **field of view**, not merely the pixel count — so the screen no longer lands at the same fraction, and the ROI + arc regions drift. (Observed after the 640×480 → 1600×1200 switch on 2026-06-22.)
- **Lighting change.** The red/green HSV bands are lighting-dependent; a different room/exposure shifts them.

There is no tool to re-derive these values. Today it is manual: capture frames, eyeball, hand-edit YAML + a Python constant in two different files. This spec defines a calibration script that regenerates all of it from three fresh sample frames, with operator confirmation, and unifies the screen ROI into the YAML config so calibration writes one file.

**Sample frames driving the design** (`media_outputs/camera_captures/`, captured at 1600×1200):
- `*_startupscreen.jpg` — Aurora showing a full-white startup screen (the screen is a bright rectangle; background contains bright windows + a doorway → "largest bright thing" is ambiguous).
- `*_red.jpg` — circular live view with two **red** arc segments (misaligned).
- `*_green.jpg` — same arcs **green** (aligned).

---

## 2. Goals / Non-goals

**Goals**
- One command re-derives, from three guided captures: the **screen ROI**, the **left/right arc regions**, the **red + green HSV bands**, and a suggested **`coverage_threshold`**.
- **Auto-detect, then confirm**, with a manual-override fallback at every stage.
- Validate against the **real runtime detector** (`arc_detector.detect()`) before writing.
- Write only on explicit operator confirmation; preserve the YAML's hand-authored comments.

**Non-goals**
- No change to the auto-trigger lifecycle (`stable_seconds`, `cooldown_seconds`, `require_clear_between`, …) — those stay hand-tuned.
- No live teleop/policy integration.
- No hardware in CI (the interactive UI is bench-only; only pure logic is unit-tested).
- No `opencv-contrib-python` — runtime is plain `opencv-python` (main modules only); contrib modules (ximgproc, …) are not importable.

---

## 3. Architecture

Two new pieces, mirroring the codebase's split of **pure device-layer logic** vs **thin script shell**:

- **`src/arm101_hand/system_camera/calibration.py`** (device layer; pure functions, no cv2 window) — the detection math. Synthetic-numpy unit-testable, in the style of `arc_detector.py`.
- **`scripts/calibration/system_camera/calibrate_view.py`** (new dir — first calibration script for this device) — the interactive shell: guided capture (cloned from `usb_camera_capture.py`), runs the detectors, drives the confirm UI, writes the config on confirmation.

**Reuse, don't reinvent (maintainability):**
- The confirm screen runs the **existing `arc_detector.detect()`** with the just-produced config, so calibration validates with the exact detector the demo runs at runtime — no second, drifting classifier.
- Rectangle detection adapts the official `references/computer-vision/opencv/samples/python/squares.py` pattern (threshold → `findContours` → `approxPolyDP` → area/convexity filter).
- The trackbar manual-override adapts `references/computer-vision/opencv/samples/python/tutorial_code/imgProc/threshold_inRange/threshold_inRange.py`.
- (References are read-only per IL-2; patterns are *adapted into* `src/`, never imported from `references/`.)

---

## 4. Calibration flow

1. **Capture #1 — white startup screen.** Live cv2 window (clone of `usb_camera_capture.py`'s live-window + SPACE loop); operator frames it, SPACE grabs.
   → detect bright rectangle → build **3 ROI candidates** → operator picks `1`/`2`/`3`, or `m` for manual `selectROI` drag, or `r` to recapture. ENTER/SPACE locks the screen ROI.
2. **Capture #2 — red arcs.** Live window **with the locked ROI box overlaid** so the operator frames the arcs inside it; SPACE grabs.
   → crop to ROI, resize to the 640×480 detection reference → find red arcs → **left/right arc regions** + **red HSV band**.
3. **Capture #3 — green arcs.** Same, ROI overlaid; SPACE grabs.
   → reuse the arc regions (same arcs, recolored) → **green HSV band**; confirm the regions agree with the red frame.
4. **Confirm screen** (see §6) — shows CV-processed mask overlays + real-detector labels.
5. **Write** — only on `y`/ENTER, write to `system_camera_config.yaml` (see §7).

**CLI:** defaults to live guided capture. Also:
- `--from-files WHITE RED GREEN` — re-run detection on already-saved frames (iterate on tuning without re-shooting; e.g. the three existing captures).
- `--camera N` / `--backend auto|dshow` — override config like the sibling scripts.

---

## 5. Detection algorithms (`calibration.py`)

### 5.1 Screen ROI — from the white frame
- `cvtColor` BGR→GRAY → Otsu `threshold(THRESH_BINARY|THRESH_OTSU)` → `morphologyEx(MORPH_CLOSE)` (fills the logo/knob/text so the screen reads as one solid blob).
- `findContours(RETR_EXTERNAL)`; for each blob compute a **screen-likeness score**: rectangularity (`approxPolyDP` ≈ 4 corners), fill ratio (`contourArea / boundingRect-area` ≈ 1), plausible size + aspect.
- Return the **top-3 ranked** bounding boxes. The 3-candidate pick exists precisely because background clutter (bright windows/doorway) makes "largest bright region" ambiguous — the human disambiguates.
- `build_roi_candidates(bbox, frame_w, frame_h)` normalizes each pick to a **distortion-free 4:3** crop about its center, clamped inside the frame.

### 5.2 Arc regions + colors — from the red & green frames
- Crop each frame to the chosen ROI; resize to the 640×480 detection reference (matching the pipeline's detection frame).
- **Red frame:** `BGR2HSV` → broad red prior mask → `MORPH_OPEN` despeckle → split ROI into left/right halves → `boundingRect` of red pixels in each half → **left/right arc regions** (padded by a small margin).
- **Color sampling:** within each arc region, take the matched pixels' HSV and compute **5th–95th percentiles** per channel → a tight, lighting-adapted band. If the red hue distribution straddles the 0/180 wrap, emit **two** bands (matching the existing red-band convention); green is a single band.
- **Green frame:** reuse the arc regions; sample green pixels' HSV → green band.

### 5.3 `coverage_threshold` suggestion
- With the fresh bands, measure green coverage on the green frame (high) vs the red frame (≈0) per arc region; set the threshold at the **separating midpoint**, floored at a small minimum (so firing tracks the real arc thickness instead of the guessed `0.04`).

### 5.4 Public functions (interfaces)
```python
def detect_screen_rect(white_bgr: np.ndarray, *, top_n: int = 3) -> list[tuple[int, int, int, int]]:
    """Ranked candidate (x, y, w, h) bounding boxes of the bright screen, best first."""

def build_roi_candidates(
    bbox: tuple[int, int, int, int], frame_w: int, frame_h: int, *, target_aspect: float = 4 / 3
) -> list[tuple[int, int, int, int]]:
    """3 distortion-free 4:3 ROI candidates around bbox, clamped inside the frame."""

def detect_arc_regions(
    roi_ref_bgr: np.ndarray, red_prior: list[HsvBand], *, pad: int = 4
) -> tuple[RoiBox, RoiBox]:
    """(left_arc, right_arc) regions in the 640x480 ROI reference, from red pixels per half."""

def sample_hsv_band(
    region_ref_bgr: np.ndarray, color: Literal["red", "green"], *, lo_pct: float = 5, hi_pct: float = 95
) -> list[HsvBand]:
    """1 band (green) or up to 2 (red hue-wrap) bracketing the matched pixels' HSV percentiles."""

def suggest_coverage_threshold(
    green_cov_on_green: float, green_cov_on_red: float, *, floor: float = 0.02
) -> float:
    """Midpoint between on/off coverage, floored."""
```
All pure (numpy in, dataclass/primitive out); no cv2 window calls.

---

## 6. Confirm UX & manual override

**Screen-ROI pick** (after capture #1): full frame with the 3 candidate boxes drawn + numbered, each candidate's 4:3 crop previewed in a side panel.
- Keys: `1`/`2`/`3` select · `m` manual `selectROI` drag · `r` recapture · ENTER/SPACE lock.

**Confirm screen** — a tiled composite shown via the existing `imshow_fit` letterbox (resizable; `WINDOW_KEEPRATIO` is a no-op on this Win32 backend):
- **Red panel:** ROI crop + both arc boxes + **red mask tinted over matched pixels**, captioned e.g. `LEFT: RED 0.21 · RIGHT: RED 0.19`.
- **Green panel:** ROI crop from the green frame + arc boxes + **green mask tinted**, captioned e.g. `LEFT: GREEN 0.18 · RIGHT: GREEN 0.20`.
- **Values panel:** the exact `screen_roi`, arc regions, red/green bands, and suggested `coverage_threshold` to be written.
- Labels come from the **real `arc_detector.detect()`** on each frame with the produced config → proves the live detector reads red-frame = *not ready*, green-frame = *ready* before saving.
- Keys: `y`/ENTER accept & write · `e` edit · `r` redo · `q` quit (writes nothing).

**Trackbar tuning** (`e`, adapted from `threshold_inRange.py`): H/S/V lo/hi sliders for red + green with a live mask preview on the captured frames; `selectROI` re-sets an arc box. Tweak → re-render confirm → accept.

---

## 7. Config, schema & write strategy

### 7.1 Schema (`src/arm101_hand/config/system_camera_config.py`)
- Rename `ArcRegion` → **`RoiBox`** (it already documents itself as "mirrors `system_camera.Roi`"); keep **`ArcRegion = RoiBox`** as a back-compat alias so existing imports/tests are untouched.
- Add a **top-level `screen_roi: RoiBox`** to `SystemCameraConfig`, default = today's `AURORA_SCREEN_ROI` values (`x=60, y=75, w=196, h=147, ref_w=640, ref_h=480`). Top-level (not in `auto_trigger`) because the preview/recording zoom uses it even when auto-trigger is off.
- Calibration writes `screen_roi` at **`ref = the calibration resolution`** (e.g. `ref_w=1600, ref_h=1200`), so at runtime the `for_frame` rescale is identity → kills the cross-resolution drift. (Re-calibrate when the resolution changes; that is the intended workflow.)
- `schema_version` bump 5 → 6.

### 7.2 `src/arm101_hand/system_camera/roi.py`
- Keep `Roi` + `AURORA_SCREEN_ROI` (now documented as the schema's fallback default).
- Promote `arc_detector._region_to_roi` to a public **`roi_from_region(region) -> Roi`** in `roi.py`; reuse it in `arc_detector` and the new consumers (DRY). Export from `system_camera/__init__.py`.

### 7.3 Consumers (read `cfg.screen_roi` instead of importing the constant)
- Demos: `grab_trigger_capture.py`, `grab_trigger_capture_analysis.py`, `grab_auto_trigger_analysis.py`.
- Diagnostics: `usb_camera_roi_preview.py` (default editable ROI from config), `usb_camera_focus_probe.py`.
- Each builds `roi_from_region(cfg.screen_roi)` and passes it where it currently passes `AURORA_SCREEN_ROI`. The constant remains the ultimate fallback.

### 7.4 Writer — ruamel.yaml round-trip
- Add **`ruamel.yaml`** as a dependency, used **only** by the calibration writer; the rest of the code keeps `yaml.safe_load` for reading (IL: never `yaml.load`; ruamel's round-trip loader is safe by default).
- Round-trip load → set `screen_roi`, `auto_trigger.left_arc/right_arc/red_bands/green_bands/coverage_threshold` → dump in place, **preserving every comment, key order, and formatting**.
- Before writing: write a `.bak` copy, and run the produced values through `SystemCameraConfig.model_validate` — on validation failure, show the error and **do not write**.

### 7.5 IL-5 framing
A calibration tool writing config is the sanctioned exception (same as hand calibration writing `hand_calib_values.yaml` and arm calibration writing `<id>.json`). Only the demos/controllers must never write config. This script writes only on explicit operator confirmation.

---

## 8. Error handling (never write a degenerate config)
- **Camera open/stall** — reuse `usb_camera_capture`'s grace + stall guards and guidance; exit non-zero, write nothing.
- **No plausible screen rectangle** — don't fail; drop into manual `selectROI`.
- **Too few arc pixels in a half** (wrong frame / arc unlit) — warn; offer redo-that-capture, manual `selectROI` for the arc box, or trackbar tuning; never emit an empty region/band.
- **`--from-files`** — missing/unreadable → clear error; image size ≠ current config `width/height` → warn (calibrating for a different resolution than the live stream).
- **Validation gate** — `SystemCameraConfig.model_validate` must pass before write; `q` writes nothing.

---

## 9. Testing (TDD, host-only — no hardware, no cv2 window)
The interactive UI (cv2 windows, `selectROI`, trackbars, `msvcrt`) is bench-only and untested, like the sibling scripts; all logic lives in the pure `calibration.py`.

- **`tests/unit/test_system_camera_calibration.py`** (synthetic numpy):
  - `detect_screen_rect` ranks a bright rectangle above a distractor bright blob; returns ≤3 candidates.
  - `build_roi_candidates` returns 3 in-bounds 4:3 boxes around a bbox.
  - `detect_arc_regions` finds a left + right region from synthetic red blobs in each half.
  - `sample_hsv_band` brackets a known HSV patch; splits into two bands on a red hue-wrap patch.
  - `suggest_coverage_threshold` lands strictly between the green/red coverages and respects the floor.
- **`tests/unit/test_system_camera_config.py`** — `screen_roi` default + round-trip; `RoiBox`/`ArcRegion` alias; `schema_version == 6`; data-yaml `screen_roi` assertion.
- **`tests/unit/test_roi.py`** — `roi_from_region(region)` builds the expected `Roi`.
- **Writer test** — ruamel round-trip on a tmp copy preserves a sample comment **and** updates values; result re-loads via `load_system_camera_config` to the intended values.
- **CI** stays green: `ruff format --check`, `ruff check`, `mypy src`, `pytest -m 'not hardware'`; `ruamel.yaml` added to deps + `uv.lock` updated.

---

## 10. Docs to refresh on completion
- `CLAUDE.md` — `system_camera` module note + the new `scripts/calibration/system_camera/` workflow line; mention `screen_roi` moved into YAML.
- `README.md` — calibration command.
- `docs/BOM.md` — no change (resolution unchanged).
- DRY registry / `06-documentation-protocol.md` — register this spec + plan.
- Memory: update `project-system-camera-2k-stream` and add a calibration-tool pointer.

---

## 11. Future-port note
The codebase targets a later port to an SL2619 (2 GB) SBC + Waveshare 7" 800×480 DSI display, likely swapping the IFWATER USB cam for an OV5640 (CSI). This calibration tool becomes the re-tune path for that pipeline: new camera + new resolution → re-run `calibrate_view.py`.
