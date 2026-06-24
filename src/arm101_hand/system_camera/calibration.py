"""Pure detection helpers for the system-camera view calibration (device layer).

Normalises the chosen Aurora screen ROI (deskewed to an upright 5:3 crop) and samples the red
HSV band(s) from the sample frames. Pure numpy + cv2 imgproc -- no HighGUI window; the one I/O
function is ``write_calibration_values`` (ruamel round-trip). Synthetic-numpy unit-testable,
mirroring ``arc_detector.py``. The interactive capture/confirm shell lives in
``scripts/calibration/system_camera/calibrate_view.py``.

The ROI is chosen interactively (manual drag + arrow-key deskew) in the calibrate_view shell; this
module only normalises the chosen rect to a 5:3 deskewed RoiBox, samples the red HSV band, runs the
red-detection sweep, and round-trips the config. Plain opencv-python only (no contrib).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from arm101_hand.config.system_camera_config import (
    AutoTriggerConfig,
    HsvBand,
    RoiBox,
    SystemCameraConfig,
)

from .arc_detector import detect  # reuse the EXACT runtime coverage path so calibration matches runtime
from .roi import roi_from_region

_Rect = tuple[tuple[float, float], tuple[float, float], float]


def screen_roi_from_rect(
    rect: _Rect, frame_w: int, frame_h: int, *, ref_w: int = 800, ref_h: int = 480
) -> RoiBox:
    """Normalise a rotated screen rect to 5:3 and store it at the ``ref_w x ref_h`` detection ref
    (so ``Roi.crop`` -> resize lands a 5:3 deskewed image). Carries the deskew ``angle``."""
    (cx, cy), (w, h), angle = rect
    target = 5 / 3
    if w / h < target:  # widen the short side to 5:3 (never shrink -> no content lost)
        w = target * h
    else:
        h = w / target
    sx, sy = ref_w / frame_w, ref_h / frame_h
    bx = int(round((cx - w / 2) * sx))
    by = int(round((cy - h / 2) * sy))
    return RoiBox(
        x=max(0, bx),
        y=max(0, by),
        w=max(1, int(round(w * sx))),
        h=max(1, int(round(h * sy))),
        ref_w=ref_w,
        ref_h=ref_h,
        angle=float(angle),
    )


def deskew_crop(frame: np.ndarray, region: RoiBox, *, out: tuple[int, int] = (800, 480)) -> np.ndarray:
    """Deskewed 5:3 crop of ``frame`` for ``region`` (screen_roi), resized to ``out``."""
    return cv2.resize(roi_from_region(region).crop(frame), out, interpolation=cv2.INTER_AREA)


# Broad colour prior used to FIND candidate red arc pixels before percentile sampling tightens the
# band. Deliberately GENEROUS on saturation/value: the Aurora alignment arcs are PALE (low-S) and
# bright, sitting over a tinted near-white disc -- bench captures measured red arcs at S~50 V~227
# hue~170, so tight S/V floors miss them. Red wraps hue 0/180 -> two bands.
_RED_PRIOR: list[HsvBand] = [
    HsvBand(h_lo=0, s_lo=35, v_lo=50, h_hi=12, s_hi=255, v_hi=255),
    HsvBand(h_lo=158, s_lo=35, v_lo=50, h_hi=180, s_hi=255, v_hi=255),
]


def _prior_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR the inRange masks for every prior band, then MORPH_OPEN(3) to despeckle."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)


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
            HsvBand(
                h_lo=0, s_lo=s_lo, v_lo=v_lo, h_hi=int(np.percentile(near_zero, hi_pct)), s_hi=255, v_hi=255
            )
        )
    if near_180.size:
        bands.append(
            HsvBand(
                h_lo=int(np.percentile(near_180, lo_pct)), s_lo=s_lo, v_lo=v_lo, h_hi=180, s_hi=255, v_hi=255
            )
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


def describe_case(expected: str, det_left: bool, det_right: bool) -> str:
    """Human sentence comparing the on-screen ground truth (`expected` is 'red' or 'clear' and
    applies to BOTH arcs, which are physically the same colour) against the detector's per-arc
    output (`det_left`/`det_right` True == detector said RED). Collapses to 'both arcs ...' when the
    two arcs share a phrase, else 'left arc ...; right arc ...'."""
    exp_red = expected == "red"

    def phrase(detected_red: bool) -> str:
        if exp_red and detected_red:
            return "correctly detected as red"
        if exp_red and not detected_red:
            return "incorrectly detected as not red"
        if not exp_red and not detected_red:
            return "correctly detected as not red"
        return "incorrectly detected as red"

    lp, rp = phrase(det_left), phrase(det_right)
    return f"both arcs {lp}" if lp == rp else f"left arc {lp}; right arc {rp}"


def pick_threshold(
    red_covs: list[float], clear_covs: list[float], *, floor: float = 0.02
) -> tuple[float, bool]:
    """Pick a coverage threshold separating red-case coverages (should be >= t) from clear-case
    coverages (should be < t).

    Separable (max(clear) < min(red)): max-margin midpoint, separable=True. Overlapping: scan
    candidate cuts (midpoints between adjacent pooled coverages, plus the two outer edges) and return
    the one minimising misclassified coverages, tie-broken by the larger distance to the nearest
    coverage (a 1-D two-class separator -- the Otsu/Triangle idea reimplemented for scalar coverages),
    separable=False. Floored to `floor` so a degenerate all-low set never yields t<=0 (which would
    call everything RED). Empty inputs -> (floor, True)."""
    if not red_covs or not clear_covs:
        return floor, True
    lo_red, hi_clear = min(red_covs), max(clear_covs)
    if hi_clear < lo_red:  # cleanly separable -> midpoint, max margin
        return max((lo_red + hi_clear) / 2.0, floor), True
    pooled = sorted(set(red_covs + clear_covs))
    candidates = [(pooled[i] + pooled[i + 1]) / 2.0 for i in range(len(pooled) - 1)]
    candidates += [max(floor, pooled[0] - 1e-3), pooled[-1] + 1e-3]

    def score(t: float) -> tuple[int, float]:
        mis = sum(c >= t for c in clear_covs) + sum(c < t for c in red_covs)
        margin = min(abs(c - t) for c in pooled)
        return (-mis, margin)

    best_t = max(candidates, key=score)
    return max(best_t, floor), False


@dataclass(frozen=True)
class ArcCase:
    """One labelled deskewed-ROI frame. `expected` ('red'|'clear') applies to BOTH arcs (they are
    physically the same colour); per-arc detector disagreement is a detection error."""

    frame: np.ndarray  # deskewed ROI, ref_w x ref_h, BGR
    expected: str


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


def _flow_map(d: dict[str, float]) -> CommentedMap:
    """A ruamel mapping rendered inline ``{a: 1, b: 2}`` to match the file's arc/band style."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map(
        {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h, "angle": r.angle}
    )


def _hsv_map(b: HsvBand) -> CommentedMap:
    return _flow_map(
        {"h_lo": b.h_lo, "s_lo": b.s_lo, "v_lo": b.v_lo, "h_hi": b.h_hi, "s_hi": b.s_hi, "v_hi": b.v_hi}
    )


def write_calibration_values(
    config_path: Path,
    *,
    screen_roi: RoiBox,
    left_arc: RoiBox,
    right_arc: RoiBox,
    red_bands: list[HsvBand],
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger arc regions / red bands / threshold. Validates the result via pydantic and writes a
    ``.bak`` copy BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()  # round-trip mode preserves comments + key order
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
    at["coverage_threshold"] = float(coverage_threshold)

    # Validate the would-be file via pydantic BEFORE writing (plain safe_load of the dumped text).
    buf = io.StringIO()
    yaml_rt.dump(data, buf)
    SystemCameraConfig.model_validate(yaml.safe_load(buf.getvalue()))

    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with config_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
