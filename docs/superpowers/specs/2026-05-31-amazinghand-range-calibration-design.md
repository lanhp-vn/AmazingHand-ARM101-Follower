# AmazingHand Range Calibration — Design

**Date:** 2026-05-31
**Status:** Approved (design); pending implementation plan
**Scope:** Add per-finger DOF motion-limit calibration to the AmazingHand, analogous to the SO-ARM101 follower's `range_min`/`range_max` sweep — discovered on real hardware, stored in version control, and consumed by the kinematics + audit scripts.

---

## 1. Problem

The SO-ARM101 follower calibration captures, per motor, a real motion envelope (`range_min`/`range_max` in raw encoder counts, found by sweeping each joint to its physical stops) plus a `homing_offset`. It lands in-tree at `scripts/calibration/so_arm101/<id>.json` (IL-5).

The AmazingHand has **no equivalent**. Its calibration (`scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`) stores only per-servo `middle_pos` (neutral offset, degrees). The actual motion extremes are **hardcoded magic numbers** scattered across the scripts and source:

- `AmazingHand_FingerTest.py` / `AmazingHand_MiddlePos_FingerCalib.py`: close `(+90, −90)`, open `(−30, +30)`.
- `AmazingHand_FullHand_Test.py`: `(−35, +35)`, `(−40, +40)`, `(−10, +70)`, `(−70, +10)`, etc.
- `src/arm101_hand/hand/kinematics.py`: `compose_finger`/`decompose_finger` default `servo_min=-40, servo_max=110, side_min=-40, side_max=40` — global reference values lifted from `references/AmazingHandControl`, identical for every finger and never measured on this hardware.

We want measured, per-finger min/max constraints for both degrees of freedom, stored in the calibration YAML and actually driving + bounding motion.

## 2. Mechanism & DOF model

Each finger is a parallel mechanism: two SCS0009 servos (odd ID = right, even ID = left; IL-3) combine into **two coordinated DOF**, already modeled in `kinematics.compose_finger`:

- **`base`** — flexion/extension. Logical frame, positive = close.
- **`side`** — abduction/adduction (sideways spread).

The composition (logical frame, **even-ID servo already inverted** via `even_id_inversion`):

```
pos1 = base − side      pos2 = base + side          # compose_finger
base = (pos1 + pos2)//2  side = (pos2 − pos1)//2     # decompose_finger
```

> **Frame correctness (a verified trap).** The `base`/`side` convention is the **logical** frame used by `kinematics`, `HandController`, the GUI, and `SafePark`. It is **not** the raw servo-frame pairing in the old scripts. Worked example — the isolation pose `Move_Index(-10, 70)` (servo frame): apply `even_id_inversion` to the even servo → logical `(−10, −70)` → `decompose_finger` → `base = −40, side = −30`. A naive servo-frame formula yields `side = +30` (sign-flipped). All stored limits use the logical `base`/`side` convention, and the implementation must verify the round-trip on an **asymmetric** spread pose (not just the symmetric close pose, which zeroes `side` and hides the error). `decompose_finger` uses floor `//2`; stored extremes must round-trip without off-by-one.

The four scalars per finger (`base_min`, `base_max`, `side_min`, `side_max`) **are** the motion envelope. Named poses derive from them: open = `compose_finger(base_min, 0)`, close = `compose_finger(base_max, 0)`, spreads = `side_min`/`side_max` at a chosen base. This satisfies the "calibrated poses" intent while storing a continuous envelope rather than fixed pose snapshots.

The envelope is treated as an independent `base × side` rectangle (matching the existing `compose_finger` model). The per-servo logical clamp is retained as a secondary corner-safety net, because a `(base_max, side_max)` corner can still drive one servo past its mechanical stop.

## 3. Decisions (from brainstorming)

| # | Decision | Choice |
|---|---|---|
| Q1 | What the params represent | Per-finger **motion poses / envelope** (hand fully assembled — Steps 1–2 already done). |
| Q2 | DOF scope | **Flexion + abduction** (full 2-DOF) per finger. |
| Q3 | Sweep method | **Active jog-to-limit** (torque ON, jog in steps, mark the limit). No backdrive. |
| Q4 | Deliverable scope | **Full**: new script + schema + refactor audit scripts + `src/` wiring. |
| Q5 | Granularity | **Per finger** (4 separate limit sets), mirroring the arm's per-joint calibration. |
| A/B | Schema strategy | **Approach B** — clean schema: required fields, `schema_version` bump, recalibrate. GUI redesign is separate and will consume the new limits. |

