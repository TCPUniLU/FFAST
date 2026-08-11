"""Frame reading must report progress so a long load is not mistaken for a stuck one.

Environment.waitForTasks(stall_timeout_s=...) decides a job is stuck when its
work state stops changing.  A silent multi-minute read would look identical to
a hang, so the reader ticks as frames arrive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ffast.io.xyz import read_ase_or_explain

# Same derivation as tests/ffast/test_cli.py: repo root / examples / data.
_DATASET = (
    Path(__file__).resolve().parents[2]
    / "examples" / "data" / "variable-sized-molecular" / "dataset.xyz"
)


@pytest.mark.skipif(not _DATASET.exists(), reason="example data not present")
def test_read_reports_progress_while_streaming_frames():
    seen: list[int] = []

    frames = read_ase_or_explain(
        _DATASET, index=":", report=seen.append, report_every=25
    )

    assert len(frames) == 100  # Same frames as a plain read
    assert len(seen) >= 3, f"expected repeated ticks over 100 frames, got {seen}"
    assert seen == sorted(seen), "frame counts must climb"
    assert seen[-1] <= 100


@pytest.mark.skipif(not _DATASET.exists(), reason="example data not present")
def test_read_without_a_reporter_is_unchanged():
    frames = read_ase_or_explain(_DATASET, index=":")
    assert len(frames) == 100
