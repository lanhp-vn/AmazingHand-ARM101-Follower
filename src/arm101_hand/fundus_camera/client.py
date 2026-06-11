"""Read-only TCP/UDP client for the Pictor Prestige Camera API (device layer).

Discovery (UDP) then a held TCP message socket on :8000. Only read commands are
implemented -- never POST_FILE (IL: no accidental writes to the camera). The camera
opens its TCP listener in response to discovery, so a dead socket is re-established by
re-discovering. ``ensure_connected`` does that only when there is no socket yet -- it
cannot detect a socket the camera closed mid-session (a one-client API drops idle/used
connections). That peer-close is caught at I/O time: ``_send`` retries once through a
fresh discover + reconnect, so a capture after an idle gap recovers transparently.
Verified on an Optomed Aurora 2026-06-10.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable

from arm101_hand.fundus_camera.protocol import (
    CODE_FAIL,
    CODE_OK,
    CODE_REQUEST,
    DETECT_CAMERA,
    GET_CAMERA_STATUS,
    GET_FILE,
    GET_FILELIST,
    PING_CAMERA,
    CameraInfo,
    CameraStatus,
    FileInfo,
    MessageFail,
    pack_header,
    unpack_header,
)

_DETECT_PACKET = pack_header(DETECT_CAMERA, 0, 0)[:4]  # discovery payload = the 4-byte cmdId
_DISCOVER_RESEND_S = 0.5  # re-send the discovery probe at least this often within the timeout
_MAX_SKIPS = 8  # give up after this many out-of-sync (phantom) frames in one reply -> drain + fail


class CameraError(RuntimeError):
    """A camera request failed (CODE_FAIL) or the camera is unreachable/busy."""


def recv_exact(sock, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise ConnectionError if the socket closes early."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf += chunk
    return bytes(buf)


class PictorClient:
    def __init__(
        self,
        *,
        host: str | None,
        discovery_port: int,
        message_port: int,
        discover_timeout_s: float,
        connect_timeout_s: float,
        io_timeout_s: float,
        trace: Callable[[str, bytes], None] | None = None,
    ):
        self.host = host
        self.discovery_port = discovery_port
        self.message_port = message_port
        self.discover_timeout_s = discover_timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.io_timeout_s = io_timeout_s
        # Optional wire tap: trace(label, raw_bytes) fires on every send + logical recv when set
        # (default None -> no overhead). Read-only diagnostic aid; never alters what is sent.
        self._trace = trace
        self._sock: socket.socket | None = None
        self._seq = 0
        self.info: CameraInfo | None = None

    # -- discovery / connection --
    def discover(self) -> CameraInfo | None:
        """Broadcast (or unicast) DETECT_CAMERA, re-sending every ``_DISCOVER_RESEND_S``
        until a reply arrives or ``discover_timeout_s`` elapses; return the parsed reply or None.

        Re-sending (vs a single send + one wait) tolerates a dropped UDP packet and a camera
        that is briefly silent -- e.g. just after another client (aurora_probe / Optomed Client)
        released the single connection the camera allows.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(min(_DISCOVER_RESEND_S, self.discover_timeout_s))
        targets = [self.host] if self.host else ["255.255.255.255"]
        deadline = time.monotonic() + self.discover_timeout_s
        try:
            while True:
                for dest in targets:
                    s.sendto(_DETECT_PACKET, (dest, self.discovery_port))
                try:
                    data, addr = s.recvfrom(2048)
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        return None
                    continue  # no reply this round -- re-send until the budget runs out
                if len(data) >= 56:
                    info = CameraInfo.parse(data)
                    if self.host is None:  # remember where the reply came from for the TCP dial
                        self.host = addr[0]
                    self.info = info
                    return info
                if time.monotonic() >= deadline:
                    return None
        finally:
            s.close()

    def connect(self) -> None:
        """Open the TCP message socket (call promptly after ``discover``)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.connect_timeout_s)
        s.connect((self.host, self.message_port))
        s.settimeout(self.io_timeout_s)
        self._sock = s

    def ensure_connected(self) -> None:
        """Idempotent: (re)discover + (re)connect if not currently connected."""
        if self._sock is not None:
            return
        info = self.discover()
        if info is None:
            raise CameraError(
                "no camera responded to discovery (on the same Wi-Fi?). If another client just "
                "released it (aurora_probe / Optomed Client), wait a few seconds and retry -- "
                "the camera allows one client at a time."
            )
        if info.reserved:
            raise CameraError("camera is busy (cameraReserved=1) -- close Optomed Client")
        self.connect()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- requests --
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _recv(self, n: int, label: str) -> bytes:
        """``recv_exact`` + an optional trace of the bytes read (for the wire tap)."""
        data = recv_exact(self._sock, n)
        if self._trace is not None:
            self._trace(label, data)
        return data

    def _drain(self) -> bytes:
        """Discard and return everything currently buffered on the socket (non-blocking).

        Recovery for an out-of-sync frame we cannot resize (an unexpected event / unknown code):
        clear the buffer so a retry starts clean rather than reading shifted garbage. Read-only.
        """
        assert self._sock is not None
        self._sock.settimeout(0)
        out = bytearray()
        try:
            while True:
                try:
                    chunk = self._sock.recv(65536)
                except (BlockingIOError, TimeoutError):
                    break
                if not chunk:
                    break
                out += chunk
        finally:
            self._sock.settimeout(self.io_timeout_s)
        return bytes(out)

    def _send(self, cmd: int, payload: bytes = b"") -> tuple[int, int, int]:
        seq = self._next_seq()
        packet = pack_header(cmd, CODE_REQUEST, seq) + payload
        if self._trace is not None:
            self._trace(f"SEND cmd=0x{cmd:X} seq={seq}", packet)
        try:
            return self._send_once(packet, seq)
        except ConnectionError:
            # The camera closed the TCP connection after the previous exchange (the Pictor API
            # serves one client and drops the socket); the cached socket then reads 0 bytes.
            # ensure_connected() can't see a peer-closed socket -- it only checks ``is not None`` --
            # so drop it, re-discover + reconnect, and retry the request once. All commands here
            # are read-only + idempotent, so re-sending on a fresh connection is safe. A second
            # failure (or a camera that won't reconnect) propagates to the caller's retry layer.
            self.close()
            self.ensure_connected()
            return self._send_once(packet, seq)

    def _send_once(self, packet: bytes, seq: int) -> tuple[int, int, int]:
        if self._sock is None:  # surfaces as a catchable CameraError, never an AssertionError
            raise CameraError("not connected")
        self._sock.sendall(packet)
        return self._recv_response(seq)

    def _recv_response(self, seq: int) -> tuple[int, int, int]:
        """Read reply headers, skipping spurious out-of-sync CODE_FAIL frames, until ours arrives.

        The Aurora intermittently emits CODE_FAIL frames (errCode ERR_UNKNOWN_TYPE, cmdId=0,
        seqId=0) that are NOT replies to the request just sent -- reading one as the reply, then
        its 68-byte payload, is what desynced the whole stream (verified on the wire: we sent
        seq=3, the failure frames came back seq=0). The spec says a real reply echoes the
        request's seqId (Table 6) and the receiver must check it (Fig 5 "Check seqNo"), so a
        CODE_FAIL whose seqId != ours is a phantom: consume its fixed-size messageFail payload and
        read the next header. A CODE_OK is taken as our reply WITHOUT a seqId check -- this camera
        is already off-spec, so we don't bet the working path on it echoing seqId on success.
        """
        for _ in range(_MAX_SKIPS):
            rcmd, code, rseq = unpack_header(self._recv(12, "RECV header"))
            if code == CODE_OK or (code == CODE_FAIL and rseq == seq):
                return rcmd, code, rseq  # our reply (OK assumed in-order; FAIL matched by seqId)
            if code == CODE_FAIL:
                self._recv(68, "RECV skip-phantom")  # spurious CODE_FAIL -> consume + keep reading
                continue
            # Unknown/unsized out-of-sync frame (e.g. an event) -> cannot realign; drain + fail.
            self._drain()
            raise CameraError(f"out-of-sync reply (cmdId=0x{rcmd:X} code=0x{code:X} seq={rseq})")
        self._drain()
        raise CameraError(f"too many out-of-sync replies (expected seq={seq})")

    def _raise_if_fail(self, code: int) -> None:
        if code == CODE_FAIL:
            fail = MessageFail.parse(self._recv(68, "RECV fail-payload"))
            raise CameraError(f"camera CODE_FAIL errCode=0x{fail.err_code:X} msg={fail.message!r}")

    @staticmethod
    def _filepath(path: str) -> bytes:
        return path.encode("ascii").ljust(64, b"\x00")

    def ping(self) -> None:
        _, code, _ = self._send(PING_CAMERA)
        self._raise_if_fail(code)

    def get_status(self) -> CameraStatus:
        _, code, _ = self._send(GET_CAMERA_STATUS)
        self._raise_if_fail(code)
        return CameraStatus.parse(self._recv(36, "RECV status"))

    def get_filelist(self, path: str) -> list[FileInfo]:
        _, code, _ = self._send(GET_FILELIST, self._filepath(path))
        self._raise_if_fail(code)
        count = int.from_bytes(self._recv(4, "RECV filelist-count"), "little")
        return [FileInfo.parse(self._recv(FileInfo.SIZE, "RECV filelist-rec")) for _ in range(count)]

    def get_file(self, path: str) -> tuple[FileInfo, bytes]:
        _, code, _ = self._send(GET_FILE, self._filepath(path))
        self._raise_if_fail(code)
        info = FileInfo.parse(self._recv(FileInfo.SIZE, "RECV fileinfo"))
        return info, self._recv(info.filesize, "RECV filedata")
