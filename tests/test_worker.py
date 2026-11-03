"""Running one task of a project, as a pod does."""

import sys

import pytest
from typer.testing import CliRunner

from pystiltctl import worker
from pystiltctl.cli import app
from pystiltctl.inputs import push


def test_task_index_from_argument_or_environment(monkeypatch):
    monkeypatch.setenv("JOB_COMPLETION_INDEX", "7")
    assert worker.task_index() == 7
    assert worker.task_index(2) == 2


@pytest.mark.parametrize("value", [None, "seven"])
def test_task_index_needs_a_whole_number(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("JOB_COMPLETION_INDEX", raising=False)
    else:
        monkeypatch.setenv("JOB_COMPLETION_INDEX", value)
    with pytest.raises(ValueError, match="JOB_COMPLETION_INDEX"):
        worker.task_index()


def test_stilt_command_passes_options_on():
    command = worker.stilt_command("/work/p", 0, 4, cpus=2, output="s3://b/out")
    assert command[:3] == [sys.executable, "-m", "stilt"]
    assert command[3:] == [
        "run",
        "/work/p",
        "--task",
        "0/4",
        "--cpus",
        "2",
        "--output",
        "s3://b/out",
    ]


def test_run_task_pulls_a_url_then_runs_stilt(
    project, bucket, tmp_path, monkeypatch, execs
):
    push(project, bucket)
    monkeypatch.setenv("JOB_COMPLETION_INDEX", "3")
    worker.run_task(bucket, 20, workdir=tmp_path / "pod")
    directory = tmp_path / "pod" / "my_project"
    assert (directory / "config.yaml").is_file()
    assert execs == [worker.stilt_command(directory, 3, 20)]


def test_run_task_uses_a_mounted_project_in_place(project, execs):
    worker.run_task(str(project), 2, index=1)
    assert execs == [worker.stilt_command(project, 1, 2)]


def test_run_task_takes_a_file_url_as_a_folder(project, execs):
    worker.run_task(f"file://{project}", 2, index=0)
    assert execs == [worker.stilt_command(project, 0, 2)]


def test_run_task_refuses_an_index_outside_the_tasks(project, execs):
    with pytest.raises(ValueError, match="not one of 0 to 3"):
        worker.run_task(str(project), 4, index=4)
    assert execs == []


def test_cli_push(project, bucket):
    result = CliRunner().invoke(app, ["push", str(project), bucket])
    assert result.exit_code == 0, result.output
    assert "Copied 3 files" in result.output


def test_cli_push_not_a_project(tmp_path, bucket):
    result = CliRunner().invoke(app, ["push", str(tmp_path), bucket])
    assert result.exit_code == 2
    assert "not a PYSTILT project" in result.output


def test_cli_worker_runs_the_task(project, bucket, tmp_path, execs):
    push(project, bucket)
    args = [
        "worker",
        bucket,
        "--tasks",
        "5",
        "--index",
        "0",
        "--cpus",
        "4",
        "--workdir",
        str(tmp_path),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert execs[0][3:] == [
        "run",
        str(tmp_path / "my_project"),
        "--task",
        "0/5",
        "--cpus",
        "4",
    ]


def test_cli_worker_no_project_is_not_retried(bucket, tmp_path, execs):
    result = CliRunner().invoke(
        app,
        ["worker", bucket, "--tasks", "2", "--index", "0", "--workdir", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert execs == []


def test_cli_worker_failed_copy_is_retried(bucket, tmp_path, monkeypatch, execs):
    def broken(url, directory):
        raise ConnectionError("the bucket did not answer")

    monkeypatch.setattr(worker, "pull", broken)
    result = CliRunner().invoke(
        app,
        ["worker", bucket, "--tasks", "2", "--index", "0", "--workdir", str(tmp_path)],
    )
    assert result.exit_code == 3
    assert "did not answer" in result.output


def test_cli_worker_without_an_index(project, monkeypatch, execs):
    monkeypatch.delenv("JOB_COMPLETION_INDEX", raising=False)
    result = CliRunner().invoke(app, ["worker", str(project), "--tasks", "2"])
    assert result.exit_code == 2
    assert "JOB_COMPLETION_INDEX" in result.output