## 4. Design

### 4.1 Schema — `src/arm101_hand/config/calibration.py` (clean v2)

```python
class DofLimits(BaseModel):                 # logical frame, degrees rel. to middle
    model_config = ConfigDict(extra="forbid")
    base_min: float                         # full extension / open
    base_max: float                         # full flexion / close
    side_min: float                         # spread one way
    side_max: float                         # spread the other
    # validators: base_min < base_max; side_min < side_max

class ServoCalibration(BaseModel):          # unchanged
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1, le=8)
    middle_pos: float

class FingerCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servo_1: ServoCalibration
    servo_2: ServoCalibration
    limits: DofLimits                       # NEW, required

class HandCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(2, ge=2)    # NEW; v1 files (no field) now rejected
    com_port: str
    baudrate: int = Field(ge=9600)
    timeout: float = Field(ge=0.0, le=5.0)
    speed: int = Field(ge=1, le=7)
    fingers: dict[str, FingerCalibration]

    def middle_pos_by_id(self) -> dict[int, float]: ...   # unchanged
    def limits_by_finger(self) -> dict[str, DofLimits]: ...  # NEW convenience
```

Required fields + `extra="forbid"` mean the existing v1 YAML (which carries only `{id, middle_pos}` and no `schema_version`) is **rejected** by `load_hand_calibration` until recalibrated. Accepted per Approach B.

### 4.2 YAML shape — `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`

```yaml
schema_version: 2
com_port: COM18
baudrate: 1000000
timeout: 0.5
speed: 6
fingers:
  index:
    servo_1: {id: 1, middle_pos: 30}
    servo_2: {id: 2, middle_pos: -2}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
  middle:
    servo_1: {id: 3, middle_pos: -32}
    servo_2: {id: 4, middle_pos: 20}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
  ring:
    servo_1: {id: 5, middle_pos: 22}
    servo_2: {id: 6, middle_pos: 0}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
  thumb:
    servo_1: {id: 7, middle_pos: 0}
    servo_2: {id: 8, middle_pos: -12}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
```

(`limits` values above are placeholders; they are overwritten by the range-calibration run. `middle_pos` values are illustrative current values.)

### 4.3 New script — `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`

Active jog-to-limit REPL, **torque ON**, arrow-key driven. Windows-only (uses `msvcrt.getwch()` — acceptable; the project is win32/PowerShell/COM-port-centric). Pure logic (key→action mapping, jog/clamp math) is separated from the thin `msvcrt` read loop so the logic is unit-testable.

Flow:
1. Load YAML (`load_hand_calibration` or raw `yaml.safe_load` for write-back). Require `middle_pos` already calibrated (Step 3); warn otherwise.
2. Prompt for a finger (`index/middle/ring/thumb`).
3. Enable torque on both servos. Maintain a live `(base, side)` cursor starting at `(0, 0)` = calibrated middle. Each command recomposes via `compose_finger(base, side)` → `degrees_to_servo_radians(sid, pos, middle_pos)` → `write_goal_position` on both servos.

Controls:

| Key | Action |
|---|---|
| ↑ / ↓ | `base` + / − (flex toward close / extend toward open) |
| → / ← | `side` + / − (spread) |
| `[` / `]` | shrink / grow jog step (default 5°, min 1°) |
| `1` `2` `3` `4` | mark `base_min` / `base_max` / `side_min` / `side_max` = live cursor |
| `h` | home to `(0, 0)` |
| `s` | save limits for this finger to YAML |
| `q` / Ctrl+C | save and exit (torque off) |

Safety:
- Conservative default step; user marks the limit **just before** the mechanical stop, not at the buzz (per README guidance).
- After every move, re-read `present_load` on both servos (the register already read in `HandController.poll_state`) and reprint a one-line status: `base=… side=… step=… load=[L1,L2]`. Print a **⚠ warning** (no auto-stop) when load crosses a threshold — the cue to back off one step and mark there.
- Torque off on all touched servos in a `finally` block.

