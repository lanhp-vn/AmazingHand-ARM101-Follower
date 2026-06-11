"""Capture-pipeline I/O: wait for the new file to land, then save it + a sidecar.

The new-file wait guards the write race (a file can appear in the filelist before its
bytes are fully flushed) by requiring its size to be nonzero and unchanged across
``stable_polls`` consecutive filelist reads. It also tolerates the Aurora's intermittent
``GET_FILELIST`` CODE_FAIL: a failed poll is reported (not raised) and polling continues,
so a later successful poll within the window still catches the capture.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from arm101_hand.camera.client import CameraError
from arm101_hand.camera.protocol import FileInfo, capture_filename, diff_new_files, sidecar_dict


def snapshot_filenames(client, dcim_root: str, *, retries: int = 5, retry_wait_s: float = 0.5) -> set[str]:
    """Baseline set of filenames under ``dcim_root`` for the pre-capture diff.

    Retries to ride out the Aurora's intermittent ``GET_FILELIST`` CODE_FAIL so a single
    transient error does not waste a trigger. Raises the last error if every attempt fails.
    """
    attempts = max(1, retries)
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            return {f.filename for f in client.get_filelist(dcim_root)}
        except (CameraError, OSError) as e:
            last_err = e
            if attempt + 1 < attempts:
                time.sleep(retry_wait_s)
    raise last_err if last_err is not None else RuntimeError("get_filelist failed")


def wait_for_new_files(
    client,
    before: set[str],
    *,
    dcim_root: str,
    timeout_s: float,
    poll_s: float,
    stable_polls: int,
    on_poll: Callable[[float, int, str], None] | None = None,
) -> list[FileInfo]:
    """Poll until new (non-dir) files appear with a size stable across ``stable_polls``
    consecutive reads, or ``timeout_s`` elapses.

    A ``GET_FILELIST`` error on any poll is reported via ``on_poll`` (as ``note`` text) and
    treated as "nothing new yet" -- polling keeps going so an intermittent camera error never
    aborts the whole wait. ``on_poll(elapsed_s, n_new, note)`` is called once per poll
    (``note`` is "" on success, else the error text). Returns the stabilized files, or ``[]``
    on timeout.
    """
    prev: dict[str, int] = {}
    rounds = 0
    start = time.monotonic()
    deadline = start + timeout_s
    while True:
        try:
            listing = client.get_filelist(dcim_root)
            note = ""
            ok = True
        except (CameraError, OSError) as e:
            listing = []
            note = f"filelist error: {e}"
            ok = False
        new = {f.filename: f for f in diff_new_files(before, listing)}
        if on_poll is not None:
            on_poll(time.monotonic() - start, len(new), note)
        # A failed poll carries no information about new files -- skip the stability update so
        # interleaved errors can't keep resetting it. Otherwise two consecutive *successful*
        # sightings never line up and a clearly-present capture is never returned.
        if ok:
            sizes = {name: f.filesize for name, f in new.items()}
            if new and sizes == prev and all(s > 0 for s in sizes.values()):
                rounds += 1
                if rounds >= stable_polls:
                    return list(new.values())
            else:
                rounds = 1 if (new and all(s > 0 for s in sizes.values())) else 0
            prev = sizes
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll_s)


def save_capture(
    info: FileInfo,
    data: bytes,
    dest_dir: Path,
    *,
    captured_at: datetime,
    trigger_no: int,
    camera_serial: str,
    camera_sw: str,
    camera_wifi: str,
) -> Path:
    """Write ``data`` to ``dest_dir/<timestamped-name>`` plus a ``.json`` sidecar; return the image path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / capture_filename(info, captured_at)
    out.write_bytes(data)
    meta = sidecar_dict(
        info,
        captured_at=captured_at,
        trigger_no=trigger_no,
        camera_serial=camera_serial,
        camera_sw=camera_sw,
        camera_wifi=camera_wifi,
    )
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out
