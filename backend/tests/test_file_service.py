"""Tests for the file-metadata service.

``FileService`` owns the ``file`` table only; blob storage is the caller's job.
The tests patch ``PostgresService`` with :class:`tests.fakes.FakeDB` so the SQL
shape and the ownership rules can be asserted without a database.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import squirrel.services.file.FileService as file_module
from squirrel.schemas.file import File, FileInUseError, FileRecordNotFound
from squirrel.services.file.FileService import FileService
from tests.fakes import FakeDB


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _row(**overrides):
    row = {
        "file_url": "s3://bucket/orders.csv",
        "file_name": "orders.csv",
        "file_type": "csv",
        "size": 128,
        "uploaded_at": NOW,
        "workspace_ids": [],
        "notebook_ids": [],
        "owner_user_id": "user-1",
    }
    row.update(overrides)
    return row


@pytest.fixture()
def db(monkeypatch) -> FakeDB:
    fake = FakeDB()
    monkeypatch.setattr(file_module, "PostgresService", lambda: fake)
    return fake


def test_init_creates_table_and_backfills_columns(db: FakeDB) -> None:
    FileService()

    assert len(db.executes) == 5
    assert "CREATE TABLE IF NOT EXISTS file" in db.executes[0].sql
    assert all("ADD COLUMN IF NOT EXISTS" in call.sql for call in db.executes[1:])
    assert db.disconnects == 1


def test_ensure_table_swallows_database_errors(monkeypatch) -> None:
    fake = FakeDB()
    monkeypatch.setattr(file_module, "PostgresService", lambda: fake)
    monkeypatch.setattr(fake, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    FileService()  # does not raise

    assert fake.disconnects == 1


def test_record_upload_builds_upsert(db: FakeDB) -> None:
    service = FileService()
    file = File(
        fileUrl="s3://bucket/orders.csv",
        fileName="orders.csv",
        size=128,
        uploadedAt=NOW,
    )

    service.record_upload(file, "user-1", workspace_id="ws-1")

    params = db.inserts[0].params
    assert "ON CONFLICT (file_url)" in db.inserts[0].sql
    assert params["file_name"] == "orders.csv"
    assert params["workspace_ids"] == ["ws-1"]
    assert params["notebook_ids"] == []
    assert params["owner_user_id"] == "user-1"
    assert params["user_uploaded"] is True


def test_record_upload_marks_non_user_artifacts(db: FakeDB) -> None:
    service = FileService()

    service.record_upload(
        File(fileUrl="s3://bucket/models/m.joblib", fileName="m.joblib"),
        None,
        notebook_id="nb-1",
        user_uploaded=False,
    )

    params = db.inserts[0].params
    assert params["notebook_ids"] == ["nb-1"]
    assert params["user_uploaded"] is False


def test_record_upload_swallows_insert_failures(monkeypatch) -> None:
    fake = FakeDB()
    monkeypatch.setattr(file_module, "PostgresService", lambda: fake)
    monkeypatch.setattr(fake, "insert", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    service = FileService()

    service.record_upload(File(fileUrl="s3://bucket/x.csv", fileName="x.csv"), "user-1")

    assert fake.disconnects == 2  # init + record_upload


def test_is_owned_by_checks_owner_filter(db: FakeDB) -> None:
    db.queue_retrieve({"rows": [_row()]})
    service = FileService()

    assert service.is_owned_by("s3://bucket/orders.csv", "user-1") is True
    assert db.retrieves[0].filters == {
        "file_url": "s3://bucket/orders.csv",
        "owner_user_id": "user-1",
    }


def test_is_owned_by_returns_false_when_absent(db: FakeDB) -> None:
    service = FileService()

    assert service.is_owned_by("s3://bucket/missing.csv", "user-1") is False


def test_rename_file_updates_record_and_notebook_sources(db: FakeDB) -> None:
    db.queue_retrieve({"rows": [_row()]}, {"rows": [_row(file_name="renamed.csv")]})
    service = FileService()

    renamed = service.rename_file("s3://bucket/orders.csv", "user-1", "renamed.csv")

    assert renamed.fileName == "renamed.csv"
    assert "UPDATE file SET file_name" in db.queries[0].sql
    assert "sqrl_notebook_data_sources" in db.queries[1].sql
    assert db.queries[1].params["label"] == "renamed.csv"


def test_rename_file_rejects_unowned_files(db: FakeDB) -> None:
    service = FileService()

    with pytest.raises(FileRecordNotFound):
        service.rename_file("s3://bucket/orders.csv", "intruder", "renamed.csv")


def test_rename_file_raises_when_reread_is_empty(db: FakeDB) -> None:
    db.queue_retrieve({"rows": [_row()]}, {"rows": []})
    service = FileService()

    with pytest.raises(FileRecordNotFound):
        service.rename_file("s3://bucket/orders.csv", "user-1", "renamed.csv")


def test_delete_file_removes_unused_record(db: FakeDB) -> None:
    db.queue_retrieve({"rows": [_row()]})
    service = FileService()

    service.delete_file("s3://bucket/orders.csv", "user-1")

    assert "DELETE FROM file" in db.queries[0].sql
    assert db.queries[0].params["owner_user_id"] == "user-1"


def test_delete_file_rejects_missing_records(db: FakeDB) -> None:
    service = FileService()

    with pytest.raises(FileRecordNotFound):
        service.delete_file("s3://bucket/missing.csv", "user-1")


@pytest.mark.parametrize("column", ["workspace_ids", "notebook_ids"])
def test_delete_file_rejects_files_in_use(db: FakeDB, column: str) -> None:
    db.queue_retrieve({"rows": [_row(**{column: ["ref-1"]})]})
    service = FileService()

    with pytest.raises(FileInUseError):
        service.delete_file("s3://bucket/orders.csv", "user-1")


def test_list_files_maps_rows_and_filters_artifacts(db: FakeDB) -> None:
    db.queue({"rows": [_row(), _row(file_url="s3://bucket/other.csv", file_name="other.csv")]})
    service = FileService()

    files = service.list_files("user-1")

    assert [f.fileName for f in files] == ["orders.csv", "other.csv"]
    params = db.queries[0].params
    assert params["owner_user_id"] == "user-1"
    assert params["preprocessed_pattern"] == "%/preprocessed/%"
    assert params["models_pattern"] == "%/models/%"
    assert params["pipelines_pattern"] == "%/pipelines/%"


def test_list_files_handles_empty_result(db: FakeDB) -> None:
    service = FileService()

    assert service.list_files("user-1") == []


def test_get_owned_file_returns_none_when_absent(db: FakeDB) -> None:
    service = FileService()

    assert service.get_owned_file("s3://bucket/missing.csv", "user-1") is None


def test_get_owned_file_returns_file(db: FakeDB) -> None:
    db.queue_retrieve({"rows": [_row()]})
    service = FileService()

    file = service.get_owned_file("s3://bucket/orders.csv", "user-1")

    assert file is not None
    assert file.fileUrl == "s3://bucket/orders.csv"
    assert file.ownerUserId == "user-1"


def test_from_dict_accepts_missing_optional_columns() -> None:
    file = FileService.from_dict(
        {"file_url": "s3://bucket/x.csv", "file_name": "x.csv"}
    )

    assert file.fileName == "x.csv"
    assert file.fileType is None
    assert file.size is None
