# grab_trigger_capture — robot-triggered fundus capture + auto-pull

**Status:** approved design (2026-06-10) · **Branch:** `feat/grab-trigger-capture`
**Devices:** SO-ARM101 follower + AmazingHand + Optomed Aurora fundus camera (Wi-Fi)

## 1. Goal

A new demo, `scripts/demos/grab_trigger_capture.py`, that closes the capture→analyze
loop with physical actuation. After the existing staged arm+hand grab settles on the
camera, pressing `SPACE` makes the index finger press the Aurora's dual-action shutter,
hold briefly, release, and then the script pulls the newly-captured image (and metadata)
off the camera over the Pictor protocol into a workspace `fundus_images/` folder — leaving
the finger ready for the next trigger.

Workflow per press:

```
SPACE → index finger presses shutter → hold N s → release
      → pull new image + metadata → save to fundus_images/ → print success (ready)
```

Structurally this is `grab_toggle.py` with a different `on_hold` hook (a one-shot
press→release→pull cycle instead of a persistent in/out toggle), plus a new read-only
camera client promoted from the validated diagnostic probes.

## 2. Background & hard constraints (established by analysis + live hardware test)

The device is an **Optomed Aurora**; its Wi-Fi interface is the **Volk Pictor Prestige
Camera-Client API** (`references/optomed-camera/SPC70001873.pdf`), interface level 2.
Confirmed against the real unit on 2026-06-10:

- Discovery: UDP `:3000` broadcast `DETECT_CAMERA` (`00 30 ac 16`) → `CAMERA_DETECTED`
  (`interfaceLevel=2`, `cameraCustomization=1`, `cameraReserved`, mac, serial).
- TCP **message socket = port 8000**; 12-byte little-endian header `<III` =
  `cmdId, code, seqId` + payload. Verified `PING_CAMERA`, `GET_CAMERA_STATUS`
  (sw `3.3.7.11860`, wifi `1.3.0.2563`), `GET_FILELIST(\DCIM)`, `GET_FILE` (pulled a
  1.79 MB valid JPEG).

Constraints that shape this design:

1. **No remote shutter.** Capture is a physical dual-action-shutter press — hence the
   robot finger. The only write command (`POST_FILE`) is out of scope.
2. **One client connection at a time.** `cameraReserved=1` when Optomed Client (or any
   client) holds the socket. **Optomed Client must be closed** while this runs.
3. **The camera opens its TCP listener in response to discovery** — a dead socket cannot
   be re-dialed without re-discovery. The client must be reconnect-capable.
4. **Capture mode is a device-side setting (NOT in the API).** Two modes: *still* and
   *video*. A sustained full press in **video** mode records a clip; in **still** mode it
   captures one image (still+autofocus uses the hold to focus then capture). Therefore the
   camera **must be in Still mode**. Unintended video is both wrong data and a light-safety
   concern (continuous illumination is capped far tighter than still pulses).
5. **Quick imaging must be ON** (device setting). With it off, each capture sits in an
   on-device instant-preview that blocks save/WLAN-transfer until the operator dismisses it
   — which would stall the automated pull.
6. Half-press capture is **off** by default (a full press captures) — no change needed.
7. **No live-view streaming** exists (no RTSP; ports 554/8554 closed) — irrelevant here but
   rules out any "see what the camera sees" path through this API.

The script cannot set mode 4/5 over the API; it **documents them as prerequisites and
verifies after the fact** (see §7).

## 3. Architecture

New package `src/arm101_hand/camera/`, mirroring the hand's pure-core / thin-shell split
(`docs/conventions/01-module-layering.md`).

### 3.1 `camera/protocol.py` — pure (no sockets, no I/O)

The testable core, analogous to `hand/kinematics.py`:

- Constants: cmdIds (`DETECT_CAMERA`, `PING_CAMERA`, `GET_CAMERA_STATUS`, `GET_FILELIST`,
  `GET_FILE`, …), codes (`CODE_OK/FAIL/REQUEST/EVENT`), errCodes, fileType bits
  (`DIRECTORY=0x10`, `FILE=0x20`, …).
- `pack_header(cmd, code, seq) -> bytes` / `unpack_header(bytes) -> (cmd, code, seq)`.
- Dataclasses + parsers: `FileInfo` (filesize, fileType, fileDate, fileTime, filename;
  40-byte record), `CameraInfo` (discovery reply), `CameraStatus`, `messageFail`.
- `decode_fat32_datetime(date_u16, time_u16) -> datetime`.
- `diff_new_files(before: set[str], after: list[FileInfo]) -> list[FileInfo]` — new entries
  by path, **excluding directories** (does NOT filter by extension — see §7).
- `classify_capture(info) -> Literal["still", "video", "other"]` (by extension + type).
- `capture_filename(info, ts) -> str` and `sidecar_dict(info, status, ts, trigger_no) -> dict`.

### 3.2 `camera/client.py` — device layer (sockets)

