"""arm101-dr-grade — batch-grade fundus photos for DR severity.

Reads media_outputs/fundus_images/*.JPG, writes <stem>.dr.json sidecars to
media_outputs/fundus_analysis/, prints a summary table. Local/offline only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config
from arm101_hand.fundus_analysis.grader import DRGrader
from arm101_hand.fundus_analysis.sidecar import sidecar_path, weights_sha8, write_sidecar

REPO_ROOT = Path(__file__).resolve().parents[3]
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def _iter_images(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.iterdir() if p.suffix.lower() in _IMAGE_EXTS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade fundus photos for diabetic retinopathy.")
    parser.add_argument(
        "--input", type=Path, default=None, help="Image file or dir (default: config images_dir)."
    )
    parser.add_argument("--force", action="store_true", help="Re-grade images that already have a sidecar.")
    parser.add_argument("--limit", type=int, default=None, help="Grade only the first N images.")
    args = parser.parse_args()

    cfg = load_fundus_analysis_config()
    input_path = args.input or (REPO_ROOT / cfg.images_dir)
    output_dir = REPO_ROOT / cfg.output_dir
    weights_path = REPO_ROOT / cfg.models_dir / cfg.weights_filename

    if not weights_path.is_file():
        print(
            f"ERROR: slim weights missing at {weights_path}\n"
            f"Run: uv run python scripts/fundus_analysis/export_weights.py",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        raise SystemExit(2)

    images = _iter_images(input_path)
    if args.limit is not None:
        images = images[: args.limit]
    if not images:
        print(f"No images found in {input_path}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading model from {weights_path.name} ...")
    grader = DRGrader(cfg, weights_path=weights_path, model_sha8=weights_sha8(weights_path))

    rows: list[tuple[str, str, str, str]] = []
    graded = skipped = fallback = errors = 0
    for img in images:
        sidecar = sidecar_path(output_dir, img.name)
        if sidecar.exists() and not args.force:
            skipped += 1
            continue
        try:
            res = grader.grade_path(img)
        except ValueError as exc:
            errors += 1
            rows.append((img.name, "ERR", str(exc)[:40], "-"))
            continue
        write_sidecar(res, output_dir)
        graded += 1
        if res.crop["fallback"]:
            fallback += 1
        rows.append(
            (
                img.name,
                str(res.grade),
                res.label,
                f"{res.confidence} {max(res.probabilities.values()):.2f}",
            )
        )

    _print_table(rows)
    print(f"\n{graded} graded, {skipped} skipped, {fallback} circle-crop fell back, {errors} errors.")
    print("Research/educational use only. Not a medical device; not for clinical diagnosis.")


def _print_table(rows: list[tuple[str, str, str, str]]) -> None:
    if not rows:
        return
    headers = ("image", "grade", "label", "confidence")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(headers))))


if __name__ == "__main__":
    main()
