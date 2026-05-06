# `data/` — runtime state for `arm101-gui`

Three YAML files plus this README. All loaded at GUI startup, validated by pydantic schemas under `src/arm101_hand/config/`. The full design lives in `docs/plans/01-unified-gui-spec.md`.

| File | Schema owner | Purpose |
|---|---|---|
| `app_config.yaml` | `arm101_hand.config.app_config` | Window state, COM ports per bus, baudrates, default speeds, safety thresholds. Saved on graceful exit. |
| `hand_config.yaml` | `arm101_hand.config.hand_poses` | Named hand poses + sequences (calibration-aware degrees, even-ID pre-inverted). Edited via the GUI's pose manager. |
| `arm_config.yaml` | `arm101_hand.config.arm_poses` | Named arm poses + the three quick-poses (`zero`, `home`, `rest`). Joint values in degrees, soft-clamped to the calibrated range from `scripts/calibration/so_arm101/so101_follower.json`. |

## Editing rules

- **Edit via the GUI** when possible — the pose manager validates names (per `validate_pose_name()`) and runs each value through the per-device clamp.
- **Hand-edits are allowed** but not validated until the next GUI start. If you break the schema, the loader exits with a clear error message.
- **`schema_version` must stay at `1`** for the v1 GUI. Future migrations bump this and ship with a one-shot upgrade routine.
- **Atomic writes:** the GUI writes via `*.tmp` + `os.replace()`. Never edit a file while the GUI is running and saving.
- **`yaml.safe_load` only** — never `yaml.load`. Per `02-code-style-python.md` §6.

## Why three files?

- `app_config.yaml` is host/workstation state.
- `hand_config.yaml` is hand-specific content portable between hands of the same calibration.
- `arm_config.yaml` is arm-specific content.

A single combined file would cross-couple hand and arm state and risk one user's edits corrupting the other. See `docs/plans/01-unified-gui-spec.md` §15.4 for the full rationale.

## Iron Law touchpoints

- **IL-5** — calibration state lives in version control. These files are committed; edits are reviewed.
- **IL-7** — the schemas live in exactly one place (the pydantic models). Documentation here is a pointer, not a restatement.
