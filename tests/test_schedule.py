"""Running a project every hour, for fixed sites."""

import threading
from datetime import UTC, datetime, timedelta

import fsspec
import pytest
import yaml
from typer.testing import CliRunner

from pystiltctl import inputs, job, schedule
from pystiltctl.cli import app
from pystiltctl.inputs import pull, push

NOW = datetime(2026, 10, 29, 18, 15)


def test_due_hours_are_the_last_day_behind_the_delay():
    hours = schedule.due_hours(NOW, delay=8)
    assert len(hours) == schedule.WINDOW_HOURS
    assert hours[-1] == datetime(2026, 10, 29, 10)
    assert hours[0] == datetime(2026, 10, 28, 11)
    assert schedule.due_hours(NOW, delay=0)[-1] == datetime(2026, 10, 29, 18)


def test_due_hours_without_since_leaves_a_gap_after_an_outage():
    """The issue's own reproduction (#26): a 36 h outage, no `since`."""
    before = schedule.due_hours(datetime(2021, 1, 15, 12, 0), delay=8)
    after = schedule.due_hours(datetime(2021, 1, 17, 0, 0), delay=8)
    assert before[-1] == datetime(2021, 1, 15, 4)
    assert after[0] == datetime(2021, 1, 15, 17)
    # Every hour strictly between the two windows is never added by either tick.
    assert after[0] - before[-1] > timedelta(hours=1)


def test_due_hours_with_since_closes_the_gap():
    before = schedule.due_hours(datetime(2021, 1, 15, 12, 0), delay=8)
    after = schedule.due_hours(datetime(2021, 1, 17, 0, 0), delay=8, since=before[-1])
    assert after[0] == before[-1] + timedelta(hours=1)  # no hour skipped


def test_due_hours_ignores_since_already_inside_the_window():
    hours = schedule.due_hours(NOW, delay=8)
    since = hours[-2]  # well within the normal window: no gap to close
    assert schedule.due_hours(NOW, delay=8, since=since) == hours


def test_due_hours_ignores_since_exactly_at_the_windows_edge():
    hours = schedule.due_hours(NOW, delay=8)
    since = hours[0] - timedelta(hours=1)  # the hour right before the window: no gap
    assert schedule.due_hours(NOW, delay=8, since=since) == hours


def test_due_hours_caps_how_far_back_it_fills():
    normal = schedule.due_hours(NOW, delay=8)
    since = normal[0] - timedelta(days=365)  # far older than any realistic outage
    hours = schedule.due_hours(NOW, delay=8, since=since)
    assert hours[0] == normal[0] - timedelta(hours=schedule.MAX_BACKFILL_HOURS)
    assert hours[-1] == normal[-1]


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


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("longitude,latitude,altitude\n-111.8,40.7\n", r"line 2: no altitude\."),
        ("longitude,latitude,altitude\n-111.8,40.7,\n", r"line 2: no altitude\."),
        ("longitude,latitude,altitude\n-111.8,nan,35\n", r"line 2: .*not all numbers"),
        (
            "longitude,latitude,altitude\n-111.8,40.7,35\n-111.8,95,35\n",
            r"line 3: latitude must be within \[-90, 90\]",
        ),
        ("longitude,latitude,altitude\n-211.8,40.7,35\n", r"line 2: longitude must"),
        ("longitude,latitude,altitude\n-111.8,40.7,-5\n", r"line 2: .*altitudes must"),
        (
            "longitude,latitude,altitude,altitude_ref\n-111.8,40.7,35,above\n",
            r"line 2: altitude_ref: .*'agl' or 'msl'",
        ),
    ],
)
def test_read_sites_names_the_row_it_refuses(project, text, message):
    (project / "sites.csv").write_text(text)
    with pytest.raises(ValueError, match=f"sites.csv {message}"):
        schedule.read_sites(project)


def test_read_sites_accepts_a_file_saved_with_a_bom(project):
    """Excel's "CSV UTF-8" puts a byte order mark before the first column name."""
    (project / "sites.csv").write_text(
        "longitude,latitude,altitude\n-111.8,40.7,35\n", encoding="utf-8-sig"
    )
    assert schedule.read_sites(project) == [
        {"longitude": -111.8, "latitude": 40.7, "altitude": 35.0}
    ]


@pytest.mark.parametrize("command", ["schedule", "tick"])
def test_cli_refuses_a_bad_site_with_its_line(project, execs, command):
    (project / "sites.csv").write_text(
        "longitude,latitude,altitude\n-111.8,40.7,35\n-111.8,95,35\n"
    )
    args = [command, str(project), *(["--dry-run"] if command == "schedule" else [])]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "sites.csv line 3: latitude must be within [-90, 90]" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert execs == []


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


