# SO-ARM101 — Follower Calibration

Calibration procedure for the SO-ARM101 follower arm with the gripper (motor ID 6) physically removed. Wraps lerobot's `lerobot-calibrate` so it does not crash on the missing motor 6.

Hardware: 5 STS3215 servos on a 12 V serial bus, IDs 1..5 shoulder → wrist (no gripper). The AmazingHand replaces the gripper at the end of the wrist.

Reference: <https://huggingface.co/docs/lerobot/so101?calibrate_follower=Command>.

---

## 1. Prerequisites

- 12 V / 3 A PSU on, Waveshare bus servo adapter (A) wired to the arm.
- Logic side of the adapter set to **3.3 V**.
- USB↔TTL adapter enumerated as a COM port. Discover with:

  ```powershell
  Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
  ```

  The Waveshare bus adapter typically enumerates as a CH343 or FT232 port. Note the COM number — it goes into `--robot.port` below.

- `uv sync` has been run from the repo root (provisions `.venv` with editable lerobot + our `SO101FollowerNoGripper` subclass).

> **Single-owner bus.** Close any other process holding the COM port (FD.exe, a serial monitor, a previous Python session) before running calibration. The driver chip will not multi-master.

## 2. Run calibration

From the repo root:

```powershell
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower
```

Replace `COM<X>` with the COM number you discovered above.

The `arm101-calibrate-follower` console script registers our `SO101FollowerNoGripper` subclass before delegating to lerobot's `calibrate()`. Without this wrapper, the upstream `lerobot-calibrate` does not know the type `so101_follower_no_gripper` exists.

`SO101FollowerNoGripperConfig` defaults `calibration_dir` to `scripts/calibration/so_arm101` (see `src/arm101_hand/robots/so101_follower_no_gripper.py`), so the JSON always lands in-tree and IL-5 is enforced by code, not by remembering a flag. Pass `--robot.calibration_dir=<path>` to override if you need a different location (rare).

## 3. What happens

Per the upstream lerobot calibration flow:

1. **Connect** — the script opens the bus and pings each of the 5 motors.
2. **Move-to-pose prompts** — you'll be asked to move the arm to mid-range, then sweep each joint through its full range. Press Enter at each step.
3. **Save** — calibration is written to:
   ```
   scripts/calibration/so_arm101/<id>.json
   ```
   where `<id>` is what you passed via `--robot.id`. With the example command above that's `scripts/calibration/so_arm101/so101_follower.json` — committed to git as the canonical follower calibration (IL-5).

## 4. Discover the COM port

```powershell
uv run python scripts/diagnostics/find_port.py
```