`PictorClient`, analogous to `hand/motion.py`. **Read-only** (no `POST_FILE`). Holds one
TCP socket; `recv_exact` helper; per-request `seqId` counter; timeouts from config.

- `discover() -> CameraInfo | None` — UDP `DETECT_CAMERA`; returns parsed reply incl.
  `cameraReserved`.
- `connect()` — TCP `:8000`, promptly after a discover.
- `ensure_connected()` — **idempotent**: if the socket is missing/dead, re-discover then
  reconnect. Called at the top of every capture cycle (resilience across operator pauses).
- `ping()`, `get_status() -> CameraStatus`, `get_filelist(path="\\DCIM") -> list[FileInfo]`,
  `get_file(path) -> tuple[FileInfo, bytes]`, `close()`.

### 3.3 `camera/capture.py` — I/O orchestration

- `wait_for_new_files(client, before, *, timeout_s, poll_s, stable_polls) -> list[FileInfo]`
  — poll `get_filelist` until new non-directory entries appear **and** each one's `filesize`
  is nonzero and unchanged across `stable_polls` consecutive reads (write-race guard).
  Returns `[]` on timeout.
- `save_capture(info, data, dest_dir, *, status, trigger_no) -> Path` — validate the bytes
  (`get_file` caller checks JPEG magic `FF D8 FF` and `len(data) == info.filesize`), write
  `<dest_dir>/<name>.jpg` + matching `.json` sidecar.

### 3.4 Press-depth state — reuse, don't duplicate

Press-depth math reuses `hand/index_toggle.py` (`in_base`, `clamp`, delta bounds/adjust).
The only new pure piece is a one-shot fire state (out_base, side, delta) — a thin
`hand/index_trigger.py` that imports `in_base`/`clamp`/delta bounds from `index_toggle`
and adds the key map `SPACE=fire`, `[`/`]`=delta, `q`=quit. No persistent `pressed` flag.

### 3.5 Config (primitive layer) — `config/camera_config.py` + `data/camera_config.yaml`

Pydantic schema + operator YAML (IL-5; never written by runtime code):

```yaml
connection: {host: null, discovery_port: 3000, message_port: 8000,
             discover_timeout_s: 4.0, connect_timeout_s: 4.0, io_timeout_s: 8.0}
capture:    {hold_seconds: 3.0, new_file_timeout_s: 15.0, poll_s: 0.5,
             stable_polls: 2, dcim_root: "\\DCIM", fundus_dir: "fundus_images"}
```

`host: null` → discover by broadcast (the camera is DHCP). Press-depth bounds reuse
`index_toggle` constants (no new knob).

### 3.6 Demo script — `scripts/demos/grab_trigger_capture.py`

- `main()`: load camera config; build `PictorClient`; `ensure_connected()` up front and
  **fail fast** (print the prerequisite checklist; if `cameraReserved=1` or unreachable,
  exit with guidance to close Optomed Client). Define an `on_hold` closure capturing the
  client + config; call `run_grab_demo(on_hold)` (unchanged shared grab); `finally` close
  the client. No change to `grab_common.py` (the camera client rides in the closure).
- `on_hold(ctx)` — the trigger loop (Windows `msvcrt`, like `grab_toggle`): seed OUT-base/
  side + static fingers from the settled grab pose; print controls + prerequisite reminder.
  Per `SPACE` (the fire cycle):
  1. `client.ensure_connected()`
  2. `before = {paths from get_filelist(dcim_root)}`
  3. index → IN (`in_base(out_base+delta, base_max)`), close speed, position-poll
  4. dwell `hold_seconds` (deliberate hold — finger holds the shutter; not a move-wait)
  5. index → OUT (`out_base`), open speed, position-poll
  6. `new = wait_for_new_files(client, before, …)`
  7. each new file: `get_file` → validate → `save_capture` → `classify_capture`
  8. warn if any file classifies as `video`/`other`, or if `new == []` (wrong mode / preview
     gate / missed press)
  9. print success (saved path[s], size) → "ready for next trigger"; high-load warning after
     the press, like `grab_toggle`
  - `[`/`]` adjust press depth (never moves the finger); `q`/`Ctrl+C` exit to the
    `grab_common` exit prompt (Enter = release in place, `h` = staged reverse).

## 4. Data flow

`SPACE` → hand bus (press/poll/release) → camera socket (`get_filelist` diff → `get_file`)
→ local disk (`fundus_images/*.jpg` + `*.json`) → stdout success. The three transports
(arm STS3215 12 V, hand SCS0009 5 V, camera TCP/Wi-Fi) are independent; dwell/poll are
timeout-bounded so nothing deadlocks.

## 5. Output layout

- `fundus_images/` at repo root, created on first save, **added to `.gitignore`** (patient
  fundus images must never be committed).
- `fundus_images/<UTC-compact>_<sanitized-camera-name>.jpg`
  (e.g. `20260610T141530Z_DCIM_P0001_IM0010EY.jpg`) — collision-free.
