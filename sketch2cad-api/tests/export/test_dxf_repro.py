"""Byte-level reproducibility of the DXF export (decision D-3, H4-4, CA-08, REQ-12).

Each cross-process export runs ``export_dxf`` in a fresh interpreter (``EXPORT_SCRIPT``):
the second run starts 2 s after the first one and uses another ``PYTHONHASHSEED`` (with
ezdxf 1.4.5, seeds 1 and 4 order the CLASSES section differently without the D-3 settings).
"""

# ezdxf leaves most attributes unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import hashlib
import importlib.metadata
import io
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dxf_snapshots import FROZEN_AT, PLAN_ID, entity, mixed_entities, snapshot
from ezdxf.document import CREATED_BY_EZDXF, WRITTEN_BY_EZDXF, Drawing
from ezdxf.filemanagement import read as read_drawing
from ezdxf.version import __version__ as ezdxf_version

from sketch2cad.catalog import default_catalog
from sketch2cad.export import export_dxf
from sketch2cad.export.dxf_repro import DRAFT_EPOCH, autocad_guid, julian_date
from sketch2cad.revisions.snapshot import snapshot_sha256

EXPORT_DIR = Path(__file__).resolve().parent
API_DIR = EXPORT_DIR.parents[1]
VERSION = "0.1.0"
SLEEP_SECONDS = 2.0
FIRST_SEED, SECOND_SEED = "1", "4"
PINNED_EZDXF = "1.4.5"
# SHA-256 of ``export_variant("frozen")``. It changes whenever the exporter output changes
# (new header setting or entity writer, ezdxf or Python upgrade, ...). Check that the change
# is intended, then print the new value from ``sketch2cad-api/`` with
#   uv run python tests/export/test_dxf_repro.py
# and paste it here (an ezdxf upgrade also needs the pin in pyproject.toml and uv.lock).
GOLDEN_SHA256 = "76011d92bdb9c7ff59bcb027587d357f00b7b9aae26ff5ae7e27c62402b3484b"
# Runs in a fresh interpreter: prints the bytes of one export variant to stdout.
EXPORT_SCRIPT = (
    "import sys; sys.path.insert(0, sys.argv[1]); import test_dxf_repro as t; "
    "sys.stdout.buffer.write(t.export_variant(sys.argv[2]))"
)
LATER_FROZEN_AT = FROZEN_AT + timedelta(days=3)


def revision(variant: str) -> tuple[dict[str, Any], datetime | None, bool]:
    """``(snapshot, frozen_at, draft)`` of a named export variant.

    ``frozen``: the fixture revision; ``other``: the next revision of the same plan (one more
    entity, frozen later); ``draft``: the fixture exported as a draft of a non-frozen revision.
    """
    if variant == "frozen":
        return snapshot(mixed_entities()), FROZEN_AT, False
    if variant == "other":
        extra = entity(11, "line", "L_ACCESS", {"coords_m": [[0.0, 3.0], [1.0, 3.0]]}, 3)
        return (
            {**snapshot([*mixed_entities(), extra]), "revision_number": 3},
            LATER_FROZEN_AT,
            False,
        )
    if variant == "draft":
        return snapshot(mixed_entities()), None, True
    raise ValueError(f"unknown variant {variant!r}")


def export_variant(variant: str) -> bytes:
    snap, frozen_at, draft = revision(variant)
    return export_dxf(
        snap, default_catalog(), draft=draft, generator_version=VERSION, frozen_at=frozen_at
    )


def export_in_subprocess(variant: str, seed: str, extra_env: dict[str, str] | None = None) -> bytes:
    env = {**os.environ, "PYTHONHASHSEED": seed, **(extra_env or {})}
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-P", "-c", EXPORT_SCRIPT, str(EXPORT_DIR), variant],
        capture_output=True,
        check=True,
        cwd=API_DIR,
        env=env,
        timeout=120,
    )
    return result.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(data: bytes) -> Drawing:
    return read_drawing(io.StringIO(data.decode("utf-8")))


def written_header(data: bytes) -> dict[str, str]:
    """Header variables as written (``ezdxf.read`` resets ``$TDCREATE`` to the clock)."""
    lines = data.decode("utf-8").split("\n")
    end = lines.index("ENDSEC")
    return {lines[i]: lines[i + 2] for i in range(end) if lines[i].startswith("$")}


def test_same_revision_in_two_processes_has_the_same_sha256() -> None:
    first = export_in_subprocess("frozen", FIRST_SEED)
    time.sleep(SLEEP_SECONDS)
    second = export_in_subprocess("frozen", SECOND_SEED)
    assert first
    assert sha256(first) == sha256(second)
    assert first == export_variant("frozen")


