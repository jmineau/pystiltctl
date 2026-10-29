"""
The ``pystiltctl`` command-line interface.

Examples::

    pystiltctl push ./my_project s3://bucket/projects/my_project
    pystiltctl worker s3://bucket/projects/my_project --tasks 20   # in a pod

``pystiltctl worker`` exits with ``stilt run``'s code: 0 when every
simulation of the task is complete, 1 when some failed, and 3 when some
were stopped. It also exits with 3 when it cannot copy the project's
inputs (a retry may work), and with 2 when its command line is wrong or
there is no project at the URL (a retry will not help).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from pystiltctl import inputs, worker

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
