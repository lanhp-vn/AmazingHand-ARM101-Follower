# AmazingHand Config SSOT + Hand `jog.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all AmazingHand calibration scripts onto the existing typed `HandCalibration` loader (plus a new save function), and add a keyboard-jog `jog.py` that saves whole-hand poses into the existing `data/hand_config.yaml` store (shared with the GUI).

**Architecture:** Reuse the existing pydantic schemas (`HandCalibration`, `HandPoseConfig`) and add their missing save halves. Extract the GUI's logical→servo conversion *formula* into a shared `kinematics` helper. Add a pure, testable jog state machine (`hand/pose_jog.py`) and a thin Windows-`msvcrt` I/O script (`jog.py`) that drives the bus and saves poses through the shared save path.

**Tech Stack:** Python 3.12, pydantic v2, PyYAML, rustypot (`Scs0009PyController`), `msvcrt`, pytest, ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-06-05-amazinghand-jog-config-ssot-design.md`

**Conventions:** Read `docs/conventions/00-iron-laws.md` (esp. IL-3 IDs 1–8, IL-4 single bus owner + torque released in `finally`, IL-5 calib YAML is canonical, IL-7 single-source-of-truth) and `docs/conventions/07-kiss-simplicity.md`.

**Key facts the implementer needs:**
- Servo-ID → positions-array index: `positions[id-1]`. Fingers in canonical order `("index","middle","ring","thumb")` own servo IDs `(1,2),(3,4),(5,6),(7,8)`.
- `data/hand_config.yaml` poses store values in **servo/YAML frame** (even-ID pre-inverted), as `positions: list[int]` of length 8.
- The calib scripts' live drive uses `compose_finger`'s `(-40, 110)` per-servo defaults. The GUI's *save* uses `(-70, 90)`. jog.py uses `(-40, 110)` for both drive and save (saved == commanded; see spec §3.1 "Servo-bound note").
- All work happens on the current branch `refactor/arm-pose-store-kiss` (user's choice). Commit after each task.

---

### Task 1: Shared logical→servo converter in `kinematics`

**Files:**
- Modify: `src/arm101_hand/hand/kinematics.py`
- Modify: `src/arm101_hand/hand/__init__.py`
- Test: `tests/unit/test_hand_kinematics.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_hand_kinematics.py`:

```python
def test_finger_positions_odd_passthrough_even_negated():
    from arm101_hand.hand import finger_positions_to_servo_frame

    # Pure flexion (base=30, side=0): pos1=pos2=30.
    # Odd id (1): passthrough -> 30. Even id (2): negated -> -30.
    odd_val, even_val = finger_positions_to_servo_frame(1, 2, 30, 0)
    assert odd_val == 30
    assert even_val == -30


def test_finger_positions_round_trip():
    from arm101_hand.hand import (
        decompose_finger,
        even_id_inversion,
        finger_positions_to_servo_frame,
    )

    base, side = 25, 10
    odd_val, even_val = finger_positions_to_servo_frame(3, 4, base, side)
    # Invert the even-ID pre-inversion to get back logical pos1/pos2, then decompose.
    pos1 = int(even_id_inversion(3, float(odd_val)))
    pos2 = int(even_id_inversion(4, float(even_val)))
    got_base, got_side = decompose_finger(pos1, pos2)
    assert (got_base, got_side) == (base, side)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_hand_kinematics.py -k finger_positions -v`
Expected: FAIL — `ImportError: cannot import name 'finger_positions_to_servo_frame'`.

- [ ] **Step 3: Implement the converter** — append to `src/arm101_hand/hand/kinematics.py` (after `compose_finger`):

```python
def finger_positions_to_servo_frame(
    odd_id: int,
    even_id: int,
    base: int,
    side: int,
    servo_min: int = -40,
    servo_max: int = 110,
) -> tuple[int, int]:
    """``(base, side)`` logical → ``(odd_servo_val, even_servo_val)`` in YAML/servo frame.

    Composes to a symmetric servo pair (clamped to ``[servo_min, servo_max]``), then
    applies even-ID pre-inversion. The caller places the results at ``out[odd_id - 1]``
    and ``out[even_id - 1]`` of an 8-long positions array. This is the single source for
    the conversion formula the GUI's ``hand_panel._snapshot_positions`` used to inline.
    """
    pos1, pos2 = compose_finger(base, side, servo_min, servo_max)
    return (
        int(even_id_inversion(odd_id, float(pos1))),
        int(even_id_inversion(even_id, float(pos2))),
    )
```

- [ ] **Step 4: Export it** — in `src/arm101_hand/hand/__init__.py`, add `finger_positions_to_servo_frame` to the `from .kinematics import (...)` block and to `__all__`:

```python
from .kinematics import (
    MAX_NAME_LEN,
    clamp,
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    even_id_inversion,
    finger_positions_to_servo_frame,
    servo_radians_to_degrees,
    validate_pose_name,
)
```

and add `"finger_positions_to_servo_frame",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/unit/test_hand_kinematics.py -k finger_positions -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/hand/kinematics.py src/arm101_hand/hand/__init__.py tests/unit/test_hand_kinematics.py
git commit -m "feat(hand): shared finger_positions_to_servo_frame converter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `PoseSpeeds` + `save_hand_calibration` in calibration schema

**Files:**
- Modify: `src/arm101_hand/config/calibration.py`
- Modify: `src/arm101_hand/config/__init__.py`
- Modify: `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`
- Test: `tests/unit/test_hand_config_save.py` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_hand_config_save.py`:

```python
from pathlib import Path

from arm101_hand.config import (
    HandCalibration,
    PoseSpeeds,
    load_hand_calibration,
    save_hand_calibration,
)

SEED = Path("scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml")


