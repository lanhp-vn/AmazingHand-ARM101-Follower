"""Shared DR-grading sidecar helpers.

Used by both the batch CLI (``arm101-dr-grade``) and the inline demo so they emit
byte-identical ``<stem>.dr.json`` artifacts. Local/offline; no network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from arm101_hand.fundus_analysis.grader import GradeResult


def weights_sha8(path: Path) -> str:
    """sha256[:8] of the weights file — ties a sidecar to the exact weights used."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def sidecar_path(output_dir: Path, source_name: str) -> Path:
    """Where the ``<source-stem>.dr.json`` sidecar for ``source_name`` lives."""
    return output_dir / f"{Path(source_name).stem}.dr.json"


def write_sidecar(result: GradeResult, output_dir: Path) -> Path:
    """Write ``result.to_dict()`` as ``<stem>.dr.json`` under ``output_dir``; return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_path(output_dir, result.source_image)
    path.write_text(json.dumps(result.to_dict(), indent=2))
    return path
