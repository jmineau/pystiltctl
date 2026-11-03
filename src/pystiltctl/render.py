"""
Draw footprints as images a web map can show.

Each footprint becomes a PNG and a small JSON beside it, under a folder
per day::

    <url>/2026-10-29/index.json                       # every footprint of the day
    <url>/2026-10-29/202610291000_-111.8476_40.7663_35.png
    <url>/2026-10-29/202610291000_-111.8476_40.7663_35.json

The PNG is the footprint summed over time, on one fixed log color scale
(:data:`VMIN` to :data:`VMAX`), so images compare from hour to hour; cells
below the scale are transparent. Its rows are spaced as on a web map
(web Mercator), so a map that stretches it between its corners, such as
Leaflet's ``imageOverlay``, draws each cell in its place. The JSON holds
those corners as ``bounds``, ``[[south, west], [north, east]]``, the
order Leaflet takes.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fsspec
import matplotlib
import numpy as np
import stilt
import xarray as xr

logger = logging.getLogger(__name__)

#: The bottom of the color scale, in the footprint's units; smaller is transparent.
VMIN = 1e-5
#: The top of the color scale; larger is drawn in the top color.
VMAX = 1e-1
#: The matplotlib colormap.
COLORMAP = "viridis"


def mercator_y(lat: Any) -> Any:
    """
    Return web Mercator's y for latitudes in degrees, in units of the Earth's radius.

    Examples
    --------
    >>> round(float(mercator_y(45.0)), 4)
    0.8814
    """
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def bounds(foot: xr.DataArray) -> list[list[float]]:
    """
    Return the footprint grid's outer corners, ``[[south, west], [north, east]]``.

    The grid's ``lat`` and ``lon`` are cell centers, so the corners are half
    a cell beyond the outer ones.
    """
    lat, lon = foot["lat"].values, foot["lon"].values
    dlat = abs(float(lat[1] - lat[0])) if lat.size > 1 else 0.0
    dlon = abs(float(lon[1] - lon[0])) if lon.size > 1 else 0.0
    return [
        [float(lat.min()) - dlat / 2, float(lon.min()) - dlon / 2],
        [float(lat.max()) + dlat / 2, float(lon.max()) + dlon / 2],
    ]


def _bands(lats: np.ndarray, south: float, north: float, rows: int) -> np.ndarray:
    """
    Return the grid row (index into *lats*, north first) each of *rows* image rows shows.

    The image rows are equal bands of web Mercator's y between *north* and
    *south*; each takes the grid row nearest its middle.
    """
    edges = np.linspace(mercator_y(north), mercator_y(south), rows + 1)
    middles = np.degrees(
        2 * np.arctan(np.exp((edges[:-1] + edges[1:]) / 2)) - np.pi / 2
    )
    return np.abs(lats[None, :] - middles[:, None]).argmin(axis=1)


def image(foot: xr.DataArray) -> np.ndarray:
    """
    Return the footprint as an RGBA image, north at the top, rows in web Mercator.

    The footprint is summed over ``time`` when it has that dimension. Each
    image row is a band of equal height on a web map, and takes the value
    of the grid row whose latitude is nearest its middle. The image has one
    row per grid row, unless that would leave a grid row out (the bands are
    taller than a cell where a wide grid is far from the equator); then it
    has as many rows as it takes for every grid row to show.
    """
    total = foot.sum("time") if "time" in foot.dims else foot
    total = total.transpose("lat", "lon").sortby("lat", ascending=False)
    values = np.asarray(total.values, dtype=float)
    (south, _), (north, _) = bounds(total)
    lats = total["lat"].values
    rows = values.shape[0]
    nearest = _bands(lats, south, north, rows)
    if len(set(nearest)) < rows:
        # Every cell is at least as tall (in Mercator y) as the smallest one,
        # so bands that high cannot step over a cell.
        cells = np.diff(mercator_y(np.linspace(north, south, rows + 1)))
        height = (mercator_y(north) - mercator_y(south)) / abs(cells).min()
        nearest = _bands(lats, south, north, int(np.ceil(height)))
    values = values[nearest]

    with np.errstate(divide="ignore", invalid="ignore"):
        scaled = (np.log10(values) - np.log10(VMIN)) / (np.log10(VMAX) - np.log10(VMIN))
    shown = np.isfinite(scaled) & (scaled >= 0)
    rgba = matplotlib.colormaps[COLORMAP](np.clip(np.nan_to_num(scaled), 0, 1))
    rgba[..., 3] = np.where(shown, 0.4 + 0.5 * np.clip(scaled, 0, 1), 0.0)
    return (rgba * 255).round().astype(np.uint8)


def png(foot: xr.DataArray) -> bytes:
    """Return :func:`image` as PNG bytes."""
    import matplotlib.image

    buffer = io.BytesIO()
    matplotlib.image.imsave(buffer, image(foot), format="png")
    return buffer.getvalue()


def utc(time: datetime) -> str:
    """
    Return *time* as text with its zone: UTC, ending in ``Z``.

    A time with no zone is UTC, as PYSTILT's are. JavaScript reads text
    without a zone as the viewer's local time, so the zone is written out.

    Examples
    --------
    >>> utc(datetime(2026, 10, 29, 10))
    '2026-10-29T10:00:00Z'
    """
    if time.tzinfo is not None:
        time = time.astimezone(UTC)
    return f"{time:%Y-%m-%dT%H:%M:%S}Z"


def entry(receptor: Any, foot: xr.DataArray) -> dict[str, Any]:
    """Return what the JSON beside a footprint's image says about it."""
    return {
        "receptor": str(receptor.id),
        "time": utc(receptor.time),
        "longitude": receptor.longitude,
        "latitude": receptor.latitude,
        "altitude": receptor.altitude,
        "bounds": bounds(foot),
        "vmin": VMIN,
        "vmax": VMAX,
        "colormap": COLORMAP,
        "units": foot.attrs.get("units", ""),
    }