def test_pose_speeds_default_when_absent():
    raw = {
        "schema_version": 2,
        "com_port": "COM18",
        "baudrate": 1000000,
        "timeout": 0.5,
        "speed": 4,
        "fingers": {
            "index": {
                "servo_1": {"id": 1, "middle_pos": 0},
                "servo_2": {"id": 2, "middle_pos": 0},
                "limits": {"base_min": -20, "base_max": 70, "side_min": -40, "side_max": 35},
            }
        },
    }
    cfg = HandCalibration.model_validate(raw)
    assert cfg.speeds == PoseSpeeds(open=5, close=3)


def test_calibration_round_trip(tmp_path):
    cfg = load_hand_calibration(SEED)
    out = tmp_path / "calib.yaml"
    save_hand_calibration(out, cfg)
    reloaded = load_hand_calibration(out)
    assert reloaded.model_dump() == cfg.model_dump()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_hand_config_save.py -v`
Expected: FAIL — `ImportError: cannot import name 'PoseSpeeds'`.

- [ ] **Step 3: Implement schema + save** — in `src/arm101_hand/config/calibration.py`:

(a) Add `import os` at the top (after `from pathlib import Path`).

(b) Add the `PoseSpeeds` model (before `HandCalibration`):

```python
class PoseSpeeds(BaseModel):
    """SetPose's open/close motion speeds (1-7 scale), distinct from the jog ``speed``."""

    model_config = ConfigDict(extra="forbid")

    open: int = Field(default=5, ge=1, le=7)  # extension (quicker)
    close: int = Field(default=3, ge=1, le=7)  # flexion (gentler settle)
```

(c) In `HandCalibration`, add the `speeds` field right after the `speed` field:

```python
    speed: int = Field(ge=1, le=7)
    speeds: PoseSpeeds = Field(default_factory=PoseSpeeds)
    fingers: dict[str, FingerCalibration]
```

(d) Add the save function at the end of the file:

```python
def save_hand_calibration(path: Path, config: HandCalibration) -> None:
    """Write a ``HandCalibration`` to YAML atomically (tmp file + ``os.replace``).

    The whole model is dumped, so a load-modify-save round-trip preserves every field;
    the per-finger partial writes the calib scripts used to do by hand are unnecessary.
    Block-style YAML (no custom inline dumper) — matches the arm's ``save_arm_poses``.
    """
    payload = config.model_dump(mode="python")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Export** — in `src/arm101_hand/config/__init__.py`, update the calibration import line and `__all__`:

```python
from .calibration import (
    FINGER_NAMES,
    DofLimits,
    HandCalibration,
    PoseSpeeds,
    load_hand_calibration,
    save_hand_calibration,
)
```

Add `"PoseSpeeds",` and `"save_hand_calibration",` to `__all__`.

- [ ] **Step 5: Add `speeds:` to the seed YAML** — in `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`, insert a `speeds:` block immediately after the `speed: 4` line:

```yaml
speed: 4
speeds:
  open: 5
  close: 3
fingers:
```

(Leave the existing `fingers:` block unchanged.)

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/unit/test_hand_config_save.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/calibration.py src/arm101_hand/config/__init__.py scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml tests/unit/test_hand_config_save.py
git commit -m "feat(config): PoseSpeeds + save_hand_calibration; seed speeds block

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `save_hand_poses` + GUI refactor onto shared save/converter

**Files:**
- Modify: `src/arm101_hand/config/hand_poses.py`
- Modify: `src/arm101_hand/config/__init__.py`
- Modify: `src/arm101_hand/gui/hand_panel.py`
- Test: `tests/unit/test_hand_config_save.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_hand_config_save.py`:

```python
def test_save_hand_poses_preserves_sequences(tmp_path):
    from arm101_hand.config import (
        HandPose,
        HandPoseConfig,
        HandSequence,
        load_hand_poses,
        save_hand_poses,
    )

    cfg = HandPoseConfig(
        poses={"grip": HandPose(positions=[1, 2, 3, 4, 5, 6, 7, 8])},
        sequences={"wave": HandSequence(steps=["SLEEP:1s"])},
    )
    out = tmp_path / "hand_config.yaml"
    save_hand_poses(out, cfg)
    reloaded = load_hand_poses(out)
    assert reloaded.poses["grip"].positions == [1, 2, 3, 4, 5, 6, 7, 8]
    assert reloaded.sequences["wave"].steps == ["SLEEP:1s"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_hand_config_save.py -k sequences -v`
Expected: FAIL — `ImportError: cannot import name 'save_hand_poses'`.

- [ ] **Step 3: Implement `save_hand_poses`** — in `src/arm101_hand/config/hand_poses.py`:

(a) Add `import os` after `from pathlib import Path`.

(b) Add at the end of the file:

```python
def save_hand_poses(path: Path, config: HandPoseConfig) -> None:
    """Write a ``HandPoseConfig`` to YAML atomically (tmp + ``os.replace``).

    The whole model is dumped, so ``sequences:`` and every other pose survive a
    load-modify-save round-trip. Shared by the GUI pose manager and ``jog.py``.
    """
    payload = config.model_dump(mode="python")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Export** — in `src/arm101_hand/config/__init__.py`, update the hand_poses import + `__all__`:

```python
from .hand_poses import (
    POSITIONS_LEN,
    HandPose,
    HandPoseConfig,
    HandSequence,
    load_hand_poses,
    save_hand_poses,
)
```

Add `"save_hand_poses",` to `__all__`.

- [ ] **Step 5: Refactor the GUI to use the shared save + converter** — in `src/arm101_hand/gui/hand_panel.py`:

(a) Add `finger_positions_to_servo_frame` to the `from arm101_hand.hand.kinematics import (...)` block (keep `compose_finger`, `decompose_finger`, `even_id_inversion` — `decompose_finger`/`even_id_inversion` are still used by `_apply_positions`; `compose_finger` may become unused after this edit — if ruff flags it, remove it).

(b) Add `save_hand_poses` to the `from arm101_hand.config import (...)` block.

(c) Replace `_snapshot_positions` (currently lines ~473-486) body with:

```python
    def _snapshot_positions(self) -> list[int]:
        """Build the YAML positions array from the current sliders.

        Slider state is in logical frame; the YAML stores the servo frame
        (even-ID pre-inverted). Delegates to the shared kinematics converter.
        """
        out = [0] * 8
        for row in self._fingers:
            odd_val, even_val = finger_positions_to_servo_frame(
                row.odd_id, row.even_id, row.base_value(), row.side_value(),
                _SERVO_LOGICAL_MIN, _SERVO_LOGICAL_MAX,
            )
            out[row.odd_id - 1] = odd_val
            out[row.even_id - 1] = even_val
        return out
