# Arc-redness LAB a* detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the arc auto-trigger's HSV red-band coverage metric with LAB a* coverage so the alignment arcs are detected even when the bright fundus (eye at the cup) washes them toward white.

**Architecture:** `detect()` classifies each arc box by the fraction of pixels whose LAB **a\*** channel (red–green axis) is ≥ a cutoff `a_star_min`, thresholded by `coverage_threshold`. The per-arc → both-red gate → `auto_trigger` lifecycle is unchanged. The calibration sweep grids over `a_star_min` instead of HSV hue/S/V bands. The HSV `red_bands` list and the `HsvBand` model are removed; `a_star_min: int` replaces them in config, `SweepResult`, and `write_calibration_values`.

**Tech Stack:** Python 3.12, OpenCV (`opencv-python` full wheel), NumPy, pydantic v2, ruamel.yaml, pytest.

## Global Constraints

- **IL-2:** never modify `references/**`. Not touched here.
- **IL-5:** runtime code never hand-writes calibration; the data yaml is operator config. The calibration writer is the sanctioned path (`write_calibration_values`, atomic + `.bak` + pydantic-validated).
- **No `yaml.load()`** — `yaml.safe_load()` only (already the case in `load_system_camera_config`).
- **Style:** Python 3.12, ruff format + ruff check + `mypy src` must pass; `from __future__ import annotations` at module top (existing convention in these files).
- **LAB detail (exact):** `cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)` 8-bit → channel index **1** is a\*, with **128 = neutral**, `>128` = redder. The cutoff lives in `[128, 255]`.
- **Detection ref:** the deskewed ROI + arc boxes are at the 640×480 reference; arc boxes are axis-aligned (angle 0), so cropping the LAB image is a plain slice.
- **Evidence-based defaults:** from the 45 bench frames, `a_star_min ≈ 134` + `coverage_threshold ≈ 0.011` separate all frames (clear ceiling 0.0068, red floor 0.0170).

---

### Task 1: src core — config + detector + calibration sweep (+ data yaml + src unit tests)

Migrate the primitive/device layer and its two unit-test files together (the `red_bands`→`a_star_min` field change ripples through all three source files and both tests at once). The two script test files and the scripts themselves are intentionally left for Task 2; this task verifies with a **targeted** pytest run so the not-yet-migrated script tests are not collected.

**Files:**
- Modify: `src/arm101_hand/config/system_camera_config.py`
- Modify: `src/arm101_hand/system_camera/arc_detector.py`
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Modify: `src/arm101_hand/data/system_camera_config.yaml`
- Test: `tests/unit/test_system_camera_config.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Produces (consumed by Task 2 + runtime):
  - `arc_detector.arc_redness_mask(lab_band: np.ndarray, a_star_min: int) -> np.ndarray`
  - `arc_detector.red_coverage(lab_band: np.ndarray, cfg: AutoTriggerConfig) -> float`
  - `arc_detector.detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState` (unchanged signature)
  - `AutoTriggerConfig.a_star_min: int` (default 134, ge 128, le 255); `red_bands` removed; `HsvBand` removed
  - `SweepResult.a_star_min: int` (replaces `red_bands`)
  - `sweep_red_detection(cases, left_arc, right_arc, *, morph_kernel=3, a_star_floors=range(128,150), floor=0.005, blank_eps=0.008) -> SweepResult`
  - `write_calibration_values(config_path, *, screen_roi, left_arc, right_arc, a_star_min: int, coverage_threshold: float) -> None`

- [ ] **Step 1: Update the config schema test to the new API (failing)**

In `tests/unit/test_system_camera_config.py`:

Replace `test_resolution_fourcc_defaults`'s last line and the two schema-version tests to 8:
```python
def test_resolution_fourcc_defaults():
    cfg = SystemCameraConfig()
    assert cfg.width is None
    assert cfg.height is None
    assert cfg.fourcc == "MJPG"
    assert cfg.schema_version == 8


def test_schema_default_version_is_8():
    assert SystemCameraConfig().schema_version == 8
