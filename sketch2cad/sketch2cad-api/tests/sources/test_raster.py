"""300 dpi raster of single-page PDFs: pure rendering, the source update and the worker task."""

import io
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import psycopg
import pypdfium2  # pyright: ignore[reportMissingTypeStubs]
import pytest
from PIL import Image
from psycopg import sql
from sqlalchemy import Engine
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import CaseFile, IncidentType, MediaType, Source, User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.sources.preview import preview_object, render_preview
from sketch2cad.sources.raster import (
    RASTER_DPI,
    RasterError,
    SourceNotFoundError,
    rasterize_pdf,
    rasterize_source,
)
from sketch2cad.storage import FilesystemStorage, Storage
from sketch2cad.worker import tasks_raster
from sketch2cad.worker.app import create_worker_app, to_conninfo

FIXTURE_PDF = Path(__file__).resolve().parents[1] / "fixtures" / "one_page.pdf"
A4_SIZE_PX = (2480, 3508)  # 210 x 297 mm at 300 dpi
SIZE_TOLERANCE_PX = 2


def pdf_with_pages(*sizes: tuple[float, float]) -> bytes:
    document = pypdfium2.PdfDocument.new()
    for width, height in sizes:
        document.new_page(width, height)  # pyright: ignore[reportUnknownMemberType]
    buffer = io.BytesIO()
    document.save(buffer)  # pyright: ignore[reportUnknownMemberType]
    document.close()
    return buffer.getvalue()


def png_of(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    image.load()
    return image


# --- rasterize_pdf -------------------------------------------------------------------------


def test_one_page_a4_pdf_rasterizes_to_2480_by_3508_px() -> None:
    raster = rasterize_pdf(FIXTURE_PDF.read_bytes())

    assert abs(raster.width_px - A4_SIZE_PX[0]) <= SIZE_TOLERANCE_PX
    assert abs(raster.height_px - A4_SIZE_PX[1]) <= SIZE_TOLERANCE_PX
    image = png_of(raster.png)
    assert image.size == (raster.width_px, raster.height_px)


def test_raster_is_a_png_with_the_drawn_content_and_300_dpi_density() -> None:
    image = png_of(rasterize_pdf(FIXTURE_PDF.read_bytes()).png)

    gray = image.convert("L")
    low, high = cast(tuple[int, int], gray.getextrema())
    assert high == 255  # white page
    assert low < 64  # the frame and diagonal of the fixture are drawn in black
    dpi = image.info["dpi"]
    assert round(float(dpi[0])) == RASTER_DPI
    assert round(float(dpi[1])) == RASTER_DPI
    assert "exif" not in image.info


def test_rasterize_pdf_is_deterministic() -> None:
    data = FIXTURE_PDF.read_bytes()

    assert rasterize_pdf(data) == rasterize_pdf(data)


def test_dpi_argument_scales_the_raster() -> None:
    raster = rasterize_pdf(pdf_with_pages((72, 144)), dpi=100)

    assert (raster.width_px, raster.height_px) == (100, 200)


@pytest.mark.parametrize(
    "data",
    [b"%PDF-1.7 not really a pdf", pdf_with_pages((595, 842), (595, 842))],
    ids=["unreadable", "two_pages"],
)
def test_rasterize_pdf_rejects_invalid_pdfs(data: bytes) -> None:
    with pytest.raises(RasterError):
        rasterize_pdf(data)


def test_rasterize_pdf_rejects_pages_over_the_pixel_budget() -> None:
    # 200 x 200 inches at 300 dpi is 3.6e9 pixels, far above Pillow's MAX_IMAGE_PIXELS.
    with pytest.raises(RasterError, match="too large"):
        rasterize_pdf(pdf_with_pages((14400, 14400)))


# --- preview_object ------------------------------------------------------------------------


def detached_source(media_type: MediaType, raster_key: str | None) -> Source:
    return Source(storage_key="original-key", media_type=media_type.value, raster_key=raster_key)


def test_preview_prefers_the_stored_raster() -> None:
    source = detached_source(MediaType.PDF, "raster-key")

    assert preview_object(source) == ("raster-key", MediaType.PNG)


@pytest.mark.parametrize("media_type", [MediaType.PDF, MediaType.JPEG])
def test_preview_falls_back_to_the_original_without_raster(media_type: MediaType) -> None:
    source = detached_source(media_type, None)

    assert preview_object(source) == ("original-key", media_type)


# --- rasterize_source and the worker task ----------------------------------------------------


@pytest.fixture(scope="module")
def raster_engine(database_url: str) -> Iterator[Engine]:
    """Own schema with the app tables; dropped afterwards."""
    schema = f"test_raster_{uuid.uuid4().hex}"
    admin = to_conninfo(database_url)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    engine = create_db_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def factory(raster_engine: Engine) -> Iterator[sessionmaker[Session]]:
    session_factory = create_session_factory(raster_engine)
    yield session_factory
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with session_factory() as db:
        db.execute(sql_text(f"TRUNCATE {names} CASCADE"))
        db.commit()


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return FilesystemStorage(str(tmp_path / "storage"))


def insert_source(
    factory: sessionmaker[Session], storage: Storage, data: bytes, media_type: MediaType
) -> uuid.UUID:
    stored = storage.put(io.BytesIO(data))
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            password_hash=hash_password("correct horse battery staple"),
            role=UserRole.TECHNICIAN,
        )
        db.add(user)
        db.flush()
        case_file = CaseFile(
            code=f"EXP-2026-{uuid.uuid4().hex[:4]}",
            incident_type=IncidentType.TRAFFIC_COLLISION,
            incident_date=date(2026, 10, 20),
            description="Colisión entre dos vehículos",
            declared_location="Av. Libertador 1200",
            created_by=user.id,
        )
        db.add(case_file)
        db.flush()
        source = Source(
            case_file_id=case_file.id,
            original_filename="plano.pdf",
            media_type=media_type.value,
            byte_size=len(data),
            sha256=stored.sha256,
            storage_key=stored.key,
            uploaded_by=user.id,
        )
        db.add(source)
        db.commit()
        return source.id