def test_tick_fills_the_gap_left_by_an_outage(sited, bucket, tmp_path, execs):
    """A tick after an outage longer than the window adds and runs the gap too (#26)."""
    push(sited, bucket)
    schedule.tick(bucket, now=NOW, workdir=tmp_path / "first")
    schedule.tick(bucket, now=NOW + timedelta(hours=36), workdir=tmp_path / "second")
    rows = receptor_rows(bucket)
    hours = sorted({row.split(",")[0] for row in rows})
    # 2026-10-28 11:00 through 2026-10-30 22:00: every hour, no gap, each twice (two sites).
    assert hours[0] == "2026-10-28 11:00:00"
    assert hours[-1] == "2026-10-30 22:00:00"
    assert len(hours) == 60
    assert len(rows) == 2 * 60
    # The gap's hours were not just added: they ran in this tick too.
    ids = (tmp_path / "second" / "my_project-due.txt").read_text().split()
    assert len(ids) == 2 * 36
    assert ids[0] == "202610291100_-111.8476_40.7663_35"


def test_the_first_tick_backfills_a_project_that_already_has_old_rows(
    sited, bucket, tmp_path, execs
):
    """Old rows count as the schedule's last hour: the first tick reaches back a week (#26)."""
    (sited / "receptors.csv").write_text(
        "time,longitude,latitude,altitude\n"
        "2021-01-15 03:00:00,-111.85,40.77,5\n"
        "2021-01-15 04:00:00,-111.85,40.77,5\n"
    )
    push(sited, bucket)
    schedule.tick(bucket, now=NOW, workdir=tmp_path / "pod")
    rows = receptor_rows(bucket)
    hours = sorted({row.split(",")[0] for row in rows if "2021" not in row})
    # The 2 old rows stay; each of the 2 sites gets the normal day plus
    # MAX_BACKFILL_HOURS before it, no more.
    assert len(hours) == schedule.WINDOW_HOURS + schedule.MAX_BACKFILL_HOURS
    assert len(rows) == 2 + 2 * len(hours)
    assert hours[0] == "2026-10-21 11:00:00"  # 7 days before the normal day's start
    assert hours[-1] == "2026-10-29 10:00:00"


def test_overlapping_ticks_keep_both_ticks_receptors(
    sited, bucket, tmp_path, monkeypatch, execs
):
    """A backfill tick that starts while the hourly tick is still writing (#24)."""
    push(sited, bucket)
    monkeypatch.setattr(inputs, "LOCK_POLL_SECONDS", 0.01)
    real_put = schedule.put
    paused, resume = threading.Event(), threading.Event()

    def slow_put(*args):
        # The first tick to write stops between its pull and its put.
        if not paused.is_set():
            paused.set()
            resume.wait(10)
        real_put(*args)

    monkeypatch.setattr(schedule, "put", slow_put)
    errors = []

    def tick(now, workdir):
        try:
            schedule.tick(bucket, now=now, workdir=tmp_path / workdir)
        except Exception as error:
            errors.append(error)

    hourly = threading.Thread(target=tick, args=(NOW, "hourly"))
    hourly.start()
    assert paused.wait(10)
    backfill = threading.Thread(target=tick, args=(NOW.replace(day=1), "backfill"))
    backfill.start()
    backfill.join(0.5)
    assert backfill.is_alive()  # it waits for the hourly tick's lock
    resume.set()
    hourly.join(10)
    backfill.join(10)
    assert errors == []
    assert len(receptor_rows(bucket)) == 2 * 2 * schedule.WINDOW_HOURS
    fs, root = fsspec.core.url_to_fs(bucket)
    assert not fs.exists(f"{root}/.receptors.csv.lock")


