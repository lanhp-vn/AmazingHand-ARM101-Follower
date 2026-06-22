"""Live USB observation-camera preview window + record toggle (device layer).

The arm-mounted host webcam that films the Optomed Aurora's screen. Used by
``scripts/demos/grab_trigger_capture.py`` and the ``scripts/diagnostics/system_camera/usb_camera_probe.py``
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

from .roi import Roi

_FPS_FALLBACK = 20.0
Backend = Literal["auto", "dshow"]

# A width/height request the driver clamps down to its largest supported mode -- how we ask for
# "the camera's max" without hardcoding a resolution (see open_capture's width/height=None path).
_MAX_DIM_REQUEST = 100_000

# Cap the INITIAL preview-window size so a high-res stream (e.g. 2592x1944) doesn't open a giant
# window. Only the on-open size -- windows are WINDOW_NORMAL (resizable) + letterboxed (imshow_fit),
# so content is never distorted. The ROI consumers (640x480) are under this and unaffected.
_MAX_INITIAL_WIN_W = 960
_MAX_INITIAL_WIN_H = 720


def _apply_format(cap: cv2.VideoCapture, fourcc: str, width: int | None, height: int | None) -> None:
    """Request ``fourcc`` then resolution on an open capture (no-op if it never opened).

    FOURCC is set BEFORE the frame size on purpose: UVC webcams expose their high-resolution modes
    only once the compressed format (MJPG) is selected -- request width/height first and the driver
    keeps you on a low-res uncompressed (YUY2) mode. ``width``/``height`` of ``None`` request the
    driver's maximum via :data:`_MAX_DIM_REQUEST` (it clamps the oversized request down).
    """
    if not cap.isOpened():
        return
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width or _MAX_DIM_REQUEST)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height or _MAX_DIM_REQUEST)


def _apply_focus(cap: cv2.VideoCapture, autofocus: bool, focus: int | None) -> None:
    """Apply the autofocus / manual-focus policy to an open capture (no-op if it never opened).

    The arm + camera + Aurora-screen geometry is fixed, so one manual lens position keeps the screen
    permanently sharp -- no autofocus hunting onto the room behind it, no breathing while recording.
    Manual mode is requested when autofocus is off OR an explicit ``focus`` value is given;
    ``CAP_PROP_AUTOFOCUS`` is set first (0 = off), then the lens position. Setting ``focus`` only
    drives the VCM on the DSHOW backend -- MSMF accepts the calls but ignores them (see the
    ``system_camera_manual_focus`` note); pick ``backend="dshow"`` when locking focus.
    """
    if not cap.isOpened():
        return
    manual = (not autofocus) or (focus is not None)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0.0 if manual else 1.0)
    if focus is not None:
        cap.set(cv2.CAP_PROP_FOCUS, float(focus))


def open_capture(
    index: int,
    backend: Backend = "auto",
    *,
    fourcc: str = "MJPG",
    width: int | None = None,
    height: int | None = None,
    autofocus: bool = True,
    focus: int | None = None,
) -> cv2.VideoCapture:
    """Open a ``cv2.VideoCapture`` for ``index`` using ``backend``, then request format + resolution.

    ``"dshow"`` (CAP_DSHOW) opens fast when it works but fails on some cameras ("can't be used
    to capture by index"); on failure it falls back to the platform default (MSMF on Windows).
    ``"auto"`` uses that platform default directly. Shared by :class:`WebcamPreview` and
    ``scripts/diagnostics/system_camera/usb_camera_capture.py`` so the dshow-quirk handling lives in one place.

    Once open, the capture is set to ``fourcc`` (default ``"MJPG"``) and ``width``/``height``;
    ``None`` for either dimension requests the camera's max (the default = full quality). See
    :func:`_apply_format` for why MJPG + the max-request matter on UVC cams. Finally focus is applied
    (:func:`_apply_focus`): the defaults (``autofocus=True``, ``focus=None``) leave the camera's
    autofocus alone, so callers that don't lock focus are unaffected.
    """
    if backend == "dshow":
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
    else:
        cap = cv2.VideoCapture(index)  # auto: platform default backend
    _apply_format(cap, fourcc, width, height)
    _apply_focus(cap, autofocus, focus)
    return cap


def read_settled_frame(
    cap: cv2.VideoCapture,
    *,
    warmup_frames: int = 5,
    target: tuple[int, int] | None = None,
    max_attempts: int = 40,
) -> np.ndarray | None:
    """Read frames until the sensor settles, returning the freshest good frame.

    Discards the first ``warmup_frames`` *good* frames (so PDAF autofocus + exposure settle after a
    fresh open) then returns the next good frame. A frame counts as good when it decodes and -- if
    ``target`` (a numpy ``(h, w)`` shape) is given -- matches that size. Up to ``max_attempts`` reads
    are made so the dead reads right after (re)opening don't exhaust the budget. Returns the last
    frame of any size if the target count is never reached, or ``None`` if no frame ever arrives.
    """
    good_seen = 0
    fallback: np.ndarray | None = None
    for _ in range(max_attempts):
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        fallback = frame
        if target is None or frame.shape[:2] == target:
            good_seen += 1
            if good_seen > warmup_frames:
                return frame
    return fallback


def grab_full_res_frame(
    cap: cv2.VideoCapture,
    index: int,
    backend: Backend = "auto",
    *,
    fourcc: str = "MJPG",
    still_width: int | None = None,
    still_height: int | None = None,
    stream_width: int | None = None,
    stream_height: int | None = None,
    warmup_frames: int = 5,
    autofocus: bool = True,
    focus: int | None = None,
) -> tuple[np.ndarray | None, cv2.VideoCapture]:
    """Grab one full-resolution still by REOPENING the device, then resume the low-res stream.

    MSMF (the Windows default backend) cannot reliably change resolution on an open capture -- it
    logs ``initStream Failed to select stream 0`` and then hands back a corrupt buffer that crashes
    ``cap.read()``. Opening *fresh* at a resolution works, so this releases ``cap``, opens a new
    capture at the still size (``still_width``x``still_height``; ``None`` = the camera's max) for one
    grab, releases that, then reopens at the stream size (``stream_width``x``stream_height``).

    ``autofocus``/``focus`` are forwarded to BOTH reopens so the locked manual focus holds through
    the full-res grab AND the resumed stream -- otherwise the grab comes back soft / the stream
    reverts to autofocus hunting. Manual focus needs ``backend="dshow"`` (see :func:`_apply_focus`).

    Returns ``(frame_or_None, new_stream_capture)``. The caller MUST adopt the returned capture --
    the original was released. The full-res open/grab/reopen costs ~1-2 s (the preview freezes);
    raise ``warmup_frames`` if the first full-res frame comes back soft (autofocus still settling).
    """
    cap.release()
    target = (still_height, still_width) if still_width and still_height else None  # numpy (h, w)
    grab_cap = open_capture(
        index,
        backend,
        fourcc=fourcc,
        width=still_width,
        height=still_height,
        autofocus=autofocus,
        focus=focus,
    )
    frame = (
        read_settled_frame(grab_cap, warmup_frames=warmup_frames, target=target)
        if grab_cap.isOpened()
        else None
    )
    grab_cap.release()
    stream_cap = open_capture(
        index,
        backend,
        fourcc=fourcc,
        width=stream_width,
        height=stream_height,
        autofocus=autofocus,
        focus=focus,
    )
    return frame, stream_cap


def _fit_within(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Scale ``(w, h)`` down to fit within ``(max_w, max_h)`` preserving aspect; never upscales.

    Returns the size unchanged if it already fits (or is degenerate). Used to bound the *initial*
    window size only -- the window stays resizable.
    """
    if w <= 0 or h <= 0:
        return w, h
    scale = min(max_w / w, max_h / h, 1.0)
    return max(1, round(w * scale)), max(1, round(h * scale))


def _letterbox(frame: np.ndarray, win_w: int, win_h: int) -> np.ndarray:
    """Scale ``frame`` to fit ``win_w x win_h`` preserving aspect ratio, padded with black bars.

    The fitted image is centred on a black canvas of exactly the window size, so when ``imshow``
    maps it 1:1 into the window the picture never stretches -- letterboxed (bars top/bottom) or
    pillarboxed (bars left/right) as the window's shape requires.
    """
    fh, fw = frame.shape[:2]
    scale = min(win_w / fw, win_h / fh)
    new_w, new_h = max(1, round(fw * scale)), max(1, round(fh * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (new_w, new_h), interpolation=interp)
    canvas = np.zeros((win_h, win_w, 3), dtype=frame.dtype)
    x0, y0 = (win_w - new_w) // 2, (win_h - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def imshow_fit(window_title: str, frame: np.ndarray) -> None:
    """``imshow`` ``frame`` letterboxed to the window's current size (aspect ratio preserved).

    Replaces the ``WINDOW_KEEPRATIO`` flag, which letterboxes only on OpenCV's Qt highgui backend
    and is a silent no-op on the Win32 backend the ``opencv-python`` Windows wheel ships -- there
    ``imshow`` stretches the image to fill a resized window. We read the window's image rect and
    pad to it ourselves, so the picture never distorts however the window is dragged. If the rect
    is not ready yet (just-created window), the raw frame is shown and the next frame letterboxes.
    """
    _, _, win_w, win_h = cv2.getWindowImageRect(window_title)
    if win_w <= 0 or win_h <= 0:
        cv2.imshow(window_title, frame)  # window not sized yet -- self-corrects next loop
        return
    cv2.imshow(window_title, _letterbox(frame, win_w, win_h))


class WebcamPreview:
    """Live USB-camera preview in a cv2 window on a daemon thread, with a record toggle.

    An optional fixed ``roi`` crops every frame to a region and upscales it back to the ROI's
    reference size, so the preview window *and* the recording show the same 4:3 zoomed feed --
    used to frame just the Optomed Aurora's screen. ``None`` (default) previews the full frame,
    so callers that want the whole scene (e.g. ``usb_camera_probe.py``) are unaffected.
    """

    def __init__(
        self,
        index: int,
        window_title: str,
        record_dir: Path,
        fps: float | None = None,
        backend: Backend = "auto",
        roi: Roi | None = None,
        fourcc: str = "MJPG",
        width: int | None = None,
        height: int | None = None,
        autofocus: bool = True,
        focus: int | None = None,
    ) -> None:
        self._index = index
        self._title = window_title
        self._record_dir = record_dir
        self._fps_override = fps
        self._backend: Backend = backend
        self._roi = roi
        self._fourcc = fourcc
        self._width = width
        self._height = height
        self._autofocus = autofocus
        self._focus = focus

        self._stop = threading.Event()
        self._record = threading.Event()
        self._lock = threading.Lock()
        self._record_path: Path | None = None

        # "last capture" still window -- callers hand encoded bytes via show_still(); the
        # capture thread decodes the latest set and re-shows it every loop (see _run).
        self._still_title = f"{window_title} -- last capture"
        self._still_lock = threading.Lock()
        self._still_pending: bytes | None = None

        # Latest ROI-cropped frame for callers that detect on it (e.g. the auto-trigger demo),
        # plus an optional status string the capture thread overlays. Both thread-safe.
        self._latest_lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._status_lock = threading.Lock()
        self._status_text = ""
        self._status_color: tuple[int, int, int] = (0, 255, 0)

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

    def latest_frame(self) -> np.ndarray | None:
        """Thread-safe copy of the most recent ROI-cropped frame, or None before the first frame."""
        with self._latest_lock:
            return None if self._latest is None else self._latest.copy()

    def set_status_text(self, text: str, color: tuple[int, int, int] = (0, 255, 0)) -> None:
        """Thread-safe: a status line the capture thread overlays bottom-left (display only)."""
        with self._status_lock:
            self._status_text = text
            self._status_color = color

    def stop(self) -> None:
        """Signal teardown and wait for the thread to release the window + cv2 on its own side."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    # ---- capture-thread internals ---------------------------------------
    def _open_capture(self) -> cv2.VideoCapture:
        return open_capture(
            self._index,
            self._backend,
            fourcc=self._fourcc,
            width=self._width,
            height=self._height,
            autofocus=self._autofocus,
            focus=self._focus,
        )

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
        # WINDOW_NORMAL: resizable. We keep the aspect ratio ourselves via imshow_fit (letterbox) --
        # WINDOW_KEEPRATIO is Qt-only and a no-op on this wheel's Win32 backend, so imshow would
        # otherwise stretch the frame on resize. resizeWindow sets a sane initial size = the
        # displayed frame (the ROI's reference size when cropping, else the camera's native size).
        disp_w = self._roi.ref_w if self._roi is not None else self.width
        disp_h = self._roi.ref_h if self._roi is not None else self.height
        disp_w, disp_h = _fit_within(disp_w, disp_h, _MAX_INITIAL_WIN_W, _MAX_INITIAL_WIN_H)
        cv2.namedWindow(self._title, cv2.WINDOW_NORMAL)
        if disp_w > 0 and disp_h > 0:
            cv2.resizeWindow(self._title, disp_w, disp_h)
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
                            # WINDOW_NORMAL + manual letterbox (imshow_fit): keep the still's aspect
                            # ratio ourselves. KEEPRATIO is Qt-only -- a no-op on the Win32 backend,
                            # where imshow would stretch the still as the window is resized.
                            cv2.namedWindow(self._still_title, cv2.WINDOW_NORMAL)
                            cv2.resizeWindow(self._still_title, 720, 720)
                            still_shown = True
                if still_frame is not None:
                    imshow_fit(self._still_title, still_frame)

                ok, frame = cap.read()
                if not ok or frame is None:
                    cv2.waitKey(30)  # transient hiccup -- keep the windows pumping, retry
                    continue

                if self._roi is not None:
                    # Crop to the fixed ROI, then upscale back to the ROI's reference size so the
                    # preview window + recording show the same 4:3 zoomed feed validated with
                    # scripts/diagnostics/system_camera/usb_camera_roi_preview.py. Done before everything below,
                    # so writer sizing, the clean write, the REC overlay, and imshow all inherit it.
                    frame = cv2.resize(
                        self._roi.crop(frame),
                        (self._roi.ref_w, self._roi.ref_h),
                        interpolation=cv2.INTER_LINEAR,
                    )

                with self._latest_lock:
                    self._latest = frame.copy()  # clean ROI frame for latest_frame() consumers

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
                with self._status_lock:
                    status, status_color = self._status_text, self._status_color
                if status:
                    cv2.putText(
                        frame,
                        status,
                        (12, frame.shape[0] - 14),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        status_color,
                        2,
                        cv2.LINE_AA,
                    )
                imshow_fit(self._title, frame)
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
