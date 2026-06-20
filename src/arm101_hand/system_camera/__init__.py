"""Arm-mounted USB observation camera device layer.

The host webcam mounted on the arm, pointed at the Optomed Aurora's screen -- a live cv2
preview window with a record toggle. Distinct from the Aurora *fundus* camera (patient
retinal images) in ``arm101_hand.fundus_camera``.
"""

from .arc_detector import AlignmentState, classify_band, detect
from .focus import best_sample, focus_reached_baseline, focus_steps, sharpness
from .preview import (
    WebcamPreview,
    grab_full_res_frame,
    imshow_fit,
    open_capture,
    read_settled_frame,
)
from .roi import AURORA_SCREEN_ROI, Roi

__all__ = [
    "AURORA_SCREEN_ROI",
    "AlignmentState",
    "Roi",
    "WebcamPreview",
    "best_sample",
    "classify_band",
    "detect",
    "focus_reached_baseline",
    "focus_steps",
    "grab_full_res_frame",
    "imshow_fit",
    "open_capture",
    "read_settled_frame",
    "sharpness",
]