Write-back reuses the existing `InlineDict` + `yaml.SafeDumper` representer pattern (as in `MotorReset`/`MiddlePos_FingerCalib`) so the YAML stays human-readable; only the active finger's `limits` block changes.

This is **Step 4** in the calibration flow: ID burn → motor reset/horn install → middle-pos fine-tune → **range calibration** → audit (FingerTest) → full-hand demo.

### 4.4 Kinematics — `src/arm101_hand/hand/kinematics.py`

- `compose_finger` / `decompose_finger` gain `base_min` / `base_max` params so `base` is clamped to the calibrated flexion range (today only `side` and per-servo `pos` are clamped). Per-servo logical clamp retained as the final corner-safety net.
- Functions stay pure (plain float params — no `config` import; keeps the primitive/device layering clean). Callers pass the four numbers from `FingerCalibration.limits`.

### 4.5 Audit-script refactor (Q4)

- `AmazingHand_FingerTest.py` and `AmazingHand_FullHand_Test.py`: replace magic numbers with envelope-derived poses — closed = `compose_finger(base_max, 0)`, open = `compose_finger(base_min, 0)`, spreads from `side_min`/`side_max`, all read from the finger's calibrated `limits`.
- `AmazingHand_MiddlePos_FingerCalib.py`: keeps its middle-tuning purpose; its hardcoded `±90`/`±30` test cycle is likewise derived from the limits (or a safe fraction of them) so it never drives past a calibrated stop.

### 4.6 What breaks (accepted, Approach B)

The new required schema fields reject the old v1 YAML, so the **current** GUI's startup load (`HandController(calibration=…)`) fails until recalibration. Per the user's decision, the GUI is being redesigned and will consume per-finger limits then; `gui/hand_panel.py`'s `_SERVO_LOGICAL_MIN/_MAX` constants are left untouched and flagged as a to-do for the GUI redesign — **out of scope for this work**.

## 5. Testing (`04-testing-verification.md`)

Introduces the repo's first `tests/` directory (host-only, no bus):

- **Schema:** required-field enforcement, `base_min < base_max`, `side_min < side_max`, v1 YAML (no `schema_version`) rejected, `extra="forbid"` rejects unknown keys.
- **Kinematics:** `compose_finger`/`decompose_finger` round-trip on an **asymmetric** spread pose routed through `even_id_inversion` (the case that exposed the sign bug); base/side clamp behavior at and beyond limits; floor-`//2` round-trip has no off-by-one.
- **Jog logic:** pure key→action mapping (arrow → base/side delta, `[`/`]` → step change, `1`–`4` → mark) without `msvcrt`.
- Anything touching the bus is `@pytest.mark.hardware`. The jog REPL itself is manual/interactive and is not unit-tested; its extracted pure helpers are.

## 6. Documentation

- Add the range-calibration step to `scripts/calibration/AmazingHand/README.md` (new Step 4; renumber audit/demo to 5/6), including the arrow-key control table and the "mark before the stop" safety note.
- Refresh `CLAUDE.md` via the `doc_update` skill: correct the stale "`hand/` and `config/` are placeholders" claim (both are populated), note the new calibration step and the v2 schema, and the new `tests/` directory.

## 7. Out of scope

- GUI redesign / wiring per-finger limits into `gui/hand_panel.py` (separate effort; this work intentionally breaks the old GUI load).
- Teleop, policy, datasets.
- Per-servo (8-way) limit storage — superseded by the per-finger `base`/`side` model (Q5).
- Coupling between `base` and `side` (non-rectangular envelope) — explicitly modeled as an independent rectangle; the per-servo clamp guards the corners.

## 8. Iron Laws touched

- **IL-5** (calibration in-tree, version-controlled): new limits live in `AmazingHand_calib_values.yaml`, committed.
- **IL-3** (motor ID canon): odd/even servo roles unchanged.
- **IL-2** (`references/` read-only): kinematics already adapted from `AmazingHandControl` into `src/`; no reference edits.
- **IL-6** (atomic cross-device commits): hand-only change; no arm coupling.
