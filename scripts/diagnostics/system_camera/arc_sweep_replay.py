"""Offline replay of the red-detection sweep over saved arc-calibration cases (host-only diagnostic).

Re-runs the calibration red-detection sweep (``sweep_red_detection``) over the labelled cases saved
by ``calibrate_view.py`` (and the ``usb_camera_arc_debug.py`` collector) with NO hardware -- no arm,
no hand, no camera, no Aurora. Each case is a ``<stem>.json`` sidecar (carrying the operator's
``expected`` 'red'/'clear' label + the dragged arc geometry) beside a ``<stem>_clean.png`` (the
640x480 deskewed ROI the detector runs on). The tool loads every labelled case, rebuilds the
``ArcCase`` list, runs the sweep, and prints the chosen a* cutoff + coverage threshold + per-case
outcome -- including the transitional one-arc-blank 'red' frames the sweep excludes from the fit.

Use it to validate a ``calibration.py`` / sweep change against the real collected evidence (e.g. after
retuning the coverage floor, hue pooling, or ``blank_eps``) WITHOUT re-running the staged bench grab.
By default it only READS the saved cases (under git-ignored ``media_outputs/``) and prints the result.

With ``--write`` it applies a SEPARABLE sweep result to ``system_camera_config.yaml`` -- the swept
``a_star_min`` + ``coverage_threshold`` plus the cases' arc geometry -- through the sanctioned
``write_calibration_values`` (atomic, pydantic-validated, writes a ``.bak``; IL-5 -- never a raw hand
edit), after a printed before/after diff + a ``[y/N]`` confirm. It PRESERVES the current ``screen_roi``
(the replay cannot re-derive the screen rect from the already-deskewed crops); if you re-positioned the
screen or ROI since capturing, run ``calibrate_view.py`` instead. It refuses to write a non-separable
sweep or a mixed-arc-geometry case set.

Cases without an ``expected`` label (e.g. raw ``usb_camera_arc_debug.py`` captures, which carry only
a free ``expected_note``) are skipped -- capture labelled cases via ``calibrate_view.py``'s test loop,
or add an ``expected`` field to their sidecars first.

Usage:
  uv run python scripts/diagnostics/system_camera/arc_sweep_replay.py [--from DIR] [--write] [--config PATH]

  --from DIR     folder of saved cases (default: <repo>/media_outputs/arc_calib)
  --write        apply a separable result to the config (prompts to confirm; preserves screen_roi)
  --config PATH  config file to update with --write (default: src/arm101_hand/data/system_camera_config.yaml)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.config.system_camera_config import RoiBox  # noqa: E402
from arm101_hand.system_camera.calibration import (  # noqa: E402
    ArcCase,
    SweepResult,
    describe_case,
    sweep_red_detection,
    write_calibration_values,
)

_DEFAULT_DIR = _REPO_ROOT / "media_outputs" / "arc_calib"
_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


@dataclass(frozen=True)
class LabeledCase:
    """One saved case loaded from disk: the file stem, the labelled ArcCase (deskewed-ROI frame +
    'red'/'clear' expected), and the arc geometry recorded in its sidecar."""

    stem: str
    case: ArcCase
    left_arc: RoiBox
    right_arc: RoiBox


def load_arc_cases(case_dir: Path) -> list[LabeledCase]:
    """Load every ``<stem>.json`` carrying a valid ``expected`` ('red'/'clear') label + its
    ``<stem>_clean.png`` from ``case_dir``. Unlabelled sidecars (missing/invalid ``expected``) and
    cases whose clean PNG is missing or unreadable are skipped. Sorted by stem for stable indices."""
    cases: list[LabeledCase] = []
    for jp in sorted(case_dir.glob("*.json")):
        meta = json.loads(jp.read_text(encoding="utf-8"))
        expected = meta.get("expected")
        if expected not in ("red", "clear"):
            continue
        frame = cv2.imread(str(jp.with_name(jp.stem + "_clean.png")))
        if frame is None:
            continue
        cases.append(
            LabeledCase(
                stem=jp.stem,
                case=ArcCase(frame=frame, expected=expected),
                left_arc=RoiBox(**meta["left_arc"]),
                right_arc=RoiBox(**meta["right_arc"]),
            )
        )
    return cases


def _arc_key(lc: LabeledCase) -> tuple[int, ...]:
    a, b = lc.left_arc, lc.right_arc
    return (a.x, a.y, a.w, a.h, b.x, b.y, b.w, b.h)


def format_sweep_report(result: SweepResult, cases: list[LabeledCase]) -> str:
    """Render the sweep outcome as text: chosen bands + threshold, separability over the FITTED set,
    the fitted/excluded tally, and a per-case line (ok / WRONG / EXCLUDED) with the describe_case
    sentence. Pure (no I/O) so it is unit-testable."""
    n_excluded = len(result.excluded_transitional)
    n_fit = len(cases) - n_excluded
    ok = n_fit - len(result.unsatisfied)
    lines = [
        f"coverage_threshold = {result.coverage_threshold}",
        f"separable (over fitted set) = {result.separable}",
        f"a*_min = {result.a_star_min}",
    ]
    lines += [
        "",
        f"{ok}/{n_fit} fitted cases correct ({n_excluded} excluded as transitional)",
        "",
        "per-case:",
    ]
    excluded = set(result.excluded_transitional)
    unsatisfied = set(result.unsatisfied)
    for i, lc in enumerate(cases):
        det_left, det_right = result.case_detections[i]
        tag = "EXCLUDED" if i in excluded else ("WRONG" if i in unsatisfied else "ok")
        lines.append(
            f"  [{i:2d}] {tag:8s} exp={lc.case.expected:5s}  "
            f"{describe_case(lc.case.expected, det_left, det_right)}"
        )
    return "\n".join(lines)


def _metric_str(a_star_min: int, coverage_threshold: float) -> str:
    """Compact one-line render of the a* cutoff + threshold for the --write before/after diff."""
    return f"a*_min={a_star_min} thr={coverage_threshold}"


def build_write_kwargs(
    result: SweepResult, cases: list[LabeledCase], current_screen_roi: RoiBox
) -> dict[str, object]:
    """Assemble the ``write_calibration_values`` kwargs for ``--write``: the swept ``a_star_min`` +
    ``coverage_threshold``, the cases' arc geometry (the cutoff/threshold are valid only for the arcs
    the sweep ran on, so they travel together), and the CURRENT ``screen_roi`` preserved unchanged (the
    replay does not re-derive the screen rect). Pure -- unit-testable."""
    return {
        "screen_roi": current_screen_roi,
        "left_arc": cases[0].left_arc,
        "right_arc": cases[0].right_arc,
        "a_star_min": result.a_star_min,
        "coverage_threshold": result.coverage_threshold,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--from",
        dest="from_dir",
        default=str(_DEFAULT_DIR),
        metavar="DIR",
        help="folder of saved cases (default: <repo>/media_outputs/arc_calib)",
    )
    ap.add_argument(
        "--write",
        action="store_true",
        help="apply a separable result to the config (prompts to confirm; preserves screen_roi)",
    )
    ap.add_argument(
        "--config",
        default=str(_CONFIG_PATH),
        metavar="PATH",
        help="config file to update with --write (default: src/arm101_hand/data/system_camera_config.yaml)",
    )
    args = ap.parse_args()

    case_dir = Path(args.from_dir)
    if not case_dir.is_dir():
        print(f"ERROR: no such directory: {case_dir}", file=sys.stderr)
        return 1

    n_json = len(list(case_dir.glob("*.json")))
    cases = load_arc_cases(case_dir)
    if not cases:
        print(
            f"ERROR: no labelled cases in {case_dir} ({n_json} .json file(s) present, none with a valid "
            "'expected' label + readable _clean.png). Capture labelled cases with calibrate_view.py.",
            file=sys.stderr,
        )
        return 1

    n_red = sum(lc.case.expected == "red" for lc in cases)
    n_clear = sum(lc.case.expected == "clear" for lc in cases)
    skipped = n_json - len(cases)
    suffix = f", {skipped} skipped" if skipped else ""
    print(f"Loaded {len(cases)} labelled case(s) from {case_dir}  ({n_red} red, {n_clear} clear{suffix})")

    left_arc, right_arc = cases[0].left_arc, cases[0].right_arc
    distinct = {_arc_key(lc) for lc in cases}
    if len(distinct) > 1:
        print(f"  NOTE: {len(distinct)} distinct arc geometries across cases; using the first.")
    print(
        f"  arcs: left={left_arc.x},{left_arc.y},{left_arc.w},{left_arc.h}  "
        f"right={right_arc.x},{right_arc.y},{right_arc.w},{right_arc.h}"
    )

    try:
        result = sweep_red_detection([lc.case for lc in cases], left_arc, right_arc)
    except ValueError as exc:
        print(f"\nSweep could not fit a band: {exc}", file=sys.stderr)
        return 1

    print("\n=== SWEEP RESULT ===")
    print(format_sweep_report(result, cases))

    if not args.write:
        return 0

    if len(distinct) > 1:
        print(
            "\nERROR: --write needs all cases to share ONE arc geometry (the swept band/threshold are "
            "valid only for the arcs the sweep ran on). Filter the case set to one geometry and retry.",
            file=sys.stderr,
        )
        return 1
    if not result.separable:
        print(
            "\nERROR: refusing to --write a NON-separable sweep. Capture cleaner both-red frames (fully "
            "misaligned, BOTH arcs strongly red) so no case is EXCLUDED as transitional, then retry.",
            file=sys.stderr,
        )
        return 1

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1
    current = load_system_camera_config(config_path)
    kwargs = build_write_kwargs(result, cases, current.screen_roi)
    at = current.auto_trigger
    new_left, new_right = cases[0].left_arc, cases[0].right_arc
    print(f"\nWILL WRITE -> {config_path}")
    print(f"  coverage_threshold: {at.coverage_threshold}  ->  {kwargs['coverage_threshold']}")
    print(f"  a*_min:  {at.a_star_min}  ->  {kwargs['a_star_min']}")
    print(
        f"  left_arc:  {at.left_arc.x},{at.left_arc.y},{at.left_arc.w},{at.left_arc.h}  ->  "
        f"{new_left.x},{new_left.y},{new_left.w},{new_left.h}"
    )
    print(
        f"  right_arc: {at.right_arc.x},{at.right_arc.y},{at.right_arc.w},{at.right_arc.h}  ->  "
        f"{new_right.x},{new_right.y},{new_right.w},{new_right.h}"
    )
    print("  screen_roi: PRESERVED from the current config (re-run calibrate_view.py to change it).")
    try:
        reply = input("\nWrite these values? [y/N]: ").strip().lower()
    except EOFError:
        reply = ""
    if reply != "y":
        print("Aborted -- nothing written.")
        return 0
    write_calibration_values(config_path, **kwargs)  # type: ignore[arg-type]
    print(f"Wrote {config_path}  (backup: {config_path.name}.bak)")
    print("Next: verify live with usb_camera_arc_debug.py before the AUTO demo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
