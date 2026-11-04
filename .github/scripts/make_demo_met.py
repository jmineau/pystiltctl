"""
Write one tiny, valid ARL file, for the image smoke test's demo project.

Since PYSTILT 0.1.0a27 (its fix for stilt#280), ``stilt run`` reads a met's
first file to find its weather product even for a project with no
receptors, so the image workflow's "no meteorology" demo needs a real file
to point at, not the placeholder directory ``stilt init`` writes.

    python make_demo_met.py /path/to/met/directory
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from arlmet import File
from arlmet.grid import Grid, Projection
from arlmet.vertical import PressureAxis


def main(directory: Path) -> None:
    """Write one minimal ARL file under *directory*."""
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


if __name__ == "__main__":
    main(Path(sys.argv[1]))