Thin wrapper over `lerobot-find-port` (see `lerobot.scripts.lerobot_find_port`). The interactive flow asks you to unplug the adapter, hit Enter, replug, hit Enter again — the difference between the two enumerations is the port you want.

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `KeyError: 'so101_follower_no_gripper'` or `unknown robot type` | You ran `lerobot-calibrate` directly instead of `arm101-calibrate-follower` | Use `uv run arm101-calibrate-follower …` so the subclass registers first |
| `Calibration saved to C:\Users\…\.cache\huggingface\lerobot\…` instead of `scripts/calibration/so_arm101/…` | The subclass default was bypassed — either you ran upstream `lerobot-calibrate` (which doesn't know our subclass) or you passed `--robot.calibration_dir` pointing outside the repo | Use `arm101-calibrate-follower` and either drop `--robot.calibration_dir` or point it back to `scripts/calibration/so_arm101`; then move/delete the cache copy |
| `serial.serialutil.SerialException: could not open port 'COM<X>'` | Wrong COM number, or another process holds the port | Re-run §1 discovery; close FD.exe / serial monitors |
| Ping fails for one motor | Bad cable, dead servo, or duplicate ID on the bus | Wire that motor alone and check with FD.exe or `lerobot-find-port` |
| Calibration completes but arm jogs the wrong direction | Wire conventions mismatched | Re-check IDs 1..5 are shoulder_pan → shoulder_lift → elbow_flex → wrist_flex → wrist_roll |
| Position drifts after release | Backlash in the harmonic / belt drive, normal at low torque | Set holding torque from the teleop side; not a calibration issue |

## 6. Verify/audit helpers

These standalone scripts sanity-check the 5 servos and the calibration against the
real mechanism. They keep `arm101-calibrate-follower` (lerobot) as the source of
truth and **never write `so101_follower.json`** (IL-5). All read `arm.port` from
`data/app_config.yaml`. Close any other bus owner first (IL-4).

The read-only diagnostics (`scan.py`, `show_calib.py`, `find_port.py`) now live in
`scripts/diagnostics/` and are dual-device — pass `--device arm` here (or `--device hand`
for the AmazingHand). The motion helpers (`sweep.py`, `set_pose.py`, `jog.py`,
`capture_pose.py`) stay in this folder.

| Script | Motion? | Torque | Norm mode | Purpose |
| --- | --- | --- | --- | --- |
| `diagnostics/show_calib.py --device arm` | no | off | DEGREES | Print per-joint calibration (id, homing, range, degree span, midpoint). `--live` also reads present position. |
| `diagnostics/scan.py --device arm` | no | off | raw | Ping motors 1–5; report position/load/voltage/temperature. Exit 1 if any motor is missing. |
| `sweep.py` | yes | on→off | RANGE_M100_100 | Drive a joint (or `all`) to its calibrated endpoints (`±margin`, default 90) and back; verify no buzz/stall. |
| `set_pose.py` | yes | on→off | DEGREES | Drive to a `poses` entry from `data/arm_config.yaml` (`home` — the folded storage pose) and hold until Enter. |
| `jog.py` | yes | on→off | DEGREES | Keyboard-jog each motor in degrees (clamped to range); `t` hand-pose toggle; `h` home a joint; `s` save current pose to `data/arm_config.yaml`. Returns home before releasing torque. |
| `capture_pose.py` | yes | off→on→off | DEGREES | Torque off so you hand-pose the arm; Enter captures the present degrees and holds; save to `data/arm_config.yaml` `poses`. `h` = home & capture another, `q` = home & quit (both park home first). |

```powershell
uv run python scripts/diagnostics/show_calib.py --device arm         # offline dump
uv run python scripts/diagnostics/show_calib.py --device arm --live  # + live position
uv run python scripts/diagnostics/scan.py --device arm               # bus health (pre-flight)
uv run python scripts/calibration/so_arm101/sweep.py wrist_flex --margin 70
uv run python scripts/calibration/so_arm101/set_pose.py home
uv run python scripts/calibration/so_arm101/jog.py                  # interactive keyboard jog
uv run python scripts/calibration/so_arm101/capture_pose.py            # hand-pose the arm, capture + save
```

**Norm-mode split.** `sweep.py` uses `RANGE_M100_100`, where the calibrated range maps
to exactly ±100 with built-in clamping — so driving to an endpoint is just commanding
±margin, inherently bounded. `set_pose.py` / `show_calib.py` use `DEGREES` to match the
`arm_config.yaml` pose convention; in that mode a value is degrees relative to the
range *midpoint*, so each joint's usable window is symmetric `[-span/2, +span/2]`.

**Onboard-frame note.** `sweep.py` and `set_pose.py` push the on-file calibration to the
motors after connecting (`bus.write_calibration`) so normalized targets physically match
the recorded range. This writes the **motors**, not the JSON. `show_calib.py --live` does
not, so its live degrees assume the motors still hold the committed calibration from the
last calibrate run.

**Release on exit (motion scripts).** `sweep.py`, `set_pose.py`, `jog.py`, and `capture_pose.py`
**never auto-return the arm to home on exit** — that would be a surprise movement. Instead, if
the arm is still holding a pose under torque, they prompt: type `h` to drive back to home
first, or just `Enter` to release torque in place (it sags under gravity from a raised pose).
`Ctrl+C`/EOF at the prompt releases in place. Torque is always released on exit (IL-4). This is
implemented once in `_common.confirm_and_release`. (`scan.py` and `show_calib.py` are read-only
— they never enable torque or move. `jog.py` left in the `t` torque-off / hand-pose state just
disconnects in place.)

**Jog controls (`jog.py`).** Torque ON to move; `msvcrt` raw keys:

| Key | Action |
| --- | --- |
| `1`–`5` | select joint (shoulder_pan … wrist_roll) |
| `↑` / `↓` | jog active joint ± step (deg), clamped to calibrated range |
| `[` / `]` | shrink / grow step (1–15°) |
| `h` | home active joint to its default-home value (`poses.home`) |
| `t` | toggle torque (off = hand-pose by hand; on = resync + hold) |
| `s` | save current pose to `data/arm_config.yaml` (prompts for a name) |
| `q` / `Ctrl+C` | on quit, prompts `h` (return home first) or `Enter` (release in place); never auto-homes (if torque off: disconnect in place) |

Saved poses land in `data/arm_config.yaml` and are drivable by name with `set_pose.py`.

**Recommended order:** `scan` (all 5 respond?) → `show_calib` (numbers sane?) →
`sweep` per joint (endpoints clean?) → `set_pose home` (parks cleanly?).
