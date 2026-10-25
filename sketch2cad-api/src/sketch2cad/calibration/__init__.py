"""Image-to-paper calibration: proposal and acceptance (REQ-03, US-03, V-05, V-08)."""

from sketch2cad.calibration.service import (
    V05_MESSAGE,
    V08_MESSAGE,
    CalibrationConflictError,
    CalibrationInput,
    CalibrationInputError,
    CalibrationTolerances,
    MetricRejectedError,
    PointInput,
    Proposal,
    ScaleInfo,
    accept,
    compute_proposal,
    propose,
)

__all__ = [
    "V05_MESSAGE",
    "V08_MESSAGE",
    "CalibrationConflictError",
    "CalibrationInput",
    "CalibrationInputError",
    "CalibrationTolerances",
    "MetricRejectedError",
    "PointInput",
    "Proposal",
    "ScaleInfo",
    "accept",
    "compute_proposal",
    "propose",
]
