"""
The ``pystiltctl`` command-line interface.

Examples::

    pystiltctl push ./my_project s3://bucket/projects/my_project
    pystiltctl submit s3://bucket/projects/my_project --tasks 20
    pystiltctl submit s3://bucket/projects/my_project --dry-run    # print the Job
    pystiltctl schedule s3://bucket/projects/towers --volume met:/met
    pystiltctl status s3://bucket/projects/my_project
    pystiltctl worker s3://bucket/projects/my_project --tasks 20   # in a pod

``pystiltctl worker`` exits with ``stilt run``'s code: 0 when every
simulation of the task is complete, 1 when some failed, and 3 when some
were stopped. It also exits with 3 when it cannot copy the project's
inputs (a retry may work), and with 2 when its command line is wrong or
there is no project at the URL (a retry will not help).
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from pystiltctl import inputs, job, status, worker
from pystiltctl import schedule as schedules

app = typer.Typer(
    name="pystiltctl",
    help="Run PYSTILT projects on Kubernetes, with inputs and results on a bucket.",
    no_args_is_help=True,
)

EXIT_USAGE, EXIT_RETRY = 2, 3


def _fail(message: str, code: int = EXIT_USAGE) -> NoReturn:
    """Print *message* as an error and exit with *code*."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


@app.command()
def push(
    project: Annotated[
        Path, typer.Argument(help="The project folder on this machine.")
    ],
    url: Annotated[
        str,
        typer.Argument(
            help="Where to copy it, such as s3://bucket/projects/my_project."
        ),
    ],
) -> None:
    """
    Copy a project's inputs to a bucket, for pods to read.

    Copies config.yaml, receptors.csv and tables/. The output directory is
    not copied: name a bucket as the output (output: s3://... in
    config.yaml, or --output), and the pods write results there.
    """
    try:
        copied = inputs.push(project, url)
    except FileNotFoundError as error:
        _fail(str(error))
    typer.echo(f"Copied {len(copied)} files to {url}")


# The options of the pods that submit and schedule start, named once.
Project = Annotated[
    str,
    typer.Argument(
        help="The project: the URL it was pushed to, or a folder on a volume."
    ),
]
Cpus = Annotated[
    int | None,
    typer.Option(
        min=1, help="Receptors each pod runs at once. Default: execution.cpus."
    ),
]
Memory = Annotated[
    str | None,
    typer.Option(help="Memory per pod, such as 8Gi. Default: execution.mem."),
]
Image = Annotated[
    str | None, typer.Option(help="Container image. Default: this version's.")
]
Output = Annotated[
    str | None,
    typer.Option(help="Output directory to use instead of config.yaml's."),
]
Secret = Annotated[
    list[str] | None,
    typer.Option(
        help="A Secret whose keys become the pods' environment, such as bucket credentials. Repeat for more."
    ),
]
ServiceAccount = Annotated[str | None, typer.Option(help="The pods' service account.")]
Volume = Annotated[
    list[str] | None,
    typer.Option(
        help="A PersistentVolumeClaim to mount, as CLAIM:/path. Repeat for more."
    ),
]
Namespace = Annotated[
    str | None, typer.Option(help="Namespace. Default: kubectl's current one.")
]
DryRun = Annotated[
    bool, typer.Option("--dry-run", help="Print the manifest instead of starting it.")
]
Delay = Annotated[
    int,
    typer.Option(
        min=0, help="Hours before an hour is run: how long its met takes to appear."
    ),
]


@app.command()
def submit(
    project: Project,
    tasks: Annotated[
        int | None,
        typer.Option(
            min=1,
            help="Tasks to split the receptors into. Default: execution.n_workers.",
        ),
    ] = None,
    parallelism: Annotated[
        int | None,
        typer.Option(
            min=1,
            help="Most pods at once. Default: execution.array_parallelism, or all.",
        ),
    ] = None,
    cpus: Cpus = None,
    memory: Memory = None,
    image: Image = None,
    output: Output = None,
    secret: Secret = None,
    service_account: ServiceAccount = None,
    volume: Volume = None,
    namespace: Namespace = None,
    dry_run: DryRun = False,
) -> None:
    """
    Run a project on Kubernetes, as an indexed Job.

    Each pod runs one task, pystiltctl worker, which runs stilt run --task
    I/N. Settings not given come from the execution section of
    config.yaml. A task that is stopped is retried; one whose simulations
    failed is not. Submit again to fill in what is missing: finished
    simulations are skipped.
    """
    try:
        spec = job.job_spec(
            project,
            tasks=tasks,
            parallelism=parallelism,
            cpus=cpus,
            memory=memory,
            image=image,
            output=output,
            secrets=secret or (),
            service_account=service_account,
            volumes=[job.Volume.parse(v) for v in volume or ()],
            namespace=namespace,
        )
    except (ValueError, FileNotFoundError) as error:
        _fail(str(error))
    manifest = job.manifest(spec)
    if dry_run:
        typer.echo(job.to_yaml(manifest), nl=False)
        return
    try:
        name = job.create(manifest)
    except (FileNotFoundError, RuntimeError) as error:
        _fail(f"{error} (--dry-run prints the Job without kubectl)")
    typer.echo(
        f"Submitted job: {name}  ({spec.tasks} tasks, {spec.parallelism} at once)"
    )


