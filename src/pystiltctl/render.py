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

import contextlib
import io
import json
import logging
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fsspec
import fsspec.implementations.local
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

    Raises
    ------
    ValueError
        If the grid crosses the antimeridian (180 degrees). PYSTILT wraps such
        a grid's longitudes into -180 to 180 and sorts them
        (``footprint/gridding.py``), so ``lon.min()``/``lon.max()`` would span
        the wrap point rather than the grid's real width. Leaflet does accept
        an east edge written past 180 degrees as a workaround, but that bends
        every later reader of ``bounds`` to a convention that towers in Utah
        never need, so this raises instead of stretching the box silently.
    """
    lat, lon = foot["lat"].values, foot["lon"].values
    dlat = abs(float(lat[1] - lat[0])) if lat.size > 1 else 0.0
    # The minimum, not the first, gap: on a grid wrapped across the
    # antimeridian the sorted longitudes are evenly spaced except for one
    # large gap at the wrap point, so the first gap can be that one.
    dlon = float(np.min(np.abs(np.diff(lon)))) if lon.size > 1 else 0.0
    if lon.size > 2 and float(np.max(np.abs(np.diff(lon)))) > 1.5 * dlon:
        raise ValueError(
            "the footprint's grid crosses the antimeridian (180 degrees); its "
            "longitudes wrap from near 180 to near -180, so lon.min()/lon.max() "
            "cannot give its real west and east edges."
        )
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

    Raises
    ------
    ValueError
        If the footprint's grid crosses the antimeridian (see :func:`bounds`).
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


def _exists(fs: fsspec.AbstractFileSystem, path: str) -> bool:
    """
    Whether *path* is a file on *fs*.

    Unlike ``fs.exists``, which fsspec defines to catch every exception and
    return False, this lets anything but a missing *path* through, so a
    refused connection, a timeout or expired credentials are not mistaken
    for an absent index.
    """
    try:
        fs.info(path)
    except FileNotFoundError:
        return False
    return True


def _atomic_pipe_file(fs: fsspec.AbstractFileSystem, path: str, data: bytes) -> None:
    """
    Write *data* to *path* on *fs*, atomically when *fs* is local.

    ``fs.pipe_file`` truncates *path* and then writes it, so on a local
    filesystem or mounted volume a reader that opens *path* while it is
    (re)written sees a half-written file (seen on ``index.json``, rewritten
    after every footprint). This instead writes a temporary name in the
    same folder and ``os.replace``s it onto *path*, which is atomic on one
    filesystem. S3 and GCS already replace the whole object in one
    request, so there ``pipe_file`` writes *path* directly; a temporary
    name would only add requests.
    """
    if not isinstance(fs, fsspec.implementations.local.LocalFileSystem):
        fs.pipe_file(path, data)
        return
    tmp = f"{path}.tmp-{uuid.uuid4().hex}"
    try:
        fs.pipe_file(tmp, data)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(Exception):
            fs.rm_file(tmp)
        raise


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
        If the project has several variants and *variant* is not given, or a
        footprint's grid crosses the antimeridian (see :func:`bounds`).
    OSError
        If a day's index at *url* cannot be checked: a refused connection, a
        timeout, a 403 or expired credentials, say.
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
            try:
                contents = fs.cat_file(path) if _exists(fs, path) else None
            except Exception as error:
                if isinstance(error, OSError):
                    raise
                raise OSError(f"cannot check {path} for an index: {error}") from error
            known = json.loads(contents) if contents is not None else []
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
        _atomic_pipe_file(fs, f"{day}/{receptor_id}.png", png(foot))
        item = entry(simulation.receptor, foot)
        _atomic_pipe_file(fs, f"{day}/{receptor_id}.json", json.dumps(item).encode())
        indexes[day][receptor_id] = item
        ordered = sorted(
            indexes[day].values(), key=lambda i: (i["time"], i["receptor"])
        )
        _atomic_pipe_file(fs, f"{day}/index.json", json.dumps(ordered).encode())
        drawn.append(receptor_id)
    logger.info("Drew %d footprints to %s", len(drawn), url)
    return drawn
