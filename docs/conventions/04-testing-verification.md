# 04 — Testing & Verification

> How we test this project. Two layers: **host unit tests** (fast, deterministic, run anywhere) and **on-bench integration** (run with the real arm + hand wired up). No HIL CI — there is no automated bench, the hardware verification is manual.

> **Scope**: applies to all code in the repo — Python, the few shell helpers. Each layer has its own runner; the principles are shared.

---

## 1. Core Principles

### 1.1 Testing is DRY — automate your manual tests

> *Every time you run a script by hand to check behavior, think: can I encode this as an automated test?*

If yes, do it now — while the mental model is fresh. It pays back every time someone else touches that code.

### 1.2 Tests are documentation

A reviewer should be able to read your test file and learn **how your API is meant to be used**. Write tests for people to read, not to placate a coverage gate.

### 1.3 Tests must be meaningful

A test that asserts `assert 1 == 1` is worse than no test — it signals coverage while providing none. If you can't articulate why a test case exists, delete it.

### 1.4 Unit tests run on the host — always

- **Pure-logic Python** (config schemas, geometry math, parsers, encode/decode round-trips) compiles and runs without the bus.
- **Robot/hand wrappers** are importable without hardware (the constructor must not open the port — see `01-module-layering.md` §4). Tests can instantiate the class, exercise pure-logic methods, and inspect state.
- **Hardware contact** is the top of the pyramid, not the default. A test that requires the bus is slow, fragile, and blocks `pytest`. Mark it explicitly.

## 2. The Pyramid

```
            +---------------------------------+
            |  On-bench integration (manual)   |    ← arm + hand wired up
            +---------------------------------+
       +--------------------------------------------+
       |     Unit tests (host, no hardware)          |    ← fast, deterministic
       +--------------------------------------------+
```

| Layer | Runner | Required for merge? | Typical cycle time |
|---|---|---|---|
| Unit | `pytest` | **Yes** (when `tests/` exists) | < 5 s |
| On-bench integration | manual + checklist | Pre-release | 5–30 min |

There is no CI yet. When tests/ is added, the unit-test gate becomes a local pre-commit + a reviewer expectation.

## 3. Shared Test Idioms

### 3.1 Table-driven tests with tuple + header comment

Keep the test-data structure flat. Never a dict per case — repeats keys.

```python
# | input | expected | description                   |
@pytest.mark.parametrize("inp,exp,desc", [
    ("valid",  True,  "accepts valid input"),
    ("",       False, "rejects empty string"),
    (None,     False, "rejects None"),
])
def test_validate(inp, exp, desc):
    assert validate(inp) == exp, desc
```

### 3.2 Rules

- **At least 2 cases per test function.** One case is under-testing. If you can only think of one, you haven't thought hard enough.
- **Header comment** describes the columns once. Don't repeat.
- **Every assertion includes `{desc}`**. A failure that says `"rejects empty string"` is dramatically more useful than `"assertion failed on line 47"`.
- **> 20 cases**: package them as a CSV or YAML file and read at test start. Keeps test code compact.

### 3.3 Inject time — never sleep

State machines with timeouts must accept a `now()` function (or a mockable clock). Tests supply a fake clock that advances on demand — they **never** `time.sleep()`.

```python
class TimedRetry:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now

# In test:
fake_clock = [0.0]
r = TimedRetry(now=lambda: fake_clock[0])
r.tick()
fake_clock[0] += 1.5
r.tick()
assert r.state == "expired"
```

### 3.4 Mock hardware via interfaces

Hardware access is behind an abstract interface. Production wires the real driver; tests wire a mock.

```python
from typing import Protocol

class FingerController(Protocol):
    def write_torque_enable(self, sid: int, enable: int) -> None: ...
    def write_goal_position(self, sid: int, rad: float) -> None: ...

class FakeFingerController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float | int]] = []
    def write_torque_enable(self, sid: int, enable: int) -> None:
        self.calls.append(("torque", sid, enable))
    def write_goal_position(self, sid: int, rad: float) -> None:
        self.calls.append(("goal", sid, rad))
```

