"""Tests for the blob-storage helpers and the MinIO service.

No test touches the network: the MinIO client is replaced with a small fake and
the abstract helper methods are exercised directly on the base class.
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from minio.error import S3Error

from squirrel.schemas.file import FileType
from squirrel.services.storage.blob import IBlobStorageService as blob_module
from squirrel.services.storage.blob import MinIOService as minio_module
from squirrel.services.storage.blob.IBlobStorageService import IBlobStorageService
from squirrel.services.storage.blob.MinIOService import MinIOService


# ——————————————————————————————————————————————————————————————
# IBlobStorageService helpers


@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        (".pdf", FileType.PDF),
        (".XLSX", FileType.XLSX),
        (".xls", FileType.XLSX),
        (".csv", FileType.CSV),
        (".doc", FileType.DOCX),
        (".docx", FileType.DOCX),
        (".json", FileType.JSON),
        (".md", FileType.MD),
        (".html", FileType.HTML),
        (".htm", FileType.HTML),
        (".png", FileType.PNG),
        (".jpg", FileType.JPG),
        (".jpeg", FileType.JPG),
        (".gif", FileType.GIF),
        (".parquet", FileType.UNKNOWN),
        ("", FileType.UNKNOWN),
    ],
)
def test_get_file_type_maps_extensions(extension: str, expected: FileType) -> None:
    assert IBlobStorageService._get_file_type(extension) is expected


def test_calculate_file_md5_matches_hashlib() -> None:
    assert (
        IBlobStorageService._calculate_file_md5(b"hello")
        == "5d41402abc4b2a76b9719d911017c592"
    )


def test_calculate_file_md5_swallows_errors(monkeypatch) -> None:
    class Broken:
        def update(self, _: bytes) -> None:
            raise RuntimeError("boom")

        def hexdigest(self) -> str:
            return "unreachable"

    monkeypatch.setattr(blob_module.hashlib, "md5", lambda: Broken())

    assert IBlobStorageService._calculate_file_md5(b"hello") == ""


def test_extract_zip_metadata_reads_each_entry(tmp_path) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("reports/orders.csv", "a,b\n1,2\n")
        zf.writestr("notes.txt", "hello")
        zf.writestr("empty/", "")

    metadata = IBlobStorageService._extract_zip_metadata(str(archive))

    assert set(metadata) == {"orders.csv", "notes.txt"}
    assert metadata["orders.csv"]["type"] is FileType.CSV
    assert metadata["orders.csv"]["size"] == 8
    assert len(metadata["orders.csv"]["md5"]) == 32
    assert isinstance(metadata["orders.csv"]["created_at"], datetime)


def test_extract_zip_metadata_returns_empty_for_bad_archive(tmp_path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")

    assert IBlobStorageService._extract_zip_metadata(str(broken)) == {}


# ——————————————————————————————————————————————————————————————
# MinIOService


class FakeMinioClient:
    def __init__(self, *, bucket_exists: bool = True) -> None:
        self.bucket_exists_result = bucket_exists
        self.buckets: list[str] = []
        self.fget_calls: list[tuple] = []
        self.fput_calls: list[tuple] = []
        self.remove_calls: list[tuple] = []
        self.stat_result = None
        self.stat_error: Exception | None = None
        self.fget_error: Exception | None = None
        self.fput_error: Exception | None = None
        self.remove_error: Exception | None = None
        self.payload = b"file-contents"

    def bucket_exists(self, bucket: str) -> bool:
        return self.bucket_exists_result

    def make_bucket(self, bucket: str) -> None:
        self.buckets.append(bucket)

    def fget_object(self, bucket: str, name: str, path: str) -> None:
        self.fget_calls.append((bucket, name, path))
        if self.fget_error:
            raise self.fget_error
        with open(path, "wb") as handle:
            handle.write(self.payload)

    def fput_object(self, bucket, name, path, metadata=None) -> None:
        self.fput_calls.append((bucket, name, path, metadata))
        if self.fput_error:
            raise self.fput_error

    def stat_object(self, bucket: str, name: str):
        if self.stat_error:
            raise self.stat_error
        return self.stat_result

    def remove_object(self, bucket: str, name: str) -> None:
        self.remove_calls.append((bucket, name))
        if self.remove_error:
            raise self.remove_error


def _s3_error(code: str) -> S3Error:
    return S3Error(
        code=code,
        message="boom",
        resource="/bucket/key",
        request_id="req",
        host_id="host",
        response=None,
    )


@pytest.fixture()
def fake_client(monkeypatch) -> FakeMinioClient:
    client = FakeMinioClient(bucket_exists=False)
    client.stat_result = SimpleNamespace(metadata={})
    monkeypatch.setattr(minio_module, "Minio", lambda *a, **k: client)
    return client


def test_init_creates_bucket_when_missing(fake_client: FakeMinioClient) -> None:
    service = MinIOService()

    assert service.client is fake_client
    assert service.bucket == minio_module.MinIOConfig.BUCKET.value
    assert fake_client.buckets == [service.bucket]


def test_init_skips_bucket_creation_when_present(fake_client: FakeMinioClient) -> None:
    fake_client.bucket_exists_result = True

    MinIOService()

    assert fake_client.buckets == []


def _service_with(client: FakeMinioClient, bucket: str = "sqrl") -> MinIOService:
    service = object.__new__(MinIOService)
    service.client = client
    service.bucket = bucket
    return service


def test_retrieve_file_reads_bytes_and_cleans_up(fake_client: FakeMinioClient) -> None:
    fake_client.payload = b"\x00binary\xff"
    service = _service_with(fake_client)

    file = service.retrieve_file("s3://sqrl/models/model.joblib")

    assert file is not None
    assert file.fileName == "model.joblib"
    assert file.fileByte == b"\x00binary\xff"
    assert file.size == len(b"\x00binary\xff")
    bucket, name, path = fake_client.fget_calls[0]
    assert (bucket, name) == ("sqrl", "models/model.joblib")
    assert not Path(path).exists()


def test_retrieve_file_accepts_bucket_relative_path(fake_client: FakeMinioClient) -> None:
    service = _service_with(fake_client)

    assert service.retrieve_file("sqrl/reports/out.csv") is not None
    assert fake_client.fget_calls[0][:2] == ("sqrl", "reports/out.csv")


def test_retrieve_file_returns_none_for_invalid_path(fake_client: FakeMinioClient) -> None:
    assert _service_with(fake_client).retrieve_file("just-a-key") is None
    assert fake_client.fget_calls == []


def test_retrieve_file_returns_none_on_s3_error(fake_client: FakeMinioClient) -> None:
    fake_client.fget_error = _s3_error("NoSuchKey")

    assert _service_with(fake_client).retrieve_file("s3://sqrl/missing.csv") is None


def test_upload_file_rejects_empty_object_name(fake_client: FakeMinioClient, tmp_path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("a,b\n")

    with pytest.raises(ValueError, match="object_name is required"):
        _service_with(fake_client).upload_file(str(path), "")


def test_upload_file_returns_none_for_missing_file(fake_client: FakeMinioClient, tmp_path) -> None:
    assert (
        _service_with(fake_client).upload_file(str(tmp_path / "missing.csv"), "k.csv")
        is None
    )


def test_upload_file_stores_md5_metadata(fake_client: FakeMinioClient, tmp_path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("a,b\n1,2\n")
    service = _service_with(fake_client)

    file = service.upload_file(str(path), "uploads/orders.csv", {"owner": "user-1"})

    assert file is not None
    assert file.fileUrl == "s3://sqrl/uploads/orders.csv"
    assert file.fileName == "uploads/orders.csv"
    bucket, name, sent_path, metadata = fake_client.fput_calls[0]
    assert (bucket, name) == ("sqrl", "uploads/orders.csv")
    assert sent_path == str(path)
    assert metadata["owner"] == "user-1"
    assert len(metadata["x-amz-meta-md5"]) == 32


def test_upload_file_skips_identical_object(fake_client: FakeMinioClient, tmp_path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("a,b\n1,2\n")
    md5 = IBlobStorageService._calculate_file_md5(path.read_bytes())
    fake_client.stat_result = SimpleNamespace(metadata={"x-amz-meta-md5": md5})

    file = _service_with(fake_client).upload_file(str(path), "orders.csv")

    assert file is not None
    assert fake_client.fput_calls == []


def test_upload_file_overwrites_different_object(fake_client: FakeMinioClient, tmp_path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("a,b\n1,2\n")
    fake_client.stat_result = SimpleNamespace(metadata={"x-amz-meta-md5": "stale"})

    assert _service_with(fake_client).upload_file(str(path), "orders.csv") is not None
    assert len(fake_client.fput_calls) == 1


def test_upload_file_treats_missing_object_as_new(fake_client: FakeMinioClient, tmp_path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("a,b\n1,2\n")
    fake_client.stat_error = _s3_error("NoSuchKey")

    assert _service_with(fake_client).upload_file(str(path), "orders.csv") is not None
    assert len(fake_client.fput_calls) == 1


def test_upload_file_propagates_unexpected_stat_errors(
    fake_client: FakeMinioClient, tmp_path
) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("a,b\n1,2\n")
    fake_client.stat_error = _s3_error("AccessDenied")

    assert _service_with(fake_client).upload_file(str(path), "orders.csv") is None


def test_upload_file_returns_none_on_s3_error(fake_client: FakeMinioClient, tmp_path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("a,b\n1,2\n")
    fake_client.stat_error = _s3_error("NoSuchKey")
    fake_client.fput_error = _s3_error("InternalError")

    assert _service_with(fake_client).upload_file(str(path), "orders.csv") is None


def test_delete_file_removes_object(fake_client: FakeMinioClient) -> None:
    assert _service_with(fake_client).delete_file("s3://sqrl/tmp/a.csv") is True
    assert fake_client.remove_calls == [("sqrl", "tmp/a.csv")]


def test_delete_file_rejects_invalid_path(fake_client: FakeMinioClient) -> None:
    assert _service_with(fake_client).delete_file("no-bucket") is False
    assert fake_client.remove_calls == []


def test_delete_file_returns_false_on_s3_error(fake_client: FakeMinioClient) -> None:
    fake_client.remove_error = _s3_error("NoSuchKey")

    assert _service_with(fake_client).delete_file("s3://sqrl/tmp/a.csv") is False
