"""
Write and start the Kubernetes Job that runs a project.

A project runs as one indexed Job: ``completions`` tasks, each a pod that
runs ``pystiltctl worker``, which runs ``stilt run --task I/N``. Kubernetes
retries a task from its exit code (``podFailurePolicy``):

- 2, a wrong command line, fails the whole Job, since every task would
  fail the same way;
- 1, some simulations failed, fails that task only, since running it again
  repeats the failure until the cause is fixed;
- 3, stopped, is retried, up to :data:`RETRIES` times per task;
- a pod evicted or preempted (a spot node taken back) is retried without
  counting against that.

Nothing here decides which simulations are finished: a task that runs
again skips the ones whose results exist.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import stilt
import yaml

import pystiltctl
from pystiltctl.inputs import is_local, local_path, project_name, pull

#: Retries per task for a stopped pod (exit 3) or a crash.
RETRIES = 3
#: How long a finished Job, and its pods' logs, stay on the cluster: a week.
KEEP_FINISHED = 7 * 24 * 3600
#: The label every pystiltctl Job and pod carries, with the project's name.
PROJECT_LABEL = "pystiltctl/project"
#: The annotation holding the project's URL, for ``pystiltctl status``.
PROJECT_ANNOTATION = "pystiltctl/project-url"
#: Where each pod keeps its inputs and its runs' workdirs: an ``emptyDir``.
SCRATCH = "/scratch"
#: The image's repository.
IMAGE_REPOSITORY = "ghcr.io/jmineau/pystiltctl"


def default_image(version: str = pystiltctl.__version__) -> str:
    """
    Return the image matching this pystiltctl.

    A release runs its own image; anything else (a development version)
    runs ``:main``.

    Examples
    --------
    >>> default_image("0.2.0")
    'ghcr.io/jmineau/pystiltctl:0.2.0'
    >>> default_image("0.2.1.dev3+g1a2b3c4")
    'ghcr.io/jmineau/pystiltctl:main'
    """
    released = re.fullmatch(r"\d+\.\d+\.\d+", version) is not None
    return f"{IMAGE_REPOSITORY}:{version if released else 'main'}"


def kubernetes_memory(mem: str) -> str:
    """
    Return a Slurm memory size, such as ``8G``, as a Kubernetes quantity.

    Slurm's ``K``, ``M``, ``G`` and ``T`` count in 1024s, and a bare number
    is megabytes; Kubernetes writes those as ``Ki``, ``Mi``, ``Gi`` and
    ``Ti``. A size already in Kubernetes' form is returned as it is.

    Examples
    --------
    >>> kubernetes_memory("8G")
    '8Gi'
    >>> kubernetes_memory("4000")
    '4000Mi'
    >>> kubernetes_memory("512Mi")
    '512Mi'
    """
    match = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)B?\s*", mem, flags=re.IGNORECASE)
    if match is None:
        return mem.strip()
    number, unit = match.groups()
    return f"{number}{(unit or 'M').upper()}i"


def label_value(name: str) -> str:
    """
    Return *name* as a Kubernetes label value and name prefix.

    Lowercase letters, digits and hyphens, at most 40 characters, starting
    and ending with a letter or digit.

    Examples
    --------
    >>> label_value("SLV_2015 winter")
    'slv-2015-winter'
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "project"


@dataclass(frozen=True)
class Volume:
    """
    A PersistentVolumeClaim mounted in every pod.

    Attributes
    ----------
    claim : str
        The claim's name.
    path : str
        Where it is mounted in the pod.
    """

    claim: str
    path: str

    @classmethod
    def parse(cls, text: str) -> Volume:
        """
        Read ``CLAIM:PATH``, as ``--volume`` takes it.

        Examples
        --------
        >>> Volume.parse("hrrr-arl:/met")
        Volume(claim='hrrr-arl', path='/met')
        """
        claim, sep, path = text.partition(":")
        if not (sep and claim and path.startswith("/")):
            raise ValueError(
                f"A volume is CLAIM:/path, such as hrrr-arl:/met; got {text!r}."
            )
        return cls(claim, path)


@dataclass(frozen=True)
class JobSpec:
    """
    What the Job for a project runs, and with what.

    Attributes
    ----------
    project : str
        The project the pods run: a URL, or a folder on a mounted volume.
    name : str
        The project's name, for the Job's name and labels.
    tasks : int
        How many tasks the receptors are split into (``completions``).
    parallelism : int
        Most pods running at once.
    cpus : int
        Receptors each pod runs at once, and the CPUs it asks for.
    memory : str or None
        Memory each pod asks for, as a Kubernetes quantity.
    image : str
        The container image.
    output : str or None
        The output directory, in place of the one ``config.yaml`` names.
    secrets : tuple of str
        Secrets whose keys become the pods' environment variables, such as
        a bucket's credentials.
    service_account : str or None
        The pods' service account, for clusters that grant bucket access
        through one.
    volumes : tuple of Volume
        PersistentVolumeClaims to mount, such as a met archive.
    namespace : str or None
        The namespace to run in; the current one if None.
    """

    project: str
    name: str
    tasks: int
    parallelism: int
    cpus: int = 1
    memory: str | None = None
    image: str = field(default_factory=default_image)
    output: str | None = None
    secrets: tuple[str, ...] = ()
    service_account: str | None = None
    volumes: tuple[Volume, ...] = ()
    namespace: str | None = None


