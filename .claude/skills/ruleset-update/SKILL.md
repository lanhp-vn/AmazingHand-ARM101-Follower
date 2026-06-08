---
name: ruleset-update
description: Review and generalize the workspace permission ruleset in .claude/settings.local.json based on commands granted during the current session. Use when the user wants to tidy up per-approval rule entries into reusable glob patterns, consolidate duplicates, and reaffirm the deny list. Does NOT broaden scope beyond what the user has already approved in this or prior sessions.
---

# ruleset-update

Rewrite `.claude/settings.local.json` so the `allow` list reflects the user's approvals generalized into reusable patterns, and the `deny` list keeps the standard safety rails.

## Procedure

1. **Read the current ruleset**:
   ```bash
   cat .claude/settings.local.json
   ```
   The `allow` array accumulates specific command strings each time the user approves a Bash call. Over a session it fills with overly narrow entries like `Bash(uv pip list | findstr ^lerobot)`.

2. **Scan this session's tool-use history** for every Bash / WebFetch call that was actually run and approved. Group by intent. Common families in this project:
   - `uv` invocations (`uv sync`, `uv run`, `uv pip list`, `uv pip install`)
   - `git` reads (`git status`, `git log`, `git diff`, `git ls-files`)
   - PowerShell PnP / port discovery (`Get-PnpDevice`, `Select-Object`, `Format-Table`)
   - Read-only inspection (`ls`, `find`, `wc`, `head`, `tail`)
   - WebFetch domains approved during the session (typically `docs.rs`, `crates.io`, `pypi.org`, `huggingface.co`)
   - Anything else the user approved

3. **Generalize each group into the narrowest glob that still covers the approved intent**. Examples for this project:

   | Session-specific entry (before) | Generalized rule (after) |
   | --- | --- |
   | `Bash(uv pip list)` | `Bash(uv pip *)` |
   | `Bash(uv run python scripts/calibration/amazing_hand/finger_test.py)` | `Bash(uv run *)` |
   | `Bash(uv sync --extra dev)` | `Bash(uv sync *)` |
   | `Bash(git log --oneline -20)` | `Bash(git *)` |
   | `Bash(Get-PnpDevice -Class Ports -Status OK)` | `Bash(Get-PnpDevice *)` |
   | `WebFetch(domain:docs.rs)` | keep as-is (domain-scoped) |

   Rules:
   - Prefer `command:*` over fully-specified strings when the user clearly approved the whole command family.
   - Keep domain scope on network calls — do not widen `WebFetch(domain:docs.rs)` to all domains.
   - Keep path scope on filesystem operations targeting submodules — never grant write/edit access to `references/**` (IL-2 read-only).
   - Drop exact duplicates and strings fully subsumed by a glob already in the list.
   - For `WebFetch`, keep the `domain:<host>` form — do not widen to all domains.

4. **Preserve the deny list** (standard safety rails — always include these, do not drop):
   - `Bash(rm -rf:*)`
   - `Bash(git push --force:*)`, `Bash(git push -f:*)`
   - `Bash(git reset --hard:*)`
   - `Bash(git clean -f:*)`
   - `Bash(git branch -D:*)`
   - `Bash(curl * | sh)`, `Bash(curl * | bash)`, `Bash(wget * | sh)` (pipe-to-shell installs)
   - `Bash(* --no-verify*)` (hook bypass)
   - **Project-specific (IL-2): any write or edit targeting `references/**`** — this path is read-only vendored code.

   If the user has added custom deny rules, keep those too.

5. **Write the new `.claude/settings.local.json`** with a single `permissions` object containing `allow` and `deny`. Preserve any non-permissions keys that were already in the file.

6. **Show the user a diff-style summary**: what consolidated into what, and anything new added to deny. Do not just rewrite silently.

## Guardrails

- **Never add a rule the user did not approve this session or in the existing file.** This skill generalizes; it does not invent new permissions.
- **Never remove a deny rule** unless the user explicitly asks. Broadening allow is reversible; dropping a deny is not.
- **Ask before widening scope** if a generalization would cover meaningfully more than what was approved — e.g. going from `Bash(uv pip list)` to `Bash(uv:*)` is a reasonable family glob, but going from `WebFetch(domain:docs.rs)` to a multi-domain wildcard requires confirmation.
- **Settings do not hot-reload.** Tell the user to restart Claude Code in this workspace for the new rules to take effect.