def choose_variant(project: stilt.Project, variant: str | None = None) -> str:
    """
    Return the variant whose footprints to draw: *variant*, or the project's only one.

    Raises
    ------
    ValueError
        If the project has several variants and *variant* is not given.
    """
    if variant is not None:
        return variant
    if len(project.variants) != 1:
        raise ValueError(
            f"The project has {len(project.variants)} variants; say which to draw "
            f"(one of {', '.join(project.variants)})."
        )
    return next(iter(project.variants))


def render(
    directory: str | Path,
    url: str,
    receptor_ids: Iterable[str],
    *,
    variant: str | None = None,
    output: str | None = None,
) -> list[str]:
    """
    Draw the finished footprints of *receptor_ids* to *url*, and add each to its day's index.

    A footprint is drawn when its day's ``index.json`` does not list it yet;
    one not run yet, or empty, is left out. The files of a footprint are
    written in the order PNG, JSON, index, and the index is written after
    each footprint, so a run that stops part way (an error, or a stop
    signal) leaves nothing that a later run does not index: a footprint is
    skipped only once it is in the index.

    Parameters
    ----------
    directory : str or Path
        The local project folder.
    url : str
        Where the images go, such as ``s3://my-site/footprints/towers``.
    receptor_ids : iterable of str
        The receptors to draw.
    variant : str, optional
        The variant whose footprints to draw. Needed when the project has
        several.
    output : str, optional
        The output directory, in place of the one ``config.yaml`` names.

    Returns
    -------
    list of str
        The receptors drawn.

    Raises
    ------
    ValueError
        If the project has several variants and *variant* is not given.
    """
    project = stilt.Project(directory, output=output)
    variant = choose_variant(project, variant)
    fs, root = fsspec.core.url_to_fs(url)
    root = root.rstrip("/")
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    drawn: list[str] = []
    for receptor_id in receptor_ids:
        simulation = project.simulation(receptor_id, variant)
        day = f"{root}/{simulation.receptor.time:%Y-%m-%d}"
        if day not in indexes:
            path = f"{day}/index.json"
            known = json.loads(fs.cat_file(path)) if fs.exists(path) else []
            indexes[day] = {i["receptor"]: i for i in known}
        if receptor_id in indexes[day]:
            continue
        try:
            foot = simulation.footprint
        except FileNotFoundError:
            continue
        if foot is None:
            continue
        fs.makedirs(day, exist_ok=True)
        fs.pipe_file(f"{day}/{receptor_id}.png", png(foot))
        item = entry(simulation.receptor, foot)
        fs.pipe_file(f"{day}/{receptor_id}.json", json.dumps(item).encode())
        indexes[day][receptor_id] = item
        ordered = sorted(
            indexes[day].values(), key=lambda i: (i["time"], i["receptor"])
        )
        fs.pipe_file(f"{day}/index.json", json.dumps(ordered).encode())
        drawn.append(receptor_id)
    logger.info("Drew %d footprints to %s", len(drawn), url)
    return drawn
