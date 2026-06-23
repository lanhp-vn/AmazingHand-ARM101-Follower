# System-Camera Arc-Detection Debug Diagnostic — Design

**Date:** 2026-06-23
**Status:** Approved (pending implementation plan)
**Scope:** One new read-only diagnostic script + a small pure sidecar-builder helper + one host unit test. No changes to the device layer, the demos, or the config schema. One subsystem; one spec.

---

## 1. Problem

The arm-mounted USB observation camera films the Optomed Aurora's screen, and `arc_detector.detect()` classifies the Aurora's two on-screen alignment arcs as RED (misaligned) or not-red (aligned) by HSV red-coverage inside two arc sub-regions of the deskewed 5:3 (800×480) `screen_roi`. The red-gate auto-trigger (`grab_auto_trigger_analysis.py`) fires a capture on a both-red → both-not-red transition.

After the 2026-06-23 deskew/circle/red-gate revision, the open item is **detection quality**: on the bench-saved captures the RED/clear classification is unreliable for some frames (e.g. one arc reads faint, ~1.2% coverage just under the 0.02 threshold; the other arc is desaturated below the sampled `s_lo`). It is not yet known whether the fix is a different `coverage_threshold`, different red HSV bands, or different arc box geometry — and there is **no tool to collect the exact frames where detection misbehaves**, at the real grab pose, with the numbers the detector saw.

This spec defines a **diagnostic** (not a demo) that stages the arm + hand into `grab`, shows the live deskewed ROI with the two arc boxes drawn and colored by their live RED/clear verdict, and on SPACE saves a self-documenting "case" (clean frame + annotated frame + a numbers sidecar). The collected cases become the labeled evidence for retuning `coverage_threshold` / `red_bands` / arc geometry.

---

## 2. Goals / Non-goals

**Goals**
- Stage arm + hand into `grab` using the **existing** staged-grab harness (`run_grab_demo`), so the camera observes the Aurora at the **only pose where the ROI geometry is valid**.
- Show a live window of the deskewed 800×480 ROI with the **two arc boxes drawn** (`left_arc`/`right_arc` from config), each colored **red when that arc reads RED, green when clear**, plus a HUD of the raw `detect()` output.
- On **SPACE**, save a three-file "case" (clean ROI / annotated ROI / numbers sidecar) sharing a timestamped stem, for later offline analysis and retuning.
- Reuse the **exact** runtime crop + detection path (`open_capture`, `roi_from_region`, `Roi.crop`, `imshow_fit`, `arc_detector.detect`) so what the diagnostic shows is what the demo sees — no second, drifting classifier.

**Non-goals**
- **No Aurora link.** No `PictorClient`, no shutter press, no capture cycle, no image pull. This is detection-only.
- **No trigger.** No `auto_trigger` lifecycle, no firing, not even a non-firing "would fire" banner — raw `detect()` classification only (the thing being debugged).
- **No recording.** Stills-only (SPACE); no `r` toggle.
- **No grade / patient turns / DR model.** None of the analysis-demo machinery.
- **No device-layer changes.** `preview.py`, `arc_detector.py`, `auto_trigger.py`, the config schema, and the demos are untouched.
- **No hardware in CI.** The window + grab path is bench-only; only the pure sidecar-builder is unit-tested.

---

## 3. Architecture

Follows the codebase split of **pure logic** vs **thin script shell**, and the established single-threaded diagnostic pattern of `scripts/diagnostics/system_camera/usb_camera_roi_preview.py`.

