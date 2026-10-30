"""Shared fixtures: a small project folder and a bucket in memory."""

import uuid

import pytest

#: The smallest config.yaml PYSTILT opens: one met and one variant.
CONFIG = """\
mets:
  hrrr: {directory: /met, file_format: "%Y%m%d_%H", file_tres: 6h}
variants:
  hrrr: {}
n_hours: -24
"""


@pytest.fixture
def project(tmp_path):
    """A project folder with inputs, a table, and output that must not be copied."""
    directory = tmp_path / "my_project"
    (directory / "tables").mkdir(parents=True)
    (directory / "config.yaml").write_text(CONFIG)
    (directory / "receptors.csv").write_text("time,longitude,latitude,altitude\n")
    (directory / "tables" / "kernels.parquet").write_bytes(b"not really parquet")
    (directory / "output").mkdir()
    (directory / "output" / "result.nc").write_bytes(b"a result")
    return directory


@pytest.fixture
def bucket():
    """A URL on fsspec's in-memory store, unique to the test."""
    return f"memory://bucket-{uuid.uuid4().hex}/projects/my_project"


@pytest.fixture
def execs(monkeypatch):
    """Record the command the worker would become, instead of becoming it."""
    from pystiltctl import worker

    calls = []
    monkeypatch.setattr(worker.os, "execv", lambda path, args: calls.append(args))
    return calls


@pytest.fixture
def sited(project):
    """The project with two sites, one with a name and an altitude reference."""
    (project / "sites.csv").write_text(
        "name,longitude,latitude,altitude,altitude_ref\n"
        "wbb,-111.8476,40.7663,35,agl\n"
        "dbk,-112.1155,40.5385,5,\n"
    )
    return project
