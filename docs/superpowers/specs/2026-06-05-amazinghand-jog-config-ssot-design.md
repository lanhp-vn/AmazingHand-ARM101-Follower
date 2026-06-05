# Design — AmazingHand config consolidation + `jog.py`

**Date:** 2026-06-05
**Scope:** Hand-only (AmazingHand). Two related requests:

1. **Config SSOT** — every script under `scripts/calibration/AmazingHand/` sources its
   serial + calibration config from `AmazingHand_calib_values.yaml` through the *existing*
   typed loader, eliminating copy-pasted YAML load/save code and in-script hardcoded values.
2. **`jog.py`** — a new keyboard-jog tool to move all four fingers and save a whole-hand
   pose, mirroring the arm's `so_arm101/jog.py`.

---

## 1. Problem

The AmazingHand calibration scripts already read `com_port` / `baudrate` / `timeout` (and four
of six read `speed`) from `AmazingHand_calib_values.yaml`, but:

- `MotorReset`, `MiddlePos_FingerCalib`, and `RangeCalib` each **copy-paste** their own YAML
  `load` / `save` helpers + an `InlineDict` representer (a DRY violation).
- `SetPose` and `FullHand_Test` hardcode their own motion speeds in-script.
- There is no CLI way to **pose all fingers freely and save** the result. The GUI's pose
  manager does this, but requires launching PySide6; a lightweight CLI tool is wanted —
  exactly parallel to the arm's `so_arm101/jog.py`.

Two pieces of infrastructure already exist and **must be reused, not reinvented**:

- `src/arm101_hand/config/calibration.py` — `HandCalibration` schema + `load_hand_calibration()`
  for `AmazingHand_calib_values.yaml`. It has **no save function** (hence the hand-rolled writes).
- `src/arm101_hand/config/hand_poses.py` — `HandPoseConfig` schema + `load_hand_poses()` for
  `data/hand_config.yaml`, the project's **canonical hand-pose store** (used by the GUI pose
  manager). Poses are stored as 8 servo-frame ints. It also has no save function (the GUI
  inlines its own `_write_yaml`).

## 2. Decisions

- **Config stays in `AmazingHand_calib_values.yaml`; poses go to `data/hand_config.yaml`.**
  These are different data with different homes. Putting jog poses into the calib YAML would
  create a *second* pose store in a different format from the GUI's — an SSOT violation. The
  arm already sets the precedent: `so_arm101/jog.py` and `capture_pose.py` save poses to
  `data/arm_config.yaml`. The hand's parallel is `data/hand_config.yaml`.
