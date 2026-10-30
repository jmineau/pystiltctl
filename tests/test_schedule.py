"""Running a project every hour, for fixed sites."""

from datetime import datetime

import fsspec
import pytest
import yaml
from typer.testing import CliRunner

from pystiltctl import job, schedule
from pystiltctl.cli import app
from pystiltctl.inputs import pull, push

NOW = datetime(2026, 10, 29, 18, 15)


def test_due_hours_are_the_last_day_behind_the_delay():
    hours = schedule.due_hours(NOW, delay=8)
    assert len(hours) == schedule.WINDOW_HOURS
    assert hours[-1] == datetime(2026, 10, 29, 10)
    assert hours[0] == datetime(2026, 10, 28, 11)
    assert schedule.due_hours(NOW, delay=0)[-1] == datetime(2026, 10, 29, 18)


def test_read_sites(sited):
    sites = schedule.read_sites(sited)
    assert sites == [
        {
            "longitude": -111.8476,
            "latitude": 40.7663,
            "altitude": 35.0,
            "altitude_ref": "agl",
        },
        {"longitude": -112.1155, "latitude": 40.5385, "altitude": 5.0},
    ]


def test_read_sites_without_the_file(project):
    with pytest.raises(FileNotFoundError, match="has no sites.csv"):
        schedule.read_sites(project)


@pytest.mark.parametrize(
    "text",
    [
        "longitude,latitude\n-111.8,40.7\n",
        "longitude,latitude,altitude\n",
        "longitude,latitude,altitude\nwest,40.7,5\n",
    ],
)
def test_read_sites_refuses_a_bad_file(project, text):
    (project / "sites.csv").write_text(text)
    with pytest.raises(ValueError, match="sites.csv"):
        schedule.read_sites(project)


def test_hourly_receptors_are_each_site_each_hour(sited):
    hours = schedule.due_hours(NOW)[-2:]
    receptors = schedule.hourly_receptors(schedule.read_sites(sited), hours)
    assert [str(r.id) for r in receptors] == [
        "202610290900_-111.8476_40.7663_35",
        "202610290900_-112.1155_40.5385_5",
        "202610291000_-111.8476_40.7663_35",
        "202610291000_-112.1155_40.5385_5",
    ]


def test_push_and_pull_carry_sites(sited, bucket, tmp_path):
    assert "sites.csv" in push(sited, bucket)
    assert (pull(bucket, tmp_path / "pod") / "sites.csv").is_file()


def receptor_rows(url):
    """The data lines of receptors.csv at *url*."""
    fs, root = fsspec.core.url_to_fs(url)
    return fs.cat_file(f"{root}/receptors.csv").decode().splitlines()[1:]


def test_tick_adds_the_due_hours_and_runs_the_last_day(sited, bucket, tmp_path, execs):
    push(sited, bucket)
    schedule.tick(bucket, now=NOW, cpus=2, workdir=tmp_path / "pod")
    assert len(receptor_rows(bucket)) == 2 * schedule.WINDOW_HOURS
    directory = tmp_path / "pod" / "my_project"
    ids = (tmp_path / "pod" / "my_project-due.txt").read_text().split()
    assert len(ids) == 2 * schedule.WINDOW_HOURS
    assert ids[-1] == "202610291000_-112.1155_40.5385_5"
    (command,) = execs
    assert command[3:] == [
        "run",
        str(directory),
        "--receptors",
        str(tmp_path / "pod" / "my_project-due.txt"),
        "--cpus",
        "2",
    ]


def test_the_next_tick_adds_only_the_new_hour(sited, bucket, tmp_path, execs):
    push(sited, bucket)
    schedule.tick(bucket, now=NOW, workdir=tmp_path / "first")
    schedule.tick(bucket, now=NOW.replace(hour=19), workdir=tmp_path / "second")
    rows = receptor_rows(bucket)
    assert len(rows) == 2 * (schedule.WINDOW_HOURS + 1)
    ids = (tmp_path / "second" / "my_project-due.txt").read_text().split()
    assert ids[0].startswith("202610281200") and ids[-1].startswith("202610291100")


def test_tick_on_a_mounted_project_changes_it_in_place(sited, execs):
    schedule.tick(str(sited), now=NOW, delay=0, workdir=sited.parent / "work")
    rows = (sited / "receptors.csv").read_text().splitlines()[1:]
    assert len(rows) == 2 * schedule.WINDOW_HOURS
    assert rows[-1].split(",")[0] == "2026-10-29 18:00:00"


def test_tick_needs_sites(project, execs):
    with pytest.raises(FileNotFoundError, match="sites.csv"):
        schedule.tick(str(project), now=NOW)
    assert execs == []


def test_cron_job_runs_tick_hourly_one_at_a_time(sited, bucket):
    (sited / "config.yaml").write_text(
        (sited / "config.yaml").read_text() + "output: s3://b/output\n"
    )
    push(sited, bucket)
    spec = job.job_spec(bucket, cpus=2, volumes=[job.Volume.parse("met:/met")])
    cron = job.cron_job(spec, delay=8)
    assert (cron["kind"], cron["metadata"]["name"]) == ("CronJob", "stilt-my-project")
    body = cron["spec"]
    assert (body["schedule"], body["concurrencyPolicy"]) == ("15 * * * *", "Forbid")
    run = body["jobTemplate"]["spec"]
    assert (run["completions"], run["parallelism"]) == (1, 1)
    (container,) = run["template"]["spec"]["containers"]
    assert container["args"] == [
        "pystiltctl",
        "tick",
        bucket,
        "--delay",
        "8",
        "--cpus",
        "2",
        "--workdir",
        "/scratch/project",
    ]
    assert (
        body["jobTemplate"]["metadata"]["labels"]["pystiltctl/project"] == "my-project"
    )
    assert {"name": "volume-0", "mountPath": "/met"} in container["volumeMounts"]


def test_cli_schedule_dry_run(sited):
    result = CliRunner().invoke(
        app, ["schedule", str(sited), "--dry-run", "--delay", "6"]
    )
    assert result.exit_code == 0, result.output
    cron = yaml.safe_load(result.output)
    assert cron["kind"] == "CronJob"
    assert (
        "6"
        in cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0][
            "args"
        ]
    )


def test_cli_schedule_applies_it(sited, monkeypatch):
    applied = []
    monkeypatch.setattr(
        job, "apply", lambda manifest: applied.append(manifest) or "stilt-my-project"
    )
    result = CliRunner().invoke(app, ["schedule", str(sited)])
    assert result.exit_code == 0, result.output
    assert (
        "Scheduled: stilt-my-project  (every hour, 2 sites, 8 h behind)"
        in result.output
    )
    assert applied[0]["kind"] == "CronJob"


def test_cli_schedule_needs_sites(project):
    result = CliRunner().invoke(app, ["schedule", str(project), "--dry-run"])
    assert result.exit_code == 2
    assert "sites.csv" in result.output


def test_cli_tick_with_a_time(sited, bucket, tmp_path, execs):
    push(sited, bucket)
    args = [
        "tick",
        bucket,
        "--now",
        "2021-01-15T12:00",
        "--delay",
        "8",
        "--workdir",
        str(tmp_path),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert receptor_rows(bucket)[-1].split(",")[0] == "2021-01-15 04:00:00"
    assert "--receptors" in execs[0]
