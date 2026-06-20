# 06 — Documentation Protocol

> How `CLAUDE.md` (repo root, agent self-reference) and `README.md` (repo root, human-facing) are refreshed after the codebase evolves. Also hosts the **DRY / single-source-of-truth registry (IL-7)** at §10–§11.

> **Scope**:
>
> 1. **Routine refresh** of the two root-level docs (`CLAUDE.md`, `README.md`) after meaningful changes — §1–§7.
> 2. **DRY / canonical ownership (IL-7)** for ALL workspace Markdown files — §10–§11.
>
> Files inside `docs/conventions/` are updated via regular PRs per `05-git-workflow.md`; this protocol does not automate those. But they are subject to IL-7, and the pointer rules in §10 are the enforcement hook.

---

## 1. When This Protocol Fires

Run this protocol when any of the following is true:

1. **New module added** under `src/arm101_hand/` (e.g., a new device wrapper, a new console script).
2. **New calibration script** added under `scripts/calibration/`.
3. **Hardware setup changed** (new bus adapter, swapped servo, new COM-port assignment, new BOM line item).
4. **`pyproject.toml` dependencies changed** materially (added a top-level package, dropped one, bumped a major version).
5. **Iron Law added, removed, or materially reworded** in `00-iron-laws.md`.
6. **Before every tagged release** — `CLAUDE.md` and `README.md` must be current as of the release commit.

Do NOT run for:

- Routine bug fixes that don't change architecture or workflows.
- Edits to files inside `docs/conventions/` — those reference each other; `CLAUDE.md` points to the directory, not individual files.

## 2. Mandatory Pre-Flight

Before proposing any changes, verify the current state. Do not assume — read.

1. `git log --oneline -20` — surface recent commits that might have changed architecture.
2. `git ls-files docs/conventions/` — confirm the convention file list matches what `CLAUDE.md` references.
3. `git ls-files src/arm101_hand/ scripts/` — confirm the source/script tree matches what `CLAUDE.md` claims.
4. Read the existing `CLAUDE.md` and `README.md` so the diff is targeted, not a full rewrite.

If hardware changed (IL-1, IL-3, IL-4 territory):

5. Verify the COM-port assignment in `docs/BOM.md` matches `Get-PnpDevice -Class Ports -Status OK`.
6. Verify the motor-ID layout in `00-iron-laws.md` §IL-3 still matches the bench reality.

## 3. Context Analysis

### 3.1 Identify what's actual vs planned

Be careful in `CLAUDE.md` to distinguish:

- **"Current" state** — what's on disk and runnable right now.
- **"Planned" state** — what we intend to build but haven't yet.

Agents reading `CLAUDE.md` will confuse the two unless they're flagged clearly. Use the phrase "(planned)" or "(not yet implemented)" inline.

### 3.2 Note the source layout

The source-of-truth tree is `src/arm101_hand/` + `scripts/`. If a new top-level dir appeared (e.g., `tests/`, `notebooks/`), `CLAUDE.md` §4 needs an entry.

## 4. Update `CLAUDE.md` (Agent Self-Reference)

**Goal**: a compact (≤ 250-line) document that an agent dropping into this repo for the first time can read to answer "what is this, how do I work here, what rules bind me?"

### 4.1 Required sections (in this order)

1. **Project TL;DR** — one paragraph; what it does, what hardware it controls.
2. **Iron Laws TL;DR** — one line per law from `00-iron-laws.md`. Point to the full file.
3. **Directory tree** — high-level (3 levels deep max) with one-line role per directory.
4. **Common workflows** — `uv sync`, calibration commands, how to discover a port. Short commands, pointers to the relevant `scripts/calibration/<device>/README.md` for depth.
5. **Convention file pointers** — one section listing every `docs/conventions/*.md` with a one-line role.
6. **Don'ts** — compressed reminder of the most common foot-guns, each citing the source rule.

### 4.2 What to keep OUT of `CLAUDE.md`

