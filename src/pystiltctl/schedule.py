"""
Run a project every hour, for fixed sites such as towers.

A scheduled project has a ``sites.csv``: the places that get a receptor
for each hour. Each hour a Kubernetes CronJob runs one pod with
:func:`tick`, which

1. adds a receptor per site for each hour of the last day that is at least
   ``delay`` hours old (``Project.add_receptors`` skips those already in
   ``receptors.csv``), reaching back further to close a gap left by an
   outage (:func:`due_hours`, :data:`MAX_BACKFILL_HOURS`),
2. writes ``receptors.csv`` back to the bucket, and
3. becomes ``stilt run --receptors`` on that day's receptors, and the
   gap's.

Steps 1 and 2 hold :func:`pystiltctl.inputs.lock` on ``receptors.csv``,
so a tick run by hand to fill in a past day, which the CronJob does not
know about, waits for the hourly one rather than writing over its hours.

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
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import stilt

from pystiltctl import render as renderer
from pystiltctl.inputs import SITES, get, is_local, lock, put
from pystiltctl.worker import WORKDIR, become, project_directory, run, stilt_command

logger = logging.getLogger(__name__)

#: Hours between an hour and the tick that first adds it: the met's lag.
DELAY_HOURS = 8
#: Hours back a tick adds, and runs again if they are not finished.
WINDOW_HOURS = 24
#: Extra hours, beyond the normal window, that one tick may reach back to
#: close a gap left by an outage; one week, bounding a single tick's
#: workload. A longer outage is closed over the ticks that follow, each
#: adding another span this wide, since every tick runs within minutes
#: of the one before (see :func:`due_hours`).
MAX_BACKFILL_HOURS = 24 * 7
#: The columns ``sites.csv`` needs; ``altitude_ref`` (``agl`` or ``msl``) is optional.
SITE_COLUMNS = ("longitude", "latitude", "altitude")


def due_hours(
    now: datetime, delay: int = DELAY_HOURS, since: datetime | None = None
) -> list[datetime]:
    """
    Return the hours a tick at *now* covers, oldest first, in UTC.

    The newest is *now* less *delay* hours, down to the hour; the oldest is
    normally :data:`WINDOW_HOURS` - 1 hours before it.

    *since*, when given, is the newest hour a project already has (the last
    row of ``receptors.csv``). If the normal window would leave a gap after
    it, the window reaches back to close the gap instead, capped at
    :data:`MAX_BACKFILL_HOURS` before its normal start: an outage longer
    than that is closed gradually, a tick at a time, rather than in one.

    Examples
    --------
    >>> hours = due_hours(datetime(2026, 10, 29, 18, 15), delay=8)
    >>> hours[0], hours[-1]
    (datetime.datetime(2026, 10, 28, 11, 0), datetime.datetime(2026, 10, 29, 10, 0))
    >>> due_hours(
    ...     datetime(2026, 10, 29, 18, 15), delay=8, since=datetime(2026, 10, 28, 0)
    ... )[0]
    datetime.datetime(2026, 10, 28, 1, 0)
    """
    newest = (now - timedelta(hours=delay)).replace(minute=0, second=0, microsecond=0)
    oldest = newest - timedelta(hours=WINDOW_HOURS - 1)
    if since is not None and since < oldest - timedelta(hours=1):
        oldest = max(
            since + timedelta(hours=1), oldest - timedelta(hours=MAX_BACKFILL_HOURS)
        )
    span = int((newest - oldest) / timedelta(hours=1))
    return [oldest + timedelta(hours=k) for k in range(span + 1)]


def _last_added(project: stilt.Project, now: datetime) -> datetime | None:
    """
    Return the newest hour in *project*'s ``receptors.csv`` up to *now*, or ``None``.

    Rows after *now* (added by a mistyped ``--now`` before it was refused)
    are left out, so they cannot hide a gap from every later tick.
    """
    times = project.receptors["time"]
    times = times[times <= now]
    if times.empty:
        return None
    return times.max().to_pydatetime()


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
        If a column is missing, a value is empty or not a number, PYSTILT
        refuses the place (a latitude beyond 90, say), or there are no rows.
        The message names the line.
    """
    path = Path(directory) / SITES
    if not path.is_file():
        raise FileNotFoundError(
            f"{Path(directory).name} has no {SITES}: a schedule needs the places "
            "to add a receptor for each hour (longitude, latitude, altitude)."
        )
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    missing = [c for c in SITE_COLUMNS if rows and c not in rows[0]]
    if not rows or missing:
        raise ValueError(f"{SITES} needs rows with columns {', '.join(SITE_COLUMNS)}.")
    sites: list[dict[str, Any]] = []
    for line, row in enumerate(rows, start=2):
        # A short row has None for what it lacks.
        empty = [c for c in SITE_COLUMNS if not (row[c] or "").strip()]
        if empty:
            raise ValueError(f"{SITES} line {line}: no {', '.join(empty)}.")
        try:
            site: dict[str, Any] = {c: float(row[c]) for c in SITE_COLUMNS}
        except ValueError:
            numbers = {c: row[c] for c in SITE_COLUMNS}
            raise ValueError(
                f"{SITES} line {line}: {numbers} is not all numbers."
            ) from None
        if row.get("altitude_ref"):
            site["altitude_ref"] = row["altitude_ref"].strip()
        try:
            _check_site(site)
        except ValueError as error:
            raise ValueError(f"{SITES} line {line}: {_reason(error)}") from None
        sites.append(site)
    return sites


