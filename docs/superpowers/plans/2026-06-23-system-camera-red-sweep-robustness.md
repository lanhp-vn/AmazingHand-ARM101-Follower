# Red-detection sweep robustness — pooled hue + low floor + transitional exclusion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sweep_red_detection` produce a separable red band + coverage threshold from the real labelled cases by pooling the hue across all red frames, fitting a low coverage floor, and excluding (and reporting) internally-contradictory "red" frames — without touching the runtime detection pipeline.

**Architecture:** All algorithm changes are pure functions in `src/arm101_hand/system_camera/calibration.py`, unit-tested with synthetic numpy frames. `scripts/calibration/system_camera/calibrate_view.py` changes only its sweep *report* to surface excluded frames. The runtime `arc_detector.detect` / `red_coverage` / both-red→both-clear gate is unchanged — calibration tunes the same pipeline used at runtime.

**Tech Stack:** Python 3.12, OpenCV (plain `opencv-python` 4.13), numpy, pydantic, ruamel.yaml, pytest, ruff, mypy.

## Global Constraints

- **Base = current working tree on branch `feat/calibrate-view-manual-roi`.** Builds on the committed arc-sweep work (`calibration.py` already has `describe_case`/`pick_threshold`/`ArcCase`/`SweepResult`/`sweep_red_detection`; `calibrate_view.py` already has `_report_sweep`).
- **Touch ONLY:** `src/arm101_hand/system_camera/calibration.py`, `tests/unit/test_system_camera_calibration.py`, `scripts/calibration/system_camera/calibrate_view.py`, `CLAUDE.md`, `docs/conventions/06-documentation-protocol.md`. Stage explicitly — the working tree also has unrelated dirty files (`ONBOARDING.md`, `hand_config.yaml`, `system_camera_config.yaml`, `usb_camera_capture.py`, `usb_camera_roi_preview.py`, `test_roi.py`) and dirty `references/` submodule pointers that must NEVER be committed (no `git add -A`/`.`).
- **Runtime detection is OUT of scope.** Do NOT modify `arc_detector.py`, `auto_trigger.py`, or any file under `scripts/demos/`. Red-only classification + both-red→both-clear gate retained (the two Aurora arcs are red together when misaligned).
- **`pick_threshold`'s own default floor stays 0.02** — thread `floor=0.005` from `sweep_red_detection` instead (no churn to `test_pick_threshold_empty_returns_floor`).
- **IL-2:** no edits under `references/`; OpenCV patterns reimplemented, never imported. **IL-5:** `system_camera_config.yaml` written only by `write_calibration_values`.
- **Gates:** `uv run ruff format <files>`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest -m 'not hardware'`.
- **Commit format:** Conventional Commits, scope `(system_camera)`.

---

### Task 1: Pooled hue anchor + DRY band-bracket helper (pure)

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py` (refactor `sample_red_band` at lines 91-115; add `_bands_from_points` + `_pooled_red_anchor`)
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Consumes: existing `_prior_mask`, `_RED_PRIOR`, `roi_from_region`, config `HsvBand`/`RoiBox`.
- Produces: `_bands_from_points(pts: np.ndarray, lo_pct: float, hi_pct: float) -> list[HsvBand]`; `sample_red_band(region_bgr, *, lo_pct=5, hi_pct=95) -> list[HsvBand]` (unchanged behaviour, now delegating); `_pooled_red_anchor(red_frames: list[np.ndarray], left_arc: RoiBox, right_arc: RoiBox, *, lo_pct=5, hi_pct=95) -> list[HsvBand]`.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_system_camera_calibration.py`)

```python
def _fill_arcs(left, right, hsv_color):
    """800x480 BGR frame with both arc boxes filled with a given HSV colour."""
    hsv = np.zeros((480, 800, 3), dtype=np.uint8)
    for arc in (left, right):
        hsv[arc.y : arc.y + arc.h, arc.x : arc.x + arc.w] = hsv_color
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_pooled_red_anchor_widens_hue_across_cases():
    from arm101_hand.system_camera.calibration import _pooled_red_anchor

    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    near0 = _fill_arcs(left, right, (2, 200, 200))  # hue 2
    near180 = _fill_arcs(left, right, (178, 200, 200))  # hue 178 (wraps to red)
    # a single near-0 frame anchors only the near-zero band
    single = sample_red_band(roi_from_region(left).crop(near0))
    assert len(single) == 1 and single[0].h_lo == 0
    # pooling a near-0 frame and a near-180 frame yields BOTH wrap bands
    pooled = _pooled_red_anchor([near0, near180], left, right)
    assert len(pooled) == 2
    assert any(b.h_lo == 0 for b in pooled) and any(b.h_hi == 180 for b in pooled)
