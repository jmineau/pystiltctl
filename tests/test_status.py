"""Where a project's runs stand."""

import json
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from pystiltctl import job, status
from pystiltctl.cli import app
from pystiltctl.inputs import project_name, push

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


def annotated(item, project):
    """Return a Job as `pystiltctl submit` made it for *project*."""
    metadata = {**item["metadata"], "annotations": {job.PROJECT_ANNOTATION: project}}
    return {**item, "metadata": metadata}


@pytest.fixture
def cluster(monkeypatch):
    """
    Stand in for kubectl.

    `get jobs -l key=value` answers with the Jobs in ``cluster.jobs`` that
    carry the label, as the API server would.
    """
    cluster = SimpleNamespace(jobs=[], calls=[])
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] != "/usr/bin/kubectl":
            return real_run(args, **kwargs)
        cluster.calls.append(args)
        key, _, value = args[args.index("-l") + 1].partition("=")
        items = [
            item
            for item in cluster.jobs
            if item["metadata"].get("labels", {}).get(key) == value
        ]
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps({"items": items}), stderr=""
        )

    monkeypatch.setattr(status.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(status.subprocess, "run", run)
    return cluster


def submitted(project, name):
    """Return the Job `pystiltctl submit` would create for *project*, named *name*."""
    spec = job.JobSpec(project, project_name(project), tasks=2, parallelism=2)
    manifest = job.manifest(spec)
    metadata = {k: v for k, v in manifest["metadata"].items() if k != "generateName"}
    return {**manifest, "metadata": {**metadata, "name": name}}


@pytest.fixture
def kubectl(cluster, bucket):
    """Answer `kubectl get jobs` with two Jobs of ``bucket``'s project."""
    labels = {job.PROJECT_LABEL: "my-project"}
    for item in (COMPLETE, FAILED):
        metadata = {**item["metadata"], "labels": labels}
        cluster.jobs.append(annotated({**item, "metadata": metadata}, bucket))
    return cluster.calls


def test_project_jobs_by_label_oldest_first(kubectl, bucket):
    jobs, unclaimed = status.project_jobs(bucket, namespace="runs")
    assert [j["name"] for j in jobs] == [
        "stilt-my-project-x7k2p",
        "stilt-my-project-a1b2c",
    ]
    assert unclaimed == []
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


def test_project_jobs_of_projects_with_one_folder_name(cluster):
    """Two projects, one label: each sees only its own Jobs (#12)."""
    a, b = "s3://lab-a/projects/winter", "s3://lab-b/runs/2025/winter"
    cluster.jobs += [submitted(a, "stilt-winter-0"), submitted(b, "stilt-winter-1")]
    assert (
        cluster.jobs[0]["metadata"]["labels"] == cluster.jobs[1]["metadata"]["labels"]
    )
    for project, name in [(a, "stilt-winter-0"), (b, "stilt-winter-1")]:
        jobs, unclaimed = status.project_jobs(project)
        assert [j["name"] for j in jobs] == [name]
        assert unclaimed == []


def test_project_jobs_of_labels_that_differ_only_by_case(cluster):
    """`SLV_2015` and `slv-2015` get the label `slv-2015`, so check the URL too."""
    a, b = "s3://lab/projects/SLV_2015", "s3://lab/projects/slv-2015"
    cluster.jobs += [submitted(a, "stilt-slv-2015-a"), submitted(b, "stilt-slv-2015-b")]
    assert [j["name"] for j in status.project_jobs(a)[0]] == ["stilt-slv-2015-a"]
    assert [j["name"] for j in status.project_jobs(b)[0]] == ["stilt-slv-2015-b"]


@pytest.mark.parametrize(
    ("submit_as", "status_of"),
    [
        ("s3://lab/projects/winter/", "s3://lab/projects/winter"),
        ("s3://lab/projects/winter", "s3://lab/projects/winter//"),
        ("S3://lab/projects/winter", "s3://lab/projects/winter"),
        ("/data/winter/", "/data/winter"),
        ("file:///data/winter", "/data/winter"),
    ],
)
def test_project_jobs_spelling_of_the_project_does_not_matter(
    cluster, submit_as, status_of
):
    cluster.jobs.append(submitted(submit_as, "stilt-winter-0"))
    assert [j["name"] for j in status.project_jobs(status_of)[0]] == ["stilt-winter-0"]


@pytest.fixture
def folders(tmp_path):
    """A project folder ``winter`` and a symlink to the folder that holds it."""
    real = tmp_path / "data"
    (real / "winter").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    return SimpleNamespace(
        root=tmp_path.resolve(),
        winter=str(real.resolve() / "winter"),
        dotdot=f"{real}/../data/winter",
        symlink=str(tmp_path / "link" / "winter"),
    )


@pytest.mark.parametrize("submit_as", ["winter", "dotdot", "symlink"])
@pytest.mark.parametrize("status_of", ["winter", "dotdot", "symlink"])
def test_project_jobs_of_a_folder_spelled_through_dotdot_or_a_symlink(
    cluster, folders, submit_as, status_of
):
    """One folder, three spellings: each finds the Job the others submitted (#18)."""
    cluster.jobs.append(submitted(getattr(folders, submit_as), "stilt-winter-0"))
    jobs, unclaimed = status.project_jobs(getattr(folders, status_of))
    assert [j["name"] for j in jobs] == ["stilt-winter-0"]
    assert unclaimed == []


def test_project_jobs_finds_a_job_whose_annotation_is_not_normalized(cluster, folders):
    """A Job made before `submit` normalized the annotation is still found."""
    old = submitted(folders.winter, "stilt-winter-old")
    old["metadata"]["annotations"][job.PROJECT_ANNOTATION] = folders.dotdot
    cluster.jobs.append(old)
    assert [j["name"] for j in status.project_jobs(folders.symlink)[0]] == [
        "stilt-winter-old"
    ]


@pytest.mark.parametrize(
    ("typed", "written"),
    [
        ("dotdot", "winter"),
        ("symlink", "winter"),
        ("winter", "winter"),
    ],
)
def test_manifest_annotates_the_project_in_its_normalized_form(folders, typed, written):
    manifest = submitted(getattr(folders, typed), "stilt-winter-0")
    assert manifest["metadata"]["annotations"] == {
        job.PROJECT_ANNOTATION: getattr(folders, written)
    }


@pytest.mark.parametrize(
    ("typed", "written"),
    [
        ("S3://lab/projects/winter/", "s3://lab/projects/winter"),
        ("file:///data/winter/", "/data/winter"),
    ],
)
def test_manifest_annotates_a_url_in_its_normalized_form(typed, written):
    manifest = submitted(typed, "stilt-winter-0")
    assert manifest["metadata"]["annotations"] == {job.PROJECT_ANNOTATION: written}


def test_manifest_keeps_the_project_as_typed_for_the_pods(folders):
    """The pods open the path as given: a link here may not exist in the pod."""
    spec = job.JobSpec(folders.symlink, project_name(folders.symlink), 2, 2)
    assert folders.symlink in job.worker_args(spec)


@pytest.mark.parametrize("spelling", [".", "./", "../winter", "dotdot", "symlink"])
def test_project_name_of_a_local_folder_is_the_folders(folders, monkeypatch, spelling):
    """`.` is the folder named `winter`, not a project called `.` (#20)."""
    monkeypatch.chdir(folders.winter)
    assert project_name(getattr(folders, spelling, spelling)) == "winter"


def test_project_name_of_a_url_is_the_last_part():
    assert project_name("s3://lab/projects/winter/") == "winter"
    assert project_name("file:///data/winter/") == "winter"


@pytest.mark.parametrize("spelling", [".", "./"])
def test_cli_status_of_dot_lists_the_projects_jobs(
    project, cluster, monkeypatch, spelling
):
    """`pystiltctl status .` inside the folder finds the Jobs submitted by path (#20)."""
    cluster.jobs.append(submitted(str(project), "stilt-my-project-0"))
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["status", spelling])
    assert result.exit_code == 0, result.output
    assert "stilt-my-project-0" in result.output
    assert "Jobs: none on the cluster" not in result.output