- **`scripts/diagnostics/system_camera/usb_camera_arc_debug.py`** (new; application/diagnostic layer) — the shell. Calls `run_grab_demo(on_hold=...)`; the hold-hook opens the camera and runs a single-threaded read → crop → `detect()` → draw → `imshow_fit` → `msvcrt.kbhit` poll loop. SPACE saves a case; `q`/ESC/Ctrl+C ends the hook and falls through to `run_grab_demo`'s normal release/reverse exit prompt.
- **A pure sidecar-builder** — a module-level function (`build_arc_case_sidecar`) that takes the `AlignmentState`, the `AutoTriggerConfig` (threshold + red bands + arc boxes), the `screen_roi`, camera metadata, and an injected timestamp, and returns the JSON-serializable `dict` the sidecar is written from. Pure and host-unit-testable (no cv2, no file I/O).
  - **Location:** defined **locally in the diagnostic script** (a module-level `def`, like `usb_camera_roi_preview._write_image`), since it is diagnostic-specific and not reused by the device layer or demos — keeping it out of `src/` honors the "no device-layer changes" non-goal.
  - **Import mechanism for the test:** `scripts/` is **not** an importable package in this repo (the scripts add `src/` to `sys.path` and import `arm101_hand`; nothing imports a script *module*). So the unit test loads the function via a path-based import (`importlib.util.spec_from_file_location` on the script's absolute path), which is safe because all heavy work (`run_grab_demo`, camera open) is guarded under `if __name__ == "__main__"` / inside `main()` and the module top-level only defines functions + constants. This is documented here so the plan does not assume `scripts/` is a package.

**Why approach B (self-contained single-threaded loop), not `WebcamPreview`:**
- Keeps `preview.py` (shared production device code) untouched — a diagnostic-only need does not justify adding a box-overlay API to the production previewer.
- Reuses the pure helpers, so the crop + detection math is byte-identical to the demo's.
- The single-threaded `kbhit` + `waitKey(1)` loop is exactly the pattern `usb_camera_roi_preview.py` already uses; the window only needs to be live during the hold-hook (after the blocking grab motion completes), so no daemon thread is required.

**Reuse, don't reinvent:**
- Grab staging + reverse-on-`h` exit: `arm101_hand.scripts.grab_common.run_grab_demo` / `GrabHoldContext` (the same harness the demos use). The hold-hook ignores `ctx` (it coordinates no hand motion — the hand just holds `grab`).
- Crop/deskew: `roi_from_region(scfg.screen_roi)` → `Roi.crop` then resize to `(ref_w, ref_h)` = 800×480 (identical to `WebcamPreview._run`).
- Detection: `arc_detector.detect(clean_roi, scfg.auto_trigger)` → `AlignmentState(left_red, right_red, left_cov, right_cov)`.
- Window: `cv2.namedWindow(WINDOW_NORMAL)` + `imshow_fit` (Win32-backend letterbox; `WINDOW_KEEPRATIO` is a no-op on this wheel).

---

## 4. Behavior / data flow

```
run_grab_demo(on_hold=_arc_debug_loop)
  └─ stages arm+hand → grab (blocking; no window yet)
     └─ on_hold(ctx):                       # arm+hand held under torque
        open_capture(index, backend, fourcc, width, height, autofocus, focus)
        roi = roi_from_region(scfg.screen_roi)            # deskewed 800×480
        loop:
          ok, frame = cap.read()                          # bail on grace/stall (as roi_preview)
          clean = resize(roi.crop(frame), (ref_w, ref_h)) # the saveable ground truth
          state = detect(clean, scfg.auto_trigger)        # raw classification
          disp  = clean.copy()
            draw left_arc box  (red if state.left_red  else green)
            draw right_arc box (red if state.right_red else green)
            draw HUD: L/R verdict + cov vs threshold + both_red/both_clear + saved count
          imshow_fit(window, disp)
          cv2.waitKey(1)
          key = poll_key()
            SPACE → save case (clean / annotated=disp / sidecar)
            q/ESC → break        # → run_grab_demo exit prompt (Enter=release, h=reverse)
  └─ exit prompt → release / reverse, cut torque on both buses
```

The detector runs **every frame** (cheap; ~800×480 HSV + two small masks). No `detect_interval_s` throttle is needed in a single-threaded loop bounded by camera FPS, and per-frame detection keeps the box colors maximally responsive while hunting for a transient failing frame.

**Arc box coordinates.** `scfg.auto_trigger.left_arc` / `right_arc` are `RoiBox`es measured against the 800×480 ROI reference — the same space the resized `clean` frame is in — so they map onto the display via `roi_from_region(arc).for_frame(clean.w, clean.h)` (identity when the frame is already at ref size). This is exactly how `detect()` crops them, so the drawn boxes are precisely the regions classified.

---

## 5. SPACE output — the "case" (level c)

Three files in `--out-dir` (default `media_outputs/arc_debug/`), sharing a millisecond-timestamp stem `arc_<YYYYmmdd_HHMMSS_fff>`:

| File | Content |
|---|---|
| `arc_<ts>_clean.png` | The un-annotated 800×480 ROI — **ground truth**, re-feedable to `detect()` / `calibrate_view.py --from-files`. |
| `arc_<ts>_annotated.png` | The display frame: ROI + arc boxes + verdict HUD burned in (at-a-glance documentation). |
| `arc_<ts>.json` | Numbers sidecar (below). |

**Sidecar schema** (built by the pure helper; `json.dump` with `indent=2`):

```json
{
  "tool": "usb_camera_arc_debug",
  "captured_at": "2026-06-23T14:05:33.482000+00:00",
  "camera": {"index": 1, "backend": "dshow", "frame_w": 1600, "frame_h": 1200},
  "screen_roi": {"x":102,"y":89,"w":202,"h":97,"ref_w":800,"ref_h":480,"angle":-2.045},
  "detection": {
    "left_red": true,  "left_cov": 0.061,
    "right_red": false, "right_cov": 0.004,
    "both_red": false, "both_clear": false,
    "coverage_threshold": 0.02
  },
  "left_arc":  {"x":144,"y":130,"w":70,"h":230,"ref_w":800,"ref_h":480,"angle":0.0},
  "right_arc": {"x":586,"y":130,"w":70,"h":230,"ref_w":800,"ref_h":480,"angle":0.0},
  "red_bands": [{"h_lo":0,"s_lo":35,"v_lo":50,"h_hi":12,"s_hi":255,"v_hi":255}, ...],
  "morph_kernel": 3,
  "expected_note": ""
}
```

**REVISION 2026-06-23 (post-bench, operator feedback): `expected_note` is captured at SPACE, not label-later.** SPACE now prompts in the terminal (`input()`) for a short note describing what is wrong with the detector's call, echoing the current verdict (e.g. `note for this case (detector said L:RED R:clr both_red=False) -- Enter to skip:`); the typed string becomes `expected_note`, and Enter-with-no-text leaves it empty. The cv2 window freezes during the prompt — acceptable here: the arm holds grab under torque and the paused frame is exactly the one being described, so there is no transient to lose. (`build_arc_case_sidecar` gained an `expected_note: str = ""` keyword param; the default keeps label-later possible and the original unit test valid.) The original design deferred labeling to avoid an `input()` freeze, but at-capture prompting proved more useful in practice — the operator labels each failing case while it is in front of them.

**Coordinate/band sources.** `screen_roi`, the arc boxes, `red_bands`, `coverage_threshold`, and `morph_kernel` are copied verbatim from the loaded `SystemCameraConfig` (`model_dump` of the relevant sub-models), so each case records the exact config that produced the verdict — essential when comparing cases after a config edit.

---

## 6. CLI / keys / output

**Invocation**
```
uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py
    [--camera N] [--backend auto|dshow] [--out-dir DIR]
```
- `--camera` / `--backend` default to `camera_index` / `backend` from `system_camera_config.yaml` (override pattern identical to `usb_camera_roi_preview.py`).
- `--out-dir` defaults to `<repo>/media_outputs/arc_debug` (created on first save).

**Keys** (focus the **terminal**, not the window):
| Key | Action |
|---|---|
| `SPACE` | prompt for a note (terminal `input()`), then save a case (clean + annotated + sidecar) |
| `q` / ESC | quit the loop → arm/hand exit prompt |
| Ctrl+C | same as `q` (then exit prompt) |

**Camera-failure handling.** Reuse `usb_camera_roi_preview.py`'s grace/stall guards: if the camera opens but delivers no frame within ~5 s, or stalls for ~3 s mid-run, print the same actionable guidance and end the loop (the grab exit prompt still runs, so torque is always released).

**No-surprise-movement compliance** (memory `no_surprise_movements`): all motion is owned by `run_grab_demo`, which already moves only on the defined staged sequence and, on exit, releases torque in place (or reverses only on explicit `h`). The diagnostic adds **no** arm/hand motion of its own.

---

## 7. Testing / verification

**Host unit test (`tests/unit/test_arc_debug_sidecar.py`, runs in CI):**
- Loads `build_arc_case_sidecar` from the script via `importlib.util.spec_from_file_location` (see §3 — `scripts/` is not a package).
- Given a synthetic `AlignmentState` + an `AutoTriggerConfig` + a `RoiBox` screen_roi + camera metadata + a fixed injected timestamp, assert the returned dict has the expected keys, the `detection` block mirrors the `AlignmentState`/threshold, the box/band blocks mirror the config (`model_dump`), and `expected_note == ""` by default. A second case passes `expected_note="..."` and asserts it is stored. Pure dict-in/dict-out; no cv2, no I/O.

**Bench-only (manual, not CI):** the window, the grab staging, SPACE prompting for a note then saving the three files (with the note in the sidecar), and the arc-box colors tracking a real red/clear screen — verified on hardware, like the other `scripts/diagnostics/system_camera/` tools.

**Lint/type/format:** `ruff format`, `ruff check`, `mypy src`, `pytest -m 'not hardware'` all green (CI enforces all four).

---

## 8. Iron-Laws check

- **IL-2 (references read-only):** patterns are adapted from existing `src/`/`scripts/` code; nothing under `references/` is imported or modified.
- **IL-4 (one owner per bus):** the arm + hand buses are opened/closed solely by `run_grab_demo`; the diagnostic opens only the USB camera (not a motor bus).
- **IL-5 (state in version control; no runtime writes to calibration):** the tool **reads** `system_camera_config.yaml` and **never writes** it. Its only writes are the case files under `media_outputs/arc_debug/` (a gitignored media output, not project state).
- **IL-7 (single source of truth):** detection + crop logic is reused from the device layer, not re-implemented; docs get a one-line pointer (CLAUDE.md §4 diagnostics list + README), per the documentation protocol.

---

## 9. Files touched

| Path | Change |
|---|---|
| `scripts/diagnostics/system_camera/usb_camera_arc_debug.py` | **new** — the diagnostic shell + pure sidecar-builder. |
| `tests/unit/test_arc_debug_sidecar.py` | **new** — host unit test for the sidecar-builder. |
| `CLAUDE.md` | add the tool to the diagnostics list (§4) + the tech-debt/CV note. |
| `README.md` | add the tool to the diagnostics list. |

No new dependencies; no config-schema change; no device-layer change.
