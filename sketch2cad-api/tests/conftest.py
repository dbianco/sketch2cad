"""Shared fixtures; PostgreSQL fixtures use an isolated schema per test session."""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.db import session as db_session
from sketch2cad.db.session import create_db_engine, create_session_factory

SKIP_REASON = (
    "DATABASE_URL is not set; PostgreSQL-backed tests require it, e.g. "
    "DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<database>"
)


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return ``DATABASE_URL`` or skip the dependent tests with a clear reason."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip(SKIP_REASON)
    return url


@pytest.fixture(scope="session")
def test_schema(database_url: str) -> Iterator[str]:
    """Create a uniquely named schema for this test session and drop it afterwards."""
    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_db_engine(database_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield schema
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="session")
def db_engine(database_url: str, test_schema: str) -> Iterator[Engine]:
    """Engine whose connections use the isolated test schema as ``search_path``."""
    engine = create_db_engine(
        database_url, connect_args={"options": f"-csearch_path={test_schema}"}
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    """Bind ``get_session`` to the test engine for the duration of one test."""
    factory = create_session_factory(db_engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    return factory


# --- Acceptance-criterion labels (spec section 16, task 001-113) --------------------------
#
# CA_TESTS maps each marker ``caNN`` to the tests that realize criterion CA-NN, as node-id
# prefixes relative to ``sketch2cad-api/``: a whole file (``tests/x.py``), a class or test
# (``tests/x.py::test_y``, every parametrization included). A test may serve several
# criteria. ``pytest -m ca03`` selects them; ``scripts/ca_report.py`` reports per criterion.
# tests/test_ca_report.py fails when an entry no longer names an existing file or test.
CA_TESTS: dict[str, tuple[str, ...]] = {
    "ca01": (
        "tests/sources/test_download_preview.py::test_download_returns_the_registered_bytes_hash_and_event",
        "tests/sources/test_ingest.py::test_valid_upload_returns_201_with_hash_event_and_queued_qc",
        "tests/sources/test_ingest.py::test_mismatching_client_sha256_returns_422_and_stores_nothing",
        "tests/storage/test_filesystem.py::test_put_returns_sha256_and_content_addressed_key",
        "tests/storage/test_filesystem.py::test_put_same_bytes_twice_keeps_key_mtime_and_content",
        "tests/storage/test_filesystem.py::test_stored_file_is_read_only_and_get_matches_hash",
    ),
    "ca02": (
        "tests/geometry/test_transforms.py::test_synthetic_photo_rectifies_within_tolerance",
        "tests/geometry/test_transforms.py::test_warp_to_rectified_canvas_restores_paper_distances",
        "tests/calibration/test_rectify.py::test_rectified_oblique_photo_measures_2000_by_1500_px_and_keeps_distances",
        "tests/calibration/test_calibration_api.py::test_proposal_of_an_oblique_photo_maps_corners_and_persists_nothing",
        "tests/calibration/test_calibration_api.py::test_as_metric_with_a_1_3_mm_error_is_422_v05_and_writes_nothing",
        "tests/calibration/test_calibration_api.py::test_the_same_error_is_accepted_as_approximate",
        "tests/calibration/test_calibration_api.py::test_method_none_leaves_the_plan_non_metric",
        "tests/calibration/test_calibration_api.py::test_as_metric_with_method_none_is_422",
        "tests/geometry/test_metric_status_basic.py::test_calibration_method_none_is_non_metric",
        "tests/geometry/test_metric_status_basic.py::test_a_plan_without_calibration_is_non_metric",
    ),
    "ca03": (
        "tests/geometry/test_cases_by_hand.py",
        "tests/geometry/test_geometry_constraints.py::test_triangulated_points_are_at_the_declared_distances",
        "tests/geometry/test_geometry_constraints.py::test_baseline_offset_has_the_declared_projection_and_offset",
        "tests/entities/test_constraints_ops.py::test_3_4_5_triangulation_returns_both_candidates_and_applies_the_nearest",
        "tests/entities/test_constraints_ops.py::test_baseline_offset_places_the_point_on_the_requested_side",
        "tests/entities/test_vertex_refs.py::test_triangulation_from_two_declared_wall_corners_gives_the_hand_point",
        "tests/entities/test_vertex_refs.py::test_baseline_offset_from_the_two_vertices_of_one_wall",
        "tests/entities/test_vertex_refs.py::test_a_wall_declared_by_fix_length_serves_and_stretching_it_recomputes_the_point",
    ),
    "ca04": (
        "tests/vision/test_recovery_benchmark.py::test_recovery_on_non_crossing_segments_meets_ca04",
        "tests/vision/test_recovery_benchmark.py::test_paper_specks_alone_give_no_suggestions",
    ),
    "ca05": (
        "tests/entities/test_bulk_review.py::test_reviewing_100_ids_changes_100_rows_and_writes_100_events",
        "tests/entities/test_bulk_review.py::test_one_invalid_id_changes_nothing_and_writes_no_event",
        "tests/entities/test_bulk_review.py::test_accepted_cannot_be_rejected_and_rejected_cannot_be_accepted",
        "tests/entities/test_bulk_review.py::test_editing_a_suggestion_marks_it_corrected_and_promotes_it",
        "tests/entities/test_bulk_review.py::test_deleting_a_suggestion_rejects_it",
        "tests/entities/test_bulk_review.py::test_every_reviewed_suggestion_has_its_review_event",
        "tests/entities/test_bulk_review.py::test_no_code_path_approves_a_suggestion_without_an_event",
        "tests/vision/test_suggest_job.py::test_suggestions_are_pending_cv_trace_only_polylines_with_one_event",
        "tests/ocr/test_ocr_job.py::test_a_dimension_from_an_ocr_reading_records_its_review",
    ),
    "ca06": (
        "tests/export/test_golden_dxf.py",
        "tests/export/test_dxf_core.py::test_entities_per_layer_and_coordinates_round_trip",
        "tests/export/test_dxf_core.py::test_reread_document_audits_clean",
        "tests/export/test_dxf_dimensions.py::test_dimensions_counted_per_layer_and_audit_clean_after_round_trip",
        "tests/export/test_dxf_symbols.py::test_round_trip_counts_per_layer_and_audit",
        "tests/export/test_dxf_symbols.py::test_every_catalog_symbol_exports_and_audits_clean",
        "tests/export/test_dxf_repro.py::test_pinned_export_audits_clean_and_uses_lf_line_endings",
    ),
    "ca07": (
        "tests/export/test_control_distances.py",
        "tests/export/test_pdf_layout.py::test_1_m_line_measures_10_mm_and_5_m_diagonal_50_mm_at_1_100",
        "tests/export/test_golden_dxf.py::test_control_segment_is_five_metres_in_model_units",
    ),
    "ca08": (
        "tests/export/test_dxf_core.py::test_export_is_byte_identical",
        "tests/export/test_dxf_repro.py::test_same_revision_in_two_processes_has_the_same_sha256",
        "tests/export/test_dxf_repro.py::test_time_zone_and_pure_python_ezdxf_give_the_same_bytes",
        "tests/export/test_dxf_repro.py::test_draft_export_is_reproducible_and_dated_at_the_epoch",
        "tests/export/test_dxf_repro.py::test_golden_sha256_of_the_fixture_export",
        "tests/export/test_pdf_layout.py::test_same_revision_in_two_processes_has_the_same_sha256",
        "tests/export/test_draft_disclaimer.py::test_draft_and_final_pdfs_are_the_same_in_two_processes",
        "tests/export/test_pdf_content.py::test_annex_export_is_the_same_in_two_processes_and_follows_the_image",
        "tests/package/test_build.py::test_identical_inputs_give_identical_zip_bytes_across_processes",
    ),
    "ca09": (
        "tests/package/test_verifier.py::test_fresh_package_verifies_with_exit_0_and_warns_about_the_embedded_key",
        "tests/package/test_verifier.py::test_one_altered_byte_fails_with_exit_1_naming_the_member",
        "tests/package/test_verifier.py::test_truncated_tail_with_fixed_hash_breaks_the_signature_and_the_count",
        "tests/package/test_verifier.py::test_resigned_truncation_is_caught_by_the_count_or_by_key_pinning",
        "tests/package/test_verifier.py::test_duplicate_extra_and_missing_members_fail",
        "tests/package/test_build.py::test_signature_verifies_with_the_included_key_and_fails_after_any_hashed_byte_changes",
        "tests/package/test_manifest_signing.py::test_changing_any_byte_of_manifest_json_breaks_verification",
        "tests/audit/test_verify.py::test_one_byte_changed_in_after_of_event_5_of_10_reports_event_5",
        "tests/audit/test_verify.py::test_rehashed_tampered_event_breaks_the_link_of_the_next_event",
        "tests/audit/test_verify.py::test_removed_middle_event_is_reported_at_its_successor",
        "tests/audit/test_verify.py::test_swapped_events_are_reported_at_the_first_swapped_position",
        "tests/audit/test_verify.py::test_mutating_any_field_of_any_event_invalidates_the_chain_at_that_event",
        "tests/audit/test_events_endpoint.py::test_tampered_page_item_fails_verification",
    ),
    "ca10": (
        "tests/plans/test_transitions.py",
        "tests/revisions/test_revisions.py::test_only_the_current_draft_can_be_frozen_and_a_frozen_row_is_immutable",
        "tests/revisions/test_revisions.py::test_frozen_snapshot_read_back_from_the_database_has_identical_bytes",
        "tests/revisions/test_revisions.py::test_reopen_creates_the_next_draft_and_keeps_the_frozen_revision",
        "tests/revisions/test_revisions.py::test_reopen_requires_a_frozen_current_revision",
    ),
    "ca11": (
        "tests/failures/test_explicit_failures.py",
        "tests/calibration/test_rectify.py::test_missing_original_fails_with_cause_and_action",
        "tests/exports/test_export_api.py::test_missing_original_fails_the_job_with_cause_and_action",
        "tests/vision/test_suggest_job.py::test_a_missing_rectified_image_fails_with_cause_and_action",
        "tests/ocr/test_ocr_job.py::test_a_missing_model_fails_the_job_with_cause_and_action",
    ),
    "ca12": (
        "tests/security/test_authorization_matrix.py",
        "tests/sources/test_download_preview.py::test_non_member_gets_403_and_access_denied_on_both_routes",
        "tests/exports/test_export_api.py::test_final_without_deliver_is_403_with_access_denied",
    ),
    "ca13": ("tests/test_check_licenses.py",),
}


def ca_markers_for(nodeid: str) -> list[str]:
    """Markers of :data:`CA_TESTS` whose entries are ``nodeid`` or a prefix of it."""
    return [
        marker
        for marker, entries in CA_TESTS.items()
        if any(nodeid == e or nodeid.startswith((f"{e}::", f"{e}[")) for e in entries)
    ]


@pytest.hookimpl(tryfirst=True)  # before ``-m`` deselects by marker
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for marker in ca_markers_for(item.nodeid):
            item.add_marker(marker)