```

(d) Replace `_write_yaml` (currently lines ~499-504) body with:

```python
    def _write_yaml(self) -> None:
        """Atomic write to ``self._poses_path`` via the shared save path."""
        save_hand_poses(self._poses_path, self._poses)
```

(e) If `import os` / `import yaml` become unused in `hand_panel.py` after (d), remove them (ruff will report `F401`).

- [ ] **Step 6: Run to verify pass + GUI regression**

Run: `uv run pytest tests/unit/test_hand_config_save.py -v`
Expected: PASS (3 passed).
Run: `uv run pytest -m 'not hardware' -k "hand" -v`
Expected: all existing hand tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/hand_poses.py src/arm101_hand/config/__init__.py src/arm101_hand/gui/hand_panel.py tests/unit/test_hand_config_save.py
git commit -m "feat(config): save_hand_poses; GUI uses shared save + converter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Refactor `AmazingHand_MotorReset.py` onto the typed loader

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_MotorReset.py`

No new unit test (hardware I/O script). Verification = import + ruff + the hardware check at the end.

- [ ] **Step 1: Replace the imports + delete the local YAML helpers.** Replace the block from `import contextlib` through the `yaml.SafeDumper.add_representer(...)` line and the `save_config` function (current lines ~10-45) with:

```python
import contextlib
import time
from pathlib import Path

import numpy as np
from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration, save_hand_calibration

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")
```

(Delete `import yaml`, the `InlineDict` class, `_inline_dict_representer`, the `add_representer` call, and `save_config`. Keep the `WARNING` string, `prompt_yes`, `prompt_finger`, and `reset_motor` unchanged.)

- [ ] **Step 2: Rewrite `main()`** to use the typed model:

```python
def main():
    print(WARNING)
    if not prompt_yes():
        print("Aborted.")
        return

    cfg = load_hand_calibration(YAML_PATH)

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )
    speed = cfg.speed

    touched = set()
    try:
        while True:
            finger = prompt_finger()
            if finger is None:
                break
            block = cfg.fingers[finger]
            id1 = block.servo_1.id
            id2 = block.servo_2.id
            print(f"\n--- {finger} finger: servo_1={id1}, servo_2={id2} ---")
            reset_motor(c, id1, speed)
            touched.add(id1)
            reset_motor(c, id2, speed)
            touched.add(id2)
            block.servo_1.middle_pos = 0
            block.servo_2.middle_pos = 0
            save_hand_calibration(YAML_PATH, cfg)
            print(f"  {finger}.middle_pos -> 0, YAML saved")
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        for sid in touched:
            with contextlib.suppress(Exception):
                c.write_torque_enable(sid, 0)
```

- [ ] **Step 3: Verify it parses + lints**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/AmazingHand_MotorReset.py').read())"`
Expected: no output (parses).
Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_MotorReset.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_MotorReset.py
git commit -m "refactor(hand): MotorReset uses typed HandCalibration load/save

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Refactor `AmazingHand_MiddlePos_FingerCalib.py` onto the typed loader

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`

- [ ] **Step 1: Replace imports + delete local YAML helpers.** Replace the block from `import time` through `save_config` (current lines ~11-52) with:

```python
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration, save_hand_calibration
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")
EXIT_TOKENS = ("save", "q", "quit")
```

(Delete `import yaml`, `InlineDict`, `_inline_dict_representer`, `add_representer`, `load_config`, `save_config`. Keep `prompt_finger`, `prompt_int`, `_send_pose`, `close_finger`, `open_finger` unchanged — they already take `limits` as a mapping; pass `block.limits` which is a `DofLimits` and supports attribute access, so update `close_finger`/`open_finger` to use `limits.base_max` / `limits.base_min`.)

- [ ] **Step 2: Update `close_finger`/`open_finger`** to attribute access:

```python
def close_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits.base_max, 0, speed)


def open_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits.base_min, 0, speed)
```

- [ ] **Step 3: Rewrite `main()`**:

```python
def main():
    cfg = load_hand_calibration(YAML_PATH)
    finger = prompt_finger()
    block = cfg.fingers[finger]
    id1 = block.servo_1.id
    id2 = block.servo_2.id
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    limits = block.limits
    speed = cfg.speed

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    try:
        while True:
            print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] MiddlePos_1={mp1}, MiddlePos_2={mp2}")
            close_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(3)
            open_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(1)

            new_mp1, exit_requested = prompt_int("MiddlePos_1", mp1)
            if exit_requested:
                break
            mp1 = new_mp1

            new_mp2, exit_requested = prompt_int("MiddlePos_2", mp2)
            if exit_requested:
                break
            mp2 = new_mp2
    except KeyboardInterrupt:
        print("\n^C -- saving and exiting")
    finally:
        block.servo_1.middle_pos = mp1
        block.servo_2.middle_pos = mp2
        save_hand_calibration(YAML_PATH, cfg)
        print(f"Saved to {YAML_PATH}")
        try:
            c.write_torque_enable(id1, 0)
            c.write_torque_enable(id2, 0)
        except Exception as e:
            print(f"warning: failed to disable torque: {e}")