- **Detailed rules** — they live in `docs/conventions/`. `CLAUDE.md` is an index and a TL;DR, not a replacement.
- **Code samples** — unless absolutely necessary for orientation, they belong in the referenced convention file or the script's README.
- **Changelogs** — git history tracks that.

### 4.3 Style

Keep it tight: short paragraphs, bullets > prose, code fences for commands, no emojis unless the user explicitly asks.

## 5. Update `README.md` (Human-Facing)

**Goal**: a developer who clones this repo for the first time can install the env, discover the COM ports, and run the first calibration command.

### 5.1 Required sections

1. **Project title + tagline** — one paragraph.
2. **What it is** — 2–3 bullets of capability.
3. **Hardware requirements** — point to `docs/BOM.md` for the BOM.
4. **Host setup** — `uv sync` from a fresh clone.
5. **First commands** — discover COM ports, run AmazingHand calibration, run SO-ARM101 calibration. Point to the per-device READMEs for depth.
6. **Repo layout** — brief tree (point to `01-module-layering.md` for the canonical layering).
7. **Conventions** — one-sentence pointer to `docs/conventions/README.md`.
8. **License** — as applicable.

### 5.2 Style

- Second-person voice ("You'll need..."). Friendly.
- Assume the reader is a senior engineer new to this repo.
- **No Iron Laws in `README.md`** — those belong in `CLAUDE.md` (agent-facing) and `00-iron-laws.md` (normative).

## 6. Execution Steps

1. **Draft updates** — propose diffs for `CLAUDE.md` and `README.md`. Prefer small, targeted edits over full rewrites unless the architecture changed materially.
2. **Show the diff** — present changed hunks; summarize why each changed.
3. **Wait for approval** — do not write until the user confirms (when working as an agent).
4. **Apply** — write the updated files.
5. **Verify** — `wc -l` as a sanity check (`CLAUDE.md` ≤ 250 lines, `README.md` ≤ 400 lines).
6. **Commit** — single commit, scope `docs`, body notes what change prompted the update.

## 7. Failure Modes to Watch

- **Drift without detection**: `CLAUDE.md` claims a directory exists that doesn't, or names a script that's been renamed. Run `git ls-files` and grep `CLAUDE.md` + `README.md` for paths; every claimed path must exist or be marked "planned".
- **Duplicate truth**: rules copied from `00-iron-laws.md` into `CLAUDE.md` start to diverge. `CLAUDE.md` holds **summaries only**; any normative text points to the convention file.
- **Overgrowth**: `CLAUDE.md` past 250 lines loses its TL;DR value. Trim.

---

## 10. DRY / Canonical Ownership Registry (IL-7)

Every normative fact, procedure, spec, or reusable table lives in **one** Markdown file. Other files point to it with `[see X §Y](path)` — never inline-restate. Pointer forms (all acceptable):

- Cross-file link with anchor: `[IL-3](00-iron-laws.md#il-3-motor-id-assignment-is-canonical)`
- Section reference: "see `01-module-layering.md` §2 for the import invariants"
- Path-only reference: "the calibration YAML lives at `scripts/calibration/amazing_hand/hand_calib_values.yaml`"

### 10.1 Registry by domain

#### Iron Laws + repo-root docs
| Topic | Canonical | Scope |
|---|---|---|
| Iron Laws (normative, full) | `00-iron-laws.md` | All 7 laws |
| Iron Laws one-line TL;DR | `CLAUDE.md` (Iron Laws section) | IL-7 exception: labeled summary, ends with pointer |
| Top-level directory tree | `CLAUDE.md` | Source |
| Source layering invariants | `01-module-layering.md` | Source |

#### Hardware
| Topic | Canonical |
|---|---|
| Bill of materials, host PC spec | `docs/BOM.md` |
| Voltage isolation, hand vs arm bus | `00-iron-laws.md` IL-1 |
| Motor ID canon (hand 1–8, arm 1–5, no gripper) | `00-iron-laws.md` IL-3 |
| COM-port discipline (single owner per bus) | `00-iron-laws.md` IL-4 |