@app.command()
def schedule(
    project: Project,
    delay: Delay = schedules.DELAY_HOURS,
    cpus: Cpus = None,
    memory: Memory = None,
    image: Image = None,
    output: Output = None,
    secret: Secret = None,
    service_account: ServiceAccount = None,
    volume: Volume = None,
    namespace: Namespace = None,
    dry_run: DryRun = False,
) -> None:
    """
    Run a project every hour, for the sites in its sites.csv.

    Starts a CronJob that runs pystiltctl tick once an hour: it adds a
    receptor per site for each hour of the last day that is at least
    --delay hours old, then runs what is not finished. Give the met a
    volume (--volume), so each downloaded file is kept for the next ticks.
    Scheduling a project again replaces its CronJob.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            directory = (
                inputs.local_path(project)
                if inputs.is_local(project)
                else inputs.pull(project, Path(tmp) / "project")
            )
            sites = schedules.read_sites(directory)
        spec = job.job_spec(
            project,
            cpus=cpus,
            memory=memory,
            image=image,
            output=output,
            secrets=secret or (),
            service_account=service_account,
            volumes=[job.Volume.parse(v) for v in volume or ()],
            namespace=namespace,
        )
    except (ValueError, FileNotFoundError) as error:
        _fail(str(error))
    manifest = job.cron_job(spec, delay)
    if dry_run:
        typer.echo(job.to_yaml(manifest), nl=False)
        return
    try:
        name = job.apply(manifest)
    except (FileNotFoundError, RuntimeError) as error:
        _fail(f"{error} (--dry-run prints the CronJob without kubectl)")
    typer.echo(f"Scheduled: {name}  (every hour, {len(sites)} sites, {delay} h behind)")


@app.command()
def tick(
    project: Annotated[
        str, typer.Argument(help="The project: a URL, or a folder on a mounted volume.")
    ],
    delay: Delay = schedules.DELAY_HOURS,
    now: Annotated[
        datetime | None,
        typer.Option(
            formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
            help="Act as if it were this time, in UTC, to fill in a past day. Default: now.",
        ),
    ] = None,
    cpus: Annotated[
        int | None, typer.Option(min=1, help="Receptors to run at once.")
    ] = None,
    output: Output = None,
    workdir: Annotated[
        Path, typer.Option(help="Where a URL's inputs are copied.")
    ] = worker.WORKDIR,
) -> None:
    """
    Add the hours now due for the project's sites, then run its last day.

    What each pod of a schedule runs. Exits with stilt run's code.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        schedules.tick(
            project, delay=delay, now=now, cpus=cpus, output=output, workdir=workdir
        )
    except (ValueError, FileNotFoundError) as error:
        _fail(str(error))
    except OSError as error:
        _fail(f"cannot copy the project's inputs from {project}: {error}", EXIT_RETRY)


@app.command(name="status")
def status_command(
    project: Annotated[
        str,
        typer.Argument(help="The project: the URL it was pushed to, or a folder."),
    ],
    output: Annotated[
        str | None,
        typer.Option(help="Output directory to use instead of config.yaml's."),
    ] = None,
    namespace: Annotated[
        str | None, typer.Option(help="Namespace. Default: kubectl's current one.")
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the same as JSON, for a program to read."),
    ] = False,
) -> None:
    """
    Show a project's Jobs on the cluster and count its simulations.

    The counts are stilt status's, read from the output directory: how many
    simulations are complete, failed, interrupted and pending. The Jobs are
    the ones pystiltctl submit started for this project, with how many of
    their tasks are done, running, and failed.
    """
    try:
        data = status.simulations(project, output=output)
    except FileNotFoundError as error:
        _fail(str(error))
    except RuntimeError as error:
        _fail(f"stilt status failed: {error}", EXIT_RETRY)
    try:
        jobs: list[dict] | str = status.project_jobs(
            inputs.project_name(project), namespace=namespace
        )
    except (FileNotFoundError, RuntimeError) as error:
        jobs = str(error)
    if as_json:
        typer.echo(json.dumps({**data, "jobs": jobs}, indent=2))
    else:
        typer.echo(status.report(data, jobs))


@app.command(name="worker")
def worker_command(
    project: Annotated[
        str, typer.Argument(help="The project: a URL, or a folder on a mounted volume.")
    ],
    tasks: Annotated[
        int, typer.Option(min=1, help="How many tasks the project is split into.")
    ],
    index: Annotated[
        int | None,
        typer.Option(
            min=0, help="This task's index. Defaults to $JOB_COMPLETION_INDEX."
        ),
    ] = None,
    cpus: Annotated[
        int | None, typer.Option(min=1, help="Receptors to run at once.")
    ] = None,
    output: Annotated[
        str | None,
        typer.Option(help="Output directory to use instead of config.yaml's."),
    ] = None,
    workdir: Annotated[
        Path, typer.Option(help="Where a URL's inputs are copied.")
    ] = worker.WORKDIR,
) -> None:
    """
    Run one task of a project here: what each pod of a Job runs.

    Copies the project's inputs from the URL, then runs stilt run --task
    I/N, where I is this pod's index and N is --tasks. Exits with stilt
    run's code.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        worker.run_task(
            project, tasks, index=index, cpus=cpus, output=output, workdir=workdir
        )
    except (ValueError, FileNotFoundError) as error:
        _fail(str(error))
    except OSError as error:
        _fail(f"cannot copy the project's inputs from {project}: {error}", EXIT_RETRY)