```
(Delete the old `test_schema_default_version_is_7`.)

Replace `test_auto_trigger_defaults` and `test_auto_trigger_is_red_only`:
```python
def test_auto_trigger_defaults():
    at = SystemCameraConfig().auto_trigger
    assert at.stable_seconds > 0
    assert at.cooldown_seconds >= 0
    assert at.detect_interval_s > 0
    assert 0.0 <= at.coverage_threshold <= 1.0
    assert 128 <= at.a_star_min <= 255  # LAB a* cutoff (128 = neutral)
    assert (at.left_arc.ref_w, at.left_arc.ref_h) == (640, 480)


def test_auto_trigger_is_red_only():
    at = SystemCameraConfig().auto_trigger
    assert 128 <= at.a_star_min <= 255
    assert (at.left_arc.ref_w, at.left_arc.ref_h) == (640, 480)
    assert (at.right_arc.ref_w, at.right_arc.ref_h) == (640, 480)
    assert not hasattr(at, "green_bands")
    assert not hasattr(at, "red_bands")
    assert not hasattr(at, "require_clear_between")
```

Replace `test_auto_trigger_rejects_empty_bands` with `a_star_min` range guards, and add `red_bands` to the removed-keys check:
```python
def test_auto_trigger_a_star_min_range():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"auto_trigger": {"a_star_min": 127}})  # below neutral
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"auto_trigger": {"a_star_min": 256}})  # above 8-bit max


def test_auto_trigger_rejects_removed_keys():
    for bad in ("green_bands", "require_no_red", "require_clear_between", "red_bands"):
        with pytest.raises(ValidationError):
            SystemCameraConfig.model_validate({"auto_trigger": {bad: [] if bad in ("green_bands", "red_bands") else True}})
```

Update `test_data_yaml_loads`'s arc assertions (drop the `red_bands` line):
```python
    at = cfg.auto_trigger
    assert at.left_arc.w > 0 and at.right_arc.w > 0  # arc bands present with positive geometry
    assert 128 <= at.a_star_min <= 255  # LAB a* cutoff present + in range
    assert at.stable_seconds > 0
```

- [ ] **Step 2: Run the config test to verify it fails**

Run: `uv run pytest tests/unit/test_system_camera_config.py -q`
Expected: FAIL — `AutoTriggerConfig` has no `a_star_min`, still has `red_bands`, `schema_version == 7`.

- [ ] **Step 3: Migrate the config schema**

In `src/arm101_hand/config/system_camera_config.py`: delete the entire `HsvBand` class. In `AutoTriggerConfig` replace the `red_bands` field and retune `coverage_threshold`:
```python
class AutoTriggerConfig(BaseModel):
    """Red-only arc auto-trigger: each arc is RED (>= coverage_threshold of pixels at LAB a* >=
    a_star_min) or not. ready/fire is gated by a red -> not-red transition (see
    arm101_hand.system_camera.auto_trigger)."""

    model_config = ConfigDict(extra="forbid")
    left_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=88, y=130, w=56, h=230, ref_w=640, ref_h=480))
    right_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=496, y=130, w=56, h=230, ref_w=640, ref_h=480))
    a_star_min: int = Field(
        default=134,
        ge=128,
        le=255,
        description="LAB a* cutoff: a pixel counts as red at a* >= this (128 = neutral, higher = redder)",
    )
    coverage_threshold: float = Field(
        default=0.01, ge=0.0, le=1.0, description="arc is RED at >= this fraction of pixels above a_star_min"
    )
    morph_kernel: int = Field(default=3, ge=1)
    stable_seconds: float = Field(default=1.0, gt=0.0)
    cooldown_seconds: float = Field(default=3.0, ge=0.0)
    detect_interval_s: float = Field(default=0.2, gt=0.0)
```
In `SystemCameraConfig` set `schema_version: int = 8`.

- [ ] **Step 4: Run the config test to verify it passes**

Run: `uv run pytest tests/unit/test_system_camera_config.py -q`
Expected: FAIL still — `test_data_yaml_loads` loads the data yaml which still has `red_bands` (extra-key forbidden). That is fixed in Step 9. All non-yaml config tests pass.

- [ ] **Step 5: Migrate the detector to LAB a***

Replace `src/arm101_hand/system_camera/arc_detector.py` body (keep the module docstring text but update the metric sentence). New imports + functions:
```python
from arm101_hand.config.system_camera_config import AutoTriggerConfig

from .roi import roi_from_region


