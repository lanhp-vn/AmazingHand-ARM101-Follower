---
name: iron-law-audit
description: Audit the current branch's diff against the seven Iron Laws (IL-1..IL-7) in docs/conventions/00-iron-laws.md. Reports per-IL pass/fail with file:line evidence and proposes the PR body's "Iron Laws touched" checklist. Read-only — does not modify code. Use before opening a PR or merging to main.
---

# iron-law-audit

The seven Iron Laws in `docs/conventions/00-iron-laws.md` are the project's spine. The PR template in `05-git-workflow.md` §5.2 asks the author to tick which IL their change touches and confirm none are violated. This skill makes that confirmation evidence-based.

## Invocation

User runs `/iron-law-audit`. The agent runs the checks below and emits a fixed-format report. Read-only — no edits.

## Procedure

### 1. Bound the audit

Run:

```bash
git rev-parse --abbrev-ref HEAD                  # current branch
git diff main...HEAD --name-only                 # changed files
git diff main...HEAD --stat                      # rough size
git log main..HEAD --oneline                     # commit list (for IL-6)
```

If `HEAD` is `main`, ask the user which ref to compare against and stop until they answer.

### 2. IL-1 — Voltage isolation (5 V hand vs 12 V arm)

Source: `docs/conventions/00-iron-laws.md` IL-1.

Checks:
- `git diff main...HEAD docs/BOM.md` — flag any change that places hand and arm on the same supply, removes the labeling rule, or merges the two PSU columns.
- Grep changed Python under `src/arm101_hand/` and `scripts/` for shared bus-handle patterns: `Grep("FeetechMotorsBus.*Scs0009|Scs0009.*FeetechMotorsBus", path="src scripts")` — flag if a single bus object talks to both servo families.

Verdict:
- `FAIL` only if there's hard evidence both rails or both controllers are coupled.
- `N/A` if the diff doesn't touch hardware-relevant files.
- `PASS` if changes touch hardware files but maintain the separation.

### 3. IL-2 — `references/` is read-only

Checks:
- `git diff main...HEAD --name-only -- 'references/*'` — must be empty. Any file under `references/` in the diff is a violation.
- Exception: `references/*/CLAUDE.md` and `references/*/CLAUDE.local.md` — these are gitignored agent notes; they should not appear in the diff anyway, but if they do, flag the gitignore as broken.
- Submodule pointer changes (a single line like `Subproject commit <sha>`) are allowed only when the commit's scope is `chore(deps)` (per `05-git-workflow.md` §8). Cross-check against the commit messages from §1.

Verdict:
- `FAIL` if any file under a submodule appears in the diff (other than the submodule pointer commit).
- `PASS` if the only references-related change is a properly-scoped pointer bump.

### 4. IL-3 — Motor ID canon

Source: hand IDs 1–8 (odd-right/even-left per finger), arm IDs 1–5 shoulder→wrist, **ID 6 absent on the arm**.

Checks:
- `Grep("\\bid\\s*=\\s*6\\b|\"gripper\"|MotorNormMode\\.RANGE_0_100", path="src/arm101_hand")` — flag any reintroduction of motor ID 6 / gripper config to the arm.
- `Grep("Motor\\(\\s*[1-9]", path="src/arm101_hand/robots")` — confirm arm motors are 1–5 only.
- `git diff main...HEAD scripts/calibration/amazing_hand/hand_calib_values.yaml` — confirm hand IDs stay within 1–8 if the YAML changed.

Verdict:
- `FAIL` if motor 6 reappears in the arm config without a deliberate IL-3 amendment in `00-iron-laws.md` (also part of the diff).
- `FAIL` if hand IDs go outside 1–8.
- `N/A` if no motor-ID code touched.

### 5. IL-4 — COM-port discipline (single owner per bus)

Checks:
- `Grep("serial\\.Serial|PortHandler\\(|Scs0009PyController\\(", path="src scripts", -n=true)` on changed files — flag scripts that open a port without obvious teardown (no `close()`, no `try/finally`, no `with`).
- Look for hardcoded COM strings in changed files: `Grep("COM\\d+", path="src scripts")` — should appear in CLI args / YAML, not as buried constants.

