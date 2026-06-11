"""Optomed Aurora / Pictor Prestige camera device layer (read-only) + pure protocol."""

from .capture import pull_file, save_capture, snapshot_filenames, wait_for_new_files
from .client import CameraError, PictorClient, recv_exact
from .protocol import (
    CameraInfo,
    CameraStatus,
    FileInfo,
    MessageFail,
    capture_filename,
    classify_capture,
    decode_fat32_datetime,
    diff_new_files,
    sidecar_dict,
)

__all__ = [
    "CameraError",
    "CameraInfo",
    "CameraStatus",
    "FileInfo",
    "MessageFail",
    "PictorClient",
    "capture_filename",
    "classify_capture",
    "decode_fat32_datetime",
    "diff_new_files",
    "pull_file",
    "recv_exact",
    "save_capture",
    "sidecar_dict",
    "snapshot_filenames",
    "wait_for_new_files",
]