```

- [ ] **Step 4: Verify parse + lint**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py').read())"`
Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`
Expected: parses; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py
git commit -m "refactor(hand): MiddlePos calib uses typed HandCalibration load/save

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Refactor `AmazingHand_RangeCalib.py` onto the typed loader

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`

This writer keeps its own `limits_error` dict-guard (so a temporarily-invalid mark never reaches `DofLimits` construction), then writes a fresh `DofLimits` on save.

- [ ] **Step 1: Replace imports + delete local YAML helpers.** Replace lines ~20-68 (from `import msvcrt` through the `save_config` function) with:

```python
import msvcrt
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import DofLimits, load_hand_calibration, save_hand_calibration
from arm101_hand.hand import (
    apply_action,
    compose_finger,
    degrees_to_servo_radians,
    format_status,
    key_to_action,
    load_warning,
)
from arm101_hand.hand.range_calib import JogState

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")
```

(Delete `import yaml`, `InlineDict`, `_inline_dict_representer`, `add_representer`, `load_config`, `save_config`. Keep `limits_error`, `prompt_finger`, `read_key`, `write_cursor`, `read_loads` unchanged.)

- [ ] **Step 2: Rewrite `main()`** — read limits via `model_dump()` into a working dict, save by constructing a fresh `DofLimits`:

```python
def main():
    cfg = load_hand_calibration(YAML_PATH)
    finger = prompt_finger()
    block = cfg.fingers[finger]
    id1 = block.servo_1.id
    id2 = block.servo_2.id
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    speed = cfg.speed
    limits = block.limits.model_dump()  # working dict; start from current stored limits

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    state = JogState()
    print(__doc__)
    print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] current limits: {limits}")
    write_cursor(c, id1, id2, mp1, mp2, state, speed)

    try:
        while True:
            key = read_key()
            action = key_to_action(key)
            if action is None:
                continue
            if action == "quit":
                break
            if action == "save":
                err = limits_error(limits)
                if err:
                    print(f"  NOT saved -- invalid limits: {err}")
                else:
                    block.limits = DofLimits(**limits)
                    save_hand_calibration(YAML_PATH, cfg)
                    print(f"  saved limits {limits} for {finger}")
                continue

            state, mark = apply_action(state, action)
            if mark is not None:
                name, value = mark
                limits[name] = value
                print(f"  marked {name} = {value}")
            else:
                write_cursor(c, id1, id2, mp1, mp2, state, speed)

            load1, load2 = read_loads(c, id1, id2)
            print("  " + format_status(state, load1, load2))
            warn = load_warning(load1, load2)
            if warn:
                print("  " + warn)
    except KeyboardInterrupt:
        print("\n^C -- saving and exiting")
    finally:
        err = limits_error(limits)
        if err:
            print(f"NOT saving -- invalid limits: {err}. Re-run and mark valid endpoints.")
        else:
            block.limits = DofLimits(**limits)
            save_hand_calibration(YAML_PATH, cfg)
            print(f"Saved limits for {finger} to {YAML_PATH}")
        for sid in (id1, id2):
            try:
                c.write_torque_enable(sid, 0)
            except Exception as e:
                print(f"warning: failed to disable torque on {sid}: {e}")
```

- [ ] **Step 3: Verify parse + lint**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py').read())"`
Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`
Expected: parses; `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py
git commit -m "refactor(hand): RangeCalib uses typed HandCalibration load/save

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Refactor `AmazingHand_FingerTest.py` onto the typed loader (read-only)

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`

- [ ] **Step 1: Replace imports.** Replace lines ~18-27 (from `import time` through the `YAML_PATH` line) with:

```python
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"
```

(Delete `import yaml`. Keep `VALID_FINGERS`, `prompt_finger`, `_send_pose` unchanged.)

- [ ] **Step 2: Update `build_sequence`** to attribute access:

```python
def build_sequence(limits):
    """Ordered (label, base, side, dwell_s) poses covering both DOF.

    Flexion is tested first at neutral spread, then abduction is tested at
    neutral flexion, with a brief return to neutral between the two phases.
    """
    return [
        ("close  (full flexion)", limits.base_max, 0, 3.0),
        ("open   (full extension)", limits.base_min, 0, 2.0),
        ("neutral", 0, 0, 1.0),
        ("abduct (spread +)", 0, limits.side_max, 2.0),
        ("adduct (spread -)", 0, limits.side_min, 2.0),
        ("neutral", 0, 0, 1.0),
    ]
```

- [ ] **Step 3: Rewrite `main()`**:

```python
def main():
    cfg = load_hand_calibration(YAML_PATH)

    finger = prompt_finger()
    block = cfg.fingers[finger]
    id1 = block.servo_1.id
    id2 = block.servo_2.id
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    limits = block.limits
    speed = cfg.speed

    sequence = build_sequence(limits)

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] limits={limits.model_dump()}")
    print("cycling flexion + abduction (from calibrated limits) -- Ctrl+C to stop")

    try:
        while True:
            for label, base, side, dwell in sequence:
                print(f"  -> {label}  (base={base}, side={side})")
                _send_pose(c, id1, id2, mp1, mp2, base, side, speed)
                time.sleep(dwell)
    except KeyboardInterrupt:
        print("\n^C -- stopping")
    finally:
        try:
            c.write_torque_enable(id1, 0)
            c.write_torque_enable(id2, 0)
        except Exception as e:
            print(f"warning: failed to disable torque: {e}")
```

- [ ] **Step 4: Verify parse + lint**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/AmazingHand_FingerTest.py').read())"`
Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`
Expected: parses; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_FingerTest.py
git commit -m "refactor(hand): FingerTest uses typed HandCalibration loader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Refactor `AmazingHand_SetPose.py` — typed loader + speeds from YAML

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_SetPose.py`

- [ ] **Step 1: Update the module docstring's speed note + delete the speed constants.** Replace the `OPEN_SPEED`/`CLOSE_SPEED` constants block (current lines ~36-39) — delete them entirely. They now come from `cfg.speeds`.