Verdict:
- `PASS` is the default unless a long-running script is added that holds a port without releasing it.
- `EVIDENCE-INSUFFICIENT` is acceptable here — IL-4 is a runtime invariant; static checks can only catch the obvious cases.

### 6. IL-5 — Calibration in version control

Checks:
- If any `scripts/calibration/amazing_hand/*.py` or any `src/arm101_hand/robots/*.py` changed in a way that alters calibration semantics (motor count, range, normalization mode), confirm `scripts/calibration/amazing_hand/hand_calib_values.yaml` is also in the diff (or that the user has noted "no calibration impact" in the commit body).
- For SO-ARM101: the calibration JSON lives outside the repo (lerobot default `~/.cache/...`). If the diff changes the arm's motor dict or normalization, flag a reminder to re-run `arm101-calibrate-follower` on the bench.

Verdict:
- `FAIL` if calibration-affecting code changed but the YAML wasn't updated and the commit body doesn't acknowledge.
- `PASS` otherwise.

### 7. IL-6 — Atomic cross-device commits

Source: changes touching both arm and hand land in one commit.

Checks:
- For each commit in `git log main..HEAD --oneline`: run `git show --stat <sha>` and classify changed paths into `arm` (`src/arm101_hand/robots/`, `scripts/calibration/so_arm101/`) vs `hand` (`src/arm101_hand/hand/`, `scripts/calibration/amazing_hand/`) vs `shared` (`src/arm101_hand/config/`, repo-root).
- If any single PR-level change touches both arm AND hand but is split across commits **without** each commit independently leaving the repo runnable, flag IL-6.

Verdict:
- `FAIL` if a cross-device change is split and intermediate commits leave inconsistent state.
- `PASS` if cross-device changes are atomic, OR the split follows the runnable-at-each-commit pattern from IL-6.

### 8. IL-7 — Single-source-of-truth docs

Source: every fact has one canonical home (registry in `06-documentation-protocol.md` §10.1); other files use pointers.

Checks:
- For every changed `.md` file, scan for newly-added normative content (specs, procedures, tables) that duplicates content already canonical elsewhere.
- Common red flags:
  - Motor IDs restated outside `00-iron-laws.md` IL-3.
  - BOM lines restated outside `docs/BOM.md`.
  - Calibration commands restated outside `scripts/calibration/<device>/README.md`.
  - Iron Law text quoted verbatim outside `00-iron-laws.md`.
- New facts MUST be registered in `06-documentation-protocol.md` §10.1 in the same PR.

Verdict:
- `FAIL` if a duplicate restatement was introduced without a pointer.
- `FAIL` if a new canonical fact was added without registry entry.
- `PASS` otherwise.

## Report format (emit exactly this shape)

```
Iron Law Audit — branch <branch> vs main
========================================

Files changed: N    Lines: +A / -B

IL-1 voltage isolation              PASS | FAIL | N/A | EVIDENCE-INSUFFICIENT
  <one-line evidence with file:line>

IL-2 references read-only           PASS | FAIL | N/A | EVIDENCE-INSUFFICIENT
  ...

IL-3 motor ID canon                 ...
IL-4 COM-port discipline            ...
IL-5 calibration in version control ...
IL-6 atomic cross-device commits    ...
IL-7 single-source-of-truth docs    ...

Proposed PR checklist (paste into PR body's "Iron Laws touched" section):
- [x] IL-3 motor ID canon — <one-line why>
- [ ] IL-1 voltage isolation
- [ ] IL-2 references read-only
- [x] IL-5 calibration in version control
- [x] IL-6 atomic cross-device commits
- [ ] IL-4 COM-port discipline
- [ ] IL-7 single-source-of-truth docs

Blocking issues: <count>
Non-blocking observations: <count>
```

Keep the full report under 80 lines. For more detail, point at `path:line`; do not embed diff hunks.

## What this skill does NOT do

- Does **not** modify any file.
- Does **not** open or update PRs.
- Does **not** assert hardware state — IL-1 / IL-3 / IL-4 evidence is static-only. Bench verification belongs to the user.
- Does **not** replace the human reviewer per `05-git-workflow.md` §6.1 — it informs the reviewer.
