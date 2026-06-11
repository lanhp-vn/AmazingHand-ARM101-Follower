# `data/` — operator config files

Four YAML files. The **arm** and **hand** each get one — the device's **single tuning surface** (connection settings, safety thresholds, motion knobs, named poses). The two **cameras** each get one — the Optomed Aurora fundus camera and the arm-mounted USB system camera. All are loaded and validated by pydantic schemas in `src/arm101_hand/config/`.

| File | Schema owner | Purpose |
|---|---|---|
| `arm_config.yaml` | `arm101_hand.config.arm_config` (`ArmConfig`) | SO-ARM101 port, safety thresholds, motion tuning knobs, and named arm poses (degrees, lerobot `use_degrees` frame). |
| `hand_config.yaml` | `arm101_hand.config.hand_config` (`HandConfig`) | AmazingHand port/baudrate/timeout, safety thresholds, motion tuning knobs, and named hand poses (per-finger `[servo_1, servo_2]` servo-frame degree pairs). |
| `fundus_config.yaml` | `arm101_hand.config.fundus_config` (`FundusConfig`) | Optomed Aurora fundus camera: Pictor Wi-Fi connection (host/ports/timeouts) + capture tuning (shutter hold, new-file wait, DCIM root, fundus save dir). |
| `system_camera_config.yaml` | `arm101_hand.config.system_camera_config` (`SystemCameraConfig`) | Arm-mounted USB observation camera (films the Aurora screen): cv2 preview/record (index, backend, window title, record dir, fps). |

---

## File structure — four sections

The arm and hand files share the same section layout (the two camera files are flat operator config — no `safety`/`poses`):

```
schema_version: 1
connection:   # COM port, baudrate, timeout — which adapter to open
safety:       # diagnostics warn thresholds
tuning:       # operator motion knobs
poses:        # named poses written by jog.py / capture_pose.py
```

---

## Arm tuning knobs (`arm_config.yaml` → `tuning`)

| Key | Default | Meaning |
|---|---|---|
| `park_velocity` | 600 | Gentle home-move Goal_Velocity (raw units, 0–4095) |
| `jog_step_min` | 1.0° | Minimum jog step size |
| `jog_step_max` | 15.0° | Maximum jog step size |
| `jog_step_default` | 5.0° | Initial jog step on startup |
| `jog_step_increment` | 1.0° | Amount to grow/shrink step per `[`/`]` keypress |
| `load_warn` | 600 | Jog high-load warning threshold (raw) |
| `load_stall` | 600 | Sweep stall-detection threshold (raw) |
| `sweep_margin_default` | 90.0° | Sweep stop margin inward from each calibrated endpoint |
| `settle_sweep_s` | 1.2 s | Dwell time after reaching sweep target |
| `settle_set_pose_s` | 2.0 s | Settle wait after set_pose target is reached |
| `settle_capture_s` | 0.5 s | Settle wait before capture_pose reads present position |
| `home_tolerance_steps` | 25 | Home-reach tolerance in raw steps |
| `home_timeout_s` | 8.0 s | Home-move timeout before giving up |
| `home_poll_s` | 0.05 s | Poll interval while waiting for home-reach |

---

## Hand tuning knobs (`hand_config.yaml` → `tuning`)

| Key | Default | Meaning |
|---|---|---|
| `speed` | 4 | Jog motion speed (1–7, SCS0009 speed scale) |
| `speeds.open` | 5 | SetPose extension (opening) speed (1–7) |
| `speeds.close` | 3 | SetPose flexion (closing) speed (1–7) |
| `step_min` | 1° | Minimum jog step size |
| `step_max` | 15° | Maximum jog step size |
| `step_default` | 5° | Initial jog step on startup |
| `pose_margin_deg` | 5.0° | Back-off from calibrated limits when driving to open/close pose |
| `pose_timeout_s` | 2.0 s | Seconds to wait for a hand move to position-confirm before continuing (a gripping close that stalls on an object simply times out and continues) |
| `pose_poll_s` | 0.03 s | Interval between `read_present_position` polls while waiting for a hand move to arrive |
| `load_warn_threshold` | 80 | SCS0009 load % duty warn level (0–100) |
| `jog_base_min` | −60° | range_calib base-DOF discovery floor (logical deg) |
| `jog_base_max` | 130° | range_calib base-DOF discovery ceiling (logical deg) |
| `jog_side_min` | −60° | range_calib side-DOF discovery floor (logical deg) |
| `jog_side_max` | 60° | range_calib side-DOF discovery ceiling (logical deg) |

---

## Calibration vs. config — the split

These files are **operator config**: connection params, safety warn levels, motion tuning, and named poses. They are the surface a developer adjusts without re-running hardware calibration.

**Calibration files are separate and live under `scripts/calibration/`:**

| File | What it holds | Written by |
|---|---|---|
| `scripts/calibration/so_arm101/so101_follower.json` | Per-joint homing offset + range min/max (lerobot format) | `arm101-calibrate-follower` |
| `scripts/calibration/amazing_hand/hand_calib_values.yaml` | Per-servo `middle_pos` + per-finger DOF `limits` (v3 schema) | `middle_calib.py`, `range_calib.py` |

No data is duplicated between the two layers. Motor IDs for the hand come from the `FINGER_SERVO_IDS` code canon in `src/arm101_hand/config/motor_ids.py` — they are not stored in either file.

---

## Editing rules

- **Poses are written by the scripts.** `jog.py` (arm and hand) and `capture_pose.py` (arm) validate names and clamp values before saving. Hand-editing poses is allowed but not validated until the next script run.
- **`connection` / `safety` / `tuning` sections are hand-editable** and take effect on the next script run. Invalid values produce a clear pydantic error on load.
- **`schema_version` must stay at `1`** for the current schema. Future migrations bump this and ship a one-shot upgrade routine.
- **Comments are regenerated on save.** The save helpers (`save_arm_config`, `save_hand_config`) prepend a fixed header and re-dump the whole model. Per-knob documentation lives here in this README, not as inline YAML comments — any inline comments you add will be lost on the next save.
- **Atomic writes.** Save helpers write to `*.tmp` then call `os.replace()`. Never hand-edit a file while a script is mid-save.
- **`yaml.safe_load` only** — never `yaml.load`. Per `02-code-style-python.md` §6.

---

## Iron Law touchpoints

- **IL-5** — runtime operator config lives in version control, in-tree only (`src/arm101_hand/data/`). These files are committed; edits are reviewed. Calibration files (measurement outputs) live separately under `scripts/calibration/` — also in-tree, also committed, but regenerated only by running calibration scripts, never hand-tuned.
- **IL-7** — the pydantic `Field(description=...)` lines in `arm_config.py` / `hand_config.py` point at this README as the single documentation home for what each tuning knob means. Do not duplicate knob descriptions in the schemas themselves.
