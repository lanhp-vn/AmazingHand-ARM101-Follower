# `data/` — runtime config and pose state

Three YAML files plus this README. All loaded and validated by pydantic schemas under `src/arm101_hand/config/`. They are read and written by the calibration, jog, and diagnostic scripts — not by any application layer binary.

| File | Schema owner | Purpose |
|---|---|---|
| `app_config.yaml` | `arm101_hand.config.app_config` | COM ports per bus, baudrates, default speeds, safety thresholds. Read by the diagnostics (`scripts/diagnostics/scan.py`) and arm scripts at startup. |
| `hand_config.yaml` | `arm101_hand.config.hand_poses` | Named hand poses + sequences (calibration-aware degrees, even-ID pre-inverted). Hand poses are saved by `scripts/calibration/amazing_hand/jog.py`. |
| `arm_config.yaml` | `arm101_hand.config.arm_poses` | Named arm poses (e.g. `home` — the folded storage pose — plus any saved via the scripts). Joint values in degrees, soft-clamped to the calibrated range from `scripts/calibration/so_arm101/so101_follower.json`. Arm poses are saved by `scripts/calibration/so_arm101/jog.py` and `capture_pose.py`. |

## Editing rules

- **Edit via the scripts** (`jog.py`, `capture_pose.py`) when adding or updating poses — they validate names (per `validate_pose_name()`) and clamp each value to the per-device calibrated range before writing.
- **Hand-edits are allowed** but not validated until the next script run. If you break the schema, the loader exits with a clear error message.
- **`schema_version` must stay at `1`** for the current schema. Future migrations bump this and ship with a one-shot upgrade routine.
- **Atomic writes:** save helpers write via `*.tmp` + `os.replace()`. Never hand-edit a file while a script is in the middle of a save.
- **`yaml.safe_load` only** — never `yaml.load`. Per `02-code-style-python.md` §6.

## Why three files?

- `app_config.yaml` is host/workstation state.
- `hand_config.yaml` is hand-specific content portable between hands of the same calibration.
- `arm_config.yaml` is arm-specific content.

A single combined file would cross-couple hand and arm state and risk one user's edits corrupting the other.

## Iron Law touchpoints

- **IL-5** — calibration state lives in version control. These files are committed; edits are reviewed.
- **IL-7** — the schemas live in exactly one place (the pydantic models). Documentation here is a pointer, not a restatement.