@dataclass(frozen=True)
class AlignmentState:
    """Per-arc red classification + coverages (coverages are for the HUD/debug)."""

    left_red: bool
    right_red: bool
    left_cov: float
    right_cov: float

    @property
    def both_red(self) -> bool:
        return self.left_red and self.right_red

    @property
    def both_clear(self) -> bool:
        return not self.left_red and not self.right_red


def arc_redness_mask(lab_band: np.ndarray, a_star_min: int) -> np.ndarray:
    """Binary (0/255) mask of pixels whose LAB a* (red-green) channel is >= a_star_min.

    a* is channel index 1 of an 8-bit BGR2LAB image; 128 = neutral, higher = redder. Saturation
    collapses near white but a* stays positive for a faint red tint, so this detects pale arcs over
    the bright fundus that an HSV red band misses."""
    return ((lab_band[:, :, 1] >= a_star_min).astype(np.uint8)) * 255


def red_coverage(lab_band: np.ndarray, cfg: AutoTriggerConfig) -> float:
    """Fraction of an arc band that is red (LAB a* >= cfg.a_star_min) after a MORPH_OPEN despeckle."""
    mask = arc_redness_mask(lab_band, cfg.a_star_min)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.morph_kernel, cfg.morph_kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    area = mask.shape[0] * mask.shape[1]
    return cv2.countNonZero(mask) / area if area else 0.0


def detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState:
    """Classify both arcs as RED / not-red by a* red coverage vs ``cfg.coverage_threshold``."""
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
    lc = red_coverage(roi_from_region(cfg.left_arc).crop(lab), cfg)
    rc = red_coverage(roi_from_region(cfg.right_arc).crop(lab), cfg)
    t = cfg.coverage_threshold
    return AlignmentState(left_red=lc >= t, right_red=rc >= t, left_cov=lc, right_cov=rc)
```
Delete `_band_mask` and the `HsvBand` import. Keep the `cv2`/`numpy`/`dataclass` imports.

- [ ] **Step 6: Rewrite the calibration unit tests for a*** 

In `tests/unit/test_system_camera_calibration.py`:

Update the imports (drop `HsvBand`, `sample_red_band`):
```python
from arm101_hand.config.system_camera_config import RoiBox
from arm101_hand.system_camera.calibration import (
    ArcCase,
    describe_case,
    deskew_crop,
    pick_threshold,
    screen_roi_from_rect,
    sweep_red_detection,
    write_calibration_values,
)
```

Delete these three HSV-specific tests entirely: `test_sample_red_band_brackets_and_wraps`,
`test_pooled_red_anchor_widens_hue_across_cases`, `test_sweep_pooled_hue_classifies_two_different_red_hues`.
(Also delete the now-unused `_fill_arcs` helper.)

Rewrite the writer tests to pass `a_star_min`:
```python
def test_write_calibration_preserves_comments_and_updates(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    write_calibration_values(
        dst,
        screen_roi=RoiBox(x=10, y=8, w=400, h=240, ref_w=640, ref_h=480, angle=-0.9),
        left_arc=RoiBox(x=90, y=130, w=70, h=230, ref_w=640, ref_h=480),
        right_arc=RoiBox(x=480, y=130, w=70, h=230, ref_w=640, ref_h=480),
        a_star_min=140,
        coverage_threshold=0.08,
    )
    text = dst.read_text(encoding="utf-8")
    assert "Automated trigger" in text and dst.with_suffix(".yaml.bak").exists()
    cfg = load_system_camera_config(dst)
    assert cfg.screen_roi.angle == -0.9 and (cfg.screen_roi.ref_w, cfg.screen_roi.ref_h) == (640, 480)
    assert cfg.auto_trigger.coverage_threshold == 0.08
    assert cfg.auto_trigger.a_star_min == 140
    assert (cfg.auto_trigger.left_arc.x, cfg.auto_trigger.right_arc.x) == (90, 480)


def test_write_calibration_rejects_invalid_without_writing(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    before = dst.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        write_calibration_values(
            dst,
            screen_roi=RoiBox(x=0, y=0, w=1, h=1),
            left_arc=RoiBox(x=0, y=0, w=1, h=1),
            right_arc=RoiBox(x=0, y=0, w=1, h=1),
            a_star_min=300,  # out of [128, 255] -> rejected by the schema; nothing written
            coverage_threshold=0.5,
        )
    assert dst.read_text(encoding="utf-8") == before
```

Rewrite the sweep tests to use `a_star_min` (note `_roi_with_arcs` stays — `(0,0,200)` BGR red is high-a*, `(80,160,80)` greenish is low-a*):
```python
def test_sweep_red_detection_separates_red_from_clear():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=640, ref_h=480)
    right = RoiBox(x=480, y=120, w=80, h=240, ref_w=640, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))  # red arcs -> high a*
    clear_frame = _roi_with_arcs(left, right, (80, 160, 80))  # greenish -> low a*
    res = sweep_red_detection([ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear")], left, right)
    assert res.separable is True
    assert res.unsatisfied == []
    assert 0.0 < res.coverage_threshold < 1.0
    assert 128 <= res.a_star_min <= 255
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, a_star_min=res.a_star_min, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red
    assert detect(clear_frame, cfg).both_clear


def test_sweep_red_detection_reports_unsatisfiable_cases():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=640, ref_h=480)
    right = RoiBox(x=480, y=120, w=80, h=240, ref_w=640, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))
    contradictory_clear = _roi_with_arcs(left, right, (0, 0, 200))  # tagged 'clear' but actually red
    res = sweep_red_detection([ArcCase(red_frame, "red"), ArcCase(contradictory_clear, "clear")], left, right)
    assert res.separable is False
    assert res.unsatisfied
    assert isinstance(res.coverage_threshold, float)


