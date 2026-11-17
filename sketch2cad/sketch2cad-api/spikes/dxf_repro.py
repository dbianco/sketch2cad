"""Spike 2 (decision D-3): byte-level reproducibility of an R2013 DXF written with ezdxf.

Usage (from ``sketch2cad-api/``)::

    uv run python spikes/dxf_repro.py            # full report: raw vs pinned, 2 s apart
    uv run python spikes/dxf_repro.py --emit pinned > plan.dxf

``build_dxf(fixture)`` is the reusable generator (spec section 13.1). With ``pinned=True``
(the default) every value that ezdxf derives from the clock or from ``uuid4`` is replaced by
a value derived from the revision itself, so two exports of the same revision are identical.
"""

# ezdxf leaves ``dxfattribs`` parameters unannotated, so strict mode sees them as Unknown.
# pyright: reportUnknownMemberType=false

import argparse
import difflib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ezdxf import units
from ezdxf.document import CREATED_BY_EZDXF, WRITTEN_BY_EZDXF, Drawing
from ezdxf.filemanagement import new as new_drawing
from ezdxf.sections.classes import REQ_R2004
from ezdxf.tools.juliandate import juliandate
from ezdxf.version import __version__ as ezdxf_version

SPIKE_FILE = Path(__file__).resolve()
FIXTURE_PATH = SPIKE_FILE.parent / "fixtures" / "plan_fixture.json"
DXF_VERSION = "R2013"
APP_ID = "SKETCH2CAD"
TEXT_STYLE = "S2C_TEXT"
DIM_STYLE = "S2C_DIM"
# Fixed namespace for the uuid5 GUIDs written to the DXF header.
GUID_NAMESPACE = uuid.UUID("5b0a3c1e-2f4d-5e6a-8b7c-9d0e1f2a3b4c")
SLEEP_SECONDS = 2.0
# With ezdxf 1.4.5 these two hash seeds order the CLASSES section differently (raw output).
FIRST_SEED = "1"
SECOND_SEED = "4"
HEX_HANDLE_CODES = frozenset({5, 105, 330, 340, 350, 360, 390})

Fixture = dict[str, Any]


class DxfValidationError(RuntimeError):
    """Raised when ``doc.audit()`` reports errors or fixes for a generated document."""


def load_fixture(path: Path = FIXTURE_PATH) -> Fixture:
    """Load the plan fixture (a stand-in for a frozen revision snapshot)."""
    return json.loads(path.read_text(encoding="utf-8"))


def _guid(name: str) -> str:
    """Deterministic GUID in the AutoCAD ``{XXXXXXXX-...}`` upper-case format."""
    return "{" + str(uuid.uuid5(GUID_NAMESPACE, name)).upper() + "}"


def _sort_classes(doc: Drawing) -> None:
    """Give the CLASSES section an order that does not depend on ``PYTHONHASHSEED``.

    ``ClassesSection.add_required_classes`` appends one class per entity type taken from the
    *set* ``entitydb.dxf_types_in_use()``; for R2013 that adds ``LAYOUT`` and
    ``ACDBPLACEHOLDER`` in hash-seed order. Required classes keep their ezdxf order, the rest
    are sorted by ``(name, cpp_class_name)``.
    """
    rank = {name: index for index, name in enumerate(REQ_R2004)}
    ordered = sorted(
        doc.classes.classes.items(), key=lambda item: (rank.get(item[0][0], len(rank)), item[0])
    )
    doc.classes.classes = OrderedDict(ordered)


def _pin_metadata(doc: Drawing, fixture: Fixture) -> None:
    """Make the save-time metadata a function of the revision instead of the clock.

    ezdxf rewrites ``$TDUPDATE``, ``$VERSIONGUID`` and the ``WRITTEN_BY_EZDXF`` marker inside
    ``Drawing._update_metadata()`` on every ``write()``, so values set beforehand are lost.
    The hook runs after the CLASSES section is completed and right before the export; it is
    wrapped on this instance only (no global ezdxf option is touched).
    """
    frozen = datetime.fromisoformat(fixture["frozen_at"]).astimezone(UTC)
    julian = juliandate(frozen.replace(tzinfo=None))
    marker = f"{ezdxf_version} @ {frozen.isoformat()}"
    original: Callable[[], None] = doc._update_metadata  # pyright: ignore[reportPrivateUsage]

    def update_metadata() -> None:
        original()
        _sort_classes(doc)
        for name in ("$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"):
            doc.header[name] = julian
        doc.header["$TDINDWG"] = 0.0
        doc.header["$TDUSRTIMER"] = 0.0
        doc.header["$FINGERPRINTGUID"] = _guid(f"plan:{fixture['plan_id']}")
        doc.header["$VERSIONGUID"] = _guid(f"snapshot:{fixture['snapshot_sha256']}")
        meta = doc.ezdxf_metadata()
        meta[CREATED_BY_EZDXF] = marker
        meta[WRITTEN_BY_EZDXF] = marker

    doc._update_metadata = update_metadata  # pyright: ignore[reportPrivateUsage]


