"""Copying a project's inputs to and from a bucket."""

import threading

import fsspec
import pytest

from pystiltctl import inputs
from pystiltctl.inputs import hash_inputs, is_local, local_path, pull, push, put
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


def test_put_is_atomic_on_a_local_folder(tmp_path):
    """
    A reader must never see a file half written while ``put`` copies it (#29).

    ``put`` always copies the same bytes here, so the only way a read can
    come back different is a reader catching the destination between its
    truncation and the new content landing: a torn write, not a stale one.
    """
    directory = tmp_path / "pod"
    directory.mkdir()
    url = tmp_path / "shared"
    url.mkdir()
    content = b"time,longitude,latitude,altitude\n" + b"x" * 50_000 + b"\n"
    (directory / "receptors.csv").write_bytes(content)
    path = url / "receptors.csv"
    path.write_bytes(content)  # the file a pod would already have pulled

    stop = threading.Event()
    bad_reads = []

    def read_forever():
        while not stop.is_set():
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                continue
            if data != content:
                bad_reads.append(len(data))

    reader = threading.Thread(target=read_forever)
    reader.start()
    try:
        for _ in range(200):
            put(directory, str(url), "receptors.csv")
    finally:
        stop.set()
        reader.join()
    assert bad_reads == []


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


def test_get_copies_one_file_back(project, bucket, tmp_path):
    push(project, bucket)
    (tmp_path / "pod").mkdir()
    inputs.get(bucket, tmp_path / "pod", "config.yaml")
    assert (tmp_path / "pod" / "config.yaml").read_text() == CONFIG


@pytest.fixture
def fast_lock(monkeypatch):
    """Locks looked at every 10 ms, and stale after 50 ms."""
    monkeypatch.setattr(inputs, "LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(inputs, "LOCK_STALE_SECONDS", 0.05)


@pytest.mark.parametrize("where", ["bucket", "folder"])
def test_lock_lets_one_holder_in_at_a_time(where, bucket, tmp_path, monkeypatch):
    monkeypatch.setattr(inputs, "LOCK_POLL_SECONDS", 0.01)
    url = bucket if where == "bucket" else str(tmp_path)
    fs, root = fsspec.core.url_to_fs(url)
    fs.makedirs(root, exist_ok=True)
    order = []

    def second():
        with inputs.lock(url, "receptors.csv"):
            order.append("second")

    with inputs.lock(url, "receptors.csv"):
        assert fs.exists(f"{root}/.receptors.csv.lock")
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(0.2)
        assert thread.is_alive()
        order.append("first")
    thread.join(10)
    assert order == ["first", "second"]
    assert not fs.exists(f"{root}/.receptors.csv.lock")


def test_lock_is_released_when_the_block_raises(bucket):
    fs, root = fsspec.core.url_to_fs(bucket)
    with (
        pytest.raises(ValueError, match="bad row"),
        inputs.lock(bucket, "receptors.csv"),
    ):
        raise ValueError("bad row")
    assert not fs.exists(f"{root}/.receptors.csv.lock")


def test_lock_takes_over_a_stale_lock(bucket, fast_lock, caplog):
    """A holder that crashed leaves its lock; the next one takes it over."""
    fs, root = fsspec.core.url_to_fs(bucket)
    fs.pipe_file(f"{root}/.receptors.csv.lock", b"a pod that crashed")
    with inputs.lock(bucket, "receptors.csv"):
        assert fs.cat_file(f"{root}/.receptors.csv.lock") != b"a pod that crashed"
    assert "Taking over" in caplog.text
    assert "a pod that crashed" in caplog.text
    assert not fs.exists(f"{root}/.receptors.csv.lock")


def test_lock_leaves_a_lock_taken_over_from_it(bucket, caplog):
    """A slow holder must not remove the lock of the program that took it over."""
    fs, root = fsspec.core.url_to_fs(bucket)
    with inputs.lock(bucket, "receptors.csv"):
        fs.pipe_file(f"{root}/.receptors.csv.lock", b"the program that took over")
    assert fs.cat_file(f"{root}/.receptors.csv.lock") == b"the program that took over"
    assert "was taken over" in caplog.text


def test_lock_says_what_it_could_not_do(bucket, monkeypatch):
    def refuse(*args):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(inputs, "_acquire", refuse)
    with (
        pytest.raises(
            OSError, match=r"cannot lock receptors.csv at memory://.*went away"
        ),
        inputs.lock(bucket, "receptors.csv"),
    ):
        pass


def test_hash_inputs_is_the_same_for_the_same_content(project, tmp_path):
    """Two separate pulls of the same content hash the same (#15)."""
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "config.yaml").write_text((project / "config.yaml").read_text())
    (copy / "receptors.csv").write_text((project / "receptors.csv").read_text())
    assert hash_inputs(project) == hash_inputs(copy)


def test_hash_inputs_changes_with_receptors(project):
    before = hash_inputs(project)
    (project / "receptors.csv").write_text(
        "time,longitude,latitude,altitude\n2021-01-15 00:00:00,-112,40,10\n"
    )
    assert hash_inputs(project) != before


def test_hash_inputs_changes_with_config(project):
    before = hash_inputs(project)
    (project / "config.yaml").write_text(CONFIG + "n_hours: -48\n")
    assert hash_inputs(project) != before


def test_hash_inputs_ignores_tables(project):
    """Only config.yaml and receptors.csv decide a run's receptor share."""
    before = hash_inputs(project)
    (project / "tables" / "kernels.parquet").write_bytes(b"different bytes")
    assert hash_inputs(project) == before


def test_hash_inputs_refuses_a_folder_that_is_not_a_project(tmp_path):
    with pytest.raises(FileNotFoundError, match="no config.yaml"):
        hash_inputs(tmp_path)


@pytest.mark.parametrize(
    ("typed", "meant"),
    [
        ("s3:/bucket/projects/p", "s3://bucket/projects/p"),
        ("gs:bucket/p", "gs://bucket/p"),
        ("memory:/b/p", "memory://b/p"),
    ],
)
def test_push_refuses_a_url_missing_a_slash(
    project, tmp_path, monkeypatch, typed, meant
):
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)
    with pytest.raises(ValueError, match=f"did you mean '{meant}'"):
        push(project, typed)
    assert list(here.iterdir()) == []  # no folder "s3:" made here


@pytest.mark.parametrize(
    "location",
    ["s3://bucket/p", "/data/p", "relative/p", "file:/data/p", "file:///data/p"],
)
def test_check_url_passes_urls_and_paths(location):
    inputs.check_url(location)


def test_cli_push_to_a_url_missing_a_slash_exits_2(project, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pystiltctl.cli import app

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["push", str(project), "s3:/bucket/p"])
    assert result.exit_code == 2
    assert "did you mean 's3://bucket/p'" in result.output
    assert "Copied" not in result.output