def job_spec(
    project: str,
    *,
    tasks: int | None = None,
    parallelism: int | None = None,
    cpus: int | None = None,
    memory: str | None = None,
    image: str | None = None,
    output: str | None = None,
    secrets: Iterable[str] = (),
    service_account: str | None = None,
    volumes: Iterable[Volume] = (),
    namespace: str | None = None,
) -> JobSpec:
    """
    Return the Job for *project*, from its ``config.yaml`` and these overrides.

    What is not given comes from the ``execution`` section of the
    project's ``config.yaml``, the same settings a Slurm run uses:
    ``n_workers`` tasks, ``array_parallelism`` pods at once (all of them if
    unset), ``cpus`` per pod, and ``mem`` per pod.

    Parameters
    ----------
    project : str
        The project the pods run: a URL its inputs were pushed to, or a
        folder that is at the same path here and in the pods.
    **overrides
        The fields of :class:`JobSpec` to set instead of the project's.

    Raises
    ------
    FileNotFoundError
        If there is no project at *project*.
    ValueError
        If the results would stay inside the pods: the project is on a
        bucket but its output directory is not.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = (
            local_path(project)
            if is_local(project)
            else pull(project, Path(tmp) / "project")
        )
        opened = stilt.Project(directory, output=output)
        execution = opened.config.execution
        results = str(opened.output.directory)
    if not is_local(project) and is_local(results):
        raise ValueError(
            "The output directory is a path, so each pod would write its results "
            "to its own disk and lose them. Set output to a bucket in config.yaml, "
            "or give --output s3://..."
        )
    tasks = tasks or execution.n_workers
    if memory is None and execution.mem is not None:
        memory = kubernetes_memory(execution.mem)
    return JobSpec(
        project=project,
        name=project_name(project),
        tasks=tasks,
        parallelism=min(parallelism or execution.array_parallelism or tasks, tasks),
        cpus=cpus or execution.cpus,
        memory=memory,
        image=image or default_image(),
        output=output,
        secrets=tuple(secrets),
        service_account=service_account,
        volumes=tuple(volumes),
        namespace=namespace,
    )


def worker_args(spec: JobSpec) -> list[str]:
    """Return the command line each pod runs."""
    args = ["pystiltctl", "worker", spec.project, "--tasks", str(spec.tasks)]
    args += ["--cpus", str(spec.cpus), "--workdir", f"{SCRATCH}/project"]
    if spec.output is not None:
        args += ["--output", spec.output]
    return args


def _labels(spec: JobSpec) -> dict[str, str]:
    """Return the labels every object for *spec*'s project carries."""
    return {
        "app.kubernetes.io/name": "pystiltctl",
        PROJECT_LABEL: label_value(spec.name),
    }


def _metadata(spec: JobSpec, **name: str) -> dict[str, Any]:
    """Return an object's metadata: *name* (``name`` or ``generateName``), labels, namespace."""
    metadata: dict[str, Any] = {
        **name,
        "labels": _labels(spec),
        "annotations": {PROJECT_ANNOTATION: spec.project},
    }
    if spec.namespace is not None:
        metadata["namespace"] = spec.namespace
    return metadata


