# Design — Single-section arm pose store + KISS convention

**Date:** 2026-06-04
**Scope:** Arm-only (SO-ARM101). Consolidate arm pose storage into one file/section; add a
KISS convention. No hand changes (the hand already uses the target shape).

---

## 1. Problem

Arm poses are spread across **three buckets in two files**, and the two readers see
different subsets:

| Bucket | Written by | Read by |
|---|---|---|
| `data/arm_config.yaml` → `quick_poses` | hand-edited only | GUI, `set_pose.py`, `_common` (home) |
| `data/arm_config.yaml` → `poses` | **nothing** (dead section) | GUI only |
| `data/arm_jog_poses.yaml` → `poses` | `jog.py` (`s` key) | `set_pose.py` only |

Consequences:
- A pose saved in `jog.py` is invisible to the GUI.
- The GUI reads an `arm_config.yaml` → `poses` section that no code ever writes.
- `set_pose.py` and the GUI never agree on the available pose list.

The split exists for one real reason: `save_arm_poses()` rewrites YAML and **strips
comments**, so `jog.py` was pointed at a separate machine-owned file to avoid clobbering
the curated comments in `arm_config.yaml`. That rationale is legitimate but the result
violates SSOT/DRY.

The **hand** already avoids all of this: `hand_config.yaml` has a single `poses` section,
written and read consistently by the GUI. The arm is the outlier.

## 2. Goal

One arm pose store: `data/arm_config.yaml` → `poses`. Remove `quick_poses` and delete
`data/arm_jog_poses.yaml`. Make the arm symmetric with the hand. Every reader and writer
converges on the same single section.

## 3. Design

### 3.1 Schema — `src/arm101_hand/config/arm_poses.py`

- Remove the `quick_poses` field from `ArmPoseConfig`. Keep `schema_version` + `poses`.
- `home` becomes an ordinary entry in `poses` — no privileged section. (`home` remains
  *semantically* special only as the default safe-park / default-home target, resolved
  by name like any other pose.)
- `save_arm_poses()` stays the atomic tmp-file + `os.replace` writer. Changes:
  - Generalize the docstring (no longer "the jog-pose file"; it is now the single arm
    pose file).
  - Emit a short regenerated header comment so the machine-owned file still carries a
    one-line "machine-managed; edit via jog.py `s` or the GUI" note. Implementation:
    prepend a fixed header string to the `safe_dump` output before writing.

### 3.2 Data files

- `data/arm_config.yaml` → `schema_version` + `poses:` containing the `home` entry
  (values unchanged from the 2026-06-03 capture). Curated safety/context notes that
  cannot survive a machine rewrite move to the README; the regenerated header carries the
  short "machine-managed" note.
- **Delete `data/arm_jog_poses.yaml`.**

### 3.3 Readers / writers (all converge on `arm_config.yaml` → `poses`)

| File | Change |
|---|---|
| `scripts/calibration/so_arm101/set_pose.py` | Read `load_arm_poses(ARM_CONFIG_PATH).poses` only. Drop the two-file merge and the `ARM_JOG_POSES_PATH` import. |
| `scripts/calibration/so_arm101/jog.py` | On `s`: load `arm_config.yaml`, set/update the entry, save back to `arm_config.yaml`. Drop `ARM_JOG_POSES_PATH`. |
| `scripts/calibration/so_arm101/_common.py` | `load_home_degrees` reads `poses["home"]` (keep the all-zeros fallback). Remove `ARM_JOG_POSES_PATH`. |
| `src/arm101_hand/gui/main_window.py` | `arm_resolver` → `arm_poses.poses.get(name)` (drop the `quick_poses or poses` fallback). |

### 3.4 Config alignment

- `data/app_config.yaml` and `SafePark.arm_pose` already default to `home` (changed in a
  prior edit). `home` resolves out of `poses` after this change — no further edit needed,
  but the comment in `app_config.yaml` stays accurate ("name from arm_config.yaml poses").

### 3.5 Tests

- `tests/unit/test_arm_poses_schema.py`:
  - `test_seeded_yaml_loads_clean` — assert `home` lives in `poses` (not `quick_poses`)
    and that `home.shoulder_lift == -104.9`.
  - Remove any `quick_poses` references; keep the empty-config and validation tests
    (adjust field names as needed).
- `tests/unit/test_safe_park.py` — unaffected (its `pose_name="rest"` strings are log
  labels fed by mock resolvers, not config lookups). Left as-is.

### 3.6 Docs

- `scripts/calibration/so_arm101/README.md` — update the pose-table and any references to
  `arm_jog_poses.yaml` / `quick_poses`; fold the moved safety notes here.
- Root `CLAUDE.md` — update the `set_pose.py` / `jog.py` lines and directory tree to drop
  `arm_jog_poses.yaml` and `quick_poses`.

## 4. KISS convention

New file `docs/conventions/07-kiss-simplicity.md` (conventions are numbered 00–06; this
slots in as 07). One-line pointer added to the CLAUDE.md §5 convention table and the
README convention list (IL-7: one canonical home, pointers elsewhere).

Content (concise by design):

> # 07 — KISS: Keep It Simple
>
> *Systems work best kept simple, not complex. Simplicity is harder to produce than
> complexity — it's the work, not the shortcut. Less is more; simplicity wins in the long run.*
>
> **The rule:** Prefer the simplest design that satisfies the requirement and the Iron
> Laws. When two designs both work, ship the one with fewer files, fewer concepts, and
> less indirection.
>
> **Why it pays off**
> - **Maintainable** — fewer moving parts to reason about.
> - **Readable** — a new contributor (or future you) follows it without untangling clever patterns.
> - **Debuggable** — straightforward control flow makes bugs visible. On hardware that
>   moves, a bug you can *see* is a bug that doesn't damage a servo.
> - **Extensible** — a simple foundation extends cleanly; a clever one resists change.
>
> **In practice**
> 1. **One responsibility per unit** — small functions/modules that each do one thing.
> 2. **Readability over cleverness** — write as if explaining to a less-experienced developer.
> 3. **No speculative abstraction (YAGNI)** — add structure when a second real case appears, not before.
> 4. **No premature optimization** — make it correct and clear first.
> 5. **Refactor toward simpler** — leave code simpler than you found it.
> 6. **Mirror existing shapes** — when one device already solves a problem (e.g., the
>    hand's single `poses` section), the other should match rather than invent a parallel scheme.
>
> **Smells of over-complexity:** the same data in two places with divergent readers; a
> config section nothing writes; a "flexible" layer with exactly one caller; needing a
> paragraph to explain why two files exist.

## 5. Out of scope (YAGNI)

- No hand changes — the hand is already single-section.
- No new pose categories, tags, or per-pose metadata.
- No migration tooling — `arm_jog_poses.yaml` is currently empty (`poses: {}`), so there
  is nothing to migrate; it is simply deleted.

## 6. Verification

- `uv run pytest -m 'not hardware'` — unit tests green.
- `uv run ruff check .` / `uv run ruff format --check .`
- Manual load check: `load_arm_poses(arm_config.yaml).poses` contains `home`;
  `safe_park.arm_pose` resolves.
- `grep` confirms no remaining references to `arm_jog_poses` or `quick_poses` in
  `src/`, `scripts/`, `tests/`, or docs.

## 7. Iron Laws touched

- **IL-5** — calibration/config in-tree only: unchanged; pose file stays under `data/`.
- **IL-6** — atomic cross-device commit: N/A (arm-only change).
- **IL-7** — single-source-of-truth: the *motivating* law; this change removes a
  duplicate store and adds the KISS doc with pointers, not copies.
