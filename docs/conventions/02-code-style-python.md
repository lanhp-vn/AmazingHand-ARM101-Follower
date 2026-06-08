# 02 — Code Style: Python

> Governs all Python in this repo — `src/arm101_hand/`, `scripts/`, and any one-off tools. Baseline: **PEP 8** + **PEP 484** (type hints) + **PEP 621** (`pyproject.toml` packaging).

> **Scope**: everything runs on a single Windows host (Python 3.12) under a `uv`-managed `.venv/`. There is no cross-compile, no on-board interpreter, no second target.

> **Tooling baseline**:
> - **`uv`** for environment + dependency management.
> - **`ruff`** replaces black + isort + flake8 in a single 10–100× faster Rust-based tool.
> - **`mypy`** for static typing where useful (project default: `ignore_missing_imports = true`, not `--strict` — many lerobot/rustypot bindings lack stubs).
> - **`pytest`** for tests; **`hypothesis`** for property-based tests where invariants exist.
> - **`pydantic`** for parsed config data.

---

## 1. Core Principles

1. **Types where they help.** Public function signatures get type hints. Internal one-liners may not.
2. **Single source of truth for config defaults.** A pydantic model's defaults must match the YAML they describe (e.g., `hand_calib_values.yaml`).
3. **Scripts must degrade gracefully.** A calibration script that opens a COM port must handle the port being held by another process — log and exit nonzero, don't traceback into the user's face.
4. **Procedural where possible; classes where state matters.** Don't over-architect a 50-line script.
5. **Fail fast on startup, degrade in the loop.** Missing port → exit 1. Transient I/O glitch in loop → log a warning and retry once.

## 2. Tooling

| Tool | Version | Role |
|---|---|---|
| `python` | 3.12 (pinned in `.python-version`) | Runtime |
| `uv` | latest | Env + dependency manager |
| `ruff` | ≥ 0.8 | Lint + format |
| `mypy` | ≥ 1.10 | Static type checking |
| `pytest` | ≥ 8 | Test runner |
| `pydantic` | ≥ 2 | Config / schema validation |

### 2.1 Project layout

```
arm101-hand/
├── pyproject.toml           # Project metadata, ruff + mypy config
├── uv.lock                  # Locked deps (committed)
├── .python-version          # 3.12
├── src/arm101_hand/
│   ├── robots/              # device layer — one robot per file
│   ├── hand/                # device layer — rustypot wrappers
│   ├── config/              # primitive layer — pydantic schemas
│   └── scripts/             # application layer — console-script entries
├── scripts/                 # application layer — runnable scripts
│   └── calibration/
│       ├── amazing_hand/
│       └── so_arm101/
└── tests/                   # pytest, when we start writing them
```

The layering rationale lives in `01-module-layering.md`.

### 2.2 `pyproject.toml`

The canonical `pyproject.toml` is at the repo root. Highlights:

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "lerobot[feetech]",
    "rustypot>=1.4,<2.0",
    "numpy>=2.0,<2.3",
    "pyyaml>=6.0",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = ["ruff>=0.8", "mypy>=1.10", "pytest>=8.0"]

[tool.ruff]
target-version = "py312"
line-length = 110

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "N", "UP", "SIM"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
follow_imports = "skip"
```

`ignore_missing_imports = true` matches the reality: lerobot, rustypot, and the Feetech SDK do not all ship type stubs. Strict mode would drown signal in noise.

### 2.3 Running

```powershell
uv sync                              # install deps
uv run ruff format .                 # format
uv run ruff check .                  # lint
uv run mypy src                      # type-check
uv run pytest                        # test
```

## 3. Formatting (handled by `ruff format`)

- **Indentation**: 4 spaces.
- **Line length**: 110 characters (matches `ruff` config; widened from default 88 for readability of motor/finger names).
- **Quotes**: double quotes everywhere (`ruff format` enforces).
- **Trailing commas**: required in multi-line collections (ruff adds).

## 4. Naming

PEP 8 with project-specific conventions:

| Kind | Convention | Example |
|---|---|---|
| Module | `snake_case` | `so101_follower_no_gripper.py` |
| Package | `snake_case` | `arm101_hand/`, `arm101_hand.robots/` |
| Function / method | `snake_case` | `close_finger()`, `prompt_finger()` |
| Class | `PascalCase` | `SO101FollowerNoGripper`, `Scs0009PyController` |
| Constant | `SCREAMING_SNAKE_CASE` | `VALID_FINGERS = ("index", "middle", "ring", "thumb")` |
| Private (module-internal) | `_leading_underscore` | `_inline_dict_representer` |
| Type variable | `T`, `T_co`, `T_contra`, `PascalCaseT` | `T = TypeVar("T")` |

## 5. Type Hints

### 5.1 Public function signatures get hints

```python
from collections.abc import Sequence
from pathlib import Path

