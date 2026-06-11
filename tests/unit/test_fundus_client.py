import struct

import pytest

from arm101_hand.fundus_camera.client import CameraError, PictorClient, recv_exact
from arm101_hand.fundus_camera.protocol import (
    CODE_FAIL,
    CODE_OK,
    FILE,
    GET_CAMERA_STATUS,
    GET_FILE,
    GET_FILELIST,
    CameraInfo,
    pack_header,
)


class FakeSocket:
    def __init__(self, to_recv: bytes):
        self._buf = bytearray(to_recv)
        self.sent = bytearray()

    def sendall(self, b):
        self.sent += b

    def recv(self, n):
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    def settimeout(self, t):
        pass

    def close(self):
        pass


def _client_with(sock):
    c = PictorClient(
        host="x",
        discovery_port=3000,
        message_port=8000,
        discover_timeout_s=1,
        connect_timeout_s=1,
        io_timeout_s=1,
    )
    c._sock = sock
    return c


def test_recv_exact_reassembles():
    assert recv_exact(FakeSocket(b"abcdef"), 6) == b"abcdef"


def test_recv_exact_raises_on_short_close():
    with pytest.raises(ConnectionError):
        recv_exact(FakeSocket(b"ab"), 6)


def test_get_status_parses_ok():
    payload = struct.pack("<I", 0) + b"3.3.7.11860".ljust(16, b"\x00") + b"1.3.0.2563".ljust(16, b"\x00")
    sock = FakeSocket(pack_header(GET_CAMERA_STATUS, CODE_OK, 1) + payload)
    st = _client_with(sock).get_status()
    assert st.sw_version == "3.3.7.11860"
    # request was sent with a filepath-free header
    assert struct.unpack("<III", bytes(sock.sent[:12]))[0] == GET_CAMERA_STATUS


def test_get_filelist_parses_records():
    rec = struct.pack("<IIHH", 100, FILE, 0, 0) + b"\\DCIM\\P0001\\IM0001EY.JPG".ljust(28, b"\x00")
    body = struct.pack("<I", 1) + rec
    sock = FakeSocket(pack_header(GET_FILELIST, CODE_OK, 1) + body)
    files = _client_with(sock).get_filelist("\\DCIM")
    assert len(files) == 1 and files[0].filesize == 100
    # request payload includes a 64-byte filepath field
    assert len(sock.sent) == 12 + 64


def test_get_file_returns_info_and_bytes():
    info = struct.pack("<IIHH", 4, FILE, 0, 0) + b"\\DCIM\\P0001\\IM0001EY.JPG".ljust(28, b"\x00")
    sock = FakeSocket(pack_header(GET_FILE, CODE_OK, 1) + info + b"\xff\xd8\xff\xe0")
    fi, data = _client_with(sock).get_file("\\DCIM\\P0001\\IM0001EY.JPG")
    assert fi.filesize == 4 and data == b"\xff\xd8\xff\xe0"


def test_fail_response_raises_camera_error():
    payload = struct.pack("<I", 0x16AC6005) + b"nope".ljust(64, b"\x00")
    sock = FakeSocket(pack_header(GET_FILE, CODE_FAIL, 1) + payload)
    with pytest.raises(CameraError) as ei:
        _client_with(sock).get_file("\\bad")
    assert "0x16AC6005" in str(ei.value)


# A spurious "phantom" CODE_FAIL the Aurora emits with seqId=0 / cmdId=0 (errCode ERR_UNKNOWN_TYPE).
# Not a reply to our request; reading it as the reply is what desynced the stream on the wire.
_PHANTOM = (
    pack_header(0, CODE_FAIL, 0) + struct.pack("<I", 0x16AC6007) + b"Unknown type: 0x0".ljust(64, b"\x00")
)


def test_out_of_sync_phantom_fails_are_skipped_until_real_reply():
    # Our first request is seq=1; the real OK reply echoes it. The two leading seq=0 phantoms must
    # be skipped (per spec: match seqId) so the genuine filelist behind them parses normally.
    rec = struct.pack("<IIHH", 100, FILE, 0, 0) + b"\\DCIM\\P0001\\IM0002EY.JPG".ljust(28, b"\x00")
    ok = pack_header(GET_FILELIST, CODE_OK, 1) + struct.pack("<I", 1) + rec
    files = _client_with(FakeSocket(_PHANTOM + _PHANTOM + ok)).get_filelist("\\DCIM")
    assert len(files) == 1 and files[0].filename == "\\DCIM\\P0001\\IM0002EY.JPG"