#### Calibration
| Topic | Canonical |
|---|---|
| AmazingHand finger calibration procedure | `scripts/calibration/amazing_hand/README.md` |
| AmazingHand calibration values (live data) | `scripts/calibration/amazing_hand/hand_calib_values.yaml` |
| SO-ARM101 follower calibration procedure | `scripts/calibration/so_arm101/README.md` |
| SO-ARM101 calibration JSON location | lerobot default; `scripts/calibration/so_arm101/README.md` §3 |

#### Code style
| Topic | Canonical |
|---|---|
| Python 3.12 style, ruff/mypy config | `02-code-style-python.md` |
| PowerShell + Bash style | `03-code-style-shell.md` |

#### Testing, git, docs
| Topic | Canonical |
|---|---|
| Testing pyramid + table-driven idioms | `04-testing-verification.md` |
| Conventional Commits, scopes, atomic cross-device commit (IL-6) | `05-git-workflow.md` |
| Doc-update protocol + DRY registry (this file) | `06-documentation-protocol.md` |

#### AI / ML inference
| Topic | Canonical |
|---|---|
| DR-grading architecture + design rationale | `docs/superpowers/specs/2026-06-11-dr-grading-design.md` |
| DR-grading inline-capture (Phase 2) design | `docs/superpowers/specs/2026-06-11-dr-grading-phase2-inline-design.md` |
| DR-grading run order + recorded APTOS validation metrics + checkpoint provenance | `scripts/fundus_analysis/README.md` |
| PyTorch checkpoint load pattern (weights_only, safetensors export, timm ViT recipe) | `.claude/skills/ml-inference/SKILL.md` |

#### Computer vision (system camera)
| Topic | Canonical |
|---|---|
| Arc auto-trigger design + lifecycle (HSV per-arc-band masking, green-clear re-arm) | `docs/superpowers/specs/2026-06-19-arc-auto-trigger-design.md` |
| Arc auto-trigger implementation plan (TDD task breakdown) | `docs/superpowers/plans/2026-06-19-arc-auto-trigger.md` |

### 10.2 When you add a new topic

1. Decide where the canonical file is. If an obvious home doesn't exist, create one.
2. Add the topic to §10.1 of this file in the **same PR**.
3. Every other file that mentions the topic uses a pointer.
4. If a topic is genuinely split (e.g., calibration procedure in the script's README + Iron Law in `00-iron-laws.md`), document the split explicitly.

## 11. Allowed Summaries (the IL-7 exceptions)

The only places where a controlled summary of normative content is permitted. Each carries a pointer to the canonical source and is clearly labeled as a summary.

| Location | Content | Constraint |
|---|---|---|
| `CLAUDE.md` Iron Laws section | One line per law | Each ends with/implies a pointer to `00-iron-laws.md` |
| `CLAUDE.md` workflow cheat sheet | Commands only | Rationale in the per-device READMEs |
| `CLAUDE.md` Don'ts | One-line pithy reminder | Each cites source |
| `README.md` capability bullets | Human orientation | Not normative; no specs |

### 11.1 Disallowed patterns (always)

- Copy-pasted table (same headers, same values) in two files.
- Multi-paragraph restatement of a rule, procedure, or spec.
- Divergent summaries of the same fact (e.g., motor IDs listed differently in two places).
- The phrase "for convenience we restate…" — smell.

---

## 12. Checklist (for a doc-update PR)

### 12.1 Routine refresh (`CLAUDE.md` / `README.md`)
- [ ] Pre-flight §2 completed; bench state confirmed (or absence flagged).
- [ ] `CLAUDE.md` ≤ 250 lines.
- [ ] `README.md` ≤ 400 lines.
- [ ] Every claimed file path exists (`git ls-files | grep <path>`) or is marked "planned".
- [ ] Commit scope `docs`, body notes what change prompted the update.

### 12.2 DRY (IL-7) — applies to any PR that touches `.md`
- [ ] New facts/specs added? Registered in §10.1 in this same PR.
- [ ] No inline restatement of content that has a canonical home.
- [ ] Summaries (if any) match the allowed-summaries list §11 and end with a pointer.
