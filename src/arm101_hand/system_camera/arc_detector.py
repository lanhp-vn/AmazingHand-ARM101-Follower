"""Red/green alignment-arc detection for the arm-mounted USB observation camera (device layer).

The Optomed Aurora shows two arcs (left + right of its circular live view): RED when misaligned,
GREEN when at the correct working distance. We classify each arc by HSV colour coverage inside a
fixed sub-region of the ROI -- cheap, robust per-region masking (a deep-dive into the vendored
OpenCV refs confirmed shape detection like HoughCircles/fitEllipse is overkill for fixed-geometry
partial arcs). Pure functions (no cv2 window); the auto-trigger demo feeds frames from the preview.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import ArcRegion, AutoTriggerConfig, HsvBand

from .roi import Roi


@dataclass(frozen=True)
class AlignmentState:
    """Per-arc classification + the overall ready flag. Coverage fractions are for the HUD/debug."""

    left: str  # "RED" | "GREEN" | "NONE"
    right: str
    ready: bool
    left_green: float
    left_red: float
    right_green: float
    right_red: float


def _region_to_roi(region: ArcRegion) -> Roi:
    return Roi(x=region.x, y=region.y, w=region.w, h=region.h, ref_w=region.ref_w, ref_h=region.ref_h)


def _band_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR together the inRange masks for every HSV band (red needs two for the 0/180 wrap)."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    return mask


def _coverage(hsv_band: np.ndarray, bands: list[HsvBand], kernel: int) -> float:
    """Fraction of the band's pixels matching any of ``bands`` after a MORPH_OPEN despeckle."""
    mask = _band_mask(hsv_band, bands)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    area = mask.shape[0] * mask.shape[1]
    return cv2.countNonZero(mask) / area if area else 0.0


def classify_band(hsv_band: np.ndarray, cfg: AutoTriggerConfig) -> tuple[str, float, float]:
    """Classify one arc band as RED/GREEN/NONE; returns (label, green_fraction, red_fraction)."""
    green = _coverage(hsv_band, cfg.green_bands, cfg.morph_kernel)
    red = _coverage(hsv_band, cfg.red_bands, cfg.morph_kernel)
    if green >= cfg.coverage_threshold and green >= red:
        return "GREEN", green, red
    if red >= cfg.coverage_threshold:
        return "RED", green, red
    return "NONE", green, red


def detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState:
    """Classify both arcs in an ROI frame; ready when EITHER arc is green (optionally + no red)."""
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    left, lg, lr = classify_band(_region_to_roi(cfg.left_arc).crop(hsv), cfg)
    right, rg, rr = classify_band(_region_to_roi(cfg.right_arc).crop(hsv), cfg)
    green_present = left == "GREEN" or right == "GREEN"
    red_present = left == "RED" or right == "RED"
    ready = green_present and (not cfg.require_no_red or not red_present)
    return AlignmentState(left, right, ready, lg, lr, rg, rr)
