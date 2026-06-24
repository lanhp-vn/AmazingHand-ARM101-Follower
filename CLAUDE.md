# CLAUDE.md — Agent Self-Reference

Workspace: `D:\0-Nouslogic\AmazingHand-ARM101-Follower`
Hardware: SO-ARM101 follower arm (5 STS3215 motors, gripper removed) + AmazingHand bionic hand (8 SCS0009 motors). Single-PC dev (no embedded board, no cross-compile). Loaded automatically by Claude Code.

---

## 1. Project TL;DR

A unified workspace for calibrating and controlling two robotic devices that share one Python process:

- **SO-ARM101 follower** — LeRobot's 6-DoF arm with the gripper (motor ID 6) physically removed. The AmazingHand mounts in its place. We subclass `SOFollower` to skip the missing motor and register a new robot type, `so101_follower_no_gripper`.
- **AmazingHand** — Pollen Robotics 4-finger bionic hand (4 fingers × 2 SCS0009 = 8 servos). Driven via the `rustypot` Python wheel (Rust + pyo3 binding).

Both devices live behind separate USB↔TTL bridges on different COM ports and different voltage rails (5 V hand, 12 V arm). They are coordinated only at the application layer (a future teleop / policy script).

## 2. Iron Laws TL;DR

Full text in `docs/conventions/00-iron-laws.md`. Read that file before editing.

- **IL-1 — Voltage isolation is physical safety.** 5 V hand bus and 12 V arm bus must never share rails. Mixing destroys SCS0009 instantly.
- **IL-2 — `references/` is read-only.** Vendored git submodules — core: lerobot, AmazingHand, rustypot, FeetechServo, FTServo_Python; plus camera + computer-vision references. Adapt by transferring into `src/` or `scripts/`.
- **IL-3 — Motor ID canon.** Hand: IDs 1–8 (odd-right/even-left per finger). Arm: IDs 1–5 shoulder→wrist. **ID 6 is physically absent on the arm.**
- **IL-4 — COM-port discipline.** Single owner per bus. Close FD.exe / serial monitors / stale Python sessions before opening.
- **IL-5 — Project state in version control, in-tree only.** Calibration — Hand: `scripts/calibration/amazing_hand/hand_calib_values.yaml`. Arm: `scripts/calibration/so_arm101/<id>.json` — the `SO101FollowerNoGripperConfig` subclass defaults `calibration_dir` to that path so `~/.cache/huggingface/lerobot/...` is never written to. Runtime operator config (connection/safety/tuning/poses) — `src/arm101_hand/data/{arm,hand}_config.yaml`.
- **IL-6 — Atomic cross-device commits.** Changes touching both arm and hand ship in one commit.
- **IL-7 — Documentation is single-source-of-truth.** One canonical home per fact; pointers everywhere else.

## 3. Directory tree