def load(factory: sessionmaker[Session], source_id: uuid.UUID) -> Source:
    with factory() as db:
        source = db.get(Source, source_id)
        assert source is not None
        return source


def test_rasterize_source_stores_the_png_and_updates_size_and_dpi(
    factory: sessionmaker[Session], storage: Storage
) -> None:
    data = FIXTURE_PDF.read_bytes()
    source_id = insert_source(factory, storage, data, MediaType.PDF)

    with factory() as db, db.begin():
        rasterize_source(db, storage, source_id)

    source = load(factory, source_id)
    assert source.raster_key is not None
    assert source.raster_key != source.storage_key
    image = png_of(storage.get(source.raster_key))
    assert image.size == (source.width_px, source.height_px)
    assert abs(image.width - A4_SIZE_PX[0]) <= SIZE_TOLERANCE_PX
    assert abs(image.height - A4_SIZE_PX[1]) <= SIZE_TOLERANCE_PX
    assert source.dpi == Decimal(RASTER_DPI)
    assert storage.get(source.storage_key) == data  # the original is untouched


def test_rasterize_source_is_idempotent(
    factory: sessionmaker[Session], storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = insert_source(factory, storage, FIXTURE_PDF.read_bytes(), MediaType.PDF)
    with factory() as db, db.begin():
        rasterize_source(db, storage, source_id)
    first = load(factory, source_id)

    def fail(_: bytes) -> None:
        raise AssertionError("an existing raster must not be rendered again")

    monkeypatch.setattr("sketch2cad.sources.raster.rasterize_pdf", fail)
    with factory() as db, db.begin():
        rasterize_source(db, storage, source_id)

    second = load(factory, source_id)
    assert (second.raster_key, second.width_px, second.height_px) == (
        first.raster_key,
        first.width_px,
        first.height_px,
    )


def test_rasterize_source_leaves_image_sources_unchanged(
    factory: sessionmaker[Session], storage: Storage
) -> None:
    buffer = io.BytesIO()
    Image.new("L", (64, 32), 255).save(buffer, format="PNG")
    source_id = insert_source(factory, storage, buffer.getvalue(), MediaType.PNG)

    with factory() as db, db.begin():
        rasterize_source(db, storage, source_id)

    source = load(factory, source_id)
    assert (source.raster_key, source.width_px, source.height_px, source.dpi) == (
        None,
        None,
        None,
        None,
    )


def test_rasterize_source_rolls_back_when_the_pdf_cannot_be_rendered(
    factory: sessionmaker[Session], storage: Storage
) -> None:
    source_id = insert_source(factory, storage, b"%PDF-1.7 broken", MediaType.PDF)

    with pytest.raises(RasterError), factory() as db, db.begin():
        rasterize_source(db, storage, source_id)

    assert load(factory, source_id).raster_key is None


def test_rasterize_source_raises_for_an_unknown_source(
    factory: sessionmaker[Session], storage: Storage
) -> None:
    with pytest.raises(SourceNotFoundError), factory() as db:
        rasterize_source(db, storage, uuid.uuid4())


def test_raster_preview_keeps_the_raster_aspect(
    factory: sessionmaker[Session], storage: Storage
) -> None:
    source_id = insert_source(factory, storage, FIXTURE_PDF.read_bytes(), MediaType.PDF)
    with factory() as db, db.begin():
        rasterize_source(db, storage, source_id)
    key, media_type = preview_object(load(factory, source_id))

    with storage.open(key) as stream:
        preview = Image.open(io.BytesIO(render_preview(stream, media_type, 1024)))

    assert preview.format == "JPEG"
    assert preview.height == 1024
    assert abs(preview.width - round(1024 * A4_SIZE_PX[0] / A4_SIZE_PX[1])) <= 1


def test_worker_task_rasterizes_and_commits(
    factory: sessionmaker[Session], storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = insert_source(factory, storage, FIXTURE_PDF.read_bytes(), MediaType.PDF)
    monkeypatch.setattr(tasks_raster, "_session_factory", lambda: factory)
    monkeypatch.setattr(tasks_raster, "_storage", lambda: storage)

    tasks_raster.rasterize(str(source_id))

    source = load(factory, source_id)
    assert source.raster_key is not None
    assert storage.exists(source.raster_key)


def test_worker_app_registers_the_rasterize_task() -> None:
    app = create_worker_app("dbname=unused")
    tasks = cast(dict[str, Any], app.tasks)  # pyright: ignore[reportUnknownMemberType]

    assert tasks[tasks_raster.RASTERIZE_TASK_NAME].func is tasks_raster.rasterize


def test_worker_storage_reads_storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "objects-root"))

    stored = tasks_raster._storage().put(io.BytesIO(b"raster"))  # pyright: ignore[reportPrivateUsage]

    assert (tmp_path / "objects-root").is_dir()
    assert stored.key.startswith("sha256/")
