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
  a task whose simulations failed is not. `--dry-run` prints the Job.
- `pystiltctl status` counts a project's simulations by state (`stilt status`)
  and lists its Jobs on the cluster, with their tasks done, running and failed.
  `--json` prints the same for a program.
- The `s3` and `gcs` extras, for `s3://` and `gs://` buckets.
- A container image, `ghcr.io/jmineau/pystiltctl`, with PYSTILT, pystiltctl and
  the bucket libraries: `:latest` for the latest release, `:main` for main.
