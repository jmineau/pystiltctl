"""Shared fixtures: a small project folder and a bucket in memory."""

import atexit
import shutil
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from arlmet import File
from arlmet.grid import Grid, Projection
from arlmet.vertical import PressureAxis


def _write_met(directory: Path) -> None:
    """Write one minimal, valid ARL file under *directory*, offline."""
    grid = Grid(
        projection=Projection(
            pole_lat=90.0,
            pole_lon=0.0,
            tangent_lat=1.0,
            tangent_lon=1.0,
            grid_size=0.0,
            orientation=0.0,
            cone_angle=0.0,
            sync_x=1.0,
            sync_y=1.0,
            sync_lat=-10.0,
            sync_lon=20.0,
        ),
        nx=20,
        ny=20,
    )
    directory.mkdir(parents=True, exist_ok=True)
    with File(
        directory / "20260101_00",
        mode="w",
        source="hrrr",
        grid=grid,
        vertical_axis=PressureAxis(levels=[0.0]),
    ) as arl:
        rs = arl.create_recordset(pd.Timestamp("2026-01-01 00:00"))
        rs.create_datarecord(
            "PRSS", level=0, forecast=0, data=np.zeros((20, 20), dtype=np.float32)
        )


# A session-wide met directory, written once: since PYSTILT#280 (0.1.0a27),
# `stilt status` reads a met's first file even for a project with no results
# yet, so every project `status` opens needs one (pystiltctl#37). An absolute
# path, not one relative to the project directory, so it is still there after
# a push/pull through a bucket, which never carries met.
_MET_DIR = Path(tempfile.mkdtemp(prefix="pystiltctl-test-met-"))
_write_met(_MET_DIR)
atexit.register(shutil.rmtree, _MET_DIR, ignore_errors=True)

#: The smallest config.yaml PYSTILT opens: one met and one variant.
CONFIG = f"""\
mets:
  hrrr: {{directory: {_MET_DIR}, file_format: "%Y%m%d_%H", file_tres: 6h}}
variants:
  hrrr: {{}}
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