```
AmazingHand-ARM101-Follower/
├── pyproject.toml             # uv-managed; lerobot[feetech] + rustypot + opencv-python (full wheel)
├── uv.lock                    # committed
├── .python-version            # 3.12
├── src/arm101_hand/
│   ├── robots/                # device layer — SO-ARM101 subclass
│   ├── hand/                  # device layer — rustypot kinematics + motion (position-poll) helpers, finger_io (shared finger read/drive), pose-jog/range-calib + index_toggle/index_trigger state machines, named-pose resolver
│   ├── fundus_camera/         # device layer — Optomed Aurora: read-only Pictor Wi-Fi client (discovery + file pull) + pure protocol (patient retinal images)
│   ├── system_camera/         # device layer — arm-mounted USB observation cam (films the Aurora screen): cv2 live preview + record + last-capture still popup; config-driven deskewed 5:3 (800x480) ROI zoom (roi.py + roi_from_region; screen_roi carries a rotation angle, lives in system_camera_config.yaml) + imshow_fit aspect-preserving letterbox; manual-focus lock (focus.py probe core + open_capture autofocus/focus, dshow-only); arc auto-trigger (arc_detector = red-only alignment-arc classification, auto_trigger = pure red->not-red gated lifecycle; WebcamPreview latest_frame snapshot + status overlay); view-calibration math (calibration.py = 5:3 deskew crop + camera-circle fit + symmetric arc bands + red-band sampling + ruamel round-trip writer)
│   ├── fundus_analysis/       # device layer — DR-grading inference: preprocess (circle-crop + eval transform), model (timm ViT-L/16 loader), grader (DRGrader load-once + GradeResult), render (combined results-panel + image codec), sidecar (shared sidecar/sha8 helpers). Local/offline only
│   ├── config/                # primitive layer — pydantic schemas (arm_config, hand_config, fundus_config, system_camera_config, fundus_analysis_config, calibration, motor_ids)
│   ├── data/                  # runtime operator config: arm_config.yaml + hand_config.yaml + fundus_config.yaml + system_camera_config.yaml + fundus_analysis_config.yaml (+ README)
│   └── scripts/               # application layer — console-script entries (incl. dr_grade) + shared device_setup/grab_common
├── scripts/
│   ├── calibration/
│   │   ├── amazing_hand/      # snake_case calibration/test/jog scripts + measurement-only YAML (v3 schema)
│   │   ├── so_arm101/         # follower calibration runner + sweep/set_pose/jog/capture_pose
│   │   └── system_camera/     # calibrate_view: interactive re-derive of deskewed 5:3 screen_roi (manual drag + arrow-key rotate) + circle-based arc bands + red HSV band -> system_camera_config.yaml
│   ├── diagnostics/           # grouped by device: motors/ (dual-device scan/show_calib --device arm|hand + device-agnostic find_port) + fundus_camera/ (read-only aurora_probe / aurora_wiredump) + system_camera/ (usb_camera_probe smoke test / usb_camera_capture still / usb_camera_roi_preview ROI check / usb_camera_focus_probe manual-focus sweep / usb_camera_arc_debug arc-detection case collector)
│   ├── fundus_analysis/       # DR-grading ops scripts: export_weights (one-time slim safetensors export) + aptos_eval (test-split validation) + README
│   ├── teleop/                # planned
│   └── demos/                 # runnable demos — grab_sequence (staged grab) + grab_toggle (index-finger button) + grab_trigger_capture (live system-cam window + 'r' record; index presses Aurora shutter, auto-pulls the fundus image) + grab_trigger_capture_analysis (same + inline DR grading: 'g' grades the patient turn -> combined results panel beside the image) + grab_auto_trigger_analysis (analysis demo + CV auto-trigger: 'm' arms AUTO, fires after the alignment arcs go RED then clear; 'n' = next patient)
├── models/                    # gitignored — slim safetensors weights exported from references/ checkpoints (regenerable)
├── tests/                     # host unit tests (tests/unit) + hardware-gated (tests/hardware)
├── docs/
│   ├── BOM.md                 # bill of materials + host PC spec
│   └── conventions/           # 00–07 normative rules
├── references/                # vendored git submodules — never modify (IL-2)
├── .github/                   # CI — ci.yml (ruff + mypy + pytest on push/PR), update-submodules.yml (weekly bump), CODEOWNERS
└── .claude/                   # local Claude Code config
```

## 4. Common workflows

