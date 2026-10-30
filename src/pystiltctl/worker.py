"""
Run one task of a project, as a pod of a Kubernetes indexed Job does.

The work is PYSTILT's: ``stilt run <project> --task I/N``. The worker only
gets the project's inputs onto the pod's disk first, when they are on a
bucket, and then becomes the ``stilt`` process, so that Kubernetes' stop
signal reaches it and its exit code is the pod's.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from pystiltctl.inputs import is_local, local_path, project_name, pull

logger = logging.getLogger(__name__)

#: The environment variable an indexed Job sets to each pod's index.
INDEX_VARIABLE = "JOB_COMPLETION_INDEX"
#: Where a URL's inputs are copied, unless told otherwise: the pod's own disk.
WORKDIR = Path("/tmp/pystiltctl")


def task_index(index: int | None = None) -> int:
    """
    Return this pod's task index: *index*, or else ``$JOB_COMPLETION_INDEX``.

    Raises
    ------
    ValueError
        If *index* is None and the variable is unset or not a whole number.
    """
    if index is not None:
        return index
    value = os.environ.get(INDEX_VARIABLE)
    if value is None:
        raise ValueError(
            f"No task index: give one, or run in a Kubernetes indexed Job, which sets {INDEX_VARIABLE}."
        )
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"{INDEX_VARIABLE} is {value!r}, not a whole number."
        ) from None


def project_directory(project: str, workdir: str | Path) -> Path:
    """
    Return the local project folder for *project*, pulling its inputs if needed.

    A local path (a volume mounted in the pod) is used where it is. A URL's
    inputs are copied into *workdir*, in a folder with the project's name.
    """
    if is_local(project):
        return local_path(project)
    return pull(project, Path(workdir) / project_name(project))


def stilt_command(
    directory: str | Path,
    task: int | None = None,
    n_tasks: int | None = None,
    *,
    receptors: str | Path | None = None,
    cpus: int | None = None,
    output: str | None = None,
) -> list[str]:
    """
    Return the ``stilt run`` command line for a share of a project.

    The share is task *task* of *n_tasks* (``--task I/N``), the receptors
    listed in the file *receptors* (``--receptors``), or both. It runs
    PYSTILT with this Python, so the ``stilt`` it runs is the one installed
    beside pystiltctl.

    Examples
    --------
    >>> stilt_command("/work/my_project", 3, 20, cpus=4)[2:]
    ['stilt', 'run', '/work/my_project', '--task', '3/20', '--cpus', '4']
    >>> stilt_command("/work/my_project", receptors="/work/ids.txt")[2:]
    ['stilt', 'run', '/work/my_project', '--receptors', '/work/ids.txt']
    """
    command = [sys.executable, "-m", "stilt", "run", str(directory)]
    if task is not None:
        command += ["--task", f"{task}/{n_tasks}"]
    if receptors is not None:
        command += ["--receptors", str(receptors)]
    if cpus is not None:
        command += ["--cpus", str(cpus)]
    if output is not None:
        command += ["--output", output]
    return command


def become(command: list[str]) -> None:
    """
    Replace this process with *command*, so its exit code and signals are this pod's.

    This function does not return.
    """
    logger.info("Running %s", " ".join(command[2:]))
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(command[0], command)


def run(command: list[str]) -> int:
    """
    Run *command* and return its exit code, passing this process's stop signals on to it.

    For when something must happen after ``stilt`` finishes, so this
    process cannot :func:`become` it. A stop signal (``SIGTERM`` from
    Kubernetes, or ``SIGINT``) is sent on to the command, which stops its
    runs cleanly and exits with 3.
    """
    logger.info("Running %s", " ".join(command[2:]))
    child = subprocess.Popen(command)

    def forward(signum: int, frame: object) -> None:
        """Send the signal this process got on to the command."""
        child.send_signal(signum)

    previous = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        return child.wait()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def run_task(
    project: str,
    n_tasks: int,
    *,
    index: int | None = None,
    cpus: int | None = None,
    output: str | None = None,
    workdir: str | Path = WORKDIR,
) -> None:
    """
    Run task *index* of *n_tasks* of *project*, in place of this process.

    Pulls the project's inputs when *project* is a URL, then replaces this
    process with ``stilt run --task I/N``, so this function does not return.
    The exit code is ``stilt run``'s: 0 when every simulation of the task is
    complete, 1 when some failed, 3 when some were stopped, 2 when the
    command line is wrong.

    Parameters
    ----------
    project : str
        The project: a URL such as ``s3://bucket/projects/my_project``, or
        a folder on a volume mounted in the pod.
    n_tasks : int
        How many tasks the project is split into (the Job's ``completions``).
    index : int, optional
        This task's index, 0 to ``n_tasks - 1``. Defaults to
        ``$JOB_COMPLETION_INDEX``, which an indexed Job sets.
    cpus : int, optional
        Receptors to run at once. Defaults to ``execution.cpus`` in
        ``config.yaml``.
    output : str, optional
        The output directory, in place of the one ``config.yaml`` names.
    workdir : str or Path
        Where a URL's inputs are copied.
    """
    task = task_index(index)
    if not 0 <= task < n_tasks:
        raise ValueError(f"Task {task} is not one of 0 to {n_tasks - 1}.")
    directory = project_directory(project, workdir)
    become(stilt_command(directory, task, n_tasks, cpus=cpus, output=output))