- **`jog.py` is keyboard-jog with torque ON** (like `RangeCalib`), not capture-by-demonstration
  (like the arm's `capture_pose.py`). The SCS0009 parallel-mechanism fingers do not back-drive
  cleanly; the operator jogs with arrow keys.
- **Plain block-style YAML on save** (KISS, matches the arm's `save_arm_poses`). This expands
  the current compact `{id: 1, middle_pos: 30}` inline dicts to block style — accepted; one
  fewer custom dumper to maintain.
- **Only `SetPose`'s open/close speeds move to the YAML.** `FullHand_Test`'s demo speeds are
  choreography, not calibration — they stay as in-script constants (YAGNI).

## 3. Design

### 3.1 Part 1 — config consolidation

**Schema (`config/calibration.py`):**

- New nested model for `SetPose` speeds (additive, so old YAML without it still validates):

  ```python
  class PoseSpeeds(BaseModel):
      model_config = ConfigDict(extra="forbid")
      open: int = Field(default=5, ge=1, le=7)   # extension (quicker)
      close: int = Field(default=3, ge=1, le=7)  # flexion (gentler settle)
  ```

- `HandCalibration` gains `speeds: PoseSpeeds = Field(default_factory=PoseSpeeds)`. Top-level
  `speed` stays as the calibration/jog speed. `schema_version` stays `2` (purely additive,
  defaulted field).

- New `save_hand_calibration(path, config)` — full-model dump, atomic write:

  ```python
  def save_hand_calibration(path: Path, config: HandCalibration) -> None:
      payload = config.model_dump(mode="python")
      tmp = path.with_suffix(path.suffix + ".tmp")
      tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
      os.replace(tmp, path)
  ```

  Because the model holds the *whole* file, load-modify-save preserves every field — the
  per-finger "partial write" the writer scripts used by hand is no longer needed.

**Schema (`config/hand_poses.py`):**

- New `save_hand_poses(path, config)` — identical body to the GUI's `_write_yaml`
  (`model_dump` → `safe_dump(sort_keys=False)` → atomic replace). `sequences:` is part of the
  model, so it survives the round-trip.

**GUI (`gui/hand_panel.py`):**

- `_write_yaml` is refactored to call `save_hand_poses(self._poses_path, self._poses)`.
- `_snapshot_positions` is refactored to call the shared converter (passing its existing
  `(-70, 90)` bounds) so the conversion formula lives in one place. Output is unchanged.

**Converter (`hand/kinematics.py`)** — new pure helper, the single source for the
logical→servo-frame conversion **formula** (the `compose_finger` + `even_id_inversion`
sequence that the GUI's `_snapshot_positions` currently inlines):

```python
def finger_positions_to_servo_frame(
    odd_id: int, even_id: int, base: int, side: int,
    servo_min: int = -40, servo_max: int = 110,
) -> tuple[int, int]:
    """(base, side) logical -> (odd_servo_val, even_servo_val) in YAML/servo frame.

    Composes to a symmetric servo pair (clamped to [servo_min, servo_max]), then
    applies even-ID pre-inversion. Caller places results at out[odd_id-1] / out[even_id-1].
    """
    pos1, pos2 = compose_finger(base, side, servo_min, servo_max)
    return (
        int(even_id_inversion(odd_id, float(pos1))),
        int(even_id_inversion(even_id, float(pos2))),
    )
```

**Servo-bound note (corrected):** the GUI clamps its save to
`_SERVO_LOGICAL_MIN, _SERVO_LOGICAL_MAX = -70, 90`, while the calib scripts' *live drive*
(`RangeCalib`, `SetPose`, `FingerTest`) uses `compose_finger`'s `(-40, 110)` defaults. The
shared helper is the **formula**, parameterized by bounds — each caller keeps its own:
- The **GUI** calls it with its existing `(-70, 90)` (behavior unchanged).
- **jog.py** uses the `(-40, 110)` defaults for *both* live drive and save, so the saved pose
  equals what the operator commanded (no corner clamp below calibrated reach — e.g. thumb
  `base_max = 100` must survive) and remains GUI-loadable (the GUI's load path,
  `decompose_finger`, accepts up to 110). "Byte-identical to the GUI" is **not** a goal;
  "saved == commanded, and GUI-loadable" is.

**Scripts (refactor all six):**

- Replace each script's `with open(...) as f: config = yaml.safe_load(f)` with
  `cfg = load_hand_calibration(PATH)`; access typed attributes (`cfg.com_port`,
  `cfg.fingers["index"].servo_1.id`, `cfg.fingers["index"].limits.base_max`, …).
- The three **writers** (`MotorReset`, `MiddlePos_FingerCalib`, `RangeCalib`) modify the model
  and call `save_hand_calibration()`. Each deletes its local `InlineDict` representer +
  `load`/`save` helpers. They construct a fresh `DofLimits` / `ServoCalibration` when changing
  values (so the `DofLimits._check_ordering` validator runs — keep each script's existing
  pre-save ordering guard so a temporarily-invalid mark never reaches model construction).
- `SetPose` reads `cfg.speeds.open` / `cfg.speeds.close` instead of module constants.
- `FullHand_Test` loads via `load_hand_calibration()` but keeps its demo-speed constants.

**Seed YAML (`AmazingHand_calib_values.yaml`):** add a `speeds:` block:

```yaml
speed: 4
speeds:
  open: 5
  close: 3
fingers:
  ...
```

### 3.2 Part 2 — `jog.py` behavior

**Startup:** load `HandCalibration` (serial + limits + middle_pos) and `HandPoseConfig`
(existing poses). Open the `Scs0009PyController`, enable torque on all 8, set `Goal_Speed =
cfg.speed`, drive every finger to neutral `(0, 0)`. Print the control map.

**Control scheme** (Windows `msvcrt` raw keys, like `RangeCalib`):

| Key | Action |
| --- | --- |
| `1` `2` `3` `4` | select active finger (index / middle / ring / thumb) |
| `↑` / `↓` | active finger `base +` / `base −` (flex / extend) |
| `→` / `←` | active finger `side +` / `side −` (spread) |
| `[` / `]` | shrink / grow jog step (1–15°, default 5°) |
| `h` | home active finger to `(0, 0)` |
| `H` | home all fingers to `(0, 0)` |
| `s` | save current whole-hand pose (prompts for a name) |
| `q` / Ctrl+C | release torque on all 8 and exit |

- **Jog clamping:** each finger's cursor is clamped to *its own calibrated*
  `base_min`/`base_max`/`side_min`/`side_max` from `cfg`. Poses can only be built inside the
  known-good envelope (no stalling into a stop).
- **On every move:** send the active finger's two servos via
  `compose_finger(base, side, base_min=…, base_max=…, side_min=…, side_max=…)` →
  `degrees_to_servo_radians(servo_id, pos, middle_pos)` (mirrors `SetPose.move_finger`).
- **Live status line:** active finger marker + all four fingers' `(base, side)` + step +
  active finger's `load=[L1,L2]`, with `load_warning()` (reused) printed when a servo is hot.
- **Save (`s`):** prompt `Save pose as (name, blank = cancel): `; validate with
  `validate_pose_name`; snapshot all four fingers → 8 servo-frame ints via the §3.3 converter;
  upsert into the in-memory `HandPoseConfig.poses` (overwrite allowed); `save_hand_poses()` to
  `data/hand_config.yaml`. Echo confirmation. `sequences:` and other poses are preserved.

### 3.3 Pure state machine (`hand/pose_jog.py`, new, testable — no hardware/`msvcrt`)

- `HandJogState` (frozen dataclass): `active: str`, `step: int`,
  `fingers: dict[str, tuple[int, int]]` (per-finger `(base, side)`).
- `key_to_action(key) -> str | None` — maps `1-4`/arrows/`[`/`]`/`h`/`H`/`s`/`q`.
- `apply_action(state, action, limits_by_finger) -> HandJogState` — moves/clamps the active
  finger to its `DofLimits`, switches active finger, adjusts step, or homes. `s`/`q` are
  no-ops on state (handled by the script).
- Reuses `range_calib.STEP_DEFAULT/STEP_MIN/STEP_MAX` and `_clamp`; reuses
  `range_calib.load_warning`. A hand-specific multi-finger status formatter lives here too.

### 3.4 Exit / safety

- `finally`: best-effort torque-off on all 8 servos (IL-4), then the controller handle is
  dropped (Rust side releases the COM port).
- IL-3: servo IDs 1–8 only. IL-5: the calib YAML stays the canonical calibration store; jog.py
  never writes it.

## 4. Files

| File | Action |
| --- | --- |
| `src/arm101_hand/config/calibration.py` | Modify — `PoseSpeeds`, `speeds` field, `save_hand_calibration` |
| `src/arm101_hand/config/hand_poses.py` | Modify — `save_hand_poses` |
| `src/arm101_hand/config/__init__.py` | Modify — export `PoseSpeeds`, `save_hand_calibration`, `save_hand_poses` |
| `src/arm101_hand/hand/kinematics.py` | Modify — `finger_positions_to_servo_frame` |
| `src/arm101_hand/hand/pose_jog.py` | Create — pure jog state machine |
| `src/arm101_hand/gui/hand_panel.py` | Modify — use `save_hand_poses` + shared converter |
| `scripts/calibration/AmazingHand/AmazingHand_MotorReset.py` | Modify — typed load/save |
| `scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py` | Modify — typed load/save |
| `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py` | Modify — typed load/save |
| `scripts/calibration/AmazingHand/AmazingHand_FingerTest.py` | Modify — typed load |
| `scripts/calibration/AmazingHand/AmazingHand_SetPose.py` | Modify — typed load; speeds from YAML |
| `scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py` | Modify — typed load (keep demo speeds) |
| `scripts/calibration/AmazingHand/jog.py` | Create — the jog tool |
| `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml` | Modify — add `speeds:` block |
| `scripts/calibration/AmazingHand/README.md` | Modify — document `jog.py` + that scripts use the typed loader |
| `CLAUDE.md` | Modify — §4 workflow: add `jog.py` |
| `tests/unit/test_pose_jog.py` | Create — state machine |
| `tests/unit/test_hand_config_save.py` | Create — `save_hand_calibration` + `save_hand_poses` round-trips |
| `tests/unit/test_kinematics.py` (or new) | Modify/Create — `finger_positions_to_servo_frame` round-trip |

## 5. Testing

**Host unit (no bus):**

- `finger_positions_to_servo_frame`: odd ID passes through, even ID negated; round-trips with
  `even_id_inversion` + `decompose_finger`; matches a hand-computed example.
- `pose_jog`: key→action map; `apply_action` clamps each finger to its `DofLimits`; finger
  selection; step bounds; `h` vs `H` home.
- `save_hand_calibration`: load seed → save → reload → models equal; `speeds` defaults
  when absent.
- `save_hand_poses`: upsert a pose → save → reload → pose present and `sequences:` preserved.
- GUI: existing `hand_panel` tests still pass after the `_write_yaml` / `_snapshot_positions`
  refactor.

**Verification:** `ruff format` + `ruff check` clean; `mypy src`; `pytest -m 'not hardware'`.

**Operator hardware check (interactive, COM18):**

1. Re-run each refactored script (`MotorReset`, `MiddlePos`, `RangeCalib`, `FingerTest`,
   `SetPose open`/`close`, `FullHand_Test`) — confirm behavior unchanged and the writers still
   persist correctly.
2. Run `jog.py`: select each finger, jog within limits, `s` to save a throwaway pose, confirm
   it lands in `data/hand_config.yaml`. `q` releases torque.
3. Launch the GUI and confirm the jog-saved pose loads/drives correctly (shared store proof).

## 6. Out of scope (YAGNI)

- No CLI to *drive to* a named saved pose (the GUI loads poses; `SetPose` does open/close).
- No "load an existing pose as the jog starting point."
- `FullHand_Test` demo speeds stay in-script.
- No capture-by-demonstration (fingers don't back-drive).
- The compact inline YAML format is dropped in favor of block style.

## 7. Iron Laws touched

- **IL-2** — `references/` untouched; conversion logic is the repo's own.
- **IL-3** — servo IDs 1–8.
- **IL-4** — single bus owner; torque released in `finally`; port closed on exit.
- **IL-5** — `AmazingHand_calib_values.yaml` stays the canonical calibration store; jog.py
  never writes it.
- **IL-7** — one canonical hand-pose store (`data/hand_config.yaml`), one calibration store,
  one logical→servo converter, one pose-save path (GUI + jog.py share it).