```powershell
# One-time setup
uv sync                                   # provisions .venv from pyproject.toml

# Discover COM ports (both buses)
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize

# AmazingHand calibration (full procedure in scripts/calibration/amazing_hand/README.md)
uv run python scripts/calibration/amazing_hand/motor_reset.py
uv run python scripts/calibration/amazing_hand/middle_calib.py
uv run python scripts/calibration/amazing_hand/range_calib.py              # per-finger DOF limits
uv run python scripts/calibration/amazing_hand/finger_test.py
uv run python scripts/calibration/amazing_hand/full_hand_test.py           # end-to-end sanity check; cycles all fingers (fist -> open -> each finger)
uv run python scripts/calibration/amazing_hand/jog.py                      # jog all fingers; save whole-hand pose to src/arm101_hand/data/hand_config.yaml
uv run python scripts/calibration/amazing_hand/set_pose.py <pose>          # drive whole hand to a named/built-in pose (open|close|grab) and hold

# SO-ARM101 follower calibration (full procedure in scripts/calibration/so_arm101/README.md)
# Output JSON lands at scripts/calibration/so_arm101/<id>.json (subclass default).
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower

# Dual-device diagnostics (read-only re: calibration; never write so101_follower.json — IL-5)
# Grouped into motors/ (bus health + calib + port), fundus_camera/ (Aurora), system_camera/ (USB cam).
uv run python scripts/diagnostics/motors/find_port.py                             # device-agnostic; lists all COM ports
uv run python scripts/diagnostics/motors/scan.py --device arm                     # bus health check (--device arm|hand, torque off)
uv run python scripts/diagnostics/motors/show_calib.py --device arm [--live]      # dump calibration; --live compares present pos
uv run python scripts/diagnostics/fundus_camera/aurora_probe.py                   # read-only Aurora reachability + status + filelist (Optomed Client must be closed)
uv run python scripts/diagnostics/fundus_camera/aurora_wiredump.py                # read-only hex dump of one GET_FILELIST/GET_FILE exchange (Pictor framing debug)
uv run python scripts/diagnostics/system_camera/usb_camera_probe.py [--camera N]  # system-cam smoke test: live cv2 window + 'r' record (no motors/Aurora)
uv run python scripts/diagnostics/system_camera/usb_camera_capture.py [--camera N]    # system-cam still: live window, SPACE saves a frame to media_outputs/camera_captures/
uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py [--camera N]  # preview the fixed ROI zoom (deskewed 5:3 crop of the Aurora screen) before the demo uses it
uv run python scripts/diagnostics/system_camera/usb_camera_focus_probe.py [--camera N]   # sweep + fine-tune MANUAL focus on the ROI vs an autofocus baseline (needs --backend dshow); found focus=600
uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py [--camera N]   # arc-detection debug: stages the arm+hand grab, draws both arc boxes (red=RED/green=clear) + a coverage HUD on the deskewed ROI; SPACE saves a clean+annotated frame + a coverage/threshold sidecar to media_outputs/arc_debug/ (motors; no Aurora, no trigger)

# System-camera view calibration (writes system_camera_config.yaml; run after a resolution/lighting change)
uv run python scripts/calibration/system_camera/calibrate_view.py            # live: white -> red -> bright/aligned -> confirm -> write
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files WHITE RED BRIGHT  # detect on saved frames

# SO-ARM101 motion helpers (read clamp range from so101_follower.json; never write it — IL-5)
# Per-script detail in scripts/calibration/so_arm101/README.md §6.
uv run python scripts/calibration/so_arm101/sweep.py <joint|all>          # range-verify sweep to endpoints (--margin 90)
uv run python scripts/calibration/so_arm101/set_pose.py home              # drive to a poses entry (home = folded storage), hold
uv run python scripts/calibration/so_arm101/jog.py                       # interactive keyboard jog; saves poses to src/arm101_hand/data/arm_config.yaml
uv run python scripts/calibration/so_arm101/capture_pose.py               # hand-pose the arm by hand, capture present degrees, save as a pose

# Demos (read calibration + config; write neither — IL-5)
uv run python scripts/demos/grab_sequence.py                              # staged arm+hand grab; 'h' on exit reverses the whole sequence
uv run python scripts/demos/grab_toggle.py                                # grab, then SPACE toggles the index finger in/out like a button
uv run python scripts/demos/grab_trigger_capture.py                       # live system-cam window, ROI-zoomed to the Aurora screen ('r' records that zoomed feed to media_outputs/camera_recordings/); SPACE presses the shutter + auto-pulls the fundus image to media_outputs/fundus_images/ + pops it up in a "last capture" window until the next trigger (camera: Still + Quick imaging, Optomed Client closed)
uv run python scripts/demos/grab_trigger_capture_analysis.py [--grade|--no-grade]  # grab_trigger_capture + inline DR grading: SPACE shows the raw pull, 'g' grades every shot of the patient turn (DRGrader) -> combined results panel + <stem>.dr.json sidecars (same as arm101-dr-grade); missing weights -> still captures, panel says "grading unavailable". inline_grading + captures_per_patient in data/fundus_analysis_config.yaml
uv run python scripts/demos/grab_auto_trigger_analysis.py [--grade|--no-grade]  # grab_trigger_capture_analysis + CV AUTO trigger: 'm' toggles MANUAL<->AUTO; in AUTO the arc_detector watches the Aurora's two alignment arcs (red-only) and gates each fire on a red->not-red transition -- BOTH arcs RED (misaligned) then BOTH not-red (aligned) held stable (stable_seconds), re-requiring red between shots (re-align per shot); 'g' grades the patient's shots, 'n' = next patient. SPACE fires only in MANUAL. Arc bands/red HSV/timing in data/system_camera_config.yaml (auto_trigger block)

# DR-grading inference (RETFound ViT-L/16 fine-tuned on APTOS2019; local/offline; reads references/ read-only — IL-2)
# Provenance: vit_large_patch16_224, global_pool=avg, input_size=224, 5 classes, epoch 27. Full procedure in scripts/fundus_analysis/README.md.
uv run python scripts/fundus_analysis/export_weights.py                   # one-time: checkpoint-best.pth -> models/retfound_aptos2019_vitl16.safetensors (~1.2 GB, gitignored)
uv run python scripts/fundus_analysis/aptos_eval.py [--limit-per-class N] # validate model+transform on the APTOS test split (accuracy / per-class F1 / quadratic-weighted kappa)
uv run arm101-dr-grade [--input PATH] [--force] [--limit N]               # grade media_outputs/fundus_images/*.JPG -> <stem>.dr.json sidecars in media_outputs/fundus_analysis/ + console table

# Lint / format / type-check / test (CI enforces all four on push/PR — .github/workflows/ci.yml; format runs as --check there)
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'           # host unit tests (no bus)
```