- [ ] **Step 2: Replace imports.** Replace lines ~21-32 (from `import contextlib` through the `YAML_PATH` line) with:

```python
import contextlib
import sys
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_POSES = ("open", "close")
```

(Delete `import yaml`. Keep `prompt_pose`, `resolve_pose`, `pose_base` unchanged.)

- [ ] **Step 3: Update `move_finger`** to attribute access on the finger block:

```python
def move_finger(c, block, base, side, speed):
    id1 = block.servo_1.id
    id2 = block.servo_2.id
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    time.sleep(0.0002)
    c.write_goal_speed(id2, speed)
    time.sleep(0.0002)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.005)
```

- [ ] **Step 4: Update `pose_base`** to attribute access:

```python
def pose_base(limits, pose):
    """Target ``base`` for the pose; spread stays neutral (side = 0)."""
    return limits.base_max if pose == "close" else limits.base_min
```

- [ ] **Step 5: Rewrite `main()`** — load typed config, read speeds from `cfg.speeds`:

```python
def main():
    pose = resolve_pose(sys.argv)

    cfg = load_hand_calibration(YAML_PATH)
    speed = cfg.speeds.close if pose == "close" else cfg.speeds.open
    fingers = cfg.fingers

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )

    for block in fingers.values():
        c.write_torque_enable(block.servo_1.id, 1)
        c.write_torque_enable(block.servo_2.id, 1)

    print(f"Setting hand to '{pose}' (all fingers, neutral spread)...")
    try:
        for block in fingers.values():
            move_finger(c, block, pose_base(block.limits, pose), 0, speed)
        print(f"Hand held at '{pose}' under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- releasing")
    finally:
        for block in fingers.values():
            for servo in (block.servo_1, block.servo_2):
                with contextlib.suppress(Exception):
                    c.write_torque_enable(servo.id, 0)
```

- [ ] **Step 6: Verify parse + lint**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/AmazingHand_SetPose.py').read())"`
Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_SetPose.py`
Expected: parses; `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_SetPose.py
git commit -m "refactor(hand): SetPose uses typed loader; speeds from YAML

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Refactor `AmazingHand_FullHand_Test.py` onto the typed loader (keep demo speeds)

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`

Demo speeds (`MaxSpeed`, `CloseSpeed`) stay as in-script constants per spec (YAGNI).

- [ ] **Step 1: Replace imports + module-level config load.** Replace lines ~17-43 (from `import contextlib` through the `Scs0009PyController(...)` constructor) with:

```python
import contextlib
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

# Demo-specific speeds, independent of YAML 'speed' (which is the calibration speed).
MaxSpeed = 7
CloseSpeed = 3

_cfg = load_hand_calibration(YAML_PATH)
_FINGERS = _cfg.fingers

c = Scs0009PyController(
    serial_port=_cfg.com_port,
    baudrate=_cfg.baudrate,
    timeout=_cfg.timeout,
)
```

(Delete `import yaml`.)

- [ ] **Step 2: Update `_move` and `_limits`** to attribute access:

```python
def _move(name, base, side, speed):
    block = _FINGERS[name]
    id1 = block.servo_1.id
    id2 = block.servo_2.id
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    time.sleep(0.0002)
    c.write_goal_speed(id2, speed)
    time.sleep(0.0002)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.005)


def _limits(name):
    return _FINGERS[name].limits
```

- [ ] **Step 3: Update the pose helpers + `main()` to attribute access.** `_close`/`_open`/`_spread_pose` read `lim.base_max` etc.; `main()`'s torque loop reads `finger.servo_1.id`:

```python
def _close(name):
    return _limits(name).base_max, 0


def _open(name):
    return _limits(name).base_min, 0


def _spread_pose(name, frac):
    # An isolation pose: nearly open, spread by ``frac`` of the side range.
    lim = _limits(name)
    side = int((lim.side_max if frac > 0 else lim.side_min) * abs(frac))
    return lim.base_min, side
```

And in `main()`, replace the two torque loops (start + `finally`):

```python
def main():
    for finger in _FINGERS.values():
        c.write_torque_enable(finger.servo_1.id, 1)
        c.write_torque_enable(finger.servo_2.id, 1)

    print("Running AmazingHand full-hand demo (right hand, one cycle)...")
    try:
        CloseHand()
        time.sleep(2)

        OpenHand()
        time.sleep(1)

        IndexOnly()
        time.sleep(1.5)

        MiddleOnly()
        time.sleep(1.5)

        RingOnly()
        time.sleep(1.5)

        ThumbOnly()
        time.sleep(1.5)

        InitialPose()
        time.sleep(1)
        print("Cycle complete -- holding middle (initial) pose under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- aborting")
    finally:
        for finger in _FINGERS.values():
            for servo in (finger.servo_1, finger.servo_2):
                with contextlib.suppress(Exception):
                    c.write_torque_enable(servo.id, 0)
```

(`InitialPose`, `OpenHand`, `CloseHand`, `_isolate`, `IndexOnly`/etc. and `_MOVERS` are unchanged.)

- [ ] **Step 4: Verify parse + lint**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py').read())"`
Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`
Expected: parses; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py
git commit -m "refactor(hand): FullHand_Test uses typed loader (demo speeds stay)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Pure jog state machine `hand/pose_jog.py`

**Files:**
- Create: `src/arm101_hand/hand/pose_jog.py`
- Test: `tests/unit/test_pose_jog.py` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_pose_jog.py`:

