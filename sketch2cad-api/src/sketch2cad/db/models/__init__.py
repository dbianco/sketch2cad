"""ORM models; importing this package registers every table on ``Base.metadata``."""

from sketch2cad.db.models.artifact import Artifact, ArtifactKind
from sketch2cad.db.models.calibration import Calibration, CalibrationMethod
from sketch2cad.db.models.case_file import CaseCodeCounter, CaseFile, CaseMember, IncidentType
from sketch2cad.db.models.catalog import Catalog
from sketch2cad.db.models.entity import Entity, EntityType, MetricOrigin, ReviewStatus
from sketch2cad.db.models.event import Event
from sketch2cad.db.models.job_meta import JobMeta, JobStatus
from sketch2cad.db.models.plan import MetricStatus, Plan, PlanStatus, SourceMode
from sketch2cad.db.models.revision import Revision, RevisionState
from sketch2cad.db.models.source import MediaType, Source
from sketch2cad.db.models.user import User, UserRole, UserSession

__all__ = [
    "Artifact",
    "ArtifactKind",
    "Calibration",
    "CalibrationMethod",
    "CaseCodeCounter",
    "CaseFile",
    "CaseMember",
    "Catalog",
    "Entity",
    "EntityType",
    "Event",
    "IncidentType",
    "JobMeta",
    "JobStatus",
    "MediaType",
    "MetricOrigin",
    "MetricStatus",
    "Plan",
    "PlanStatus",
    "ReviewStatus",
    "Revision",
    "RevisionState",
    "Source",
    "SourceMode",
    "User",
    "UserRole",
    "UserSession",
]