```

Also add `roi_from_region` to the test's imports — add this line to the top import block (after the calibration import block):

```python
from arm101_hand.system_camera import roi_from_region
```

- [ ] **Step 2: Run it, verify failure**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py::test_pooled_red_anchor_widens_hue_across_cases -q`
Expected: FAIL — `ImportError: cannot import name '_pooled_red_anchor'`.

- [ ] **Step 3: Implement** — in `calibration.py`, REPLACE the entire existing `sample_red_band` function (lines 91-115) with the helper + refactored sampler + pooled anchor below:

```python
def _bands_from_points(pts: np.ndarray, lo_pct: float, hi_pct: float) -> list[HsvBand]:
    """Percentile-bracket pre-masked HSV points into 1-2 red bands (0/180 hue wrap). Shared by
    ``sample_red_band`` (single region) and ``_pooled_red_anchor`` (pooled over many frames)."""
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
    bands: list[HsvBand] = []
    near_zero = hue[hue <= 90]
    near_180 = hue[hue > 90]
    if near_zero.size:
        bands.append(
            HsvBand(h_lo=0, s_lo=s_lo, v_lo=v_lo, h_hi=int(np.percentile(near_zero, hi_pct)), s_hi=255, v_hi=255)
        )
    if near_180.size:
        bands.append(
            HsvBand(h_lo=int(np.percentile(near_180, lo_pct)), s_lo=s_lo, v_lo=v_lo, h_hi=180, s_hi=255, v_hi=255)
        )
    return bands


def sample_red_band(region_bgr: np.ndarray, *, lo_pct: float = 5, hi_pct: float = 95) -> list[HsvBand]:
    """Percentile-bracket the red arc pixels into 1-2 bands (0/180 hue wrap). Raises if none match."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    pts = hsv[_prior_mask(hsv, _RED_PRIOR) > 0]
    if pts.size == 0:
        raise ValueError("no red pixels to sample in this region")
    return _bands_from_points(pts, lo_pct, hi_pct)


def _pooled_red_anchor(
    red_frames: list[np.ndarray],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    lo_pct: float = 5,
    hi_pct: float = 95,
) -> list[HsvBand]:
    """Pool ``_RED_PRIOR``-masked pixels across the LEFT+RIGHT arc crops of ALL red cases, then
    percentile-bracket the pooled hue into 1-2 bands (0/180 wrap). Wider / more robust than anchoring
    on a single frame -- catches arcs whose hue/saturation drifts frame to frame. Raises ValueError if
    no red pixels are found across any red case."""
    chunks: list[np.ndarray] = []
    for frame in red_frames:
        for arc in (left_arc, right_arc):
            hsv = cv2.cvtColor(roi_from_region(arc).crop(frame), cv2.COLOR_BGR2HSV)
            pts = hsv[_prior_mask(hsv, _RED_PRIOR) > 0]
            if pts.size:
                chunks.append(pts)
    if not chunks:
        raise ValueError("no red pixels to anchor the hue across the red cases")
    return _bands_from_points(np.concatenate(chunks), lo_pct, hi_pct)
```

- [ ] **Step 4: Run it, verify pass (new + the unchanged sampler guard)**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k "pooled_red_anchor or sample_red_band" -q`
Expected: PASS (2 tests — the new pooled test + the unchanged `test_sample_red_band_brackets_and_wraps`).

- [ ] **Step 5: Format, lint, type-check, commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run mypy src
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(system_camera): pooled red-hue anchor across all red cases"
```

Expected: ruff clean; mypy Success; commit succeeds.

---