def _setup_tables(doc: Drawing, fixture: Fixture) -> None:
    for layer in fixture["layers"]:
        doc.layers.add(layer["name"], color=layer["color"])
    doc.styles.add(TEXT_STYLE, font="DejaVuSans.ttf")
    doc.appids.add(APP_ID)
    scale = fixture["paper_scale"] / 1000.0  # paper mm -> model metres
    doc.dimstyles.add(
        DIM_STYLE,
        dxfattribs={
            "dimtxsty": TEXT_STYLE,
            "dimtxt": 2.5 * scale,
            "dimasz": 2.5 * scale,
            "dimexe": 1.25 * scale,
            "dimexo": 0.625 * scale,
            "dimgap": 0.625 * scale,
            "dimblk": "ARCHTICK",
            "dimclrd": 256,
            "dimclre": 256,
            "dimclrt": 256,
        },
    )
    marker = doc.blocks.new("S2C_evidence_marker")
    marker.add_circle((0.0, 0.0), radius=0.5)
    marker.add_attdef(
        "evidence_number",
        insert=(0.0, 0.0),
        dxfattribs={"height": 0.4, "style": TEXT_STYLE, "halign": 1, "valign": 2},
    ).dxf.align_point = (0.0, 0.0)


def _add_entities(doc: Drawing, fixture: Fixture) -> None:
    msp = doc.modelspace()
    scale = fixture["paper_scale"] / 1000.0
    for entity in fixture["entities"]:
        layer = entity["layer"]
        kind = entity["kind"]
        if kind == "polyline":
            msp.add_lwpolyline(
                entity["points"], format="xy", close=entity["closed"], dxfattribs={"layer": layer}
            )
        elif kind == "text":
            msp.add_text(
                entity["text"],
                height=entity["height_mm_paper"] * scale,
                dxfattribs={"layer": layer, "style": TEXT_STYLE, "insert": entity["insert"]},
            )
        elif kind == "symbol":
            size = entity["size_mm_paper"] * scale
            ref = msp.add_blockref(
                f"S2C_{entity['symbol_id']}",
                entity["insert"],
                dxfattribs={"layer": layer, "xscale": size, "yscale": size, "zscale": size},
            )
            ref.add_auto_attribs(dict(sorted(entity["attributes"].items())))
        elif kind == "dimension":
            dim = msp.add_aligned_dim(
                p1=entity["p1"],
                p2=entity["p2"],
                distance=entity["distance"],
                text=entity["declared_text"],
                dimstyle=DIM_STYLE,
                dxfattribs={"layer": layer},
            )
            dim.render()
        else:
            raise ValueError(f"unsupported entity kind: {kind!r}")
    carrier = msp.add_point((0.0, 0.0), dxfattribs={"layer": "L_SHEET", "invisible": 1})
    carrier.set_xdata(
        APP_ID,
        [
            (1000, f"plan_id={fixture['plan_id']}"),
            (1000, f"snapshot_sha256={fixture['snapshot_sha256']}"),
            (1000, f"metric_status={fixture['metric_status']}"),
            (1000, f"generator_version={fixture['generator_version']}"),
            (1071, fixture["revision_number"]),
        ],
    )


def new_document(fixture: Fixture, *, pinned: bool = True) -> Drawing:
    """Build the ezdxf document for ``fixture`` and check that ``doc.audit()`` is clean."""
    doc = new_drawing(DXF_VERSION, setup=True, units=units.M)
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    _setup_tables(doc, fixture)
    _add_entities(doc, fixture)
    auditor = doc.audit()
    if auditor.has_errors or auditor.has_fixes:
        raise DxfValidationError(
            f"audit reported {len(auditor.errors)} errors and {len(auditor.fixes)} fixes"
        )
    if pinned:
        _pin_metadata(doc, fixture)
    return doc


def build_dxf(fixture: Fixture, *, pinned: bool = True) -> bytes:
    """Return the DXF bytes (UTF-8, ``\\n`` line endings on every platform) of ``fixture``."""
    doc = new_document(fixture, pinned=pinned)
    stream = io.StringIO()
    doc.write(stream)
    return doc.encode(stream.getvalue())


@dataclass(frozen=True)
class FieldDiff:
    """One differing tag: where it is (section, header variable or entity) and both values."""

    section: str
    owner: str
    code: int
    first: str
    second: str

    def describe(self) -> str:
        return f"{self.section}/{self.owner} code {self.code}: {self.first!r} != {self.second!r}"


