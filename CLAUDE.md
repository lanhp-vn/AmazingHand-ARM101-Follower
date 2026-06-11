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
│   ├── system_camera/         # device layer — arm-mounted USB observation cam (films the Aurora screen): cv2 live preview + record + last-capture still popup; fixed-ROI zoom (roi.py) + imshow_fit aspect-preserving letterbox
│   ├── fundus_analysis/       # device layer — DR-grading inference: preprocess (circle-crop + eval transform), model (timm ViT-L/16 loader), grader (DRGrader load-once + GradeResult). Local/offline only
│   ├── config/                # primitive layer — pydantic schemas (arm_config, hand_config, fundus_config, system_camera_config, fundus_analysis_config, calibration, motor_ids)
│   ├── data/                  # runtime operator config: arm_config.yaml + hand_config.yaml + fundus_config.yaml + system_camera_config.yaml + fundus_analysis_config.yaml (+ README)
│   └── scripts/               # application layer — console-script entries (incl. dr_grade) + shared device_setup/grab_common
├── scripts/
│   ├── calibration/
│   │   ├── amazing_hand/      # snake_case calibration/test/jog scripts + measurement-only YAML (v3 schema)
│   │   └── so_arm101/         # follower calibration runner + sweep/set_pose/jog/capture_pose
│   ├── diagnostics/           # grouped by device: motors/ (dual-device scan/show_calib --device arm|hand + device-agnostic find_port) + fundus_camera/ (read-only aurora_probe / aurora_wiredump) + system_camera/ (usb_camera_probe smoke test / usb_camera_capture still / usb_camera_roi_preview ROI check)
│   ├── fundus_analysis/       # DR-grading ops scripts: export_weights (one-time slim safetensors export) + aptos_eval (test-split validation) + README
│   ├── teleop/                # planned
│   └── demos/                 # runnable demos — grab_sequence (staged grab) + grab_toggle (index-finger button) + grab_trigger_capture (live system-cam window + 'r' record; index presses Aurora shutter, auto-pulls the fundus image)
├── models/                    # gitignored — slim safetensors weights exported from references/ checkpoints (regenerable)
├── tests/                     # host unit tests (tests/unit) + hardware-gated (tests/hardware)
├── docs/
│   ├── BOM.md                 # bill of materials + host PC spec
│   └── conventions/           # 00–07 normative rules
├── references/                # vendored git submodules — never modify (IL-2)
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
uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py [--camera N]  # preview the fixed ROI zoom (4:3 crop of the Aurora screen) before the demo uses it

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

# DR-grading inference (RETFound ViT-L/16 fine-tuned on APTOS2019; local/offline; reads references/ read-only — IL-2)
# Provenance: vit_large_patch16_224, global_pool=avg, input_size=224, 5 classes, epoch 27. Full procedure in scripts/fundus_analysis/README.md.
uv run python scripts/fundus_analysis/export_weights.py                   # one-time: checkpoint-best.pth -> models/retfound_aptos2019_vitl16.safetensors (~1.2 GB, gitignored)
uv run python scripts/fundus_analysis/aptos_eval.py [--limit-per-class N] # validate model+transform on the APTOS test split (accuracy / per-class F1 / quadratic-weighted kappa)
uv run arm101-dr-grade [--input PATH] [--force] [--limit N]               # grade media_outputs/fundus_images/*.JPG -> <stem>.dr.json sidecars in media_outputs/fundus_analysis/ + console table

# Lint / format / type-check / test
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
- **System-camera preview + DR-grading inference are the computer-vision code so far.** `src/arm101_hand/system_camera/` (cv2 HighGUI window + recording, plus a "last capture" still popup the same thread hosts via `WebcamPreview.show_still`) films the Aurora screen; used by `grab_trigger_capture` + `usb_camera_probe`. A fixed ROI (`roi.py` / `AURORA_SCREEN_ROI`) zooms the preview + recording onto the screen, and `imshow_fit` letterboxes to preserve aspect ratio because `cv2.WINDOW_KEEPRATIO` is a silent no-op on this wheel's Win32 highgui backend. It needs the full `opencv-python` wheel, so `pyproject.toml` drops lerobot's `opencv-python-headless` pin via `[tool.uv] override-dependencies` — keep that override (a headless-pinning dep re-clobbers `cv2`). The vendored `references/computer-vision/` (OpenCV, MediaPipe, GazeTracking) is still untouched, for planned gaze work.
- **DR grading is inference-only (Phase 1).** `src/arm101_hand/fundus_analysis/` + `arm101-dr-grade` batch-grade Aurora captures with a RETFound ViT-L/16 (timm) fine-tuned on APTOS2019; design/plan in `docs/superpowers/{specs,plans}/2026-06-11-dr-grading-*`. Pipeline validated on the APTOS test split (quadratic-weighted kappa 0.91 — see `scripts/fundus_analysis/README.md` for the full metrics). No fine-tuning path. Domain shift (APTOS/EyePACS training vs Optomed Aurora handheld) means real-capture accuracy can drop below test-split numbers — research/educational, not diagnostic. Phase 2 (wire `DRGrader` into `grab_trigger_capture`) is the remaining DR work.
- **No CI.** `ruff` / `pytest` are local-only for now.
- **No discrete GPU.** Local ML training is CPU-bound; ViT-L inference runs on CPU (~2-5 s/image); large-policy work needs cloud.