```python
from arm101_hand.config import DofLimits
from arm101_hand.hand.pose_jog import (
    FINGERS,
    HandJogState,
    apply_action,
    key_to_action,
)

LIMITS = {
    "index": DofLimits(base_min=-20, base_max=70, side_min=-40, side_max=35),
    "middle": DofLimits(base_min=-35, base_max=65, side_min=-20, side_max=15),
    "ring": DofLimits(base_min=-35, base_max=65, side_min=-25, side_max=20),
    "thumb": DofLimits(base_min=-40, base_max=100, side_min=-55, side_max=50),
}


def test_key_to_action_map():
    assert key_to_action("2") == "select_middle"
    assert key_to_action("UP") == "base+"
    assert key_to_action("H") == "home_all"
    assert key_to_action("z") is None


def test_select_changes_active():
    state = apply_action(HandJogState(), "select_thumb", LIMITS)
    assert state.active == "thumb"


def test_base_clamps_to_calibrated_max():
    state = HandJogState(active="index", step=15)
    for _ in range(20):  # would reach 300 unclamped
        state = apply_action(state, "base+", LIMITS)
    assert state.fingers["index"][0] == 70  # index base_max


def test_side_clamps_to_calibrated_min():
    state = HandJogState(active="middle", step=15)
    for _ in range(20):
        state = apply_action(state, "side-", LIMITS)
    assert state.fingers["middle"][1] == -20  # middle side_min


def test_step_bounds():
    state = HandJogState(step=1)
    state = apply_action(state, "step-", LIMITS)
    assert state.step == 1  # STEP_MIN
    state = HandJogState(step=15)
    state = apply_action(state, "step+", LIMITS)
    assert state.step == 15  # STEP_MAX


def test_home_active_only():
    state = HandJogState(active="index")
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "select_thumb", LIMITS)
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "home", LIMITS)  # homes thumb only
    assert state.fingers["thumb"] == (0, 0)
    assert state.fingers["index"][0] > 0


def test_home_all():
    state = HandJogState(active="index")
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "select_thumb", LIMITS)
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "home_all", LIMITS)
    assert all(state.fingers[f] == (0, 0) for f in FINGERS)


def test_save_and_quit_are_state_noops():
    state = HandJogState(active="ring")
    assert apply_action(state, "save", LIMITS) == state
    assert apply_action(state, "quit", LIMITS) == state
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_pose_jog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.hand.pose_jog'`.

- [ ] **Step 3: Implement `src/arm101_hand/hand/pose_jog.py`**:

```python
"""Pure multi-finger jog state machine for ``scripts/calibration/AmazingHand/jog.py``.

No hardware, no ``msvcrt`` — the testable core of the hand jog tool. The script reads
raw keys, maps them via ``key_to_action``, advances a ``HandJogState`` via
``apply_action`` (clamping the active finger to its calibrated ``DofLimits``), then
composes each finger's ``(base, side)`` cursor into servo commands.

Frame: ``base``/``side`` are the logical DOF (see ``hand.kinematics``). Unlike
``range_calib`` (single finger, generous discovery envelope, mark actions), this jogs
*all four* fingers within their already-measured limits and saves a whole-hand pose.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from arm101_hand.config import DofLimits
from arm101_hand.hand.kinematics import clamp
from arm101_hand.hand.range_calib import STEP_DEFAULT, STEP_MAX, STEP_MIN

FINGERS: tuple[str, ...] = ("index", "middle", "ring", "thumb")

_KEY_ACTIONS: dict[str, str] = {
    "1": "select_index",
    "2": "select_middle",
    "3": "select_ring",
    "4": "select_thumb",
    "UP": "base+",
    "DOWN": "base-",
    "RIGHT": "side+",
    "LEFT": "side-",
    "[": "step-",
    "]": "step+",
    "h": "home",
    "H": "home_all",
    "s": "save",
    "q": "quit",
}

_SELECT: dict[str, str] = {
    "select_index": "index",
    "select_middle": "middle",
    "select_ring": "ring",
    "select_thumb": "thumb",
}


def _neutral_fingers() -> dict[str, tuple[int, int]]:
    return {name: (0, 0) for name in FINGERS}


@dataclass(frozen=True)
class HandJogState:
    """Immutable cursor: active finger, shared step, and each finger's (base, side)."""

    active: str = "index"
    step: int = STEP_DEFAULT
    fingers: dict[str, tuple[int, int]] = field(default_factory=_neutral_fingers)


def key_to_action(key: str) -> str | None:
    """Map a normalized key token to an action name, or ``None`` if unmapped."""
    return _KEY_ACTIONS.get(key)


def apply_action(
    state: HandJogState,
    action: str,
    limits_by_finger: dict[str, DofLimits],
) -> HandJogState:
    """Apply an action, clamping the active finger to its calibrated ``DofLimits``.

    ``save`` / ``quit`` / unknown actions are no-ops on state (handled by the script).
    """
    if action in _SELECT:
        return replace(state, active=_SELECT[action])
    if action == "step+":
        return replace(state, step=int(clamp(state.step + 1, STEP_MIN, STEP_MAX)))
    if action == "step-":
        return replace(state, step=int(clamp(state.step - 1, STEP_MIN, STEP_MAX)))
    if action == "home":
        fingers = dict(state.fingers)
        fingers[state.active] = (0, 0)
        return replace(state, fingers=fingers)
    if action == "home_all":
        return replace(state, fingers=_neutral_fingers())
    if action in ("base+", "base-", "side+", "side-"):
        base, side = state.fingers[state.active]
        lim = limits_by_finger[state.active]
        if action == "base+":
            base = clamp(base + state.step, lim.base_min, lim.base_max)
        elif action == "base-":
            base = clamp(base - state.step, lim.base_min, lim.base_max)
        elif action == "side+":
            side = clamp(side + state.step, lim.side_min, lim.side_max)
        else:  # side-
            side = clamp(side - state.step, lim.side_min, lim.side_max)
        fingers = dict(state.fingers)
        fingers[state.active] = (int(base), int(side))
        return replace(state, fingers=fingers)
    return state  # save / quit / unmapped


def format_hand_status(state: HandJogState) -> str:
    """One-line multi-finger status; the active finger is marked with ``*``."""
    parts = []
    for name in FINGERS:
        base, side = state.fingers[name]
        mark = "*" if name == state.active else " "
        parts.append(f"{mark}{name[:3]} b={base:>4} s={side:>4}")
    return f"step={state.step:>2} | " + " | ".join(parts)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_pose_jog.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/hand/pose_jog.py tests/unit/test_pose_jog.py
git commit -m "feat(hand): pure multi-finger jog state machine (pose_jog)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The `jog.py` I/O script

**Files:**
- Create: `scripts/calibration/AmazingHand/jog.py`

Hardware I/O — no unit test; verified by parse + lint + the hardware check (Task 12).

- [ ] **Step 1: Create `scripts/calibration/AmazingHand/jog.py`**:

```python
"""AmazingHand Jog & Save-Pose tool — arrow-key jog of all fingers, then save.

Move all four fingers with the keyboard (torque ON the whole time), then save the
resulting whole-hand pose by name into ``data/hand_config.yaml`` — the same store the
unified GUI's pose manager uses. Mirrors the arm's ``so_arm101/jog.py``.

Config (serial + per-finger middle_pos + limits) is read from the canonical
``AmazingHand_calib_values.yaml`` (IL-5: this script never writes it).

Controls (torque ON the whole time):
  1 2 3 4       select active finger (index / middle / ring / thumb)
  Up / Down     active finger base + / -   (flex / extend)
  Right / Left  active finger side + / -   (spread)
  [ / ]         shrink / grow the jog step
  h             home the active finger to (0, 0) neutral
  H             home ALL fingers to (0, 0) neutral
  s             save the current whole-hand pose (prompts for a name)
  q / Ctrl+C    release torque on all 8 servos and exit

Each finger's cursor is clamped to its calibrated base/side limits, so you can only
build poses inside the known-good envelope. Windows-only: uses ``msvcrt`` for raw keys.
"""

