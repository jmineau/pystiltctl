"""Running one task of a project, as a pod does."""

import os
import signal
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from pystiltctl import worker
from pystiltctl.cli import app
from pystiltctl.inputs import hash_inputs, push


def _alive(pid: int) -> bool:
    """Whether a process still exists (signal 0; a permission error still means yes)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until(predicate, *, timeout: float = 5.0, step: float = 0.05) -> bool:
    """Poll *predicate* until it is true or *timeout* elapses; return its last value."""
    deadline = time.monotonic() + timeout
    result = predicate()
    while not result and time.monotonic() < deadline:
        time.sleep(step)
        result = predicate()
    return result


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


def test_run_task_accepts_a_matching_input_hash(project, bucket, tmp_path, execs):
    push(project, bucket)
    worker.run_task(
        bucket, 2, index=0, workdir=tmp_path / "pod", input_hash=hash_inputs(project)
    )
    directory = tmp_path / "pod" / "my_project"
    assert execs == [worker.stilt_command(directory, 0, 2)]


def test_run_task_refuses_a_project_pushed_again_after_submit(
    project, bucket, tmp_path, execs
):
    """
    Reproduce #15: a push between two pods' pulls must not run silently.

    Task 0 pulls the receptors ``submit`` saw, whose hash matches. Before
    task 1 pulls, the project is pushed again with a different
    ``receptors.csv`` (as the issue describes: a Job's pods can start
    minutes to hours apart, and a push in between changes the split
    ``stilt run --task I/N`` would make). Task 1 must fail instead of
    silently running that different split.
    """
    push(project, bucket)
    submit_hash = hash_inputs(project)

    worker.run_task(
        bucket, 2, index=0, workdir=tmp_path / "pod0", input_hash=submit_hash
    )
    assert execs == [worker.stilt_command(tmp_path / "pod0" / "my_project", 0, 2)]

    (project / "receptors.csv").write_text(
        "time,longitude,latitude,altitude\n2021-01-15 00:00:00,-112,40,10\n"
    )
    push(project, bucket)

    with pytest.raises(ValueError, match="changed after submit; submit again"):
        worker.run_task(
            bucket, 2, index=1, workdir=tmp_path / "pod1", input_hash=submit_hash
        )
    assert len(execs) == 1  # task 1 never ran stilt


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


def test_cli_worker_accepts_a_matching_input_hash(project, bucket, tmp_path, execs):
    push(project, bucket)
    result = CliRunner().invoke(
        app,
        [
            "worker",
            bucket,
            "--tasks",
            "2",
            "--index",
            "0",
            "--workdir",
            str(tmp_path),
            "--input-hash",
            hash_inputs(project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert execs != []


def test_cli_worker_refuses_a_project_changed_since_submit(
    project, bucket, tmp_path, execs
):
    """Fails the Job (exit 2), the code the issue itself asks for (#15)."""
    push(project, bucket)
    submit_hash = hash_inputs(project)
    (project / "receptors.csv").write_text(
        "time,longitude,latitude,altitude\n2021-01-15 00:00:00,-112,40,10\n"
    )
    push(project, bucket)
    result = CliRunner().invoke(
        app,
        [
            "worker",
            bucket,
            "--tasks",
            "2",
            "--index",
            "0",
            "--workdir",
            str(tmp_path),
            "--input-hash",
            submit_hash,
        ],
    )
    assert result.exit_code == 2, result.output
    assert "changed after submit; submit again" in result.output
    assert execs == []


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


def test_cli_worker_bucket_error_is_retried_not_failed(
    bucket, tmp_path, monkeypatch, execs
):
    """A bucket error while copying the inputs retries the task, not the whole Job (#11)."""
    import fsspec

    from pystiltctl import inputs

    class _Unreachable:
        def __init__(self, fs):
            self.fs = fs

        def __getattr__(self, name):
            return getattr(self.fs, name)

        def info(self, path, **kwargs):
            raise TimeoutError("timed out reaching the bucket")

    fs_for = fsspec.core.url_to_fs
    monkeypatch.setattr(
        inputs.fsspec.core,
        "url_to_fs",
        lambda url: (_Unreachable(fs_for(url)[0]), fs_for(url)[1]),
    )
    result = CliRunner().invoke(
        app,
        ["worker", bucket, "--tasks", "2", "--index", "0", "--workdir", str(tmp_path)],
    )
    assert result.exit_code == 3, result.output
    assert "timed out" in result.output
    assert execs == []


def test_cli_worker_without_an_index(project, monkeypatch, execs):
    monkeypatch.delenv("JOB_COMPLETION_INDEX", raising=False)
    result = CliRunner().invoke(app, ["worker", str(project), "--tasks", "2"])
    assert result.exit_code == 2
    assert "JOB_COMPLETION_INDEX" in result.output


def test_run_returns_the_commands_exit_code():
    assert worker.run([sys.executable, "-c", "raise SystemExit(3)"]) == 3


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux only")
def test_run_does_not_orphan_the_child_on_sigkill(tmp_path):
    """
    Reproduce #30 for real: SIGKILL of the parent must not orphan the child.

    A "parent" process runs :func:`worker.run` on a sleeping child, then
    this test SIGKILLs the parent from outside (a signal no handler can
    forward). The child writes its own PID as its first action, so the
    test can watch it after the parent is gone.
    """
    pidfile = tmp_path / "child.pid"
    child_command = [
        sys.executable,
        "-c",
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        str(pidfile),
    ]
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "from pystiltctl import worker\n"  # sys.executable: the env that has it installed
        f"worker.run({child_command!r})\n"
    )
    parent = subprocess.Popen([sys.executable, str(parent_script)])
    try:
        assert _wait_until(pidfile.exists), "the child never started"
        child_pid = int(pidfile.read_text())
        assert _alive(child_pid), (
            "the child exited before the test could kill the parent"
        )

        parent.send_signal(signal.SIGKILL)
        parent.wait(timeout=5)

        # PR_SET_PDEATHSIG's SIGTERM arrives the moment the parent is gone;
        # give the child a moment to act on it.
        assert _wait_until(lambda: not _alive(child_pid)), (
            "the child outlived its SIGKILLed parent (#30)"
        )
    finally:
        if _alive(parent.pid):
            parent.kill()
        if pidfile.exists():
            child_pid = int(pidfile.read_text())
            if _alive(child_pid):
                os.kill(child_pid, signal.SIGKILL)