def test_too_many_out_of_sync_phantoms_raise():
    with pytest.raises(CameraError) as ei:
        _client_with(FakeSocket(_PHANTOM * 20)).get_filelist("\\DCIM")
    assert "out-of-sync" in str(ei.value)


def test_send_reconnects_after_peer_closed_socket(monkeypatch):
    # The camera closes the TCP connection after a capture (the Pictor API serves one client).
    # The cached-but-dead socket then reads 0 bytes -> ConnectionError; ensure_connected() can't
    # see a peer-closed socket. _send must drop it, re-discover + reconnect, and retry the request
    # once -- so the next capture succeeds instead of failing "socket closed after 0/12 bytes".
    dead = FakeSocket(b"")  # empty buffer -> recv() returns b"" -> ConnectionError on the header
    payload = struct.pack("<I", 0) + b"3.3.7.11860".ljust(16, b"\x00") + b"1.3.0.2563".ljust(16, b"\x00")
    fresh = FakeSocket(pack_header(GET_CAMERA_STATUS, CODE_OK, 1) + payload)

    c = _client_with(dead)
    # Stand in for the real discover()/connect() handshake: hand back a (non-reserved) camera and
    # install the fresh socket, mimicking the camera reopening its listener on re-discovery.
    monkeypatch.setattr(c, "discover", lambda: CameraInfo(2, "mac", 0, 1, "S"))
    monkeypatch.setattr(c, "connect", lambda: setattr(c, "_sock", fresh))

    st = c.get_status()  # raises ConnectionError on the un-healed client
    assert st.sw_version == "3.3.7.11860"
    assert fresh.sent  # the retried request landed on the reconnected socket


def test_send_surfaces_camera_error_when_reconnect_fails(monkeypatch):
    # If the camera is truly gone, the peer-close heal reconnects, fails discovery, and must
    # surface a *catchable* CameraError -- never an AssertionError that would crash the demo's
    # "press SPACE to try again" degrade path. The follow-up call on the now-dropped socket must
    # also raise CameraError("not connected"), not AssertionError.
    c = _client_with(FakeSocket(b""))  # dead socket -> ConnectionError on the first read
    monkeypatch.setattr(c, "discover", lambda: None)  # camera does not answer re-discovery

    with pytest.raises(CameraError):
        c.get_status()
    assert c._sock is None  # the dead socket was dropped
    with pytest.raises(CameraError):  # not connected -> CameraError, never AssertionError
        c.get_status()


# Real 56-byte CAMERA_DETECTED reply (serial 1125581093422), reused as a discovery payload.
_CAMERA_DETECTED = bytes.fromhex(
    "0130ac16"
    "02000000"
    "64632d66332d31632d33662d32312d6130000000"
    "01000000"
    "01000000"
    "3131323535383130393334323200000000000000"
)


class FakeUDPSocket:
    """Discovery-socket stand-in: the first ``timeouts`` recvfrom calls raise TimeoutError,
    then a reply arrives from ``addr`` -- mimics a lossy / briefly-silent camera."""

    def __init__(self, reply, addr, timeouts):
        self._reply = reply
        self._addr = addr
        self._timeouts = timeouts
        self.send_count = 0

    def setsockopt(self, *a):
        pass

    def settimeout(self, t):
        pass

    def sendto(self, data, dest):
        self.send_count += 1

    def recvfrom(self, n):
        if self._timeouts > 0:
            self._timeouts -= 1
            raise TimeoutError
        return self._reply, self._addr

    def close(self):
        pass


def _discovering_client(timeout_s):
    return PictorClient(
        host=None,
        discovery_port=3000,
        message_port=8000,
        discover_timeout_s=timeout_s,
        connect_timeout_s=1,
        io_timeout_s=1,
    )


def test_discover_retries_until_reply(monkeypatch):
    # Camera answers only after two missed probes; discover must re-send, not give up after one.
    fake = FakeUDPSocket(_CAMERA_DETECTED, ("192.168.1.50", 3000), timeouts=2)
    monkeypatch.setattr("arm101_hand.fundus_camera.client.socket.socket", lambda *a, **k: fake)
    info = _discovering_client(2.0).discover()
    assert info is not None
    assert info.serial == "1125581093422"
    assert fake.send_count >= 3  # re-sent the probe rather than giving up on the first timeout


def test_discover_returns_none_when_never_answered(monkeypatch):
    fake = FakeUDPSocket(b"", None, timeouts=10**9)
    monkeypatch.setattr("arm101_hand.fundus_camera.client.socket.socket", lambda *a, **k: fake)
    assert _discovering_client(0.02).discover() is None
