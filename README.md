# AmazingHand + SO-ARM101 Follower

Unified Python workspace for calibrating and controlling a **LeRobot SO-ARM101 follower arm** with the gripper replaced by a **Pollen Robotics AmazingHand** bionic hand. Single-PC development on Windows 11 — no embedded board, no cross-compile.

## What it is

- A 5-DoF arm + 4-finger bionic hand sharing one Python process.
- Two independent serial buses: **5 V** for the hand (8 × Feetech SCS0009), **12 V** for the arm (5 × Feetech STS3215, no gripper).
- Calibration scripts that produce version-controlled state, ready for downstream teleop / policy work.

The arm side is a thin layer over [`huggingface/lerobot`](https://github.com/huggingface/lerobot) — we subclass `SOFollower` to skip the physically-absent gripper motor (ID 6) and register the new type as `so101_follower_no_gripper`. The hand side uses [`pollen-robotics/rustypot`](https://github.com/pollen-robotics/rustypot) directly through its Python wheel.

## Hardware

Full bill of materials, current budgets, and host PC spec live in [`docs/BOM.md`](docs/BOM.md). Quick summary:

| Role | Part | Qty | Bus |
|---|---|---|---|
| Arm actuators | Feetech STS3215 (12 V) | 5 (IDs 1–5) | Waveshare Bus Servo Adapter (A) |
| Arm power | 12 V / 3 A DC | 1 | — |
| Hand actuators | Feetech SCS0009 (5 V) | 8 (IDs 1–8) | Seeed XIAO Bus Servo Driver |
| Hand power | 5 V / 3 A DC | 1 | — |
| USB↔TTL bridge (hand) | Waveshare CH343 (VID 1A86 / PID 55D3) | 1 | enumerates as `COM18` on the dev host |
| USB↔TTL bridge (arm) | Waveshare adapter (CH343 / FT232 typical) | 1 | discover with PnP query below |

**The 5 V and 12 V rails must never share** — mixing destroys SCS0009 servos instantly. See `docs/conventions/00-iron-laws.md` IL-1.

## Host setup

You'll need:

- **Windows 10/11** (developed on Windows 11 Home build 22635).
- **Python 3.12** (declared in `.python-version`; the Microsoft Store install or `https://www.python.org/downloads/` is fine).
- **`uv`** package manager — install per [`https://github.com/astral-sh/uv`](https://github.com/astral-sh/uv).

From a fresh clone:

```powershell
git clone --recurse-submodules <this-repo-url>
cd AmazingHand-ARM101-Follower
uv sync
```

`uv sync` provisions `.venv/` from `pyproject.toml` (lerobot[feetech] editable from the submodule, rustypot wheel, numpy, pyyaml, pydantic, opencv-python, timm). About 60 packages get installed including `torch` — first sync takes a few minutes.

You do **not** need to activate the venv. Use `uv run <command>` for everything.

## First commands

### 1. Discover your COM ports

```powershell
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
```

Note the COM number for each USB↔TTL bridge. On the dev host the AmazingHand bridge is `COM18`; the SO-ARM101 bridge gets a separate number.

Alternatively, lerobot's interactive port helper (device-agnostic — lists all ports):

```powershell
uv run python scripts/diagnostics/motors/find_port.py
```

### 2. AmazingHand finger calibration

Full procedure (5 steps: ID burn → 0° reset → middle-position tuning → range-limit calibration → audit) is in [`scripts/calibration/amazing_hand/README.md`](scripts/calibration/amazing_hand/README.md). After the first ID-burn step (one-time, with FD.exe), the Python flow is:

```powershell
uv run python scripts/calibration/amazing_hand/motor_reset.py
uv run python scripts/calibration/amazing_hand/middle_calib.py
uv run python scripts/calibration/amazing_hand/range_calib.py
uv run python scripts/calibration/amazing_hand/finger_test.py
```

All state lives in `scripts/calibration/amazing_hand/hand_calib_values.yaml`.

### 3. SO-ARM101 follower calibration

Full procedure in [`scripts/calibration/so_arm101/README.md`](scripts/calibration/so_arm101/README.md). Quick form:

```powershell
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower
```

Replace `COM<X>` with the COM number from step 1. The output JSON goes to `scripts/calibration/so_arm101/so101_follower.json` — the `SO101FollowerNoGripperConfig` subclass defaults `calibration_dir` to that in-tree location, so the upstream `~/.cache/huggingface/lerobot/...` path is never used (IL-5). Pass `--robot.calibration_dir=<path>` if you want to override.

> **Important.** Use `arm101-calibrate-follower` (defined in `pyproject.toml`), not `lerobot-calibrate`. The upstream entry point doesn't know our `so101_follower_no_gripper` subclass exists.

Once calibrated, two kinds of read-only / motion helpers are available. Dual-device diagnostics live under `scripts/diagnostics/motors/`: `scan.py --device arm|hand` (pre-flight bus health check) and `show_calib.py --device arm|hand [--live]` (dump the calibration, `--live` compares present positions). Arm-specific motion helpers stay alongside the runner under `scripts/calibration/so_arm101/`: `sweep.py` (range-verify sweep to the calibrated endpoints), `set_pose.py` (drive to a named pose and hold), `jog.py` (keyboard-jog each joint in degrees within its calibrated range, saving named poses to `src/arm101_hand/data/arm_config.yaml`), and `capture_pose.py` (hand-pose the arm, capture present degrees, save). None of these write `so101_follower.json` — `arm101-calibrate-follower` stays the only thing that does (IL-5). Per-script detail, including the full key controls for `jog.py`, is in [`scripts/calibration/so_arm101/README.md`](scripts/calibration/so_arm101/README.md) §6.

```powershell
uv run python scripts/diagnostics/motors/scan.py --device arm
uv run python scripts/diagnostics/motors/show_calib.py --device arm
uv run python scripts/calibration/so_arm101/jog.py
```

### 4. Robot-triggered fundus capture (optional)

Two cameras are involved: the **Optomed Aurora** *fundus* camera (captures patient retinal images, over Wi-Fi/Pictor) and an arm-mounted USB *system* camera that films the Aurora's screen. With the Aurora on the same Wi-Fi, the hand presses its dual-action shutter and the workspace auto-pulls the freshly-captured image over the Pictor protocol; the demo also opens a live window for the system camera, zoomed to a fixed region of interest on the Aurora's screen (press `r` to record that zoomed feed). After each successful pull the captured image pops up in its own window and stays until the next shutter press.

```powershell
uv run python scripts/diagnostics/fundus_camera/aurora_probe.py        # read-only Aurora reachability + status + filelist
uv run python scripts/diagnostics/system_camera/usb_camera_probe.py    # system-cam smoke test: live window + 'r' record (no motors/Aurora)
uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py  # preview the fixed ROI zoom of the Aurora screen (no motors/Aurora)
uv run python scripts/demos/grab_trigger_capture.py                    # live ROI-zoomed system-cam window; SPACE presses the shutter, image lands in media_outputs/fundus_images/ and pops up until the next capture
```

Camera prerequisites (the API cannot set these — do it on the device): **Still imaging** mode, **Quick imaging ON**, and **Optomed Client closed** (the Pictor API allows a single client connection — `aurora_probe` counts too, so if you just ran it, give the camera a few seconds to free the slot before launching the demo). The system-cam preview uses the full `opencv-python` wheel. Pulled fundus images + their JSON sidecars and any system-cam recordings save under `media_outputs/` (git-ignored — never commit medical images).

### 5. Diabetic-retinopathy grading (optional)

Grade the severity of diabetic retinopathy (0 = No DR through 4 = Proliferative) on captured fundus photos using a **RETFound** Vision Transformer (ViT-L/16) fine-tuned on the APTOS2019 dataset. This runs **fully locally and offline** — no image or result leaves the machine. It is a research/educational tool, **not a medical device, and not for clinical diagnosis**.

Full procedure is in [`scripts/fundus_analysis/README.md`](scripts/fundus_analysis/README.md). The flow is a one-time weight export, an optional accuracy validation, then grading:

```powershell
uv run python scripts/fundus_analysis/export_weights.py     # one-time: slim weights -> models/ (~1.2 GB, git-ignored)
uv run python scripts/fundus_analysis/aptos_eval.py         # optional: validate accuracy on the APTOS test split
uv run arm101-dr-grade                                       # grade media_outputs/fundus_images/*.JPG
```

`arm101-dr-grade` writes a `<image>.dr.json` sidecar (predicted grade, per-class probabilities, confidence band, crop region, model hash) to `media_outputs/fundus_analysis/` and prints a summary table. Re-runs skip already-graded images unless you pass `--force`. The slim weights come from the read-only checkpoint under `references/AIML-models/APTOS2019/` (IL-2); the export never modifies it.

## Repo layout

```
src/arm101_hand/        # device + application layer (subclass + console scripts + fundus_analysis DR grader)
scripts/calibration/    # AmazingHand + SO-ARM101 calibration runners
scripts/diagnostics/    # motors/ (scan / show_calib / find_port) + fundus_camera/ (aurora_probe / aurora_wiredump) + system_camera/ (usb_camera_probe / usb_camera_capture / usb_camera_roi_preview)
scripts/demos/          # runnable demos (grab_sequence: staged grab; grab_toggle: + index-finger toggle; grab_trigger_capture: live system-cam window + Aurora shutter press + auto-pull)
scripts/fundus_analysis/ # DR-grading ops: export_weights (slim weight export) + aptos_eval (validation)
models/                 # git-ignored slim model weights exported from references/ checkpoints
docs/BOM.md             # bill of materials, host PC spec
docs/conventions/       # 00 Iron Laws → 07 KISS
references/             # vendored git submodules — read-only (IL-2)
```

Layering rationale: [`docs/conventions/01-module-layering.md`](docs/conventions/01-module-layering.md).

## Conventions

Project-wide rules are in [`docs/conventions/`](docs/conventions/README.md). Read `00-iron-laws.md` first — everything else is an application of those.

## License

This repository's own code is unlicensed (default copyright). The submodules under `references/` carry their own licenses (Apache-2.0, MIT, BSD variants — see each submodule's `LICENSE`).
