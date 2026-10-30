"""Where a project's runs stand."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from pystiltctl import status
from pystiltctl.cli import app
from pystiltctl.inputs import push

#: A Job as `kubectl get job -o json` shows it, part way through.
RUNNING = {
    "metadata": {
        "name": "stilt-my-project-x7k2p",
        "creationTimestamp": "2026-10-29T16:00:00Z",
    },
    "spec": {"completions": 20, "parallelism": 5},
    "status": {"active": 5, "completedIndexes": "0-9,12", "failedIndexes": "10,11"},
}
#: The same Job once it gave up on its failed tasks.
FAILED = {
    **RUNNING,
    "status": {
        "completedIndexes": "0-9,12-19",
        "failedIndexes": "10,11",
        "conditions": [
            {"type": "FailureTarget", "status": "True", "reason": "FailedIndexes"},
            {"type": "Failed", "status": "True", "reason": "FailedIndexes"},
        ],
    },
}
COMPLETE = {
    **RUNNING,
    "metadata": {
        "name": "stilt-my-project-a1b2c",
        "creationTimestamp": "2026-10-30T09:00:00Z",
    },
    "status": {
        "completedIndexes": "0-19",
        "conditions": [{"type": "Complete", "status": "True"}],
    },
}


@pytest.mark.parametrize(("indexes", "n"), [("0-3,7", 5), ("4", 1), ("", 0), (None, 0)])
def test_count_indexes(indexes, n):
    assert status.count_indexes(indexes) == n


def test_job_summary_running():
    summary = status.job_summary(RUNNING)
    assert summary == {
        "name": "stilt-my-project-x7k2p",
        "state": "running",
        "reason": None,
        "created": "2026-10-29T16:00:00Z",
        "tasks": 20,
        "done": 11,
        "failed": 2,
        "failed_indexes": "10,11",
        "running": 5,
    }


def test_job_summary_failed_and_complete():
    assert (
        status.job_summary(FAILED)["state"],
        status.job_summary(FAILED)["reason"],
    ) == ("failed", "FailedIndexes")
    assert status.job_summary(COMPLETE)["state"] == "complete"
    assert (
        status.job_summary({**RUNNING, "spec": {"suspend": True}})["state"]
        == "suspended"
    )


@pytest.fixture
def kubectl(monkeypatch):
    """Answer `kubectl get jobs` with two Jobs, and record the call."""
    calls = []
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] != "/usr/bin/kubectl":
            return real_run(args, **kwargs)
        calls.append(args)
        items = {"items": [COMPLETE, FAILED]}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(items), stderr="")

    monkeypatch.setattr(status.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(status.subprocess, "run", run)
    return calls


def test_project_jobs_by_label_oldest_first(kubectl):
    jobs = status.project_jobs("my_project", namespace="runs")
    assert [j["name"] for j in jobs] == [
        "stilt-my-project-x7k2p",
        "stilt-my-project-a1b2c",
    ]
    assert kubectl == [
        [
            "/usr/bin/kubectl",
            "get",
            "jobs",
            "-l",
            "pystiltctl/project=my-project",
            "-o",
            "json",
            "--namespace",
            "runs",
        ]
    ]


def test_project_jobs_without_kubectl(monkeypatch):
    monkeypatch.setattr(status.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="kubectl"):
        status.project_jobs("my_project")


def test_simulations_of_a_pushed_project(project, bucket):
    push(project, bucket)
    data = status.simulations(bucket, output="memory://results/output")
    assert data["project"] == bucket
    assert data["simulations"]["total"] == 0


def test_simulations_refuses_output_left_in_the_pods(project, bucket):
    """A bucket project whose output is a path has no results here to count (#13)."""
    push(project, bucket)  # config.yaml has no output, so ./output
    with pytest.raises(ValueError, match="output directory is a path"):
        status.simulations(bucket)


def test_cli_status_refuses_what_submit_refuses(project, bucket):
    push(project, bucket)
    submit = CliRunner().invoke(app, ["submit", bucket, "--dry-run"])
    result = CliRunner().invoke(app, ["status", bucket])
    assert submit.exit_code == result.exit_code == 2
    assert result.output == submit.output
    assert "--output s3://" in result.output


def test_simulations_no_project(bucket):
    with pytest.raises(FileNotFoundError):
        status.simulations(bucket)


def test_report():
    data = {
        "project": "s3://b/projects/my_project",
        "output": "s3://b/output",
        "simulations": {
            "total": 3,
            "complete": 1,
            "failed": 1,
            "interrupted": 0,
            "pending": 1,
        },
        "variants": {"hrrr": {}},
        "failed": {"met_missing": 1},
    }
    text = status.report(data, [status.job_summary(FAILED)])
    assert (
        "Simulations:  total=3  complete=1  failed=1  interrupted=0  pending=1" in text
    )
    assert "  failed: met_missing 1" in text
    assert (
        "  stilt-my-project-x7k2p  failed (FailedIndexes)  18 of 20 tasks done, 0 running, "
        "2 failed (tasks 10,11)  started 2026-10-29T16:00:00Z"
    ) in text
    assert "Jobs: none on the cluster" in status.report(data, [])
    assert "Jobs: not listed. no cluster" in status.report(data, "no cluster")


def test_cli_status_without_a_cluster(project, monkeypatch):
    monkeypatch.setattr(status.shutil, "which", lambda name: None)
    result = CliRunner().invoke(app, ["status", str(project)])
    assert result.exit_code == 0, result.output
    assert "Simulations:  total=0" in result.output
    assert "Jobs: not listed. kubectl is not installed" in result.output


def test_cli_status_json(project, bucket, kubectl):
    push(project, bucket)
    result = CliRunner().invoke(
        app, ["status", bucket, "--output", "memory://results/output", "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["simulations"]["total"] == 0
    assert [j["state"] for j in data["jobs"]] == ["failed", "complete"]


def test_cli_status_no_project(bucket):
    result = CliRunner().invoke(app, ["status", bucket])
    assert result.exit_code == 2


def test_simulations_reports_stilts_error(project):
    (project / "receptors.csv").write_text("when,where\n")
    with pytest.raises(RuntimeError, match="Receptor table must contain columns"):
        status.simulations(str(project))