import msvcrt
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import (
    HandPose,
    load_hand_calibration,
    load_hand_poses,
    save_hand_poses,
)
from arm101_hand.hand import (
    compose_finger,
    degrees_to_servo_radians,
    finger_positions_to_servo_frame,
    load_warning,
    validate_pose_name,
)
from arm101_hand.hand.pose_jog import (
    FINGERS,
    HandJogState,
    apply_action,
    format_hand_status,
    key_to_action,
)

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"
# scripts/calibration/AmazingHand/jog.py -> repo root is parents[2].
REPO_ROOT = SCRIPT_DIR.parents[2]
HAND_CONFIG_PATH = REPO_ROOT / "data" / "hand_config.yaml"


def read_key():
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def read_loads(c, id1, id2):
    def _scalar(v):
        return int(v[0]) if isinstance(v, (list, tuple)) else int(v)

    return _scalar(c.read_present_load(id1)), _scalar(c.read_present_load(id2))


def drive_finger(c, block, base, side, speed):
    """Command one finger's two servos to (base, side), clamped to its limits."""
    lim = block.limits
    pos1, pos2 = compose_finger(
        base,
        side,
        base_min=lim.base_min,
        base_max=lim.base_max,
        side_min=lim.side_min,
        side_max=lim.side_max,
    )
    c.write_goal_speed(block.servo_1.id, speed)
    c.write_goal_speed(block.servo_2.id, speed)
    c.write_goal_position(block.servo_1.id, degrees_to_servo_radians(block.servo_1.id, pos1, block.servo_1.middle_pos))
    c.write_goal_position(block.servo_2.id, degrees_to_servo_radians(block.servo_2.id, pos2, block.servo_2.middle_pos))


def snapshot_positions(cfg, state):
    """Whole-hand (base, side) cursor -> 8-int servo-frame positions array."""
    out = [0] * 8
    for name in FINGERS:
        block = cfg.fingers[name]
        base, side = state.fingers[name]
        odd_val, even_val = finger_positions_to_servo_frame(
            block.servo_1.id, block.servo_2.id, base, side
        )
        out[block.servo_1.id - 1] = odd_val
        out[block.servo_2.id - 1] = even_val
    return out


def maybe_save(cfg, poses_cfg, state):
    name = input("Save pose as (name, blank = cancel): ").strip()
    if not name:
        print("  (not saved)")
        return
    ok, err = validate_pose_name(name)
    if not ok:
        print(f"  invalid name: {err}")
        return
    poses_cfg.poses[name] = HandPose(positions=snapshot_positions(cfg, state))
    save_hand_poses(HAND_CONFIG_PATH, poses_cfg)
    print(f"  saved pose '{name}' -> {HAND_CONFIG_PATH}")


