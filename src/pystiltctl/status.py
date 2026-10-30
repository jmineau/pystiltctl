"""
Where a project's runs stand: its Jobs on the cluster, and its simulations.

The Jobs come from ``kubectl``, found by the ``pystiltctl/project`` label
that :func:`pystiltctl.job.manifest` puts on them. The simulations come
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
from pathlib import Path
from typing import Any

from pystiltctl.inputs import is_local, local_path, pull
from pystiltctl.job import PROJECT_LABEL, label_value


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


def project_jobs(name: str, *, namespace: str | None = None) -> list[dict[str, Any]]:
    """
    Return the summaries of a project's Jobs, oldest first.

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
        f"{PROJECT_LABEL}={label_value(name)}",
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
    jobs = [job_summary(item) for item in json.loads(result.stdout)["items"]]
    return sorted(jobs, key=lambda j: j["created"] or "")


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


def report(data: dict[str, Any], jobs: list[dict[str, Any]] | str) -> str:
    """
    Return the text ``pystiltctl status`` prints.

    Parameters
    ----------
    data : dict
        :func:`simulations`' result.
    jobs : list of dict, or str
        :func:`project_jobs`' result, or why the Jobs could not be listed.
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
    return "\n".join(lines)