def test_cli_tick_says_it_cannot_lock(sited, bucket, tmp_path, monkeypatch):
    push(sited, bucket)

    def refuse(*args):
        raise PermissionError("access denied")

    monkeypatch.setattr(inputs, "_acquire", refuse)
    args = ["tick", bucket, "--now", "2021-01-15T12:00", "--workdir", str(tmp_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 3
    assert "cannot lock receptors.csv at memory://" in result.output
    assert "access denied" in result.output


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
    assert cron["kind"] == "CronJob"
    assert cron["metadata"]["name"] == job.cron_job_name(bucket)
    assert cron["metadata"]["name"].startswith("stilt-my-project-")
    body = cron["spec"]
    assert (body["schedule"], body["concurrencyPolicy"]) == ("15 * * * *", "Forbid")
    run = body["jobTemplate"]["spec"]
    assert (run["completions"], run["parallelism"]) == (1, 1)
    assert run["activeDeadlineSeconds"] == job.TICK_DEADLINE
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


def test_cli_tick_says_which_way_a_failed_copy_went(
    sited, bucket, tmp_path, monkeypatch
):
    """A tick that cannot write receptors.csv back must not blame the inputs."""
    push(sited, bucket)

    def refuse(*args):
        raise PermissionError("access denied")

    monkeypatch.setattr(schedule, "put", refuse)
    args = ["tick", bucket, "--now", "2021-01-15T12:00", "--workdir", str(tmp_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 3
    assert "cannot copy receptors.csv back to memory://" in result.output
    assert "access denied" in result.output
    assert "inputs" not in result.output


def test_cli_tick_says_which_way_a_failed_pull_went(bucket, tmp_path, monkeypatch):
    def refuse(*args):
        raise PermissionError("access denied")

    monkeypatch.setattr(schedule, "project_directory", refuse)
    result = CliRunner().invoke(app, ["tick", bucket, "--workdir", str(tmp_path)])
    assert result.exit_code == 3
    assert "cannot copy the project's inputs from memory://" in result.output


def test_tick_with_render_runs_stilt_then_draws(
    sited, bucket, tmp_path, monkeypatch, execs
):
    ran, drew = [], []
    monkeypatch.setattr(schedule, "run", lambda command: ran.append(command) or 1)
    monkeypatch.setattr(
        schedule.renderer,
        "render",
        lambda directory, url, ids, variant=None, output=None: drew.append(
            (url, len(ids), variant)
        ),
    )
    push(sited, bucket)
    code = schedule.tick(
        bucket, now=NOW, render="memory://site/footprints", workdir=tmp_path
    )
    assert code == 1
    assert execs == []
    assert "--receptors" in ran[0]
    assert drew == [("memory://site/footprints", 2 * schedule.WINDOW_HOURS, "hrrr")]


@pytest.fixture
def two_variants(sited):
    """The sited project with two variants, which a render cannot choose between."""
    config = sited / "config.yaml"
    config.write_text(
        config.read_text()
        .replace(
            "mets:\n",
            "mets:\n  nam: {directory: /met2, file_format: x, file_tres: 6h}\n",
        )
        .replace(
            "variants:\n  hrrr: {}\n", "variants:\n  hrrr: {}\n  nam: {met: nam}\n"
        )
    )
    return sited


def test_tick_with_render_refuses_several_variants_before_running(
    two_variants, tmp_path, monkeypatch, execs
):
    ran = []
    monkeypatch.setattr(schedule, "run", lambda command: ran.append(command) or 0)
    before = (two_variants / "receptors.csv").read_text()
    args = [
        "tick",
        str(two_variants),
        "--now",
        "2021-01-15T12:15",
        "--render",
        "memory://s/f",
    ]
    result = CliRunner().invoke(app, [*args, "--workdir", str(tmp_path)])
    assert result.exit_code == 2
    assert "2 variants" in result.output
    assert ran == [] and execs == []
    assert (two_variants / "receptors.csv").read_text() == before


@pytest.mark.parametrize(
    ("stilt_code", "problem", "exit_code"),
    [
        (0, None, 0),
        (1, None, 1),
        (3, None, 3),
        (1, OSError("503"), 1),  # stilt's code, not a retry
        (3, OSError("503"), 3),
        (1, RuntimeError("boom"), 1),
        (0, OSError("503"), 3),  # only the drawing failed: tried again
        (0, RuntimeError("boom"), 1),  # a bug: not retried
    ],
)
def test_tick_with_render_exits_with_stilts_code(
    sited, tmp_path, monkeypatch, caplog, stilt_code, problem, exit_code
):
    """A drawing that fails must not replace the code of the run it follows."""
    monkeypatch.setattr(schedule, "run", lambda command: stilt_code)

    def draw(*args, **kwargs):
        if problem:
            raise problem
        return []

    monkeypatch.setattr(schedule.renderer, "render", draw)
    args = ["tick", str(sited), "--now", "2021-01-15T12:15", "--render", "memory://s/f"]
    result = CliRunner().invoke(app, [*args, "--workdir", str(tmp_path)])
    assert result.exit_code == exit_code
    if problem and stilt_code:
        # Not hidden: the pod's log says the drawing failed.
        assert "Could not draw the footprints to memory://s/f" in caplog.text
        assert str(problem) in caplog.text
    if problem and not stilt_code and isinstance(problem, OSError):
        assert "cannot draw the footprints to memory://s/f: 503" in result.output


def test_cron_job_passes_render_on(sited):
    spec = job.job_spec(str(sited))
    args = job.cron_job(spec, delay=8, render="s3://site/f")["spec"]["jobTemplate"][
        "spec"
    ]["template"]["spec"]["containers"][0]["args"]
    assert args[-2:] == ["--render", "s3://site/f"]


def test_tick_refuses_a_now_later_than_the_clock(sited, bucket, tmp_path, execs):
    """A year typo (2062 for 2026) would add future hours and stop gap closing (#55)."""
    push(sited, bucket)
    with pytest.raises(ValueError, match="later than the clock"):
        schedule.tick(bucket, now=datetime(2062, 10, 1), workdir=tmp_path / "pod")
    assert receptor_rows(bucket) == []
    assert execs == []


def test_cli_tick_with_a_future_now_exits_2(sited, bucket, tmp_path, execs):
    push(sited, bucket)
    args = ["tick", bucket, "--now", "2062-10-01T00:00", "--workdir", str(tmp_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "later than the clock" in result.output


def test_rows_in_the_future_do_not_stop_gap_closing(sited, bucket, tmp_path, execs):
    """Rows already past the clock (added before #55) are not taken as the last hour."""
    push(sited, bucket)
    schedule.tick(bucket, now=NOW, workdir=tmp_path / "first")
    fs, root = fsspec.core.url_to_fs(bucket)
    path = f"{root}/receptors.csv"
    future = "2062-09-30 16:00:00,-111.8476,40.7663,35\n"
    fs.pipe_file(path, fs.cat_file(path) + future.encode())
    schedule.tick(bucket, now=NOW + timedelta(hours=36), workdir=tmp_path / "second")
    hours = sorted({row.split(",")[0] for row in receptor_rows(bucket)} - {future[:19]})
    # The same 60 hours as an outage with no future row: the gap is closed.
    assert (hours[0], hours[-1], len(hours)) == (
        "2026-10-28 11:00:00",
        "2026-10-30 22:00:00",
        60,
    )


def test_tick_converts_a_now_with_a_time_zone_to_utc(sited, bucket, tmp_path, execs):
    from zoneinfo import ZoneInfo

    push(sited, bucket)
    denver = NOW.replace(tzinfo=UTC).astimezone(ZoneInfo("America/Denver"))
    schedule.tick(bucket, now=denver, workdir=tmp_path / "first")
    schedule.tick(bucket, now=denver, workdir=tmp_path / "second")
    hours = sorted({row.split(",")[0] for row in receptor_rows(bucket)})
    assert (hours[0], hours[-1]) == ("2026-10-28 11:00:00", "2026-10-29 10:00:00")


@pytest.fixture
def sited_on_bucket(sited, bucket):
    """The sited project pushed to a bucket, with its output on a bucket."""
    (sited / "config.yaml").write_text(
        (sited / "config.yaml").read_text() + "output: s3://b/output\n"
    )
    push(sited, bucket)
    return bucket


@pytest.mark.parametrize("render", ["footprints", "/data/footprints", "s3:/site/f"])
def test_cli_schedule_refuses_a_render_path_for_a_bucket_project(
    sited_on_bucket, render
):
    args = ["schedule", sited_on_bucket, "--render", render, "--dry-run"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "is a path" in result.output


def test_cli_schedule_refuses_an_empty_render(sited):
    result = CliRunner().invoke(
        app, ["schedule", str(sited), "--render", "", "--dry-run"]
    )
    assert result.exit_code == 2
    assert "--render is empty" in result.output


def test_cron_job_takes_a_render_path_for_a_project_on_a_volume(sited):
    spec = job.job_spec(str(sited))
    args = job.cron_job(spec, delay=8, render="/data/footprints")["spec"][
        "jobTemplate"
    ]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[-2:] == ["--render", "/data/footprints"]


def test_tick_says_which_line_of_config_is_not_yaml(sited, tmp_path):
    (sited / "config.yaml").write_text("mets: [unclosed\n")
    args = ["tick", str(sited), "--now", "2021-01-15T12:15"]
    result = CliRunner().invoke(app, [*args, "--workdir", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert "Error: config.yaml line " in result.output


def test_cli_schedule_says_which_line_of_config_is_not_yaml(sited):
    (sited / "config.yaml").write_text("mets: [unclosed\n")
    result = CliRunner().invoke(app, ["schedule", str(sited), "--dry-run"])
    assert result.exit_code == 2, result.output
    assert result.output.startswith("Error: config.yaml line ")