### Task 2: `SweepResult.excluded_transitional` + reworked `sweep_red_detection` (pure)

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py` (add field to `SweepResult` lines 177-186; rewrite `sweep_red_detection` lines 189-261)
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Consumes: `_pooled_red_anchor` (Task 1), `pick_threshold`, `detect`, `AutoTriggerConfig`/`HsvBand`/`RoiBox`, `ArcCase`.
- Produces: `SweepResult(..., excluded_transitional: list[int])`; `sweep_red_detection(cases, left_arc, right_arc, *, morph_kernel=3, s_floors=tuple(range(15,130,5)), v_floors=tuple(range(30,150,10)), floor=0.005, blank_eps=0.008) -> SweepResult`.

- [ ] **Step 1: Write the failing tests** (append to the test file; `_roi_with_arcs` already exists at line 148, `_fill_arcs` was added in Task 1)

```python
def test_sweep_excludes_transitional_red_case():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))  # both arcs red
    clear_frame = _roi_with_arcs(left, right, (80, 160, 80))  # both greenish/clear
    transitional = _roi_with_arcs(left, right, (80, 160, 80))  # start greenish...
    transitional[left.y : left.y + left.h, left.x : left.x + left.w] = (0, 0, 200)  # ...left arc red only
    cases = [ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear"), ArcCase(transitional, "red")]
    res = sweep_red_detection(cases, left, right)
    assert res.separable is True  # the clean both-red + clear separate
    assert 2 in res.excluded_transitional  # the one-arc-red 'red' frame is excluded from fitting
    assert 2 not in res.unsatisfied  # and is not counted as a fit failure
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, red_bands=res.red_bands, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red and detect(clear_frame, cfg).both_clear


def test_sweep_pooled_hue_classifies_two_different_red_hues():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_a = _fill_arcs(left, right, (2, 200, 200))  # hue 2
    red_b = _fill_arcs(left, right, (178, 200, 200))  # hue 178 (wraps to red)
    clear = _roi_with_arcs(left, right, (80, 160, 80))
    res = sweep_red_detection(
        [ArcCase(red_a, "red"), ArcCase(red_b, "red"), ArcCase(clear, "clear")], left, right
    )
    assert res.separable is True
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, red_bands=res.red_bands, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_a, cfg).both_red and detect(red_b, cfg).both_red and detect(clear, cfg).both_clear
```

- [ ] **Step 2: Run it, verify failure**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k "transitional or two_different_red_hues" -q`
Expected: FAIL — `AttributeError: 'SweepResult' object has no attribute 'excluded_transitional'` (and/or the contradictory-case still dragged into the fit).

- [ ] **Step 3: Add the `excluded_transitional` field to `SweepResult`** — replace the `SweepResult` dataclass (lines 177-186) with:

```python
@dataclass(frozen=True)
class SweepResult:
    """Best-effort tuned detection: the red bands + threshold, whether every FITTED (non-excluded)
    case is satisfied, the fitted cases still misclassified, the per-case (det_left, det_right), and
    the 'red' cases excluded from fitting as transitional (one arc read clear)."""

    red_bands: list[HsvBand]
    coverage_threshold: float
    separable: bool
    unsatisfied: list[int]
    case_detections: list[tuple[bool, bool]]
    excluded_transitional: list[int]
```

- [ ] **Step 4: Rewrite `sweep_red_detection`** — replace the entire existing function (lines 189-261) with:

