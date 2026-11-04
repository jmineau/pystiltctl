"""
Copy a project's inputs to and from a bucket.

A PYSTILT project's inputs are its ``config.yaml``, its ``receptors.csv``
and its ``tables/`` folder, and a ``sites.csv`` when the project runs on a
schedule (:mod:`pystiltctl.schedule`). Pods on a cluster read them from a bucket (or
any folder fsspec can reach), so they are copied there first with
:func:`push`, and each pod copies them back to its own disk with
:func:`pull`. The output directory is not copied: PYSTILT writes results
straight to it, wherever it is. A program that changes one of these files
where it is, as a schedule's tick adds to ``receptors.csv``, holds
:func:`lock` while it does.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import fsspec
import fsspec.implementations.local

logger = logging.getLogger(__name__)

#: The files of a project folder that a run reads, as PYSTILT lays it out.
INPUT_FILES = ("config.yaml", "receptors.csv")
#: The folder of extra input tables (``project.add_table``), copied when present.
TABLES = "tables"
#: The places a schedule adds a receptor for each hour, copied when present.
SITES = "sites.csv"
#: Seconds a lock may stay the same before a program waiting for it takes it over.
LOCK_STALE_SECONDS = 300
#: Seconds between a waiting program's looks at a lock.
LOCK_POLL_SECONDS = 2


def is_local(location: str | Path) -> bool:
    """
    Whether *location* is a path on this machine rather than a URL.

    Examples
    --------
    >>> is_local("/data/my_project")
    True
    >>> is_local("s3://bucket/projects/my_project")
    False
    """
    return fsspec.utils.get_protocol(str(location)) in ("file", "local")


def local_path(location: str | Path) -> Path:
    """
    Return a local *location* as a path, without a ``file://`` in front.

    Examples
    --------
    >>> local_path("file:///data/my_project")
    PosixPath('/data/my_project')
    """
    return Path(fsspec.core.strip_protocol(str(location)))


def project_name(location: str | Path) -> str:
    """
    Return a project's name, the last part of its folder or URL.

    PYSTILT names a project after its folder, so a pod that pulls the
    inputs into a folder of this name runs a project of the same name. A
    folder on this machine is named by where it really is, so ``.``, a
    path through ``..`` and a symlink to it all give the same name.

    Examples
    --------
    >>> project_name("s3://bucket/projects/my_project/")
    'my_project'
    >>> project_name("file:///data/my_project/")
    'my_project'
    """
    if is_local(location):
        return local_path(location).resolve().name
    return str(location).rstrip("/").rsplit("/", 1)[-1]


def project_key(project: str | Path) -> str:
    """
    Return *project* in the form every spelling of one project shares.

    A folder becomes its real path (``..`` collapsed, symlinks followed); a
    URL loses its trailing slashes and gets a lowercase scheme.
    ``pystiltctl submit`` writes this into the Job, and ``status`` compares
    projects through it.

    Examples
    --------
    >>> project_key("S3://bucket/projects/my_project/")
    's3://bucket/projects/my_project'
    >>> project_key("file:///data/../data/my_project/")
    '/data/my_project'
    """
    if is_local(project):
        return str(local_path(project).resolve())
    scheme, sep, rest = str(project).partition("://")
    return f"{scheme.lower()}{sep}{rest.rstrip('/')}"


def push(project: str | Path, url: str) -> list[str]:
    """
    Copy a project's inputs to *url*.

    Copies ``config.yaml``, ``receptors.csv`` and, when the project has
    them, ``tables/`` and ``sites.csv``. Files already at *url* are replaced; files there that
    the project no longer has are left alone. Nothing under the project's
    output directory is copied.

    Parameters
    ----------
    project : str or Path
        The project folder on this machine.
    url : str
        Where to copy it, such as ``s3://bucket/projects/my_project``, or
        a folder.

    Returns
    -------
    list of str
        The files copied, relative to the project folder.

    Raises
    ------
    FileNotFoundError
        If *project* has no ``config.yaml`` or ``receptors.csv``.
    """
    project = Path(project)
    for name in INPUT_FILES:
        if not (project / name).is_file():
            raise FileNotFoundError(
                f"{project} is not a PYSTILT project: it has no {name}."
            )
    fs, root = fsspec.core.url_to_fs(url)
    root = root.rstrip("/")
    fs.makedirs(root, exist_ok=True)
    copied = []
    for name in (*INPUT_FILES, SITES):
        if (project / name).is_file():
            fs.put_file(str(project / name), f"{root}/{name}")
            copied.append(name)
    for path in sorted((project / TABLES).rglob("*")):
        if path.is_file():
            name = path.relative_to(project).as_posix()
            fs.makedirs(
                f"{root}/{path.parent.relative_to(project).as_posix()}", exist_ok=True
            )
            fs.put_file(str(path), f"{root}/{name}")
            copied.append(name)
    logger.info("Copied %d files to %s", len(copied), url)
    return copied


def _exists(fs: fsspec.AbstractFileSystem, path: str, kind: str = "file") -> bool:
    """
    Whether *path* is a *kind* (``"file"`` or ``"directory"``) on *fs*.

    Unlike ``fs.isfile``/``fs.isdir``, which fsspec defines to catch every
    exception and return False, this lets anything but a missing *path*
    through, so a refused connection, a timeout or expired credentials are
    not mistaken for an absent file.
    """
    try:
        info = fs.info(path)
    except FileNotFoundError:
        return False
    return info["type"] == kind


def pull(url: str, directory: str | Path) -> Path:
    """
    Copy a project's inputs from *url* into *directory*.

    The reverse of :func:`push`: ``config.yaml``, ``receptors.csv``, and
    ``tables/`` and ``sites.csv`` when *url* has them.

    Parameters
    ----------
    url : str
        Where the inputs are, such as ``s3://bucket/projects/my_project``.
    directory : str or Path
        The local folder to copy them into. It is created if needed.

    Returns
    -------
    Path
        *directory*, which is now a project folder ``stilt`` can open.

    Raises
    ------
    FileNotFoundError
        If *url* has no ``config.yaml`` or ``receptors.csv``.
    OSError
        If *url* cannot be reached, such as a refused connection, a
        timeout or expired credentials: worth a retry, unlike a project
        that is genuinely not there.
    """
    directory = Path(directory)
    fs, root = fsspec.core.url_to_fs(url)
    root = root.rstrip("/")
    try:
        for name in INPUT_FILES:
            if not _exists(fs, f"{root}/{name}"):
                raise FileNotFoundError(
                    f"No PYSTILT project at {url}: it has no {name}."
                )
        directory.mkdir(parents=True, exist_ok=True)
        for name in INPUT_FILES:
            fs.get_file(f"{root}/{name}", str(directory / name))
        if _exists(fs, f"{root}/{SITES}"):
            fs.get_file(f"{root}/{SITES}", str(directory / SITES))
        if _exists(fs, f"{root}/{TABLES}", kind="directory"):
            for remote in fs.find(f"{root}/{TABLES}"):
                local = directory / remote.removeprefix(f"{root}/")
                local.parent.mkdir(parents=True, exist_ok=True)
                fs.get_file(remote, str(local))
    except FileNotFoundError:
        raise
    except Exception as error:
        if isinstance(error, OSError):
            raise
        raise OSError(f"cannot reach {url}: {error}") from error
    logger.info("Copied the inputs at %s to %s", url, directory)
    return directory


def _atomic_put_file(fs: fsspec.AbstractFileSystem, lpath: str, rpath: str) -> None:
    """
    Copy *lpath* to *rpath* on *fs*, atomically when *fs* is local.

    ``fs.put_file`` truncates *rpath* and then writes it, so on a local
    filesystem or mounted volume a reader that opens *rpath* while it is
    copied sees a half-written file. This instead copies to a temporary
    name in the same folder and ``os.replace``s it onto *rpath*, which is
    atomic on one filesystem. S3 and GCS already replace the whole object
    in one request, so there ``put_file`` writes *rpath* directly; a
    temporary name would only add requests.
    """
    if not isinstance(fs, fsspec.implementations.local.LocalFileSystem):
        fs.put_file(lpath, rpath)
        return
    tmp = f"{rpath}.tmp-{uuid.uuid4().hex}"
    try:
        fs.put_file(lpath, tmp)
        os.replace(tmp, rpath)
    except Exception:
        with contextlib.suppress(Exception):
            fs.rm_file(tmp)
        raise


def put(directory: str | Path, url: str, name: str) -> None:
    """
    Copy one file of a project folder back to *url*, such as a grown ``receptors.csv``.

    Parameters
    ----------
    directory : str or Path
        The local project folder.
    url : str
        The project's location, as :func:`push` copied it there.
    name : str
        The file, relative to the project folder.
    """
    fs, root = fsspec.core.url_to_fs(url)
    _atomic_put_file(fs, str(Path(directory) / name), f"{root.rstrip('/')}/{name}")


def get(url: str, directory: str | Path, name: str) -> None:
    """
    Copy one file from *url* into a project folder, the reverse of :func:`put`.

    Parameters
    ----------
    url : str
        The project's location, as :func:`push` copied it there.
    directory : str or Path
        The local project folder.
    name : str
        The file, relative to the project folder.
    """
    fs, root = fsspec.core.url_to_fs(url)
    fs.get_file(f"{root.rstrip('/')}/{name}", str(Path(directory) / name))


@contextlib.contextmanager
def lock(url: str, name: str) -> Iterator[None]:
    """
    Hold a lock on the file *name* at *url* while the ``with`` block runs.

    Use it around reading a file, changing it and writing it back, so a
    second program doing the same waits for the first instead of writing
    over its change. A schedule's tick holds it on ``receptors.csv`` while it
    copies the file (:func:`get`), adds its hours, and copies it back
    (:func:`put`).

    The lock is a small file ``.<name>.lock`` next to *name*, created only
    if it is not there (``open(..., "x")``: ``If-None-Match`` on S3,
    ``ifGenerationMatch=0`` on Google Cloud Storage, ``O_EXCL`` on a
    folder), and deleted at the end of the block. It says which host and
    process holds it. A program that finds it waits, and takes it over if it
    has not changed for :data:`LOCK_STALE_SECONDS` by its own clock, so a
    holder that crashed does not stop every later one.

    Parameters
    ----------
    url : str
        The project's location, a URL or a folder.
    name : str
        The file to lock, relative to the project folder.

    Raises
    ------
    OSError
        If the lock cannot be made or read, such as a refused connection or
        expired credentials.

    Notes
    -----
    A store that ignores ``If-None-Match`` (some older S3-compatible ones)
    lets two programs make the lock at once, so it does not protect there.
    A holder slower than :data:`LOCK_STALE_SECONDS` can be taken over; it
    then logs a warning when it finishes.

    Examples
    --------
    >>> import fsspec
    >>> fs = fsspec.filesystem("memory")
    >>> with lock("memory://lock-example/project", "receptors.csv"):
    ...     fs.exists("/lock-example/project/.receptors.csv.lock")
    True
    >>> fs.exists("/lock-example/project/.receptors.csv.lock")
    False
    """
    fs, root = fsspec.core.url_to_fs(url)
    path = f"{root.rstrip('/')}/.{name}.lock"
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = f"{socket.gethostname()} pid {os.getpid()} since {stamp} {uuid.uuid4().hex}"
    try:
        _acquire(fs, path, token)
    except Exception as error:
        raise OSError(f"cannot lock {name} at {url}: {error}") from error
    try:
        yield
    finally:
        try:
            if not _release(fs, path, token):
                logger.warning(
                    "%s was taken over while this program held it, so another "
                    "may have written %s at the same time.",
                    path,
                    name,
                )
        except Exception:
            # What the block wrote is written; a lock left behind is taken over later.
            logger.warning(
                "Could not remove %s; it is taken over after %d s.",
                path,
                LOCK_STALE_SECONDS,
                exc_info=True,
            )


def _acquire(fs: fsspec.AbstractFileSystem, path: str, token: str) -> None:
    """
    Create the lock file *path* holding *token*, waiting while another holds it.

    A lock is stale when it reads the same for :data:`LOCK_STALE_SECONDS` on
    this program's own clock (``time.monotonic``), so no two clocks have to
    agree; a stale lock is removed and made again.
    """
    seen, since = None, 0.0
    while True:
        try:
            with fs.open(path, "x") as file:
                file.write(token)
            return
        except FileExistsError:
            pass
        try:
            held = fs.read_text(path)
        except FileNotFoundError:
            continue  # released while we looked: try again
        if held != seen:
            logger.info("Waiting for %s, held by %s", path, held)
            seen, since = held, time.monotonic()
        elif time.monotonic() - since >= LOCK_STALE_SECONDS:
            logger.warning(
                "Taking over %s, the same for %d s: %s",
                path,
                LOCK_STALE_SECONDS,
                held,
            )
            _release(fs, path, held)
            seen = None
            continue
        time.sleep(LOCK_POLL_SECONDS)


def _release(fs: fsspec.AbstractFileSystem, path: str, token: str | bytes) -> bool:
    """
    Remove the lock file *path* if it still holds *token*; return whether it did.

    Reading first keeps a program from removing a lock that another has
    taken over since.
    """
    try:
        if fs.read_text(path) != token:
            return False
        fs.rm_file(path)
    except FileNotFoundError:
        return False
    return True