def test_other_revision_has_another_sha256() -> None:
    frozen, other = export_variant("frozen"), export_variant("other")
    assert sha256(frozen) != sha256(other)
    snap, _, _ = revision("frozen")
    moved = export_dxf(
        snap, default_catalog(), draft=False, generator_version=VERSION, frozen_at=LATER_FROZEN_AT
    )
    assert sha256(moved) != sha256(frozen)


def test_time_zone_and_pure_python_ezdxf_give_the_same_bytes() -> None:
    reference = export_variant("frozen")
    santiago = export_in_subprocess("frozen", FIRST_SEED, {"TZ": "America/Santiago"})
    pure_python = export_in_subprocess("frozen", SECOND_SEED, {"EZDXF_DISABLE_C_EXT": "1"})
    assert santiago == reference
    assert pure_python == reference


def test_frozen_at_offset_does_not_change_the_bytes() -> None:
    snap, _, _ = revision("frozen")
    santiago_time = FROZEN_AT.astimezone(timezone(timedelta(hours=-3)))
    data = export_dxf(
        snap, default_catalog(), draft=False, generator_version=VERSION, frozen_at=santiago_time
    )
    assert data == export_variant("frozen")


def test_clock_and_uuid_values_are_derived_from_the_revision() -> None:
    snap, _, _ = revision("frozen")
    data = export_variant("frozen")
    header, doc = written_header(data), read(data)
    julian = julian_date(FROZEN_AT)
    for name in ("$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"):
        assert float(header[name]) == julian
        assert header[name] == repr(julian)
    assert header["$TDINDWG"] == header["$TDUSRTIMER"] == "0.0"
    assert doc.header["$TDUPDATE"] == julian
    assert doc.header["$FINGERPRINTGUID"] == autocad_guid(f"plan:{PLAN_ID}")
    assert doc.header["$VERSIONGUID"] == autocad_guid(f"snapshot:{snapshot_sha256(snap)}")
    marker = f"{ezdxf_version} @ 2026-10-21T09:30:15.123456+00:00"
    meta = doc.ezdxf_metadata()
    assert meta[CREATED_BY_EZDXF] == marker
    assert meta[WRITTEN_BY_EZDXF] == marker


def test_fingerprint_is_per_plan_and_version_guid_per_snapshot() -> None:
    frozen, other = read(export_variant("frozen")), read(export_variant("other"))
    assert frozen.header["$FINGERPRINTGUID"] == other.header["$FINGERPRINTGUID"]
    assert frozen.header["$VERSIONGUID"] != other.header["$VERSIONGUID"]
    assert frozen.header["$VERSIONGUID"].startswith("{")
    assert frozen.header["$VERSIONGUID"] == frozen.header["$VERSIONGUID"].upper()


def test_draft_export_is_reproducible_and_dated_at_the_epoch() -> None:
    first = export_in_subprocess("draft", FIRST_SEED)
    second = export_in_subprocess("draft", SECOND_SEED)
    assert first == second == export_variant("draft")
    assert float(written_header(first)["$TDCREATE"]) == julian_date(DRAFT_EPOCH)
    assert read(first).ezdxf_metadata()[CREATED_BY_EZDXF].endswith("@ 2000-01-01T00:00:00+00:00")


def test_naive_frozen_at_is_rejected() -> None:
    snap, _, _ = revision("frozen")
    with pytest.raises(ValueError, match="timezone-aware"):
        export_dxf(
            snap,
            default_catalog(),
            draft=False,
            generator_version=VERSION,
            frozen_at=datetime(2026, 10, 21, 9, 30),
        )


def test_pinned_export_audits_clean_and_uses_lf_line_endings() -> None:
    for variant in ("frozen", "draft"):
        data = export_variant(variant)
        assert b"\r\n" not in data
        auditor = read(data).audit()
        assert len(auditor.errors) == 0
        assert len(auditor.fixes) == 0


def test_golden_sha256_of_the_fixture_export() -> None:
    assert sha256(export_variant("frozen")) == GOLDEN_SHA256, (
        "The DXF export of the fixture revision changed. If intended (exporter change or "
        "ezdxf/Python upgrade), regenerate GOLDEN_SHA256 as described next to it."
    )


def test_ezdxf_is_pinned() -> None:
    assert importlib.metadata.version("ezdxf") == PINNED_EZDXF
    assert DRAFT_EPOCH.tzinfo is UTC


if __name__ == "__main__":
    print(sha256(export_variant("frozen")))