def _job_body(
    spec: JobSpec, args: list[str], completions: int, parallelism: int
) -> dict[str, Any]:
    """
    Return the ``spec`` of an indexed Job whose pods run *args*.

    The exit-code rules are described in this module's docstring.
    """
    resources: dict[str, Any] = {"requests": {"cpu": str(spec.cpus)}}
    if spec.memory is not None:
        resources["requests"]["memory"] = spec.memory
        resources["limits"] = {"memory": spec.memory}
    container: dict[str, Any] = {
        "name": "stilt",
        "image": spec.image,
        "args": args,
        "env": [{"name": "PYSTILT_COMPUTE_ROOT", "value": f"{SCRATCH}/runs"}],
        "resources": resources,
        "volumeMounts": [{"name": "scratch", "mountPath": SCRATCH}]
        + [
            {"name": f"volume-{i}", "mountPath": v.path}
            for i, v in enumerate(spec.volumes)
        ],
    }
    if spec.secrets:
        container["envFrom"] = [{"secretRef": {"name": name}} for name in spec.secrets]
    pod: dict[str, Any] = {
        "restartPolicy": "Never",
        "containers": [container],
        "volumes": [{"name": "scratch", "emptyDir": {}}]
        + [
            {"name": f"volume-{i}", "persistentVolumeClaim": {"claimName": v.claim}}
            for i, v in enumerate(spec.volumes)
        ],
    }
    if spec.service_account is not None:
        pod["serviceAccountName"] = spec.service_account

    def on_exit(action: str, code: int) -> dict[str, Any]:
        """Return the rule that takes *action* when the worker exits with *code*."""
        rule = {"containerName": "stilt", "operator": "In", "values": [code]}
        return {"action": action, "onExitCodes": rule}

    return {
        "completionMode": "Indexed",
        "completions": completions,
        "parallelism": parallelism,
        "backoffLimitPerIndex": RETRIES,
        "podFailurePolicy": {
            "rules": [
                on_exit("FailJob", 2),
                on_exit("FailIndex", 1),
                # `status` is required in a CronJob's template; the API server
                # fills it in only for a Job created on its own.
                {
                    "action": "Ignore",
                    "onPodConditions": [{"type": "DisruptionTarget", "status": "True"}],
                },
            ]
        },
        "ttlSecondsAfterFinished": KEEP_FINISHED,
        "template": {"metadata": {"labels": _labels(spec)}, "spec": pod},
    }


def manifest(spec: JobSpec) -> dict[str, Any]:
    """
    Return the indexed Job that runs *spec*, as a Kubernetes manifest.

    The Job's name is ``stilt-<project>-`` and a suffix Kubernetes picks,
    so the same project can be submitted again. Its exit-code rules are
    described in this module's docstring.
    """
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _metadata(spec, generateName=f"stilt-{label_value(spec.name)}-"),
        "spec": _job_body(spec, worker_args(spec), spec.tasks, spec.parallelism),
    }


def tick_args(spec: JobSpec, delay: int, render: str | None = None) -> list[str]:
    """Return the command line a schedule's pod runs each hour."""
    args = ["pystiltctl", "tick", spec.project, "--delay", str(delay)]
    args += ["--cpus", str(spec.cpus), "--workdir", f"{SCRATCH}/project"]
    if spec.output is not None:
        args += ["--output", spec.output]
    if render is not None:
        args += ["--render", render]
    return args


def cron_job(spec: JobSpec, delay: int, render: str | None = None) -> dict[str, Any]:
    """
    Return the CronJob that runs ``pystiltctl tick`` for *spec* every hour.

    One pod at a time (a tick that is still running delays the next), at
    15 minutes past the hour. Its Jobs carry the project's label, so
    ``pystiltctl status`` lists them, and the last day's are kept. The
    CronJob is named ``stilt-<project>``, so scheduling a project again
    replaces it.
    """
    body = _job_body(spec, tick_args(spec, delay, render), completions=1, parallelism=1)
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": _metadata(spec, name=f"stilt-{label_value(spec.name)}"),
        "spec": {
            "schedule": "15 * * * *",
            "timeZone": "Etc/UTC",
            "concurrencyPolicy": "Forbid",
            "startingDeadlineSeconds": 1800,
            "successfulJobsHistoryLimit": 24,
            "failedJobsHistoryLimit": 24,
            "jobTemplate": {"metadata": {"labels": _labels(spec)}, "spec": body},
        },
    }


def to_yaml(job: dict[str, Any]) -> str:
    """Return *job* as YAML, in the order it was written."""
    return yaml.safe_dump(job, sort_keys=False)


def create(job: dict[str, Any]) -> str:
    """
    Create *job* on the cluster ``kubectl`` points at, and return its name.

    Raises
    ------
    FileNotFoundError
        If ``kubectl`` is not installed.
    RuntimeError
        If ``kubectl`` refuses the Job; the message is its error.
    """
    return _kubectl("create", job)


def apply(manifest: dict[str, Any]) -> str:
    """
    Create *manifest*, or update it if it exists, and return its name.

    Used for a schedule's CronJob, which has a fixed name.

    Raises
    ------
    FileNotFoundError
        If ``kubectl`` is not installed.
    RuntimeError
        If ``kubectl`` refuses it; the message is its error.
    """
    return _kubectl("apply", manifest)


def _kubectl(verb: str, manifest: dict[str, Any]) -> str:
    """Run ``kubectl <verb> -f -`` on *manifest* and return the object's name."""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise FileNotFoundError("kubectl is not installed, or not on PATH.")
    result = subprocess.run(
        [kubectl, verb, "-f", "-", "-o", "name"],
        input=to_yaml(manifest),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"kubectl exited with {result.returncode}"
        )
    return result.stdout.strip().split("/", 1)[-1]
