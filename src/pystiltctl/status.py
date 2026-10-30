"""
Where a project's runs stand: its Jobs on the cluster, and its simulations.

The Jobs come from ``kubectl``, found by the ``pystiltctl/project`` label
that :func:`pystiltctl.job.manifest` puts on them and kept when their
``pystiltctl/project-url`` annotation is the project. The simulations come
from ``stilt status --json``, which reads the output directory: PYSTILT
decides what is finished, from the files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pystiltctl.inputs import is_local, local_path, project_name, pull
from pystiltctl.job import PROJECT_ANNOTATION, PROJECT_LABEL, label_value


def count_indexes(indexes: str | None) -> int:
    """
    Return how many task indexes a Job's index list holds, such as ``0-3,7``.

    Examples
    --------
    >>> count_indexes("0-3,7")
    5
    >>> count_indexes(None)
    0
    """
    total = 0
    for part in (indexes or "").split(","):
        if part:
            first, _, last = part.partition("-")
            total += int(last or first) - int(first) + 1
    return total


def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    """
    Return the facts about one Job, from ``kubectl get job -o json``.

    ``state`` is ``complete``, ``failed``, ``suspended`` or ``running``;
    ``reason`` is Kubernetes' reason for a failed Job, such as
    ``FailedIndexes``. ``failed_indexes`` lists the tasks that gave up, as
    Kubernetes writes them (``1,4-6``).
    """
    spec, status = job.get("spec", {}), job.get("status", {})
    conditions = {
        c["type"]: c for c in status.get("conditions", []) if c.get("status") == "True"
    }
    reason = None
    if "Complete" in conditions:
        state = "complete"
    elif "Failed" in conditions:
        state, reason = "failed", conditions["Failed"].get("reason")
    elif spec.get("suspend"):
        state = "suspended"
    else:
        state = "running"
    return {
        "name": job["metadata"]["name"],
        "state": state,
        "reason": reason,
        "created": job["metadata"].get("creationTimestamp"),
        "tasks": spec.get("completions"),
        "done": count_indexes(status.get("completedIndexes")),
        "failed": count_indexes(status.get("failedIndexes")),
        "failed_indexes": status.get("failedIndexes") or "",
        "running": status.get("active", 0),
    }


def project_key(project: str) -> str:
    """
    Return *project* in the form two spellings of one project share.

    ``pystiltctl submit`` writes the project into the Job as it was typed,
    so ``status`` compares projects through this: a folder as its path, a
    URL without trailing slashes and with a lowercase scheme.

    Examples
    --------
    >>> project_key("S3://bucket/projects/my_project/")
    's3://bucket/projects/my_project'
    >>> project_key("file:///data/my_project/")
    '/data/my_project'
    """
    if is_local(project):
        return str(local_path(project))
    scheme, sep, rest = project.partition("://")
    return f"{scheme.lower()}{sep}{rest.rstrip('/')}"


def project_jobs(
    project: str, *, namespace: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Return the summaries of *project*'s Jobs, oldest first, and the Jobs left out.

    The label ``kubectl`` selects by is only the project's folder name, so
    two projects can share it; a Job is kept only if its project annotation
    is *project* (see :func:`project_key`). A Job with the label but no
    annotation cannot be told from another project's, so it is left out:
    the second value lists the names of those.

    Raises
    ------
    FileNotFoundError
        If ``kubectl`` is not installed.
    RuntimeError
        If ``kubectl`` cannot list the Jobs; the message is its error.
    """
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise FileNotFoundError("kubectl is not installed, or not on PATH.")
    args = [
        kubectl,
        "get",
        "jobs",
        "-l",
        f"{PROJECT_LABEL}={label_value(project_name(project))}",
        "-o",
        "json",
    ]
    if namespace is not None:
        args += ["--namespace", namespace]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"kubectl exited with {result.returncode}"
        )
    jobs, unclaimed = [], []
    for item in json.loads(result.stdout)["items"]:
        claimed = item["metadata"].get("annotations", {}).get(PROJECT_ANNOTATION)
        if claimed is None:
            unclaimed.append(item["metadata"]["name"])
        elif project_key(claimed) == project_key(project):
            jobs.append(job_summary(item))
    return sorted(jobs, key=lambda j: j["created"] or ""), unclaimed


def simulations(project: str, *, output: str | None = None) -> dict[str, Any]:
    """
    Return ``stilt status --json`` for *project*, a URL or a local folder.

    A URL's inputs are copied to a temporary folder first, so ``project``
    in the result is *project* as given.

    Raises
    ------
    FileNotFoundError
        If there is no project at *project*.
    RuntimeError
        If ``stilt status`` fails; the message is its error.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = (
            local_path(project)
            if is_local(project)
            else pull(project, Path(tmp) / "project")
        )
        args = [sys.executable, "-m", "stilt", "status", str(directory), "--json"]
        if output is not None:
            args += ["--output", output]
        # A plain traceback, not typer's boxed one, so the error is its last line, whole.
        env = {**os.environ, "_TYPER_STANDARD_TRACEBACK": "1"}
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, env=env
        )
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise RuntimeError(
            lines[-1] if lines else f"stilt status exited with {result.returncode}"
        )
    data = json.loads(result.stdout)
    data["project"] = project
    return data


def _counts(counts: dict[str, int]) -> str:
    """Return ``total=  complete=  ...`` as ``stilt status`` prints it."""
    return "  ".join(f"{name}={n}" for name, n in counts.items())


def report(
    data: dict[str, Any],
    jobs: list[dict[str, Any]] | str,
    unclaimed: Sequence[str] = (),
) -> str:
    """
    Return the text ``pystiltctl status`` prints.

    Parameters
    ----------
    data : dict
        :func:`simulations`' result.
    jobs : list of dict, or str
        :func:`project_jobs`' first result, or why the Jobs could not be listed.
    unclaimed : sequence of str
        :func:`project_jobs`' second result: Jobs it left out.
    """
    lines = [f"Project: {data['project']}", f"Output: {data['output']}"]
    lines.append(f"Simulations:  {_counts(data['simulations'])}")
    if len(data.get("variants", {})) > 1:
        lines += [f"  {name}: {_counts(c)}" for name, c in data["variants"].items()]
    if data.get("failed"):
        lines.append(
            "  failed: " + ", ".join(f"{why} {n}" for why, n in data["failed"].items())
        )
    if isinstance(jobs, str):
        lines.append(f"Jobs: not listed. {jobs}")
    elif not jobs:
        lines.append("Jobs: none on the cluster")
    else:
        lines.append("Jobs:")
        for job in jobs:
            state = job["state"] + (f" ({job['reason']})" if job["reason"] else "")
            tasks = (
                f"{job['done']} of {job['tasks']} tasks done, {job['running']} running"
            )
            if job["failed"]:
                tasks += f", {job['failed']} failed (tasks {job['failed_indexes']})"
            lines.append(f"  {job['name']}  {state}  {tasks}  started {job['created']}")
    if unclaimed:
        lines.append(
            f"Not counted: {', '.join(unclaimed)}. They carry this project's label "
            f"but no {PROJECT_ANNOTATION} annotation, so they may belong to another "
            "project."
        )
    return "\n".join(lines)
