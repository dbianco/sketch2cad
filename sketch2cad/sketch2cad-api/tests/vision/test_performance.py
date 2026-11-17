"""Performance of ``suggest_lines`` on a 4000 x 3000 px sketch (REQ-17, spec H5-2).

H5-2: under 30 s on a 4-core CPU. The test records the CPU count (and the CPUs this
process may run on, where the OS reports it) and fails on a machine with fewer than 4
only when ``CI`` is unset; on CI the count is recorded and the timing is still asserted.
"""

import os
import time

import pytest

from sketch2cad.vision import suggest_polylines
from sketch2cad.vision.synthetic_sketch import generate_sketch

SIZE_PX = (4000, 3000)
SEGMENTS = 60
LIMIT_S = 30.0
REQUIRED_CPUS = 4


def usable_cpus() -> int | None:
    affinity = getattr(os, "sched_getaffinity", None)
    return len(affinity(0)) if affinity is not None else None


@pytest.mark.perf
def test_4000x3000_sketch_is_processed_under_30_seconds() -> None:
    cpus = os.cpu_count() or 0
    usable = usable_cpus()
    print(f"\ncpu_count={cpus} usable_cpus={usable} CI={os.environ.get('CI')!r}")
    if os.environ.get("CI") is None and min(cpus, usable or cpus) < REQUIRED_CPUS:
        pytest.fail(f"H5-2 is defined on {REQUIRED_CPUS} CPU cores; this machine has {cpus}")
    sketch = generate_sketch(101, SEGMENTS, SIZE_PX, allow_crossings=True, bgr=True)

    start = time.perf_counter()
    result = suggest_polylines(sketch.image)
    elapsed = time.perf_counter() - start

    print(f"suggest_polylines {SIZE_PX[0]}x{SIZE_PX[1]} with {SEGMENTS} strokes: {elapsed:.3f} s")
    assert result
    assert elapsed < LIMIT_S