def main():
    cfg = load_hand_calibration(YAML_PATH)
    poses_cfg = load_hand_poses(HAND_CONFIG_PATH)
    limits_by_finger = cfg.limits_by_finger()
    all_ids = [s for block in cfg.fingers.values() for s in (block.servo_1.id, block.servo_2.id)]

    c = Scs0009PyController(
        serial_port=cfg.com_port,
        baudrate=cfg.baudrate,
        timeout=cfg.timeout,
    )
    for sid in all_ids:
        c.write_torque_enable(sid, 1)

    state = HandJogState()
    print(__doc__)
    for name in FINGERS:
        base, side = state.fingers[name]
        drive_finger(c, cfg.fingers[name], base, side, cfg.speed)
    print("  " + format_hand_status(state))

    try:
        while True:
            action = key_to_action(read_key())
            if action is None:
                continue
            if action == "quit":
                break
            if action == "save":
                maybe_save(cfg, poses_cfg, state)
                continue

            state = apply_action(state, action, limits_by_finger)

            if action == "home_all":
                for name in FINGERS:
                    base, side = state.fingers[name]
                    drive_finger(c, cfg.fingers[name], base, side, cfg.speed)
            else:
                base, side = state.fingers[state.active]
                drive_finger(c, cfg.fingers[state.active], base, side, cfg.speed)

            block = cfg.fingers[state.active]
            load1, load2 = read_loads(c, block.servo_1.id, block.servo_2.id)
            print("  " + format_hand_status(state))
            warn = load_warning(load1, load2)
            if warn:
                print("  " + warn)
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        for sid in all_ids:
            try:
                c.write_torque_enable(sid, 0)
            except Exception as e:
                print(f"warning: failed to disable torque on {sid}: {e}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify parse + lint + format**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/AmazingHand/jog.py').read())"`
Run: `uv run ruff check scripts/calibration/AmazingHand/jog.py`
Run: `uv run ruff format --check scripts/calibration/AmazingHand/jog.py`
Expected: parses; `All checks passed!`; format clean (if not, run `uv run ruff format scripts/calibration/AmazingHand/jog.py` and re-stage).

- [ ] **Step 3: Verify imports resolve** (no bus needed — only constructs nothing at import):

Run: `uv run python -c "import importlib.util, pathlib; spec=importlib.util.spec_from_file_location('jog', 'scripts/calibration/AmazingHand/jog.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('imports OK')"`
Expected: `imports OK` (module-level code only defines functions + paths; `rustypot` import must succeed but no port is opened).

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/jog.py
git commit -m "feat(hand): jog.py -- keyboard-jog all fingers and save a whole-hand pose

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Docs + full verification

**Files:**
- Modify: `scripts/calibration/AmazingHand/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document `jog.py` in the AmazingHand README.** In `scripts/calibration/AmazingHand/README.md`, after the "Utility — park the hand open or closed" section (around line 301), add:

```markdown
### Utility — jog all fingers and save a pose

**Script:** `scripts/calibration/AmazingHand/jog.py`

Not a calibration step — a convenience for posing the whole hand by keyboard and saving
the result. Torque stays ON; you jog each finger within its calibrated limits, then save
the whole-hand pose by name into `data/hand_config.yaml` (the same store the unified GUI's
pose manager reads). Mirrors the arm's `so_arm101/jog.py`.

```powershell
uv run python scripts/calibration/AmazingHand/jog.py
```

Controls: `1`-`4` select finger; arrows jog base/side; `[`/`]` step; `h`/`H` home
finger/all; `s` save (prompts for a name); `q` release torque and exit.
```

Also update §4 ("Where the calibration lives") to note the YAML now carries a `speeds:`
block (open/close speeds for `SetPose`) and that **all scripts read it via the typed
`load_hand_calibration` loader** (the writer scripts save via `save_hand_calibration`);
replace the line "Editing the YAML by hand is fine — the scripts preserve any top-level
keys you add." with: "The scripts load and save this file through the typed
`HandCalibration` schema (`extra='forbid'`), so the recognized fields are validated; hand
edits to those fields are fine, but unknown top-level keys are rejected."

- [ ] **Step 2: Document `jog.py` in `CLAUDE.md` §4.** In the AmazingHand calibration block of `CLAUDE.md` §4 (after the `AmazingHand_FingerTest.py` line), add:

```powershell
uv run python scripts/calibration/AmazingHand/jog.py                       # jog all fingers; save whole-hand pose to data/hand_config.yaml
```

- [ ] **Step 3: Full verification sweep**

Run: `uv run ruff format --check .`
Run: `uv run ruff check .`
Run: `uv run mypy src`
Run: `uv run pytest -m 'not hardware'`
Expected: format clean; `All checks passed!`; mypy no *new* errors beyond the known PyYAML `import-untyped` baseline (see CLAUDE.md §7); all tests pass.

(If `ruff format --check` fails on any refactored file, run `uv run ruff format .` and amend the relevant task's commit or add a follow-up `style:` commit.)

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/README.md CLAUDE.md
git commit -m "docs(hand): document jog.py + typed-loader config in README and CLAUDE.md

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Operator hardware check (manual, COM18 — the one step the agent cannot run).**

1. Each refactored script behaves as before:
   `MotorReset` (reset a finger, middle_pos→0 saved), `MiddlePos` (adjust + save),
   `RangeCalib` (mark limits + save), `FingerTest` (cycles), `SetPose open`/`close`
   (uses the YAML `speeds`), `FullHand_Test` (full demo).
2. `jog.py`: select each finger, jog within limits, `s` to save a throwaway pose; confirm
   it lands in `data/hand_config.yaml`; `q` releases torque on all 8.
3. Launch the GUI (`uv run arm101-gui`) and confirm the jog-saved pose appears in the pose
   manager and drives correctly — proves the shared store.

---

## Self-Review

**Spec coverage:**
- Config SSOT — typed loader across all 6 scripts (Tasks 4-9), `save_hand_calibration`
  (Task 2), `save_hand_poses` + GUI refactor (Task 3). ✓
- `speeds:` block, SetPose reads it, FullHand keeps in-script (Tasks 2, 8, 9). ✓
- Shared converter (Task 1), GUI uses it (Task 3), jog.py uses it (Task 11). ✓
- `jog.py` behavior + clamping + save path (Tasks 10, 11). ✓
- Pure state machine + tests (Task 10). ✓
- Docs (Task 12). ✓
- Testing: converter, schema saves, pose_jog all unit-tested; hardware check listed. ✓

**Placeholder scan:** none — every code step has complete code; every run step has a
command + expected output.

**Type consistency:** `finger_positions_to_servo_frame(odd_id, even_id, base, side,
servo_min=-40, servo_max=110)` used identically in Tasks 1/3/11. `save_hand_calibration` /
`save_hand_poses` signatures match across Tasks 2/3/4/5/6/11. `HandJogState` /
`apply_action(state, action, limits_by_finger)` / `key_to_action` consistent across Tasks
10/11. `cfg.fingers[name].servo_1.id` / `.middle_pos` / `.limits.base_max` attribute access
consistent across all refactored scripts.