def _check_site(site: dict[str, Any]) -> None:
    """
    Raise ``ValueError`` if PYSTILT would refuse a receptor at *site*.

    The check is PYSTILT's own: a receptor is built, so a latitude of 95 or
    an ``altitude_ref`` it does not know fails here, when the schedule is
    made, and not in every tick.
    """
    if not all(math.isfinite(site[c]) for c in SITE_COLUMNS):
        raise ValueError("not all numbers.")
    stilt.PointReceptor(time=datetime(2000, 1, 1), **site)


def _reason(error: ValueError) -> str:
    """Return what PYSTILT's validation says, without pydantic's framing."""
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return str(error)
    reasons = []
    for item in errors():
        where = ".".join(str(part) for part in item["loc"])
        reasons.append(
            f"{where + ': ' if where else ''}{item['msg']}".replace("Value error, ", "")
        )
    return "; ".join(reasons)


def hourly_receptors(
    sites: Iterable[dict[str, Any]], hours: Iterable[datetime]
) -> list[stilt.PointReceptor]:
    """Return a receptor for each site at each hour, hour by hour."""
    sites = list(sites)
    return [stilt.PointReceptor(time=hour, **site) for hour in hours for site in sites]


def check_render(directory: str | Path, output: str | None = None) -> None:
    """
    Raise if the project in *directory* cannot be drawn by ``tick --render``.

    ``tick`` makes this check first (a project with several variants has no
    one to draw), so ``schedule`` makes it too, rather than apply a CronJob
    whose every tick would fail.

    Raises
    ------
    ValueError
        If the project has several variants.
    """
    renderer.choose_variant(stilt.Project(directory, output=output))


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

    If an outage left a gap before the normal window (the last row of
    ``receptors.csv`` is older than the window's start), this tick also adds
    and runs the gap's hours, capped at :data:`MAX_BACKFILL_HOURS` (see
    :func:`due_hours`).

    This is what each pod of a schedule's CronJob runs. Without *render* it
    does not return: it becomes ``stilt run --receptors``, whose exit code
    is the pod's. With *render*, it runs ``stilt`` and then draws the day's
    finished footprints to *render* (:func:`pystiltctl.render.render`), and
    returns ``stilt run``'s exit code. A drawing that fails does not
    replace a code other than 0: the error is logged and ``stilt``'s code is
    returned. After a clean run it raises, so the pod does not look
    successful with footprints missing.

    Parameters
    ----------
    project : str
        The project: a URL, or a folder on a mounted volume.
    delay : int
        Hours before an hour is added; see :data:`DELAY_HOURS`.
    now : datetime, optional
        The time to act as if it were, in UTC. Defaults to now; an earlier
        time fills in a past day. A time with a time zone is converted to
        UTC.
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
        If *now* is later than the clock, ``sites.csv`` cannot be read, or
        *render* is given and the project has several variants (all checked
        before ``stilt`` runs).
    OSError
        If the project's inputs cannot be copied from *project*, or
        ``receptors.csv`` locked or copied back to it, or (when ``stilt``
        exited with 0) the footprints cannot be drawn to *render*; the
        message says which.
    """
    clock = datetime.now(UTC).replace(tzinfo=None)
    if now is None:
        now = clock
    elif now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    if now > clock:
        raise ValueError(
            f"--now {now:%Y-%m-%dT%H:%M} is later than the clock "
            f"({clock:%Y-%m-%dT%H:%M} UTC): a tick can only fill in hours that are past."
        )
    try:
        directory = project_directory(project, workdir)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise OSError(
            f"cannot copy the project's inputs from {project}: {error}"
        ) from error
    # Another tick (a backfill by hand, say) may be adding its hours too. With
    # the lock, receptors.csv is this tick's alone from a fresh copy to its put.
    with lock(project, "receptors.csv"):
        if not is_local(project):
            try:
                get(project, directory, "receptors.csv")
            except OSError as error:
                raise OSError(
                    f"cannot copy the project's inputs from {project}: {error}"
                ) from error
        stilt_project = stilt.Project(directory, output=output)
        # Before anything runs: a project that cannot be drawn fails now, not after the day's run.
        variant = None if render is None else renderer.choose_variant(stilt_project)
        # Read after the fresh copy: the last row there is where a gap starts.
        hours = due_hours(now, delay, since=_last_added(stilt_project, now))
        receptors = hourly_receptors(read_sites(directory), hours)
        added = stilt_project.add_receptors(receptors)
        logger.info("Added %d receptors, through %s", len(added), receptors[-1].time)
        if added and not is_local(project):
            try:
                put(directory, project, "receptors.csv")
            except OSError as error:
                raise OSError(
                    f"cannot copy receptors.csv back to {project}: {error}"
                ) from error
    ids = Path(workdir) / f"{directory.name}-due.txt"
    ids.parent.mkdir(parents=True, exist_ok=True)
    ids.write_text("".join(f"{r.id}\n" for r in receptors))
    command = stilt_command(directory, receptors=ids, cpus=cpus, output=output)
    if render is None:
        become(command)
        return 0  # reached only when become is replaced, as in the tests
    code = run(command)
    try:
        renderer.render(
            directory,
            render,
            [r.id for r in receptors],
            variant=variant,
            output=output,
        )
    except Exception as error:
        if code != 0:
            # stilt's code says more than a failed drawing does; keep it.
            logger.error("Could not draw the footprints to %s", render, exc_info=True)
            return code
        if isinstance(error, OSError):
            raise OSError(f"cannot draw the footprints to {render}: {error}") from error
        raise
    return code
