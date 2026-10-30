"""The Kubernetes Job for a project."""

import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from pystiltctl import job
from pystiltctl.cli import app
from pystiltctl.inputs import push
from tests.conftest import CONFIG


@pytest.fixture
def pushed(project, bucket):
    """The project on a bucket, with its output on a bucket too."""
    (project / "config.yaml").write_text(
        CONFIG
        + "output: s3://bucket/output\n"
        + "execution: {n_workers: 12, cpus: 4, mem: 8G, array_parallelism: 5}\n"
    )
    push(project, bucket)
    return bucket


def test_job_spec_takes_the_projects_execution_settings(pushed):
    spec = job.job_spec(pushed)
    assert (spec.tasks, spec.parallelism, spec.cpus, spec.memory) == (12, 5, 4, "8Gi")
    assert spec.name == "my_project"


def test_job_spec_options_win(pushed):
    spec = job.job_spec(
        pushed, tasks=3, parallelism=10, cpus=2, memory="1Gi", image="me/stilt:1"
    )
    assert (spec.tasks, spec.parallelism, spec.cpus, spec.memory, spec.image) == (
        3,
        3,
        2,
        "1Gi",
        "me/stilt:1",
    )


def test_job_spec_refuses_output_left_in_the_pods(project, bucket):
    push(project, bucket)  # config.yaml has no output, so ./output
    with pytest.raises(ValueError, match="lose them"):
        job.job_spec(bucket)
    assert (
        job.job_spec(bucket, output="s3://bucket/output").output == "s3://bucket/output"
    )


def test_job_spec_of_a_mounted_project_keeps_a_local_output(project):
    spec = job.job_spec(str(project))
    assert (spec.tasks, spec.parallelism, spec.cpus, spec.memory) == (1, 1, 1, None)


def test_job_spec_no_project(bucket):
    with pytest.raises(FileNotFoundError):
        job.job_spec(bucket)


def test_manifest_is_an_indexed_job_of_workers(pushed):
    spec = job.job_spec(
        pushed,
        secrets=["creds"],
        service_account="stilt",
        namespace="runs",
        volumes=[job.Volume.parse("hrrr:/met")],
    )
    manifest = job.manifest(spec)
    assert manifest["metadata"]["generateName"] == "stilt-my-project-"
    assert manifest["metadata"]["namespace"] == "runs"
    body = manifest["spec"]
    assert (body["completionMode"], body["completions"], body["parallelism"]) == (
        "Indexed",
        12,
        5,
    )
    pod = body["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["serviceAccountName"] == "stilt"
    (container,) = pod["containers"]
    assert container["args"] == [
        "pystiltctl",
        "worker",
        pushed,
        "--tasks",
        "12",
        "--cpus",
        "4",
        "--workdir",
        "/scratch/project",
    ]
    assert container["resources"] == {
        "requests": {"cpu": "4", "memory": "8Gi"},
        "limits": {"memory": "8Gi"},
    }
    assert container["envFrom"] == [{"secretRef": {"name": "creds"}}]
    assert {"name": "volume-0", "mountPath": "/met"} in container["volumeMounts"]
    assert {"name": "volume-0", "persistentVolumeClaim": {"claimName": "hrrr"}} in pod[
        "volumes"
    ]


def test_manifest_retries_from_exit_codes(pushed):
    rules = job.manifest(job.job_spec(pushed))["spec"]["podFailurePolicy"]["rules"]
    by_code = {
        r["onExitCodes"]["values"][0]: r["action"] for r in rules if "onExitCodes" in r
    }
    assert by_code == {2: "FailJob", 1: "FailIndex"}
    assert {
        "action": "Ignore",
        "onPodConditions": [{"type": "DisruptionTarget"}],
    } in rules


def test_manifest_yaml_has_no_anchors(pushed):
    text = job.to_yaml(job.manifest(job.job_spec(pushed)))
    assert "&id" not in text
    assert yaml.safe_load(text)["kind"] == "Job"


@pytest.mark.parametrize(
    ("version", "tag"),
    [("0.2.0", "0.2.0"), ("0.2.1.dev3+g1a2b3c4", "main"), ("0+unknown", "main")],
)
def test_default_image(version, tag):
    assert job.default_image(version) == f"ghcr.io/jmineau/pystiltctl:{tag}"


@pytest.mark.parametrize(
    ("mem", "quantity"),
    [
        ("8G", "8Gi"),
        ("500m", "500Mi"),
        ("4000", "4000Mi"),
        ("2GB", "2Gi"),
        ("1.5Gi", "1.5Gi"),
    ],
)
def test_kubernetes_memory(mem, quantity):
    assert job.kubernetes_memory(mem) == quantity


@pytest.mark.parametrize(
    ("name", "value"),
    [("my_project", "my-project"), ("__", "project"), ("a" * 50, "a" * 40)],
)
def test_label_value(name, value):
    assert job.label_value(name) == value


@pytest.mark.parametrize("text", ["hrrr", "hrrr:met", ":/met"])
def test_volume_needs_claim_and_absolute_path(text):
    with pytest.raises(ValueError, match="CLAIM:/path"):
        job.Volume.parse(text)


def test_create_runs_kubectl(monkeypatch, pushed):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs["input"]))
        return subprocess.CompletedProcess(
            args, 0, stdout="job.batch/stilt-my-project-x7k2p\n", stderr=""
        )

    monkeypatch.setattr(job.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(job.subprocess, "run", run)
    manifest = job.manifest(job.job_spec(pushed))
    assert job.create(manifest) == "stilt-my-project-x7k2p"
    ((args, text),) = calls
    assert args == ["/usr/bin/kubectl", "create", "-f", "-", "-o", "name"]
    assert yaml.safe_load(text) == manifest


def test_create_reports_kubectls_error(monkeypatch, pushed):
    monkeypatch.setattr(job.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(
        job.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="", stderr="forbidden"
        ),
    )
    with pytest.raises(RuntimeError, match="forbidden"):
        job.create(job.manifest(job.job_spec(pushed)))


def test_cli_submit_dry_run(pushed):
    result = CliRunner().invoke(app, ["submit", pushed, "--dry-run", "--tasks", "4"])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output)["spec"]["completions"] == 4


def test_cli_submit_without_kubectl(monkeypatch, pushed):
    monkeypatch.setattr(job.shutil, "which", lambda name: None)
    result = CliRunner().invoke(app, ["submit", pushed])
    assert result.exit_code == 2
    assert "--dry-run" in result.output


def test_cli_submit_creates_the_job(monkeypatch, pushed):
    monkeypatch.setattr(job, "create", lambda manifest: "stilt-my-project-x7k2p")
    result = CliRunner().invoke(app, ["submit", pushed])
    assert result.exit_code == 0, result.output
    assert "stilt-my-project-x7k2p  (12 tasks, 5 at once)" in result.output


def test_cli_submit_bad_volume(pushed):
    result = CliRunner().invoke(
        app, ["submit", pushed, "--volume", "hrrr", "--dry-run"]
    )
    assert result.exit_code == 2
    assert "CLAIM:/path" in result.output
