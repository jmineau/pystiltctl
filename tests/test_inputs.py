"""Copying a project's inputs to and from a bucket."""

import fsspec
import pytest

from pystiltctl import inputs
from pystiltctl.inputs import is_local, local_path, pull, push
from tests.conftest import CONFIG


def test_push_copies_inputs_and_tables_not_output(project, bucket):
    copied = push(project, bucket)
    assert copied == ["config.yaml", "receptors.csv", "tables/kernels.parquet"]
    fs, root = fsspec.core.url_to_fs(bucket)
    assert sorted(p.removeprefix(root + "/") for p in fs.find(root)) == sorted(copied)


def test_pull_restores_what_push_copied(project, bucket, tmp_path):
    push(project, bucket)
    directory = pull(bucket, tmp_path / "pulled")
    assert (directory / "config.yaml").read_text() == CONFIG
    assert (
        directory / "tables" / "kernels.parquet"
    ).read_bytes() == b"not really parquet"
    assert not (directory / "output").exists()


def test_push_and_pull_through_a_local_folder(project, tmp_path):
    push(project, str(tmp_path / "shared" / "my_project"))
    directory = pull(str(tmp_path / "shared" / "my_project"), tmp_path / "pod")
    assert (directory / "receptors.csv").is_file()


def test_project_without_tables(project, bucket, tmp_path):
    for path in (project / "tables").iterdir():
        path.unlink()
    (project / "tables").rmdir()
    assert push(project, bucket) == ["config.yaml", "receptors.csv"]
    assert not (pull(bucket, tmp_path / "pod") / "tables").exists()


def test_push_refuses_a_folder_that_is_not_a_project(tmp_path, bucket):
    with pytest.raises(FileNotFoundError, match="no config.yaml"):
        push(tmp_path, bucket)


def test_pull_refuses_a_url_with_no_project(bucket, tmp_path):
    with pytest.raises(FileNotFoundError, match="No PYSTILT project"):
        pull(bucket, tmp_path / "pod")


class _Unreachable:
    """Wraps a real filesystem but raises *error* from ``info``, as a broken bucket would."""

    def __init__(self, fs, error: Exception):
        self.fs = fs
        self.error = error

    def __getattr__(self, name):
        return getattr(self.fs, name)

    def info(self, path, **kwargs):
        raise self.error


class _EndpointConnectionError(Exception):
    """Stands in for a botocore error s3fs does not translate into an OSError."""


def _break(monkeypatch, error: Exception) -> None:
    """Make every ``fsspec.core.url_to_fs`` call in ``inputs`` return a broken filesystem."""
    fs_for = fsspec.core.url_to_fs

    def broken(url):
        fs, root = fs_for(url)
        return _Unreachable(fs, error), root

    monkeypatch.setattr(inputs.fsspec.core, "url_to_fs", broken)


def test_pull_raises_oserror_on_a_bucket_error_fsspec_does_not_translate(
    bucket, tmp_path, monkeypatch
):
    """A bucket error that is not a plain missing file must not look like one (#11)."""
    _break(monkeypatch, _EndpointConnectionError("could not connect to the endpoint"))
    with pytest.raises(OSError, match="could not connect") as excinfo:
        pull(bucket, tmp_path / "pod")
    assert not isinstance(excinfo.value, FileNotFoundError)


def test_pull_lets_an_oserror_from_the_bucket_through_unchanged(
    bucket, tmp_path, monkeypatch
):
    """A connectivity error fsspec already raises as OSError is not swallowed either."""
    _break(monkeypatch, ConnectionError("refused"))
    with pytest.raises(OSError, match="refused") as excinfo:
        pull(bucket, tmp_path / "pod")
    assert not isinstance(excinfo.value, FileNotFoundError)


@pytest.mark.parametrize(
    ("location", "local"),
    [
        ("/data/my_project", True),
        ("relative/folder", True),
        ("s3://bucket/p", False),
        ("memory://b/p", False),
    ],
)
def test_is_local(location, local):
    assert is_local(location) is local


def test_local_path_drops_file_protocol():
    assert str(local_path("file:///data/p")) == "/data/p"
    assert str(local_path("/data/p")) == "/data/p"