- Matching `.json` sidecar: camera filename, size, fileType, decoded FAT32 date/time, host
  capture timestamp (UTC), camera serial + SW + wifi version (from `get_status` at connect),
  and this session's trigger number.

## 6. Safety & failure handling

- **Single connection:** fail fast at startup on `cameraReserved=1` (print: close Optomed
  Client). `ensure_connected()` per cycle survives idle drops (re-discovers).
- **Capture mode (verify/warn):** startup prints the prerequisite checklist —
  *Still mode · Quick imaging ON · Optomed Client closed · study selected*. Each cycle warns
  on a video/other file or an empty result. (No hard-abort — operator stays in control.)
- **Write-race:** size-stability in `wait_for_new_files`; magic + length validation in
  `save_capture`'s caller.
- **Diff is not extension-filtered** so a stray video is reported, not silently dropped.
- **No `cameraCustomization` model filter** (this unit reports `1`, not the spec's `14`).
- **No surprise movement:** the index moves only on `SPACE`; `[`/`]` never move it. On exit,
  `grab_common`'s prompt (Enter release in place / `h` staged reverse). Torque on BOTH buses
  is always released (IL-4). Consistent with the `no_surprise_movements` convention.
- Camera unreachable mid-session: `ensure_connected()` retries; if still dead, warn and stay
  in the loop (operator can fix and retry) — never crash mid-grab.

## 7. Operator prerequisites (printed at startup)

1. Camera in **Still imaging** mode (Optoroller → Capture mode).
2. **Quick imaging ON** (so captures save/transfer immediately).
3. **Optomed Client closed** (single connection).
4. A **study/patient selected** on the camera (new images land in the current study folder).
5. Clinical setup (focus, brightness, fixation target) is the operator's, out of scope here.

## 8. Testing (`docs/conventions/04-testing-verification.md`)

- **Pure unit tests** (`tests/unit`, no bus/network), seeded with the *real bytes captured on
  2026-06-10*: header pack/unpack roundtrip; `FileInfo`/`CameraInfo`/`CameraStatus`/
  `messageFail` parse; `diff_new_files` (excludes dirs, finds new); `decode_fat32_datetime`;
  `classify_capture`; `capture_filename`/`sidecar_dict`; press-depth clamp/delta-bounds.
- **Hardware-gated/manual** (`@pytest.mark.hardware`): live discover/connect/`get_file`, and
  the full press→pull cycle.

## 9. Housekeeping & scope

- **Diagnostics:** consolidate the four untracked `scripts/diagnostics/aurora_*_probe.py`
  into one committed read-only `scripts/diagnostics/aurora_probe.py` (built on `PictorClient`
  — no duplicate protocol code); delete the other three.
- **Commit:** one atomic feature commit (hand + camera + arm-reuse; IL-6). `references/`
  untouched (IL-2). Refresh `CLAUDE.md`/`README.md` (demos list + camera module in the tree)
  after implementation.
- **Out of scope (YAGNI):** worklist/`POST_FILE` push; `SUBSCRIBE`/events; video capture
  beyond detect-and-warn; setting capture mode over the API (impossible); Optomed
  Client/Cloud integration; any arm-logic change beyond reusing `run_grab_demo`.

## 10. File-change summary

| Path | Change |
|---|---|
| `src/arm101_hand/camera/__init__.py` | new — package exports |
| `src/arm101_hand/camera/protocol.py` | new — pure framing/parse/diff/classify/naming |
| `src/arm101_hand/camera/client.py` | new — `PictorClient` (read-only sockets) |
| `src/arm101_hand/camera/capture.py` | new — `wait_for_new_files`, `save_capture` |
| `src/arm101_hand/hand/index_trigger.py` | new — pure one-shot fire state (reuses `index_toggle`) |
| `src/arm101_hand/config/camera_config.py` | new — pydantic schema + loader |
| `src/arm101_hand/data/camera_config.yaml` | new — operator config |
| `scripts/demos/grab_trigger_capture.py` | new — the demo |
| `scripts/diagnostics/aurora_probe.py` | new — consolidated read-only diagnostic |
| `scripts/diagnostics/aurora_{discover,tcp,read,getfile}_probe.py` | delete — superseded |
| `tests/unit/test_camera_protocol.py` (+ trigger state) | new — pure tests |
| `.gitignore` | add `fundus_images/` |
| `CLAUDE.md`, `README.md` | refresh after implementation |

## 11. Iron Laws touched

- **IL-2** read-only references — honored (built fresh in `src/`).
- **IL-4** single bus owner / torque always released — honored.
- **IL-5** project state in-tree — camera config under `src/arm101_hand/data/`.
- **IL-6** atomic cross-device commit — hand + camera (+ arm reuse) ship together.
- **IL-7** single-source-of-truth — protocol logic lives once in `camera/protocol.py`.