## 5. Convention files

| File | Role |
|---|---|
| `docs/conventions/00-iron-laws.md` | **Read first.** Non-negotiable invariants. |
| `docs/conventions/01-module-layering.md` | Where code goes; the four import invariants. |
| `docs/conventions/02-code-style-python.md` | Python 3.12 style; ruff/mypy config; pydantic patterns. |
| `docs/conventions/03-code-style-shell.md` | PowerShell (primary) + Bash (rare). |
| `docs/conventions/04-testing-verification.md` | Test pyramid; `@pytest.mark.hardware` for bus-required tests. |
| `docs/conventions/05-git-workflow.md` | Branches, commit format, atomic cross-device commit rule. |
| `docs/conventions/06-documentation-protocol.md` | When to refresh CLAUDE.md/README.md; DRY registry. |
| `docs/conventions/07-kiss-simplicity.md` | KISS — prefer the simplest design that satisfies the requirement + Iron Laws. |

## 6. Don'ts (compressed)

- **Don't modify `references/**`** — IL-2. Adapt by transferring into `src/` or `scripts/`.
- **Don't share power rails between hand and arm** — IL-1. Two PSUs, two cables, labeled.
- **Don't run `lerobot-calibrate` directly for the follower** — it doesn't know `so101_follower_no_gripper`. Use `arm101-calibrate-follower`.
- **Don't open a COM port from two processes** — IL-4. Close other holders first.
- **Don't hand-edit calibration YAML/JSON while a controller is connected** — IL-5. Re-run the calibration script.
- **Don't use `yaml.load()`** — `yaml.safe_load()` only.
- **Don't `subprocess.*` without `timeout=`** — see `02-code-style-python.md` §8.
- **Don't commit secrets** — `~/.cache/huggingface/token` lives outside the repo.

