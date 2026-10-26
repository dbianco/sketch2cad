"""Byte-level reproducibility settings of the DXF export (decision D-3, spec section 13.1).

Ported from the ``pinned`` mode of ``spikes/dxf_repro.py`` (``docs/spikes/dxf-reproducibility.md``).
Without these settings ezdxf 1.4.5 writes values taken from the clock, from ``uuid4`` and
from the iteration order of a set (``PYTHONHASHSEED``), so two exports of the same revision
differ. :func:`pin_document` replaces every one of them with a value derived from the
revision:

- ``$TDCREATE``, ``$TDUCREATE``, ``$TDUPDATE`` and ``$TDUUPDATE``: Julian date of
  ``frozen_at`` in UTC (never the local time zone); ``$TDINDWG`` and ``$TDUSRTIMER``: ``0.0``;
- ``$FINGERPRINTGUID = uuid5(GUID_NAMESPACE, "plan:<plan_id>")`` (same for every revision of
  a plan) and ``$VERSIONGUID = uuid5(GUID_NAMESPACE, "snapshot:<snapshot_sha256>")``, in the
  AutoCAD ``{XXXXXXXX-...}`` upper-case format;
- the ``CREATED_BY_EZDXF`` and ``WRITTEN_BY_EZDXF`` markers: ``"<ezdxf version> @ <frozen_at>"``;
- the CLASSES section: required classes in ezdxf order, the rest sorted by name.

ezdxf rewrites ``$TDUPDATE``, ``$VERSIONGUID`` and the written-by marker in
``Drawing._update_metadata()`` on every ``write()``, so the values are applied by wrapping
that method on the document instance (private API of ezdxf 1.4.5; no global ezdxf option is
touched). The bytes are only stable for the pinned ezdxf version (``ezdxf==1.4.5``) and
Python 3.12; upgrading either requires regenerating the reference hashes.
"""

import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ezdxf.document import CREATED_BY_EZDXF, WRITTEN_BY_EZDXF, Drawing
from ezdxf.sections.classes import REQ_R2004
from ezdxf.tools.juliandate import juliandate
from ezdxf.version import __version__ as ezdxf_version

# Fixed namespace of the uuid5 GUIDs written to the DXF header (same as the D-3 spike).
GUID_NAMESPACE = uuid.UUID("5b0a3c1e-2f4d-5e6a-8b7c-9d0e1f2a3b4c")
# Date written to draft exports: a revision that is not frozen has no ``frozen_at``, and a
# constant keeps drafts reproducible too.
DRAFT_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
DATE_HEADER_VARS = ("$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE")
TIMER_HEADER_VARS = ("$TDINDWG", "$TDUSRTIMER")


@dataclass(frozen=True)
class ReproSettings:
    """The save-time values of one export, all derived from the revision."""

    julian_date: float
    fingerprint_guid: str
    version_guid: str
    ezdxf_marker: str


def to_utc(frozen_at: datetime | None) -> datetime:
    """``frozen_at`` in UTC, or :data:`DRAFT_EPOCH` when the revision is not frozen."""
    if frozen_at is None:
        return DRAFT_EPOCH
    if frozen_at.tzinfo is None:
        raise ValueError("frozen_at must be timezone-aware")
    return frozen_at.astimezone(UTC)


def julian_date(frozen_at: datetime) -> float:
    """Julian date of the timezone-aware ``frozen_at``, taken in UTC."""
    return juliandate(to_utc(frozen_at).replace(tzinfo=None))


def autocad_guid(name: str) -> str:
    """Deterministic GUID of ``name`` in the AutoCAD ``{XXXXXXXX-...}`` upper-case format."""
    return "{" + str(uuid.uuid5(GUID_NAMESPACE, name)).upper() + "}"


def repro_settings(plan_id: str, sha256: str, frozen_at: datetime | None) -> ReproSettings:
    """Settings for revision ``sha256`` of plan ``plan_id`` (``None``: draft epoch)."""
    moment = to_utc(frozen_at)
    return ReproSettings(
        julian_date=julian_date(moment),
        fingerprint_guid=autocad_guid(f"plan:{plan_id}"),
        version_guid=autocad_guid(f"snapshot:{sha256}"),
        ezdxf_marker=f"{ezdxf_version} @ {moment.isoformat()}",
    )


def sort_classes(doc: Drawing) -> None:
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


def apply_settings(doc: Drawing, settings: ReproSettings) -> None:
    """Write ``settings`` into the header, the ezdxf markers and the CLASSES order."""
    sort_classes(doc)
    for name in DATE_HEADER_VARS:
        doc.header[name] = settings.julian_date
    for name in TIMER_HEADER_VARS:
        doc.header[name] = 0.0
    doc.header["$FINGERPRINTGUID"] = settings.fingerprint_guid
    doc.header["$VERSIONGUID"] = settings.version_guid
    meta = doc.ezdxf_metadata()
    meta[CREATED_BY_EZDXF] = settings.ezdxf_marker
    meta[WRITTEN_BY_EZDXF] = settings.ezdxf_marker


def pin_document(doc: Drawing, settings: ReproSettings) -> None:
    """Apply ``settings`` at save time, after ezdxf's own metadata update.

    ``write()`` runs ``Drawing._update_metadata()`` after completing the CLASSES section and
    right before exporting the sections; wrapping it on this instance keeps the pinned
    values in the written bytes.
    """
    original: Callable[[], None] = doc._update_metadata  # pyright: ignore[reportPrivateUsage]

    def update_metadata() -> None:
        original()
        apply_settings(doc, settings)

    doc._update_metadata = update_metadata  # pyright: ignore[reportPrivateUsage]
