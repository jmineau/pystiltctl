"""
Run a project every hour, for fixed sites such as towers.

A scheduled project has a ``sites.csv``: the places that get a receptor
for each hour. Each hour a Kubernetes CronJob runs one pod with
:func:`tick`, which

1. adds a receptor per site for each hour of the last day that is at least
   ``delay`` hours old (``Project.add_receptors`` skips those already in
   ``receptors.csv``),
2. writes ``receptors.csv`` back to the bucket, and
3. becomes ``stilt run --receptors`` on that day's receptors.

Finished simulations are skipped, so a tick only runs what is new or what
failed before: an hour whose met was not there yet is tried again by every
tick for a day.

``delay`` is how long the met takes to appear. NOAA's HRRR archive posts
each 6-hour block about 40 minutes after it ends, and a backward run also
needs the hour after its receptor, so an hour's met is complete at most
about 7 hours 40 minutes later: :data:`DELAY_HOURS`.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import stilt

from pystiltctl import render as renderer
from pystiltctl.inputs import SITES, is_local, put
from pystiltctl.worker import WORKDIR, become, project_directory, run, stilt_command

logger = logging.getLogger(__name__)

#: Hours between an hour and the tick that first adds it: the met's lag.
DELAY_HOURS = 8
#: Hours back a tick adds, and runs again if they are not finished.
WINDOW_HOURS = 24
#: The columns ``sites.csv`` needs; ``altitude_ref`` (``agl`` or ``msl``) is optional.
SITE_COLUMNS = ("longitude", "latitude", "altitude")


def due_hours(now: datetime, delay: int = DELAY_HOURS) -> list[datetime]:
    """
    Return the hours a tick at *now* covers, oldest first, in UTC.

    The newest is *now* less *delay* hours, down to the hour; the oldest is
    :data:`WINDOW_HOURS` - 1 hours before it.

    Examples
    --------
    >>> hours = due_hours(datetime(2026, 10, 29, 18, 15), delay=8)
    >>> hours[0], hours[-1]
    (datetime.datetime(2026, 10, 28, 11, 0), datetime.datetime(2026, 10, 29, 10, 0))
    """
    last = (now - timedelta(hours=delay)).replace(minute=0, second=0, microsecond=0)
    return [last - timedelta(hours=k) for k in range(WINDOW_HOURS - 1, -1, -1)]


def read_sites(directory: str | Path) -> list[dict[str, Any]]:
    """
    Return the sites in a project's ``sites.csv``.

    Each row needs ``longitude``, ``latitude`` and ``altitude``; an
    ``altitude_ref`` column (``agl``, the default, or ``msl``) is kept.
    Other columns, such as a site's name, are ignored.

    Raises
    ------
    FileNotFoundError
        If the project has no ``sites.csv``.
    ValueError
        If a column is missing, a value is not a number, or there are no rows.
    """
    path = Path(directory) / SITES
    if not path.is_file():
        raise FileNotFoundError(
            f"{Path(directory).name} has no {SITES}: a schedule needs the places "
            "to add a receptor for each hour (longitude, latitude, altitude)."
        )
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    missing = [c for c in SITE_COLUMNS if rows and c not in rows[0]]
    if not rows or missing:
        raise ValueError(f"{SITES} needs rows with columns {', '.join(SITE_COLUMNS)}.")
    sites: list[dict[str, Any]] = []
    for line, row in enumerate(rows, start=2):
        try:
            site: dict[str, Any] = {c: float(row[c]) for c in SITE_COLUMNS}
        except ValueError:
            raise ValueError(
                f"{SITES} line {line}: {row} is not all numbers."
            ) from None
        if row.get("altitude_ref"):
            site["altitude_ref"] = row["altitude_ref"].strip()
        sites.append(site)
    return sites


def hourly_receptors(
    sites: Iterable[dict[str, Any]], hours: Iterable[datetime]
) -> list[stilt.PointReceptor]:
    """Return a receptor for each site at each hour, hour by hour."""
    sites = list(sites)
    return [stilt.PointReceptor(time=hour, **site) for hour in hours for site in sites]


def tick(
    project: str,
    *,
    delay: int = DELAY_HOURS,
    now: datetime | None = None,
    cpus: int | None = None,
    output: str | None = None,
    render: str | None = None,
    workdir: str | Path = WORKDIR,
) -> int:
    """
    Add the hours now due to *project*, then run its last day.

    This is what each pod of a schedule's CronJob runs. Without *render* it
    does not return: it becomes ``stilt run --receptors``, whose exit code
    is the pod's. With *render*, it runs ``stilt`` and then draws the day's
    finished footprints to *render* (:func:`pystiltctl.render.render`), and
    returns ``stilt run``'s exit code.

    Parameters
    ----------
    project : str
        The project: a URL, or a folder on a mounted volume.
    delay : int
        Hours before an hour is added; see :data:`DELAY_HOURS`.
    now : datetime, optional
        The time to act as if it were, in UTC. Defaults to now; an earlier
        time fills in a past day.
    cpus : int, optional
        Receptors to run at once. Defaults to ``execution.cpus`` in
        ``config.yaml``.
    output : str, optional
        The output directory, in place of the one ``config.yaml`` names.
    render : str, optional
        Where to draw the footprints for a web map, such as
        ``s3://my-site/footprints/towers``.
    workdir : str or Path
        Where a URL's inputs are copied, and the list of receptors written.

    Returns
    -------
    int
        ``stilt run``'s exit code, when *render* is given.

    Raises
    ------
    FileNotFoundError
        If there is no project, or it has no ``sites.csv``.
    ValueError
        If ``sites.csv`` cannot be read.
    """
    now = now or datetime.now(UTC).replace(tzinfo=None)
    directory = project_directory(project, workdir)
    receptors = hourly_receptors(read_sites(directory), due_hours(now, delay))
    added = stilt.Project(directory, output=output).add_receptors(receptors)
    logger.info("Added %d receptors, through %s", len(added), receptors[-1].time)
    if added and not is_local(project):
        put(directory, project, "receptors.csv")
    ids = Path(workdir) / f"{directory.name}-due.txt"
    ids.parent.mkdir(parents=True, exist_ok=True)
    ids.write_text("".join(f"{r.id}\n" for r in receptors))
    command = stilt_command(directory, receptors=ids, cpus=cpus, output=output)
    if render is None:
        become(command)
        return 0  # reached only when become is replaced, as in the tests
    code = run(command)
    renderer.render(directory, render, [str(r.id) for r in receptors], output=output)
    return code
