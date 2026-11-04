# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and versions
follow [PEP 440](https://peps.python.org/pep-0440/).

## [Unreleased]

### Added

- `pystiltctl push` copies a project's inputs (`config.yaml`, `receptors.csv`,
  `tables/`) to a bucket or a folder, for pods to read.
- `pystiltctl worker` runs one task of a project in a pod: it copies the inputs
  from the bucket, then runs `stilt run --task I/N` with the pod's index from
  `JOB_COMPLETION_INDEX`, and exits with its code.
- `pystiltctl submit` runs a project as a Kubernetes indexed Job, with the
  tasks, CPUs and memory from `config.yaml`'s `execution` section unless given.
  A stopped task is retried, a preempted pod is retried without counting, and
  a task whose simulations failed or hit an unexpected error is not.
  `--dry-run` prints the Job.
- `pystiltctl schedule` runs a project every hour for the sites in its
  `sites.csv`, as a Kubernetes CronJob; each hour's pod runs `pystiltctl tick`,
  which adds the hours now due (8 hours behind the clock, for the met archive),
  writes `receptors.csv` back to the bucket, and runs the last day's receptors.
  `push` and `pull` carry `sites.csv`. A tick holds a lock on `receptors.csv`
  while it adds its hours, so a tick run by hand for a past day does not write
  over the hourly one's.
- `--render URL` on `schedule` and `tick` draws each finished footprint for a
  web map: a PNG on one log color scale, rows in web Mercator, with a JSON of
  its corners, and an `index.json` per day.
- `pystiltctl status` counts a project's simulations by state (`stilt status`)
  and lists its Jobs on the cluster, with their tasks done, running and failed.
  `--json` prints the same for a program.
- A docs page, "Try it on your computer", that runs a small project on a kind
  cluster, as the end-to-end test does.
- The `s3` and `gcs` extras, for `s3://` and `gs://` buckets.
- A container image, `ghcr.io/jmineau/pystiltctl`, with PYSTILT, pystiltctl and
  the bucket libraries: `:latest` for the latest release, `:main` for main.

### Fixed

- A pre-release or post-release version (`0.2.0a1`, `0.2.0.post1`) now runs
  its own image instead of `:main`; only an untagged development version does.
- `tick --render` no longer leaves `stilt` running after `kill -9` of the
  parent, outside a container, where a signal handler cannot forward it.
- `render.bounds` raises a clear error for a footprint grid that crosses the
  antimeridian, instead of reporting it as spanning the full 360 degrees.
- `pystiltctl schedule`'s CronJob now fails a tick's Job after 45 minutes if
  it hangs (a stalled download, a bucket call with no timeout), instead of
  staying "active" forever and, with `concurrencyPolicy: Forbid`, blocking
  every later tick until someone deletes it by hand.
- `pystiltctl submit` now passes each pod a hash of the `config.yaml` and
  `receptors.csv` it saw; `pystiltctl worker` exits 2 ("the project changed
  after submit; submit again") if what it pulls hashes differently, instead
  of running with it. A Job's pods can start minutes to hours apart, and a
  push to the project in between used to split the receptors differently for
  earlier and later pods, so some receptors ran twice and others never, while
  the Job still reported `Complete`.