def _tags(data: bytes) -> list[tuple[int, str, str, str]]:
    """Split DXF bytes into ``(code, value, section, owner)`` with their context."""
    lines = data.decode("utf-8").split("\n")
    result: list[tuple[int, str, str, str]] = []
    section = owner = ""
    expect_section_name = False
    for index in range(0, len(lines) - 1, 2):
        code, value = int(lines[index]), lines[index + 1]
        if code == 0:
            expect_section_name = value == "SECTION"
            owner = value
        elif code == 2 and expect_section_name:
            section, expect_section_name = value, False
        elif code == 9:
            owner = value
        elif code == 5 and section != "HEADER":
            owner = f"{owner.split('#')[0]}#{value}"
        result.append((code, value, section, owner))
    return result


def diff_fields(first: bytes, second: bytes) -> list[FieldDiff]:
    """List every tag that differs between two DXF byte strings (empty when identical)."""
    if first == second:
        return []
    tags_a, tags_b = _tags(first), _tags(second)
    matcher = difflib.SequenceMatcher(
        a=[t[:2] for t in tags_a], b=[t[:2] for t in tags_b], autojunk=False
    )
    diffs: list[FieldDiff] = []
    for op, a_lo, a_hi, b_lo, b_hi in matcher.get_opcodes():
        if op == "equal":
            continue
        for offset in range(max(a_hi - a_lo, b_hi - b_lo)):
            a = tags_a[a_lo + offset] if a_lo + offset < a_hi else None
            b = tags_b[b_lo + offset] if b_lo + offset < b_hi else None
            ref = a or b
            assert ref is not None  # noqa: S101 - one side always exists
            diffs.append(
                FieldDiff(
                    ref[2], ref[3], ref[0], a[1] if a else "<missing>", b[1] if b else "<missing>"
                )
            )
    if not diffs:  # same tags, different bytes: line endings or encoding
        diffs.append(FieldDiff("FILE", "bytes", -1, f"{len(first)} B", f"{len(second)} B"))
    return diffs


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_in_subprocess(mode: str, hash_seed: str, extra_env: dict[str, str] | None = None) -> bytes:
    """Generate the DXF in a fresh interpreter and return its bytes."""
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, **(extra_env or {})}
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(SPIKE_FILE), "--emit", mode],
        capture_output=True,
        check=True,
        env=env,
    )
    return result.stdout


def compare_runs(
    mode: str, sleep_seconds: float = SLEEP_SECONDS
) -> tuple[str, str, list[FieldDiff]]:
    """Two separate processes, ``sleep_seconds`` apart and with different hash seeds."""
    first = run_in_subprocess(mode, FIRST_SEED)
    time.sleep(sleep_seconds)
    second = run_in_subprocess(mode, SECOND_SEED)
    return sha256(first), sha256(second), diff_fields(first, second)


def _report(title: str, sha_a: str, sha_b: str, diffs: Sequence[FieldDiff]) -> None:
    print(f"== {title}")
    print(f"run 1 sha256: {sha_a}")
    print(f"run 2 sha256: {sha_b}")
    print(f"differing fields: {len(diffs)}")
    for diff in diffs:
        print(f"  - {diff.describe()}")


def _side_checks(fixture: Fixture) -> None:
    """``saveas`` vs ``write``, CRLF, rebuild in-process, other TZ and the pure-Python path."""
    reference = build_dxf(fixture)
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "saveas.dxf"
        new_document(fixture).saveas(target)
        print(f"saveas() equals write()+encode(): {target.read_bytes() == reference}")
    crlf = reference.replace(b"\n", b"\r\n")
    print(f"CRLF variant sha256: {sha256(crlf)} (differs: {crlf != reference})")
    print(f"second build in the same process equal: {build_dxf(fixture) == reference}")
    other_tz = run_in_subprocess("pinned", FIRST_SEED, {"TZ": "America/Santiago"})
    print(f"TZ=America/Santiago equals default: {other_tz == reference}")
    no_c = run_in_subprocess("pinned", FIRST_SEED, {"EZDXF_DISABLE_C_EXT": "1"})
    print(f"EZDXF_DISABLE_C_EXT=1 equals default: {no_c == reference}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spike 2: DXF reproducibility")
    parser.add_argument("--emit", choices=("raw", "pinned"), help="write one DXF to stdout")
    parser.add_argument("--sleep", type=float, default=SLEEP_SECONDS)
    args = parser.parse_args(argv)
    fixture = load_fixture()
    if args.emit:
        sys.stdout.buffer.write(build_dxf(fixture, pinned=args.emit == "pinned"))
        return 0
    print(f"ezdxf {ezdxf_version}, Python {sys.version.split()[0]}, {DXF_VERSION}")
    raw = compare_runs("raw", args.sleep)
    _report("raw ezdxf output (no fixes)", *raw)
    pinned = compare_runs("pinned", args.sleep)
    _report("pinned output (with fixes)", *pinned)
    _side_checks(fixture)
    return 0 if not pinned[2] else 1


if __name__ == "__main__":
    raise SystemExit(main())