```python
def sweep_red_detection(
    cases: list[ArcCase],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    morph_kernel: int = 3,
    s_floors: Sequence[int] = tuple(range(15, 130, 5)),
    v_floors: Sequence[int] = tuple(range(30, 150, 10)),
    floor: float = 0.005,
    blank_eps: float = 0.008,
) -> SweepResult:
    """Tune the red HSV band + coverage threshold against all labelled cases.

    1. Hue anchor: pool ``_RED_PRIOR`` pixels across the arc crops of EVERY 'red' case
       (``_pooled_red_anchor``) -> hue bounds spanning the full observed range (+ the 0/180 wrap).
    2. Grid over (s_floor, v_floor): build candidate red_bands (pooled hue/s_hi/v_hi, swept floors),
       compute per-arc coverage for every case via the runtime ``detect`` (same masking + morph). A
       'red' case whose weaker arc is < ``blank_eps`` is TRANSITIONAL (it cannot be a true both-red
       frame -- the two arcs share a colour) and is excluded from the fit pool. Pool the clean
       red-case coverages vs clear-case coverages -> threshold = ``pick_threshold(..., floor=floor)``.
    3. Score by (#clean+clear cases classified to expected, then -#transitional, then margin); return
       the best as a SweepResult with red_bands + threshold + excluded_transitional + the fitted-case
       unsatisfied indices + per-case detections.

    Raises ValueError if there is no 'red' case (or no red pixels) to anchor the hue, or if no
    candidate band recovers a single clean both-red case."""
    red_cases = [c for c in cases if c.expected == "red"]
    if not red_cases:
        raise ValueError("sweep needs at least one 'red' case to anchor the hue")
    anchor = _pooled_red_anchor([c.frame for c in red_cases], left_arc, right_arc)

    def _transitional(c: ArcCase, lc: float, rc: float) -> bool:
        return c.expected == "red" and min(lc, rc) < blank_eps

    best_key: tuple[int, int, float] = (-1, -(10**9), -1.0)
    best: tuple[list[HsvBand], float, list[tuple[float, float]]] | None = None
    for s_floor in s_floors:
        for v_floor in v_floors:
            bands = [
                HsvBand(h_lo=b.h_lo, s_lo=s_floor, v_lo=v_floor, h_hi=b.h_hi, s_hi=b.s_hi, v_hi=b.v_hi)
                for b in anchor
            ]
            trial = AutoTriggerConfig(
                left_arc=left_arc, right_arc=right_arc, red_bands=bands, morph_kernel=morph_kernel
            )
            covs: list[tuple[float, float]] = []
            for c in cases:
                st = detect(c.frame, trial)
                covs.append((st.left_cov, st.right_cov))
            red_pool = [
                v
                for c, (lc, rc) in zip(cases, covs, strict=True)
                if c.expected == "red" and not _transitional(c, lc, rc)
                for v in (lc, rc)
            ]
            clear_pool = [
                v for c, (lc, rc) in zip(cases, covs, strict=True) if c.expected != "red" for v in (lc, rc)
            ]
            if not red_pool:  # this band recovered no clean both-red case -> useless
                continue
            t, _sep = pick_threshold(red_pool, clear_pool, floor=floor)
            clean_correct = 0
            n_transitional = 0
            for c, (lc, rc) in zip(cases, covs, strict=True):
                if _transitional(c, lc, rc):
                    n_transitional += 1
                    continue
                want = c.expected == "red"
                if (lc >= t) == want and (rc >= t) == want:
                    clean_correct += 1
            margin = min((abs(v - t) for v in red_pool + clear_pool), default=0.0)
            key = (clean_correct, -n_transitional, margin)
            if key > best_key:
                best_key = key
                best = (bands, t, covs)

    if best is None:
        raise ValueError("sweep recovered no clean both-red case; show clearer RED arcs and re-capture")
    bands, t, covs = best
    excluded = [
        i for i, (c, (lc, rc)) in enumerate(zip(cases, covs, strict=True)) if _transitional(c, lc, rc)
    ]
    excluded_set = set(excluded)
    dets = [(lc >= t, rc >= t) for (lc, rc) in covs]
    unsatisfied = [
        i
        for i, (c, (dl, dr)) in enumerate(zip(cases, dets, strict=True))
        if i not in excluded_set and not (dl == (c.expected == "red") and dr == (c.expected == "red"))
    ]
    return SweepResult(
        red_bands=bands,
        coverage_threshold=round(float(t), 4),
        separable=not unsatisfied,
        unsatisfied=unsatisfied,
        case_detections=dets,
        excluded_transitional=excluded,
    )
```

- [ ] **Step 5: Run the sweep tests, verify pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k sweep -q`
Expected: PASS (4 tests — the two new ones + `test_sweep_red_detection_separates_red_from_clear` + `test_sweep_red_detection_reports_unsatisfiable_cases`, which both still hold: pure-both-red is clean/separable; the contradictory-`clear` case is not excluded → non-separable, `unsatisfied` non-empty).

- [ ] **Step 6: Full suite + type-check + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(system_camera): low-floor sweep + transitional-frame exclusion"
```

Expected: ruff `All checks passed!`; mypy Success; pytest green (the 2 new tests added; nothing removed).

---

### Task 3: `_report_sweep` surfaces the excluded transitional frames (GUI report)

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py` (`_report_sweep` at lines 422-432)

**Interfaces:**
- Consumes: `SweepResult.excluded_transitional` (Task 2), existing `describe_case`, `SweepResult`, `ArcCase`.

GUI code is not unit-tested (cv2 + msvcrt); verification is ruff + the `--help` import smoke. `_sweep`, `_confirm`, `_test_loop`, and `main()` are unchanged — they call the new sweep with its defaults.

- [ ] **Step 1: Replace `_report_sweep`** — replace the entire existing function (lines 422-432) with:

```python
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
```

- [ ] **Step 2: Format, lint, smoke**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check scripts/calibration/system_camera/calibrate_view.py
uv run python scripts/calibration/system_camera/calibrate_view.py --help
```

Expected: ruff clean (`SweepResult`/`ArcCase`/`describe_case` already imported); `--help` exits 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/calibration/system_camera/calibrate_view.py
git commit -m "feat(system_camera): report transitional excluded frames in calibrate_view sweep"
```

---

### Task 4: Docs — CLAUDE.md §7 + DRY registry

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/conventions/06-documentation-protocol.md`

