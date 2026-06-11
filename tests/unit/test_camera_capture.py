import json
from datetime import UTC, datetime

import pytest

from arm101_hand.camera.capture import (
    pull_file,
    save_capture,
    snapshot_filenames,
    wait_for_new_files,
)
from arm101_hand.camera.client import CameraError
from arm101_hand.camera.protocol import FILE, FileInfo


class _FakeClient:
    def __init__(self, listings):
        self._listings = list(listings)

    def get_filelist(self, path):
        return self._listings.pop(0) if len(self._listings) > 1 else self._listings[0]


def _fi(name, size):
    return FileInfo(filesize=size, file_type=FILE, file_date=0, file_time=0, filename=name)


def test_wait_for_new_files_returns_when_size_stable():
    before = {"\\DCIM\\P0001\\IM0001EY.JPG"}
    new = _fi("\\DCIM\\P0001\\IM0002EY.JPG", 500)
    # poll 1: appears (size 500); poll 2: same size 500 -> stable (stable_polls=2)
    client = _FakeClient([[new], [new]])
    out = wait_for_new_files(client, before, dcim_root="\\DCIM", timeout_s=5, poll_s=0.0, stable_polls=2)
    assert [f.filename for f in out] == ["\\DCIM\\P0001\\IM0002EY.JPG"]


def test_wait_for_new_files_empty_on_timeout():
    before = set()
    client = _FakeClient([[]])  # nothing ever appears
    out = wait_for_new_files(client, before, dcim_root="\\DCIM", timeout_s=0.05, poll_s=0.0, stable_polls=2)
    assert out == []


def test_save_capture_writes_jpeg_and_sidecar(tmp_path):
    info = _fi("\\DCIM\\P0001\\IM0010EY.JPG", 4)
    ts = datetime(2026, 6, 10, 14, 15, 30, tzinfo=UTC)
    path = save_capture(
        info,
        b"\xff\xd8\xff\xe0",
        tmp_path,
        captured_at=ts,
        trigger_no=1,
        camera_serial="S",
        camera_sw="sw",
        camera_wifi="wifi",
    )
    assert path.name == "20260610T141530Z_DCIM_P0001_IM0010EY.JPG"
    assert path.read_bytes() == b"\xff\xd8\xff\xe0"
    sidecar = path.with_suffix(path.suffix + ".json")
    meta = json.loads(sidecar.read_text())
    assert meta["filesize"] == 4 and meta["trigger_no"] == 1


class _ErroringClient:
    """get_filelist replays ``script`` in order: an Exception item is raised, a list item is
    returned. Once the script is exhausted, the last returned list is repeated."""

    def __init__(self, script):
        self._script = list(script)
        self._last: list = []

    def get_filelist(self, path):
        if not self._script:
            return self._last
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        self._last = item
        return item


def test_wait_for_new_files_tolerates_transient_filelist_error():
    before = {"\\DCIM\\P0001\\IM0001EY.JPG"}
    new = _fi("\\DCIM\\P0001\\IM0002EY.JPG", 500)
    # poll 1 errors; polls 2 & 3 show the new file at a stable size -> returned despite the error.
    client = _ErroringClient([CameraError("Unknown type: 0x0"), [new], [new]])
    out = wait_for_new_files(client, before, dcim_root="\\DCIM", timeout_s=5, poll_s=0.0, stable_polls=2)
    assert [f.filename for f in out] == ["\\DCIM\\P0001\\IM0002EY.JPG"]


def test_wait_for_new_files_reports_progress_and_errors_via_on_poll():
    calls = []
    client = _ErroringClient([CameraError("boom"), []])
    wait_for_new_files(
        client,
        set(),
        dcim_root="\\DCIM",
        timeout_s=0.05,
        poll_s=0.0,
        stable_polls=2,
        on_poll=lambda elapsed, n_new, note: calls.append(note),
    )
    assert calls  # progress callback fired
    assert any("boom" in note for note in calls)  # transient error surfaced, not raised


def test_snapshot_filenames_retries_past_transient_error():
    new = _fi("\\DCIM\\P0001\\IM0001EY.JPG", 100)
    client = _ErroringClient([CameraError("x"), [new]])
    assert snapshot_filenames(client, "\\DCIM", retries=3, retry_wait_s=0.0) == {
        "\\DCIM\\P0001\\IM0001EY.JPG"
    }


def test_snapshot_filenames_raises_after_exhausting_retries():
    client = _ErroringClient([CameraError("a"), CameraError("b")])
    with pytest.raises(CameraError):
        snapshot_filenames(client, "\\DCIM", retries=2, retry_wait_s=0.0)


class _CyclingClient:
    """get_filelist alternates forever: returns ``listing`` on odd calls, raises ``error`` on even.

    Mimics the Aurora seen on hardware -- the new file shows up on every *successful* poll but
    failed polls are interleaved between them, so two successes are never literally back-to-back.
    """

    def __init__(self, listing, error):
        self._listing = listing
        self._error = error
        self._n = 0

    def get_filelist(self, path):
        self._n += 1
        if self._n % 2 == 1:
            return self._listing
        raise self._error


def test_wait_for_new_files_errors_do_not_reset_size_stability():
    # The file is seen at a stable size on every successful poll, but errors fall between them.
    # A failed poll must NOT reset the stability counter, or the capture is never returned.
    new = _fi("\\DCIM\\P0001\\IM0002EY.JPG", 500)
    client = _CyclingClient([new], CameraError("Unknown type: 0x0"))
    out = wait_for_new_files(client, set(), dcim_root="\\DCIM", timeout_s=0.3, poll_s=0.0, stable_polls=2)
    assert [f.filename for f in out] == ["\\DCIM\\P0001\\IM0002EY.JPG"]


def test_wait_for_new_files_downloads_on_first_sighting_with_stable_polls_1():
    # stable_polls=1 -> return on the FIRST poll that sees a real new file (no 2nd confirmation).
    new = _fi("\\DCIM\\P0001\\IM0002EY.JPG", 500)
    calls = {"n": 0}

    class _Counting:
        def get_filelist(self, path):
            calls["n"] += 1
            return [new]

    out = wait_for_new_files(_Counting(), set(), dcim_root="\\DCIM", timeout_s=5, poll_s=0.0, stable_polls=1)
    assert [f.filename for f in out] == ["\\DCIM\\P0001\\IM0002EY.JPG"]
    assert calls["n"] == 1  # returned on the first poll -- no second-confirmation wait


class _ErroringGetFileClient:
    """get_file replays ``script``: an Exception item is raised, a (FileInfo, bytes) item returned."""

    def __init__(self, script):
        self._script = list(script)

    def get_file(self, path):
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_pull_file_retries_past_transient_error():
    info = _fi("\\DCIM\\P0001\\IM0002EY.JPG", 4)
    client = _ErroringGetFileClient([CameraError("Unknown type: 0x0"), (info, b"\xff\xd8\xff\xe0")])
    fi, data = pull_file(client, "\\DCIM\\P0001\\IM0002EY.JPG", retries=3, retry_wait_s=0.0)
    assert fi.filesize == 4 and data == b"\xff\xd8\xff\xe0"


def test_pull_file_raises_after_exhausting_retries():
    client = _ErroringGetFileClient([CameraError("a"), CameraError("b")])
    with pytest.raises(CameraError):
        pull_file(client, "\\bad", retries=2, retry_wait_s=0.0)