def load_yaml_config(path: Path) -> dict[str, object]:
    ...

def send_command(ids: Sequence[int], positions: Sequence[int]) -> bool:
    ...
```

### 5.2 Modern syntax

- `list[int]`, not `List[int]` (Python 3.9+).
- `dict[str, int]`, not `Dict[str, int]`.
- `X | Y` for union types, not `Union[X, Y]` (Python 3.10+).
- `int | None` instead of `Optional[int]` for clarity.

### 5.3 Protocols over ABCs for structural typing

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ServoController(Protocol):
    def write_torque_enable(self, servo_id: int, enable: int) -> None: ...
    def write_goal_position(self, servo_id: int, radians: float) -> None: ...
```

## 6. Config Loading (Pydantic)

All runtime config uses Pydantic models. Defaults must match the reference YAML.

```python
from pydantic import BaseModel, Field

class AmazingHandConfig(BaseModel):
    com_port: str = "COM18"
    baudrate: int = Field(default=1_000_000, ge=9600)
    timeout: float = Field(default=0.5, ge=0.0, le=5.0)
    speed: int = Field(default=6, ge=1, le=100)
```

Load with explicit failure:

```python
import sys
import yaml
from pathlib import Path
from pydantic import ValidationError

def load_config(path: Path) -> AmazingHandConfig:
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        return AmazingHandConfig.model_validate(raw)
    except ValidationError as e:
        print(f"Config validation failed: {e}", file=sys.stderr)
        raise
```

**Rules**:

- Always `yaml.safe_load()`. `yaml.load()` can execute arbitrary Python.
- Defaults in the Pydantic model **must match** the reference YAML in `scripts/calibration/amazing_hand/hand_calib_values.yaml`. Drift breaks the "works out of the box" promise.
- **Never read config mid-run.** Load at startup, use the immutable Pydantic object thereafter.

## 7. Logging

Use `logging` module; one `logging.getLogger(__name__)` per module that needs it.

```python
import logging

log = logging.getLogger(__name__)

def do_something(x: int) -> None:
    log.info("doing something with %d", x)      # lazy formatting
    if x < 0:
        log.warning("negative x — using absolute value")
        x = abs(x)
```

**Rules**:

- `%`-style formatting with `log.info("... %d ...", x)` — not f-strings — so the formatting cost is skipped when the level is filtered.
- **Calibration scripts may use `print()` directly** since they're interactive — that's the right tool for prompts and progress.
- Library code under `src/arm101_hand/` uses `logging`, not `print()`.

## 8. Error Handling

- **Specific exceptions.** Never bare `except:` and never `except Exception:` unless you re-raise or log+exit.

  ```python
  try:
      config = load_config(path)
  except (FileNotFoundError, yaml.YAMLError) as e:
      log.error("config load failed: %s", e)
      sys.exit(1)
  ```

- **`subprocess` calls have a timeout**:

  ```python
  import subprocess
  res = subprocess.run(
      ["uv", "run", "lerobot-find-port"],
      capture_output=True, text=True, timeout=30, check=False,
  )
  if res.returncode != 0:
      log.warning("find-port failed rc=%d stderr=%s", res.returncode, res.stderr)
  ```

- **Resource cleanup via `with`** or `try/finally` for port handles:

  ```python
  c = Scs0009PyController(serial_port=port, baudrate=br, timeout=t)
  try:
      c.write_torque_enable(servo_id, 1)
      ...
  finally:
      c.write_torque_enable(servo_id, 0)
  ```

  The AmazingHand calibration scripts already follow this pattern — copy it for any new servo script.