**The script's `main()` wires the real `Scs0009PyController`**; tests wire a `FakeFingerController`. No `if TESTING:` branches in production code.

## 4. Python Tests (pytest + hypothesis)

See `02-code-style-python.md` §10 for the style baseline. Project-specific patterns:

### 4.1 Property-based tests with Hypothesis

For invariants (encode/decode symmetry, ordering, idempotence):

```python
from hypothesis import given, strategies as st

@given(deg=st.integers(min_value=-180, max_value=180))
def test_deg_to_rad_to_deg_roundtrip(deg: int) -> None:
    rad = math.radians(deg)
    deg2 = math.degrees(rad)
    assert abs(deg - deg2) < 1e-9
```

Hypothesis will generate hundreds of cases and shrink to a minimal failing case if it finds a bug.

### 4.2 Mark hardware tests

```python
import pytest

@pytest.mark.hardware
def test_finger_close_open_cycle():
    """Requires a powered AmazingHand on COM18."""
    ...
```

Run with:

```powershell
uv run pytest -m 'not hardware'      # default (no bus needed)
uv run pytest -m 'hardware'           # on-bench
```

### 4.3 Loading the calibration YAML in tests

Tests that need a realistic calibration shape can load the canonical YAML directly — it's checked into the repo:

```python
from pathlib import Path
import yaml

CALIB_PATH = Path("scripts/calibration/amazing_hand/hand_calib_values.yaml")

def test_calibration_yaml_has_all_fingers():
    cfg = yaml.safe_load(CALIB_PATH.read_text())
    for finger in ("index", "middle", "ring", "thumb"):
        assert finger in cfg["fingers"], f"missing {finger}"
        assert cfg["fingers"][finger]["servo_1"]["id"] in range(1, 9)
        assert cfg["fingers"][finger]["servo_2"]["id"] in range(1, 9)
```

## 5. On-Bench Integration

These checks require the real arm + hand wired up. There is no automation; they are run by a human before a release tag.

### 5.1 Hand integration checklist

- All 8 SCS0009 servos respond to ping (`uv run python scripts/calibration/amazing_hand/finger_test.py` exercises one finger; cycle through all four).
- Calibration YAML matches mechanical reality (no servo whines at endpoints, no horn sits visibly off the middle plane at full-close).
- Torque release on Ctrl+C / `q` works — the hand goes limp, no servo holds position.

### 5.2 Arm integration checklist

- All 5 STS3215 servos respond to ping (`uv run arm101-calibrate-follower --robot.type=so101_follower_no_gripper --robot.port=COM<X> --robot.id=so101_follower`).
- Calibration completes through every pose without an unhandled exception.
- The output JSON is well-formed (parses with `json.loads(Path(...).read_text())`).
- E-Stop (cut PSU) immediately drops torque — arm goes limp.

### 5.3 Cross-device sanity

- Both the hand bus and arm bus can be opened simultaneously by two separate Python processes (different COM ports — see IL-4).
- A simulated coordinator (a small script that pings both buses in a loop) does not desync over 5 minutes of operation.

## 6. Anti-Patterns

| Anti-pattern | Why it's bad |
|---|---|
| Single-case test | Under-tested; delete or expand |
| `time.sleep(1)` in a unit test | Flaky, slow; inject time |
| `assert True, "TODO"` | Worse than no test |
| Testing private implementation | Brittle; couples test to internals |
| Mocking the system under test | Points to poor design — refactor |
| `setUp` that opens a real COM port | Not a unit test; gate with `@pytest.mark.hardware` |
| Silent test data (no `{desc}`) | Opaque failures |
| Identical tests copy-pasted per case | Use parametrize |

---

## 7. Checklist (paste into PR)

- [ ] Every new module has at least one host-side unit test (when `tests/` exists)
- [ ] Table-driven tests use tuple + header comment
- [ ] At least 2 cases per test function
- [ ] Every assertion has a `{desc}` string
- [ ] No `sleep()` in unit tests — time is injected
- [ ] Hardware-required tests tagged `@pytest.mark.hardware`
- [ ] `ruff check` clean on changed files
- [ ] `pytest -m 'not hardware'` passes locally