def test_project_jobs_leaves_out_a_job_with_no_annotation(cluster):
    """An older Job cannot be told from another project's: skip it, and say so."""
    project = "s3://lab-a/projects/winter"
    old = submitted(project, "stilt-winter-old")
    del old["metadata"]["annotations"]
    cluster.jobs += [old, submitted(project, "stilt-winter-1")]
    jobs, unclaimed = status.project_jobs(project)
    assert [j["name"] for j in jobs] == ["stilt-winter-1"]
    assert unclaimed == ["stilt-winter-old"]


def test_project_jobs_without_kubectl(monkeypatch):
    monkeypatch.setattr(status.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="kubectl"):
        status.project_jobs("my_project")


def test_simulations_of_a_pushed_project(project, bucket):
    push(project, bucket)
    data = status.simulations(bucket, output="memory://results/output")
    assert data["project"] == bucket
    assert data["simulations"]["total"] == 0


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
    text = status.report(data, [], ["stilt-old-x1"])
    assert "Not counted: stilt-old-x1. They carry this project's label" in text
    assert "Not counted" not in status.report(data, [])


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
    assert data["jobs_not_counted"] == []


def test_cli_status_says_which_jobs_it_did_not_count(project, bucket, cluster):
    push(project, bucket)
    old = submitted(bucket, "stilt-my-project-old")
    del old["metadata"]["annotations"]
    cluster.jobs += [old, submitted("s3://other/my_project", "stilt-my-project-else")]
    args = ["status", bucket, "--output", "memory://results/output"]
    text = CliRunner().invoke(app, args)
    assert text.exit_code == 0, text.output
    assert "Jobs: none on the cluster" in text.output
    assert "Not counted: stilt-my-project-old." in text.output
    assert "stilt-my-project-else" not in text.output
    data = json.loads(CliRunner().invoke(app, [*args, "--json"]).output)
    assert data["jobs"] == []
    assert data["jobs_not_counted"] == ["stilt-my-project-old"]


def test_cli_status_no_project(bucket):
    result = CliRunner().invoke(app, ["status", bucket])
    assert result.exit_code == 2


def test_simulations_reports_stilts_error(project):
    (project / "receptors.csv").write_text("when,where\n")
    with pytest.raises(RuntimeError, match="Receptor table must contain columns"):
        status.simulations(str(project))