## 9. Performance & Memory

These rules apply selectively — most of our work is one-shot calibration, not a hot loop.

- **Precompile regex at module load** if used in a loop:

  ```python
  import re
  _ID_RE = re.compile(r"\b(\d+)\b")
  ```

- **`__slots__`** on hot-path dataclasses (rare in our code):

  ```python
  from dataclasses import dataclass

  @dataclass(slots=True, frozen=True)
  class MotorReading:
      servo_id: int
      position_rad: float
      load_pct: float
  ```

- **NumPy-specific**:
  - `np.deg2rad` / `np.rad2deg` for angle conversions (already used in calibration scripts).
  - Avoid `np.append` in loops (O(n²)). Allocate up front if you need an array.

## 10. Testing (this section is an index — full rules in `04-testing-verification.md`)

- `pytest` for unit tests.
- **Table-driven tests** via `@pytest.mark.parametrize` with tuple + header comment:

  ```python
  # | input | expected | description                   |
  @pytest.mark.parametrize("inp,exp,desc", [
      ("valid",  True,  "accepts valid input"),
      ("",       False, "rejects empty string"),
      (None,     False, "rejects None"),
  ])
  def test_validate(inp: object, exp: bool, desc: str) -> None:
      assert validate(inp) == exp, desc
  ```

- **Every assertion includes a `desc` string** so test failures self-identify.
- **At least 2 cases per test function.** A single-case test is under-tested.
- **`hypothesis`** for invariant-driven tests (encode/decode symmetry, ordering properties).

## 11. Useful Patterns

### 11.1 Entry-point scripts

Every standalone tool follows this template:

```python
#!/usr/bin/env python3
"""Short description of what this tool does."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=Path("scripts/calibration/amazing_hand/hand_calib_values.yaml"))
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()

def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### 11.2 Console-script entries via `pyproject.toml`

The `arm101-calibrate-follower` console script is registered in `pyproject.toml` under `[project.scripts]`. The pattern: a thin `main()` in `src/arm101_hand/scripts/<name>.py` that imports any registration-side-effects (e.g., the `SO101FollowerNoGripper` subclass) before delegating to lerobot's draccus-wrapped entry.

```python
# src/arm101_hand/scripts/calibrate_follower.py
from arm101_hand.robots import SO101FollowerNoGripper  # noqa: F401  (registers via decorator)
from lerobot.scripts.lerobot_calibrate import calibrate

def main() -> None:
    calibrate()
```

The subclass's `@RobotConfig.register_subclass(...)` decorator runs at import time, putting our type into lerobot's robot registry before `calibrate()` parses `--robot.type`.

## 12. Forbidden

| Pattern | Why |
|---|---|
| `yaml.load()` | Arbitrary code execution. Use `safe_load`. |
| `eval()`, `exec()` with external input | Same. |
| Bare `except:` | Hides bugs; catches `KeyboardInterrupt`. |
| `pickle.load()` on untrusted data | Arbitrary code execution. |
| `subprocess.*` without `timeout=` | Hangs tools indefinitely. |
| `os.system()` | Shell-injection risk; use `subprocess.run` with a list. |
| Mutable default arguments (`def f(x=[]):`) | Classic bug; use `x: list[int] | None = None`. |
| Star imports (`from m import *`) outside interactive scratch | Pollutes namespace. |
| Logging in `%` + pre-formatted string (`log.info(f"x={x}")`) | Defeats level filtering. Use `log.info("x=%s", x)`. |
| Modifying anything under `references/` | IL-2 — read-only. |

---

## 13. Checklist (paste into PR)

- [ ] `uv run ruff format .` idempotent
- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy src` clean (or noted exceptions documented inline)
- [ ] `uv run pytest` passes
- [ ] Public function signatures have type hints
- [ ] All YAML loaded via `yaml.safe_load`
- [ ] Every `subprocess` call has `timeout=`
- [ ] No `print()` in library modules under `src/arm101_hand/`; calibration scripts use `print()` for prompts
- [ ] Pydantic defaults match the reference YAML in `scripts/calibration/`
