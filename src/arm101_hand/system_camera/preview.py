"""Live USB observation-camera preview window + record toggle (device layer).

The arm-mounted host webcam that films the Optomed Aurora's screen. Used by
``scripts/demos/grab_trigger_capture.py`` and the ``scripts/diagnostics/usb_camera_probe.py``
smoke test. NOT the Aurora fundus camera (that is ``arm101_hand.fundus_camera``).

Needs the full ``opencv-python`` wheel for HighGUI (``cv2.imshow``); the project drops
lerobot's ``opencv-python-headless`` pin in ``pyproject.toml`` (``[tool.uv] override-dependencies``)
so the full build is the sole ``cv2``. Capture + ``VideoWriter`` work headless; only the window
needs GUI.

Threading contract (the part that matters):
  * The capture thread is the ONLY place the cv2 window / capture / writer are touched
    (created, pumped, destroyed). It is a daemon thread, so a caller's blocking
    ``msvcrt.getwch`` keyboard loop on the main thread never freezes the window.
    Because HighGUI is single-threaded, this same thread also hosts the optional
    "last capture" still window (see :meth:`show_still`) -- callers never touch cv2.
  * Callers interact ONLY through :meth:`start`, :meth:`toggle_record`, :meth:`show_still`,
    :meth:`stop`.
  * Recording is requested via a thread-safe Event; the capture thread owns the writer
    and reacts to it -- the ``VideoWriter`` is created lazily from a real captured frame
    (size + fps), and the CLEAN frame is written BEFORE any on-screen overlay.
  * ALL teardown (writer + capture + ``destroyAllWindows``) runs on the capture thread,
    triggered by a stop Event that :meth:`stop` sets then joins.

Best-effort by design: if the camera index will not open, :meth:`start` returns ``False``
and the caller continues without a preview (the camera/grab workflow is the priority).
"""

from __future__ import annotations

import datetime as _dt
import math
import threading
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

_FPS_FALLBACK = 20.0
Backend = Literal["auto", "dshow"]


