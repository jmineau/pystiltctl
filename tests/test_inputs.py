"""Copying a project's inputs to and from a bucket."""

import fsspec
import pytest

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