## 7. Tech-debt & known limitations

- **No teleop, no policy, no dataset code.** The jog / calibration / diagnostic scripts drive poses; no teleoperation or learned-policy path yet.
- **System-camera preview + DR-grading inference are the computer-vision code so far.** `src/arm101_hand/system_camera/` (cv2 HighGUI window + recording, plus a "last capture" still popup the same thread hosts via `WebcamPreview.show_still`) films the Aurora screen; used by `grab_trigger_capture` + `usb_camera_probe`. The screen ROI now lives in `system_camera_config.yaml` (`screen_roi`, read via `roi_from_region`; `roi.py`'s `AURORA_SCREEN_ROI` is only the fallback default) as a **deskewed 5:3 (800x480) crop** — `screen_roi` carries a rotation `angle` so a slightly-tilted screen comes out upright (`Roi.crop` rotates about the box centre when `angle != 0`, plain-slice fast path otherwise), and `imshow_fit` letterboxes to preserve aspect ratio because `cv2.WINDOW_KEEPRATIO` is a silent no-op on this wheel's Win32 highgui backend. The `screen_roi` (with its angle), the two symmetric alignment-arc bands, and the red HSV band are all re-derived by `scripts/calibration/system_camera/calibrate_view.py` from **3 captures (white startup -> red arcs -> bright/aligned screen)**: the white frame is the drag-and-rotate backdrop for the deskewed 5:3 screen_roi (manual box + arrow-key rotation), red samples the arc red band, and the bright frame fits the camera circle (the arc bands are mirror-symmetric on the circle's left/right edges, battery corner excluded) and sets the coverage threshold; interactive confirm + comment-preserving ruamel writer, pure detection math in `system_camera/calibration.py`, run after any resolution or lighting change. Focus is locked to a fixed manual value (`system_camera_config.yaml` `autofocus: false` / `focus: 600`, applied in `open_capture` via `_apply_focus`) so the fixed-geometry Aurora screen stays sharp instead of autofocus hunting/breathing — this **requires `backend: dshow`** (MSMF accepts the focus calls but doesn't drive the VCM); the value was found with `usb_camera_focus_probe.py` (sharpness math in `focus.py`). It needs the full `opencv-python` wheel, so `pyproject.toml` drops lerobot's `opencv-python-headless` pin via `[tool.uv] override-dependencies` — keep that override (a headless-pinning dep re-clobbers `cv2`). The newest CV piece is the **arc auto-trigger**, now **red-only + red->not-red gated**: `arc_detector.detect` classifies each of the two on-screen alignment arcs as RED (>= `coverage_threshold` red HSV coverage) or not (the bright/aligned screen reads greenish and unreliable, so only red is thresholded), and `auto_trigger.update` is a pure clock-injected gated lifecycle `WAIT_RED -> WAIT_CLEAR -> STABILIZING -> COOLDOWN`: a fire requires BOTH arcs RED (misaligned, the per-capture gate) THEN BOTH not-red (aligned) held `stable_seconds`, then cooldown, then re-require red — a blank/menu/already-aligned screen never passes the gate (no false fire). Both feed `grab_auto_trigger_analysis` (multi-shot per patient, 'g' grades, 'n' next) via `WebcamPreview.latest_frame` + a status overlay (HUD shows L/R red/clear + the phase), config in the `system_camera_config.yaml` `auto_trigger` block (`left_arc`/`right_arc`/`red_bands`/`coverage_threshold`/timing; `schema_version` 7), design/plan in `docs/superpowers/{specs,plans}/2026-06-23-system-camera-deskew-circle-redgate*` (supersedes the 2026-06-19 + 2026-06-22 arc geometry/logic). To collect frames where the RED/clear classification misbehaves (for retuning `coverage_threshold` / `red_bands` / arc geometry), `scripts/diagnostics/system_camera/usb_camera_arc_debug.py` stages the grab, draws the arc boxes on the live deskewed ROI, and SPACE-saves a clean+annotated frame + a numbers sidecar to `media_outputs/arc_debug/` (no Aurora, no trigger; design/plan in `docs/superpowers/{specs,plans}/2026-06-23-system-camera-arc-debug-diagnostic*`). The vendored `references/computer-vision/` (OpenCV, MediaPipe, GazeTracking) is still untouched, for planned gaze work.
- **DR grading is inference-only.** `src/arm101_hand/fundus_analysis/` grades Aurora captures with a RETFound ViT-L/16 (timm) fine-tuned on APTOS2019 via two entry points: the batch CLI `arm101-dr-grade` (Phase 1) and inline grading in the `grab_trigger_capture_analysis` demo (Phase 2 — grades each patient turn on 'g', shows a combined results panel beside the image, writes identical `.dr.json` sidecars); `grab_trigger_capture` stays capture-only. Design/plan in `docs/superpowers/{specs,plans}/2026-06-11-dr-grading-*`. Pipeline validated on the APTOS test split (quadratic-weighted kappa 0.91 — see `scripts/fundus_analysis/README.md` for the full metrics). No fine-tuning path. Domain shift (APTOS/EyePACS training vs Optomed Aurora handheld) means real-capture accuracy can drop below test-split numbers — research/educational, not diagnostic.
- **Aurora has two independent, fixed idle-power timers — an API keep-alive does NOT prevent sleep.** Per the User Manual (p.20, no menu setting to disable/lengthen): SCREEN power-save at 2 minutes idle, full POWER-OFF at 10 minutes idle, woken only "by using any control button" (a physical press); the charging cradle forces power save (p.18), so the camera must be lifted off it for use. Bench probe `scripts/diagnostics/fundus_camera/aurora_keepalive_probe.py --duration 720` (PING_CAMERA every 15 s, run 2026-06-23) confirmed the API stayed RESPONSIVE from 0 to ~9:10 (well past the 2-minute mark), then the first ping failed at +565.8 s and the device went undiscoverable by +634.8 s — consistent with the documented 10-minute power-off. Interpretation: TWO power domains — the 2-minute timer sleeps only the SCREEN (Wi-Fi + Pictor API stay up, which is why pings kept succeeding for ~9 minutes), while the 10-minute timer powers OFF the whole device and is NOT reset by API traffic. Consequence for future work: a software heartbeat / keep-alive is a no-op against the 10-minute power-off (and unneeded for the 2-10 minute window, where the API stays up on its own). To keep the camera available: keep it OFF the cradle, keep inter-use gaps under ~10 minutes, and wake/power it via a PHYSICAL control button (the robot's physical shutter press counts as a control button — it wakes the screen). In `grab_auto_trigger_analysis.py` the on-screen alignment arcs cannot be detected while the screen is asleep (>2 minutes idle), so the screen must be woken before AUTO can fire. A sleep-recovery feature (bounded reconnect-wait at the capture boundary + operator guidance, no automatic arm/hand wake motion) is DESIGNED but DEFERRED — only the constraint is recorded here.
- **CI runs lint + tests only — never hardware.** `.github/workflows/ci.yml` runs ruff format-check + ruff lint + mypy(src) + pytest (`not hardware`) on every push/PR (Ubuntu, Python 3.12; only the `lerobot` submodule is initialized). `update-submodules.yml` bumps the public reference submodules weekly (excludes `lerobot` and the private `nous_vclient`, which the runner token cannot reach). Bus-required tests stay bench-only.
- **No discrete GPU.** Local ML training is CPU-bound; ViT-L inference runs on CPU (~2-5 s/image); large-policy work needs cloud.