class WebcamPreview:
    """Live USB-camera preview in a cv2 window on a daemon thread, with a record toggle."""

    def __init__(
        self,
        index: int,
        window_title: str,
        record_dir: Path,
        fps: float | None = None,
        backend: Backend = "auto",
    ) -> None:
        self._index = index
        self._title = window_title
        self._record_dir = record_dir
        self._fps_override = fps
        self._backend: Backend = backend

        self._stop = threading.Event()
        self._record = threading.Event()
        self._lock = threading.Lock()
        self._record_path: Path | None = None

        # "last capture" still window -- callers hand encoded bytes via show_still(); the
        # capture thread decodes the latest set and re-shows it every loop (see _run).
        self._still_title = f"{window_title} -- last capture"
        self._still_lock = threading.Lock()
        self._still_pending: bytes | None = None

        self._ready = threading.Event()  # set once the thread has tried to open the camera
        self.ok = False  # True iff the camera opened
        self.width = 0
        self.height = 0
        self.src_fps = 0.0

        self._thread = threading.Thread(target=self._run, name="usb-preview", daemon=True)

    # ---- caller (main-thread) API ---------------------------------------
    def start(self) -> bool:
        """Spawn the capture thread; block until the camera is open (or failed). Returns ``ok``."""
        self._thread.start()
        self._ready.wait(timeout=10.0)
        return self.ok

    def toggle_record(self) -> Path | None:
        """Flip recording on/off. Returns the new clip path when starting, ``None`` when stopping."""
        if self._record.is_set():
            self._record.clear()
            return None
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._record_dir / f"usb_cam_{ts}.avi"
        self._record_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._record_path = path
        self._record.set()
        return path

    @property
    def recording(self) -> bool:
        return self._record.is_set()

    def show_still(self, image_bytes: bytes) -> None:
        """Hand an encoded image (JPEG/PNG bytes) to the capture thread to display in a second
        window titled ``"<window_title> -- last capture"``. It stays up, unchanged, until the
        next ``show_still`` replaces it -- the capture thread (the sole cv2 owner) decodes it once
        and re-shows it every loop, so it never goes stale or first-paints gray. Thread-safe and
        non-blocking; a no-op if the capture thread never opened the camera."""
        with self._still_lock:
            self._still_pending = image_bytes

    def stop(self) -> None:
        """Signal teardown and wait for the thread to release the window + cv2 on its own side."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    # ---- capture-thread internals ---------------------------------------
    def _open_capture(self) -> cv2.VideoCapture:
        if self._backend == "dshow":
            # CAP_DSHOW opens fast when it works, but fails on some cameras ("can't be used to
            # capture by index") -- fall back to the platform default (MSMF on Windows).
            cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self._index)
            return cap
        return cv2.VideoCapture(self._index)  # auto: platform default backend

    def _writer_fps(self, cap: cv2.VideoCapture) -> float:
        fps = self._fps_override if self._fps_override else cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 1 or math.isnan(fps):
            return _FPS_FALLBACK
        return float(fps)

    def _run(self) -> None:
        cap = self._open_capture()
        if not cap.isOpened():
            self._ready.set()  # ok stays False -> start() returns False
            return
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.src_fps = float(cap.get(cv2.CAP_PROP_FPS))
        self.ok = True
        cv2.namedWindow(self._title, cv2.WINDOW_NORMAL)
        self._ready.set()  # camera + window up -> unblock start()

        writer: cv2.VideoWriter | None = None
        recording = False
        still_frame = None  # decoded latest capture; re-shown every loop once set
        still_shown = False
        try:
            while not self._stop.is_set():
                # A new capture handed via show_still()? Decode once (cheap, only on change),
                # then re-imshow every loop below so the window never goes gray or stale.
                with self._still_lock:
                    pending = self._still_pending
                    self._still_pending = None
                if pending is not None:
                    decoded = cv2.imdecode(np.frombuffer(pending, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if decoded is not None:
                        still_frame = decoded
                        if not still_shown:
                            cv2.namedWindow(self._still_title, cv2.WINDOW_NORMAL)
                            cv2.resizeWindow(self._still_title, 720, 720)
                            still_shown = True
                if still_frame is not None:
                    cv2.imshow(self._still_title, still_frame)

                ok, frame = cap.read()
                if not ok or frame is None:
                    cv2.waitKey(30)  # transient hiccup -- keep the windows pumping, retry
                    continue

                want = self._record.is_set()
                if want and not recording:
                    writer = self._open_writer(cap, frame)
                    recording = writer is not None
                elif not want and recording:
                    if writer is not None:
                        writer.release()
                    writer = None
                    recording = False

                if recording and writer is not None:
                    writer.write(frame)  # write the CLEAN frame BEFORE the on-screen overlay

                if recording:  # putText mutates in place -> display only, after the write above
                    cv2.putText(
                        frame, "REC", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2, cv2.LINE_AA
                    )
                cv2.imshow(self._title, frame)
                cv2.waitKey(1)  # pump GUI events; required for the window to stay responsive
        finally:
            if writer is not None:
                writer.release()
            cap.release()
            cv2.destroyAllWindows()

    def _open_writer(self, cap: cv2.VideoCapture, frame) -> cv2.VideoWriter | None:
        h, w = frame.shape[:2]
        fourcc = cv2.VideoWriter.fourcc(*"MJPG")  # MJPG/.avi: most reliable on Windows
        with self._lock:
            path = self._record_path
        writer = cv2.VideoWriter(str(path), fourcc, self._writer_fps(cap), (w, h))
        if not writer.isOpened():
            print(f"  WARNING: could not open VideoWriter for {path} -- not recording.")
            writer.release()
            self._record.clear()
            return None
        return writer
