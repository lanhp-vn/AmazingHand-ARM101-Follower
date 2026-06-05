# `docs/conventions/` — AmazingHand + SO-ARM101 Follower

> Normative rules for this project. Read `00-iron-laws.md` first; everything else is an application of those.

## Reading order

New contributors should read in this order:

1. **[00-iron-laws.md](00-iron-laws.md)** — non-negotiable invariants. Voltage isolation, references-are-read-only, motor ID canon, COM-port discipline, calibration as YAML, atomic cross-device commits, single-source-of-truth.
2. **[01-module-layering.md](01-module-layering.md)** — what lives in `src/arm101_hand/{robots,hand,config}` vs `scripts/`, and the four import invariants that keep the layout coherent.
3. The relevant **code style** (`02` Python, `03` Shell).
4. **[04-testing-verification.md](04-testing-verification.md)** before adding code, not after.
5. **[05-git-workflow.md](05-git-workflow.md)** when you're ready to commit.
6. **[06-documentation-protocol.md](06-documentation-protocol.md)** when you change something that the repo-root `CLAUDE.md` or `README.md` describes.
7. **[07-kiss-simplicity.md](07-kiss-simplicity.md)** — cross-cutting: prefer the simplest design that satisfies the requirement and the Iron Laws.

## All files

| # | File | Governs |
|---|---|---|
| 00 | [00-iron-laws.md](00-iron-laws.md) | Non-negotiable invariants (voltage isolation, references-read-only, motor ID canon, COM discipline, calibration YAML, atomic commits, DRY) |
| 01 | [01-module-layering.md](01-module-layering.md) | Module layering: import-graph invariants for `src/arm101_hand/{robots,hand,config}` and `scripts/` |
| 02 | [02-code-style-python.md](02-code-style-python.md) | Python 3.12 style for everything in `src/`, `scripts/`, and ad-hoc tools |
| 03 | [03-code-style-shell.md](03-code-style-shell.md) | PowerShell (primary) + Bash (rare) style for the few shell helpers |
| 04 | [04-testing-verification.md](04-testing-verification.md) | Testing pyramid: host unit → on-bench integration. No HIL CI. |
| 05 | [05-git-workflow.md](05-git-workflow.md) | Branches, commits, PRs, atomic cross-device commit rule |
| 06 | [06-documentation-protocol.md](06-documentation-protocol.md) | How `CLAUDE.md` and `README.md` at repo root get refreshed; DRY / canonical ownership registry (IL-7) |
| 07 | [07-kiss-simplicity.md](07-kiss-simplicity.md) | KISS — prefer the simplest design; one responsibility per unit; YAGNI; mirror existing shapes |

`docs/BOM.md` lives one level up — it's the bill of materials + host PC spec snapshot, not a convention.

## Cross-cutting principles

Every file here respects these:

- **Iron Laws win**. If you find a conflict between a convention file and `00-iron-laws.md`, the Iron Law is authoritative. File a PR to fix the convention.
- **Point, don't copy**. Specific rules live in one file and are referenced from others. Duplicated rules drift.
- **Cite the source**. Hardware claims cite the relevant submodule under `references/`; lerobot-derived behavior cites `references/lerobot/src/lerobot/...`; rustypot behavior cites `references/rustypot/src/...`.
- **Meaningful over complete**. We optimize for clarity and relevance, not comprehensiveness. A rule that isn't enforced or checked is noise.

## What lives OUTSIDE this directory

| File | Role |
|---|---|
| `../../CLAUDE.md` | Agent self-reference — Iron Laws TL;DR + workflows. Loaded automatically by Claude Code. |
| `../../README.md` | Human-facing project overview + setup instructions. |
| `../BOM.md` | Bill of materials (servos, adapters, PSUs, host PC spec). |

## Maintenance

- **Every PR** that changes a rule here lands with commit scope `conventions` (see `05-git-workflow.md` §3.2).
- **A rule change typically cascades**: bumping an Iron Law may require touching several downstream convention files. Do it in one PR, not in drips.
- **No dead pointers.** Before merging, `grep -r 'docs/conventions/' .` and confirm every reference points at a file that exists.