- [ ] **Step 1: CLAUDE.md §7** — in the `System-camera preview + DR-grading inference ...` bullet (line 160), find the clause:

```
the red HSV band + coverage threshold are auto-swept against a RED + CLEAR reference panel (pick_threshold = max-margin when separable, min-misclassification when not) then refined by an arc-debug-style test loop that re-sweeps over operator-labelled frames
```

Replace it with:

```
the red HSV band + coverage threshold are auto-swept against a RED + CLEAR reference panel (hue POOLED across ALL labelled red frames, a low coverage floor, pick_threshold = max-margin when separable / min-misclassification when not, and transitional one-arc-blank 'red' frames excluded from the fit + reported) then refined by an arc-debug-style test loop that re-sweeps over operator-labelled frames
```

- [ ] **Step 2: DRY registry** — in `docs/conventions/06-documentation-protocol.md` §10.1, under "Computer vision (system camera)", add two rows (after the existing arc-sweep calibration rows):

```
| System-camera red-detection sweep robustness design (pooled hue anchor across all red frames + low coverage floor + transitional one-arc-blank frame exclusion; runtime detection unchanged) | `docs/superpowers/specs/2026-06-23-system-camera-red-sweep-robustness-design.md` |
| System-camera red-detection sweep robustness implementation plan | `docs/superpowers/plans/2026-06-23-system-camera-red-sweep-robustness.md` |
```

- [ ] **Step 3: Verify cap + commit**

```bash
test "$(wc -l < CLAUDE.md)" -le 250 && echo "CLAUDE.md within cap"
git add CLAUDE.md docs/conventions/06-documentation-protocol.md
git commit -m "docs(system_camera): document red-sweep robustness (pooled hue + low floor + transitional exclusion)"
```

Expected: CLAUDE.md ≤ 250 lines (currently 164); commit succeeds.

---

## Manual bench verification (operator-run, after all tasks)

Hardware (arm + hand + USB camera + Aurora screen), or re-use the existing flow:

1. `uv run python scripts/calibration/system_camera/calibrate_view.py` — stage the grab, drag the screen ROI + the two arc boxes, capture RED + CLEAR panels.
2. Read the sweep report: it should now report `separable=True` over the fitted cases, with any transitional (one-arc-blank) "red" frames listed as `EXCLUDED (transitional)`.
3. Press `t`, label a few clean both-red and both-clear frames (skip transitional ones), `d`; confirm the re-sweep stays separable.
4. `y` to write; verify the `.bak` and the new `red_bands`/`coverage_threshold` in `system_camera_config.yaml` (threshold should be low, ~0.005-0.02, not pinned at the floor with most cases wrong).
5. Confirm in `usb_camera_arc_debug.py` / `grab_auto_trigger_analysis.py` that aligned reads both-clear and misaligned reads both-red live.

## Self-review

**Spec coverage:**
- Lower the sweep's coverage floor → Task 2 (`floor=0.005` param threaded into `pick_threshold`). ✓
- Pool the hue anchor across all red cases → Task 1 (`_pooled_red_anchor`) + Task 2 (sweep uses it). ✓
- Exclude + report internally-contradictory "red" frames → Task 2 (`excluded_transitional`, `_transitional`) + Task 3 (`_report_sweep`). ✓
- Runtime untouched (`arc_detector`/`auto_trigger`/demos) → no task modifies them. ✓
- `pick_threshold` default floor unchanged → Task 2 threads floor; `test_pick_threshold_empty_returns_floor` not edited. ✓
- DRY `_bands_from_points` shared by `sample_red_band` + `_pooled_red_anchor` → Task 1. ✓
- Tests: pooled-hue widening (Task 1), transitional exclusion + pooled-hue end-to-end (Task 2), existing sweep/sample tests preserved. ✓
- Docs (CLAUDE.md §7 + DRY registry) → Task 4. ✓

**Placeholder scan:** none — every code step shows complete content.

**Type consistency:** `_bands_from_points(np.ndarray, float, float) -> list[HsvBand]`; `_pooled_red_anchor(list[np.ndarray], RoiBox, RoiBox, *, lo_pct, hi_pct) -> list[HsvBand]`; `SweepResult` gains `excluded_transitional: list[int]`; `sweep_red_detection(..., floor=0.005, blank_eps=0.008) -> SweepResult`; `_report_sweep(SweepResult, list[ArcCase]) -> None` reads `.excluded_transitional`. Consistent across tasks; `pick_threshold` signature unchanged (sweep passes `floor=` keyword).
