# 03 — Code Style: Shell

> Governs shell helpers in this repo. The primary host shell is **PowerShell** (Windows 11). Bash is available via `uv run`-launched WSL sessions or the few cross-platform `.sh` files we may add for setup.

> **Scope**: any `.ps1`, `.psm1`, or `.sh` file in this repo. Most logic lives in Python — shell is for one-shot setup, port discovery, and `uv`/`git` wrappers.

---

## 1. Core Principles

1. **Prefer Python.** If a shell helper exceeds 30 lines or branches on more than two conditions, rewrite it as a Python script under `scripts/` and invoke from a one-line shell wrapper. PowerShell and Bash both have rough edges that Python avoids.
2. **Quote everything.** Every variable expansion is double-quoted (PowerShell) or `"$var"`-quoted (Bash). No exceptions.
3. **Fail fast.** PowerShell: `$ErrorActionPreference = 'Stop'` at the top of any non-interactive script. Bash: `set -euo pipefail`.
4. **No shell aliases in committed scripts.** They depend on user environment.
5. **Wrap long-running probes with a timeout.** A hung COM-port open should not hang a script indefinitely.

## 2. PowerShell (primary)

### 2.1 Standard preamble

```powershell
#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
```

| Setting | Effect |
|---|---|
| `#requires -Version 5.1` | Documents minimum PowerShell version (Windows PowerShell 5.1 is the default on Windows 11 Home — see `docs/BOM.md` §1) |
| `$ErrorActionPreference = 'Stop'` | Cmdlet errors throw instead of silently continuing |
| `Set-StrictMode -Version Latest` | Catches uninitialized variables, mistyped property names, etc. |

### 2.2 Native command exit codes

PowerShell does **not** propagate native command exit codes by default — `& cmd.exe /c "exit 1"` does not throw. Check `$LASTEXITCODE` after every native invocation:

```powershell
& uv sync
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed with exit code $LASTEXITCODE"
}
```

### 2.3 Quoting and string interpolation

```powershell
# Good
$port = "COM18"
Write-Host "Opening $port at $baudrate bps"

# Bad — single quotes don't interpolate
Write-Host 'Opening $port at $baudrate bps'   # literal "$port"

# Good — Get-PnpDevice probe with a real cmdlet
Get-PnpDevice -Class Ports -Status OK |
    Select-Object Name, DeviceID |
    Format-Table -AutoSize
```

### 2.4 Error handling

```powershell
try {
    Test-Path "scripts/calibration/amazing_hand/hand_calib_values.yaml" -PathType Leaf
    if ($LASTEXITCODE -ne 0) { throw "calibration YAML missing" }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
```

### 2.5 Filename and naming

| Kind | Convention | Example |
|---|---|---|
| Filename | `Verb-Noun.ps1` (PowerShell convention) | `Find-RobotPort.ps1` |
| Function | `Verb-Noun` from the [approved verbs list](https://learn.microsoft.com/powershell/scripting/developer/cmdlet/approved-verbs-for-windows-powershell-commands) | `Get-RobotPort`, `Test-CalibrationYaml` |
| Variable | `$camelCase` (script-scope), `$script:CamelCase` (module-scope) | `$port`, `$script:DefaultBaudrate` |
| Constant | `$ALL_CAPS` (convention; PowerShell has no const) | `$DEFAULT_BAUDRATE = 1000000` |

Use `Set-Variable -Option Constant` if you want immutability enforced.

## 3. Bash (rare)

If we add a `.sh` file (cross-platform setup helper, CI script), use Bash, not `/bin/sh` POSIX. Target Bash 5+.

### 3.1 Standard preamble

```bash
#!/bin/bash
set -euo pipefail
IFS=$'\n\t'
```

### 3.2 Filename, naming, structure

| Kind | Convention | Example |
|---|---|---|
| Filename | `kebab-case.sh` | `setup-uv.sh` |
| Function | `snake_case()` | `verify_uv()`, `discover_port()` |
| Global / readonly | `UPPER_SNAKE` | `DEFAULT_PORT`, `BAUDRATE` |
| Local | `lower_snake` | `local src dst` |

### 3.3 Quoting

```bash
# Good
for finger in "${VALID_FINGERS[@]}"; do ... done
if [[ -z "$port" ]]; then ... fi

# Bad — unquoted, single-bracket test
for finger in ${VALID_FINGERS[@]}
if [ -z $port ]
```

### 3.4 Builtins over external processes

| Prefer | Over | Why |
|---|---|---|
| `${var//old/new}` | `echo "$var" \| sed 's/old/new/'` | Builtin |
| `${#string}` | `echo "$string" \| wc -c` | Builtin |
| `$(< "$file")` | `$(cat "$file")` | Builtin |
| `[[ "$x" == prefix* ]]` | `echo "$x" \| grep -q '^prefix'` | Builtin |

### 3.5 ShellCheck

If the project ever adds Bash scripts in earnest, gate on `shellcheck` clean:

```bash
shellcheck scripts/*.sh
```

CI is not set up; this is reviewer-enforced for now.

## 4. What we don't write

- **systemd units.** No board, no embedded Linux.
- **Cross-host SSH wrappers.** No remote target.
- **Deployment scripts.** No deployment — `uv sync` is the entire setup.

If the project's scope expands to include a remote board or a deployment story, this file gets a new section then. Don't add empty placeholder sections now.

---

## 5. Checklist (paste into PR if your change touches a `.ps1` or `.sh`)

- [ ] PowerShell: `$ErrorActionPreference = 'Stop'` at the top
- [ ] PowerShell: `$LASTEXITCODE` checked after every native invocation
- [ ] Bash: `set -euo pipefail`, `shellcheck` clean
- [ ] Every `$var` is double-quoted
- [ ] Filename matches the convention (`Verb-Noun.ps1` or `kebab-case.sh`)
- [ ] No interactive prompts in non-interactive scripts (use `Read-Host`/`read` only when running interactively)
- [ ] Long-running operations have a timeout