def test_sweep_excludes_transitional_red_case():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=640, ref_h=480)
    right = RoiBox(x=480, y=120, w=80, h=240, ref_w=640, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))  # both arcs red
    clear_frame = _roi_with_arcs(left, right, (80, 160, 80))  # both greenish/clear
    transitional = _roi_with_arcs(left, right, (80, 160, 80))  # start greenish...
    transitional[left.y : left.y + left.h, left.x : left.x + left.w] = (0, 0, 200)  # ...left arc red only
    cases = [ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear"), ArcCase(transitional, "red")]
    res = sweep_red_detection(cases, left, right)
    assert res.separable is True
    assert 2 in res.excluded_transitional  # the one-arc-red 'red' frame is excluded from fitting
    assert 2 not in res.unsatisfied
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, a_star_min=res.a_star_min, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red and detect(clear_frame, cfg).both_clear
```

Add one new test asserting a\* recovers a pale arc that an HSV-style high-V/low-S patch represents (a near-white arc with a slight red tint), to lock in the core win:
```python
def test_sweep_detects_pale_washed_red_arc():
    # A washed arc: nearly white (high value) with only a faint red tint -- HSV saturation would be
    # ~0, but LAB a* stays above neutral. Both arcs get the same pale-red so it is a true both-red.
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=640, ref_h=480)
    right = RoiBox(x=480, y=120, w=80, h=240, ref_w=640, ref_h=480)
    pale_red = (235, 235, 255)  # BGR: near white, red channel slightly higher -> a* > 128
    clear_frame = _roi_with_arcs(left, right, (245, 245, 245))  # neutral near-white -> a* ~128
    red_frame = _roi_with_arcs(left, right, pale_red)
    res = sweep_red_detection([ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear")], left, right)
    assert res.separable is True
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect

    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, a_star_min=res.a_star_min, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red and detect(clear_frame, cfg).both_clear
```

- [ ] **Step 7: Run the calibration tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -q`
Expected: FAIL — `sweep_red_detection` still returns `red_bands`, `write_calibration_values` still expects `red_bands`, imports of `sample_red_band` removed but implementation unchanged.

- [ ] **Step 8: Migrate `calibration.py`**

In `src/arm101_hand/system_camera/calibration.py`:

Delete `_RED_PRIOR`, `_prior_mask`, `_bands_from_points`, `sample_red_band`, `_pooled_red_anchor`, `_hsv_map`. Remove the `HsvBand` import (keep `RoiBox`, `AutoTriggerConfig`, `SystemCameraConfig`). Keep `from .arc_detector import detect`.

Replace `SweepResult` and `sweep_red_detection`:
```python
@dataclass(frozen=True)
class SweepResult:
    """Best-effort tuned detection: the a* cutoff + threshold, whether every FITTED (non-excluded)
    case is satisfied, the fitted cases still misclassified, the per-case (det_left, det_right), and
    the 'red' cases excluded from fitting as transitional (one arc read clear)."""

    a_star_min: int
    coverage_threshold: float
    separable: bool
    unsatisfied: list[int]
    case_detections: list[tuple[bool, bool]]
    excluded_transitional: list[int]


def sweep_red_detection(
    cases: list[ArcCase],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    morph_kernel: int = 3,
    a_star_floors: Sequence[int] = tuple(range(128, 150)),
    floor: float = 0.005,
    blank_eps: float = 0.008,
) -> SweepResult:
    """Tune the LAB a* cutoff + coverage threshold against all labelled cases.

    Grid over candidate ``a_star_min`` cutoffs; for each, compute per-arc a* coverage for every case
    via the runtime ``detect`` (same mask + morph). A 'red' case whose weaker arc is < ``blank_eps``
    is TRANSITIONAL (cannot be a true both-red frame -- the arcs share a colour) and is excluded from
    the fit pool. Pool clean red-case coverages vs clear-case coverages -> threshold =
    ``pick_threshold(..., floor=floor)``. Score by (#clean+clear correct, -#transitional, margin) and
    return the best as a SweepResult.

    Raises ValueError if there is no 'red' case or no cutoff recovers a single clean both-red case."""
    red_cases = [c for c in cases if c.expected == "red"]
    if not red_cases:
        raise ValueError("sweep needs at least one 'red' case")

    def _transitional(c: ArcCase, lc: float, rc: float) -> bool:
        return c.expected == "red" and min(lc, rc) < blank_eps

    best_key: tuple[int, int, float] = (-1, -(10**9), -1.0)
    best: tuple[int, float, list[tuple[float, float]]] | None = None
    for a_min in a_star_floors:
        trial = AutoTriggerConfig(
            left_arc=left_arc, right_arc=right_arc, a_star_min=a_min, morph_kernel=morph_kernel
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
        if not red_pool:  # this cutoff recovered no clean both-red case -> useless
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
            best = (a_min, t, covs)

    if best is None:
        raise ValueError("sweep recovered no clean both-red case; show clearer RED arcs and re-capture")
    a_min, t, covs = best
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
        a_star_min=a_min,
        coverage_threshold=round(float(t), 4),
        separable=not unsatisfied,
        unsatisfied=unsatisfied,
        case_detections=dets,
        excluded_transitional=excluded,
    )
```

Replace `write_calibration_values` (param + writer; **must delete any stale `red_bands` key** so the dumped data validates under `extra="forbid"`):
```python
def write_calibration_values(
    config_path: Path,
    *,
    screen_roi: RoiBox,
    left_arc: RoiBox,
    right_arc: RoiBox,
    a_star_min: int,
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger arc regions / a* cutoff / threshold. Validates via pydantic and writes a ``.bak``
    BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at.pop("red_bands", None)  # drop the retired HSV bands so extra="forbid" passes
    at["a_star_min"] = int(a_star_min)
    at["coverage_threshold"] = float(coverage_threshold)

    buf = io.StringIO()
    yaml_rt.dump(data, buf)
    SystemCameraConfig.model_validate(yaml.safe_load(buf.getvalue()))

    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with config_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
```
Keep `_flow_map` and `_roibox_map` as-is.

- [ ] **Step 9: Migrate the data yaml**

In `src/arm101_hand/data/system_camera_config.yaml`, in the `auto_trigger:` block, replace the two `red_bands` lines + the `coverage_threshold` line:
```yaml
  red_bands:                       # red wraps hue 0/180 -> two bands OR'd (pale arcs -> low S/V floors)
  - {h_lo: 0, s_lo: 15, v_lo: 30, h_hi: 11, s_hi: 255, v_hi: 255}
  - {h_lo: 159, s_lo: 15, v_lo: 30, h_hi: 180, s_hi: 255, v_hi: 255}
  coverage_threshold: 0.0055       # an arc counts as RED at >= this fraction
```
with:
```yaml
  a_star_min: 134                  # LAB a* cutoff: a pixel counts as red at a* >= this (128 = neutral)
  coverage_threshold: 0.011        # an arc counts as RED at >= this fraction of pixels above a_star_min
```
If the file has a top-level `schema_version:` key, set it to `8`. (`screen_roi`, `left_arc`, `right_arc` are geometry — leave them; they stay valid for a*.)

- [ ] **Step 10: Run both src test files to verify they pass**

Run: `uv run pytest tests/unit/test_system_camera_config.py tests/unit/test_system_camera_calibration.py -q`
Expected: PASS (all). The script test files are NOT run here (migrated in Task 2).

- [ ] **Step 11: Lint + type-check the migrated source**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`
Expected: clean (mypy on `src` only; scripts/ are not in `src`).

- [ ] **Step 12: Commit**

```bash
git add src/arm101_hand/config/system_camera_config.py src/arm101_hand/system_camera/arc_detector.py src/arm101_hand/system_camera/calibration.py src/arm101_hand/data/system_camera_config.yaml tests/unit/test_system_camera_config.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(system_camera): LAB a* arc-redness metric (detector + sweep + config)

Replace HSV red-band coverage with LAB a* coverage in detect()/red_coverage
and the calibration sweep; a_star_min replaces red_bands in AutoTriggerConfig,
SweepResult, write_calibration_values, and the data yaml. schema_version 7->8.
a* (red-green) stays positive for pale arcs over the bright fundus where HSV
saturation collapses. Scripts migrate in the next commit.

Claude-Session: https://claude.ai/code/session_01H4cFt79uTQveEVF3riAdBY"
```

---

### Task 2: scripts — calibrate_view + arc_sweep_replay + arc_debug (+ their tests) + full-suite green + real-frame validation

Migrate the three scripts and their two test files to the a* API, restore full-suite green, then validate the metric against the real 45 saved frames.

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py`
- Modify: `scripts/diagnostics/system_camera/arc_sweep_replay.py`
- Modify: `scripts/diagnostics/system_camera/usb_camera_arc_debug.py`
- Test: `tests/unit/test_arc_sweep_replay.py`
- Test: `tests/unit/test_arc_debug_sidecar.py`

**Interfaces:**
- Consumes (from Task 1): `arc_redness_mask`, `AutoTriggerConfig.a_star_min`, `SweepResult.a_star_min`, `write_calibration_values(..., a_star_min=...)`.
- Produces: `arc_sweep_replay.format_sweep_report(result, cases) -> str`, `arc_sweep_replay.build_write_kwargs(result, cases, current_screen_roi) -> dict` (now keyed `a_star_min`), `usb_camera_arc_debug.build_arc_case_sidecar(...) -> dict` (now keyed `a_star_min`) — all unchanged signatures.

- [ ] **Step 1: Update the script test files to the new API (failing)**

In `tests/unit/test_arc_sweep_replay.py`: drop `HsvBand` from the import (`from arm101_hand.config.system_camera_config import RoiBox`). Replace the two `SweepResult(...)` constructions and the `build_write_kwargs` assertion:
```python
def test_format_sweep_report_marks_excluded_wrong_and_ok():
    mod = _load_module()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cases = [
        mod.LabeledCase("c0", ArcCase(frame=frame, expected="red"), _LEFT, _RIGHT),
        mod.LabeledCase("c1", ArcCase(frame=frame, expected="red"), _LEFT, _RIGHT),
        mod.LabeledCase("c2", ArcCase(frame=frame, expected="clear"), _LEFT, _RIGHT),
    ]
    res = SweepResult(
        a_star_min=134,
        coverage_threshold=0.0096,
        separable=False,
        unsatisfied=[1],
        case_detections=[(True, True), (False, False), (False, False)],
        excluded_transitional=[0],
    )
    report = mod.format_sweep_report(res, cases)
    assert "1/2 fitted cases correct (1 excluded as transitional)" in report
    assert "[ 0] EXCLUDED" in report
    assert "[ 1] WRONG" in report
    assert "[ 2] ok" in report


def test_build_write_kwargs_preserves_screen_roi_and_uses_case_arcs():
    mod = _load_module()
    screen_roi = RoiBox(x=200, y=220, w=410, h=246, ref_w=640, ref_h=480, angle=-1.0)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cases = [mod.LabeledCase("c0", ArcCase(frame=frame, expected="red"), _LEFT, _RIGHT)]
    res = SweepResult(
        a_star_min=134,
        coverage_threshold=0.0096,
        separable=True,
        unsatisfied=[],
        case_detections=[(True, True)],
        excluded_transitional=[],
    )
    kw = mod.build_write_kwargs(res, cases, screen_roi)
    assert kw["screen_roi"] is screen_roi
    assert kw["left_arc"] == _LEFT and kw["right_arc"] == _RIGHT
    assert kw["a_star_min"] == res.a_star_min
    assert kw["coverage_threshold"] == 0.0096
```
The round-trip test gains an `a_star_min` assertion:
```python
    reloaded = load_system_camera_config(dst)
    assert reloaded.auto_trigger.coverage_threshold == res.coverage_threshold
    assert reloaded.auto_trigger.a_star_min == res.a_star_min
    assert (reloaded.auto_trigger.left_arc.x, reloaded.auto_trigger.right_arc.x) == (_LEFT.x, _RIGHT.x)
    assert reloaded.screen_roi == current.screen_roi
    assert dst.with_suffix(".yaml.bak").exists()
```

In `tests/unit/test_arc_debug_sidecar.py`, `test_build_arc_case_sidecar_shape`: replace the `red_bands` assertion line
```python
    assert out["red_bands"] == [b.model_dump() for b in cfg.red_bands]
```
with
```python
    assert out["a_star_min"] == cfg.a_star_min
```

- [ ] **Step 2: Run the script tests to verify they fail**

Run: `uv run pytest tests/unit/test_arc_sweep_replay.py tests/unit/test_arc_debug_sidecar.py -q`
Expected: FAIL — scripts still import `HsvBand` / emit `red_bands`; `SweepResult` no longer accepts `red_bands` so the old test bodies error.

- [ ] **Step 3: Migrate `usb_camera_arc_debug.py`**

In `build_arc_case_sidecar`, replace the `red_bands` line:
```python
        "left_arc": cfg.left_arc.model_dump(),
        "right_arc": cfg.right_arc.model_dump(),
        "a_star_min": cfg.a_star_min,
        "morph_kernel": cfg.morph_kernel,
```
(No other change — `_annotate`'s HUD already uses coverage, and there is no `HsvBand` import.)

- [ ] **Step 4: Migrate `arc_sweep_replay.py`**

Drop `HsvBand` from the config import (`from arm101_hand.config.system_camera_config import RoiBox`).

In `format_sweep_report`, replace the red-band lines:
```python
    lines = [
        f"coverage_threshold = {result.coverage_threshold}",
        f"separable (over fitted set) = {result.separable}",
        f"a*_min = {result.a_star_min}",
    ]
    lines += [
        "",
        f"{ok}/{n_fit} fitted cases correct ({n_excluded} excluded as transitional)",
        "",
        "per-case:",
    ]
```
(Delete the old `lines += [... "red_bands:" ...]` block and the `for b in result.red_bands` line.)

Replace `_bands_str` with a metric string and update its two call sites:
```python
def _metric_str(a_star_min: int, coverage_threshold: float) -> str:
    """Compact one-line render of the a* cutoff + threshold for the --write before/after diff."""
    return f"a*_min={a_star_min} thr={coverage_threshold}"
```
In `build_write_kwargs`, replace `"red_bands": result.red_bands` with `"a_star_min": result.a_star_min`.

In `main()`'s `--write` diff block, replace the `red_bands` lines:
```python
    print(f"\nWILL WRITE -> {config_path}")
    print(f"  coverage_threshold: {at.coverage_threshold}  ->  {kwargs['coverage_threshold']}")
    print(f"  a*_min:  {at.a_star_min}  ->  {kwargs['a_star_min']}")
    print(
        f"  left_arc:  {at.left_arc.x},{at.left_arc.y},{at.left_arc.w},{at.left_arc.h}  ->  "
        f"{new_left.x},{new_left.y},{new_left.w},{new_left.h}"
    )
```
(The `_metric_str` helper is available if a combined line is preferred; the explicit lines above are sufficient. Remove the two `_bands_str(...)` lines.)

- [ ] **Step 5: Migrate `calibrate_view.py`**

Update the arc_detector import (drop `_band_mask`, add `arc_redness_mask`):
```python
from arm101_hand.system_camera.arc_detector import (  # noqa: E402
    AlignmentState,
    arc_redness_mask,
    detect,
)
```
In `_sweep` (inside `main`), change the `AutoTriggerConfig` construction:
```python
                return AutoTriggerConfig(
                    left_arc=left_arc,
                    right_arc=right_arc,
                    a_star_min=result.a_star_min,
                    coverage_threshold=result.coverage_threshold,
                )
```
In the `write_calibration_values(...)` call (action == "y" branch), change `red_bands=trial.red_bands` to `a_star_min=trial.a_star_min`.

In `_save_calib_case`, replace the `red_bands`/`morph_kernel` sidecar lines:
```python
        "left_arc": cfg.left_arc.model_dump(),
        "right_arc": cfg.right_arc.model_dump(),
        "a_star_min": cfg.a_star_min,
        "morph_kernel": cfg.morph_kernel,
        "camera": {"index": camera_index, "backend": backend},
```
In `_confirm`, replace the HSV tint with the a* mask (per panel):
```python
            for label, frame in (("RED frame", red_ref), ("CLEAR frame", clear_ref)):
                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                panel = _tint_mask(frame, arc_redness_mask(lab, cfg.a_star_min), (0, 0, 255))
```
(Delete the `hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)` line it replaces.)

- [ ] **Step 6: Run the full host test suite to verify green**

Run: `uv run pytest -m 'not hardware' -q`
Expected: PASS (all). This is the first point the whole suite is green again.

- [ ] **Step 7: Lint + type-check everything**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy src`
Expected: clean.

- [ ] **Step 8: Real-frame validation — re-sweep the 45 saved frames**

Run: `uv run python scripts/diagnostics/system_camera/arc_sweep_replay.py`
Expected output (acceptance gate):
- `Loaded 45 labelled case(s) ... (34 red, 11 clear)`
- `separable (over fitted set) = True`
- `a*_min = <128..149>`
- `45/45 fitted cases correct (0 excluded as transitional)`
- every per-case line tagged `ok` (no `EXCLUDED`, no `WRONG`).

If any frame is still EXCLUDED/WRONG: STOP. Do not patch the threshold blindly — re-open the
investigation (the a* cutoff grid or `blank_eps` may need widening; record the offending frame's
`(left_cov, right_cov)` first). This is the whole point of the change.

- [ ] **Step 9: Commit**

```bash
git add scripts/calibration/system_camera/calibrate_view.py scripts/diagnostics/system_camera/arc_sweep_replay.py scripts/diagnostics/system_camera/usb_camera_arc_debug.py tests/unit/test_arc_sweep_replay.py tests/unit/test_arc_debug_sidecar.py
git commit -m "feat(system_camera): migrate calibrate_view + replay + arc_debug to a*

Switch the calibration confirm tint, sweep wiring, sidecars, and the offline
replay report/--write diff from HSV red_bands to the a_star_min cutoff. Full
host suite green; offline replay over the 45 saved frames now classifies all
45 correctly with 0 exclusions (was 19 excluded under HSV).

Claude-Session: https://claude.ai/code/session_01H4cFt79uTQveEVF3riAdBY"
```

---

## Notes for the executor
- **Hardware step is out of scope.** Final on-device confidence (and the real-fundus false-positive
  check from the spec's risk #2) requires running `calibrate_view.py` / `usb_camera_arc_debug.py` on
  the bench — the operator does that separately. This plan's acceptance gate is the offline replay.
- **The working-tree yaml** already carries the operator's geometry (screen_roi + arc boxes) from an
  earlier HSV calibration; Task 1 Step 9 only swaps the `red_bands` block for `a_star_min`, leaving
  geometry intact.
- **Docs:** CLAUDE.md / README still describe HSV red bands. Refreshing them is a separate
  `/doc_update` follow-up (per the spec's out-of-scope), not part of this plan.

## Self-Review
- **Spec coverage:** detector change (Task 1 Step 5) ✓; config `a_star_min` + schema 8 (Step 3) ✓;
  sweep rework + delete HSV helpers (Step 8) ✓; `write_calibration_values` (Step 8) ✓; yaml (Step 9)
  ✓; calibrate_view/replay/arc_debug migration (Task 2 Steps 3-5) ✓; tests (both tasks) ✓; real-frame
  validation 0-excluded gate (Task 2 Step 8) ✓; risks/out-of-scope captured in Notes ✓. Part 4
  (full-corpus report in calibrate_view) is correctly absent — deferred per spec.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output.
- **Type consistency:** `a_star_min: int` and `coverage_threshold: float` are used identically across
  `AutoTriggerConfig`, `SweepResult`, `sweep_red_detection`, `write_calibration_values`,
  `build_write_kwargs`, and all sidecars; `arc_redness_mask(lab_band, a_star_min)` matches its
  call sites in `red_coverage` and `calibrate_view._confirm`.
