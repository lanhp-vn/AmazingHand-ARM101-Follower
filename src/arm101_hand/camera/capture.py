"""Capture-pipeline I/O: wait for the new file to land, then save it + a sidecar.

The new-file wait returns as soon as a new file is seen at a nonzero size (``stable_polls=1``,
the default); a higher ``stable_polls`` adds a write-race guard by requiring the size to be
unchanged across that many successful reads. It tolerates the Aurora's intermittent
``GET_FILELIST`` CODE_FAIL: a failed poll is reported (not raised) and polling continues, so a
later successful poll within the window still catches the capture. ``snapshot_filenames`` and
``pull_file`` add the same retry resilience to the baseline read and the download.
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


def pull_file(client, path: str, *, retries: int = 4, retry_wait_s: float = 0.5) -> tuple[FileInfo, bytes]:
    """GET_FILE with retries past the Aurora's intermittent CODE_FAIL.

    Returns ``(FileInfo, data)``; raises the last error if every attempt fails. Mirrors
    ``snapshot_filenames`` so the download rides through the same transient camera errors
    that the filelist polling does.
    """
    attempts = max(1, retries)
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            return client.get_file(path)
        except (CameraError, OSError) as e:
            last_err = e
            if attempt + 1 < attempts:
                time.sleep(retry_wait_s)
    raise last_err if last_err is not None else RuntimeError("get_file failed")


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
    (``note`` is "" on success, else the error text). Returns the new files, or ``[]`` on timeout.

    ``stable_polls`` is the number of consecutive *successful* polls that must see the same
    nonzero-size new set before returning. ``stable_polls=1`` returns on the FIRST poll that
    sees a real new file (download immediately); ``>=2`` adds the write-race guard (wait for
    the size to settle). Failed polls never reset the counter, so interleaved camera errors
    do not prevent successful sightings from lining up.
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
        if ok:
            sizes = {name: f.filesize for name, f in new.items()}
            stable = bool(new) and all(s > 0 for s in sizes.values())
            if stable and sizes == prev:
                rounds += 1
            else:
                rounds = 1 if stable else 0
            prev = sizes
            if rounds >= stable_polls:
                return list(new.values())
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
