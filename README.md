# pystiltctl

[![Tests](https://github.com/jmineau/pystiltctl/actions/workflows/tests.yml/badge.svg)](https://github.com/jmineau/pystiltctl/actions/workflows/tests.yml)
[![Code Quality](https://github.com/jmineau/pystiltctl/actions/workflows/quality.yml/badge.svg)](https://github.com/jmineau/pystiltctl/actions/workflows/quality.yml)
[![Documentation](https://github.com/jmineau/pystiltctl/actions/workflows/docs.yml/badge.svg)](https://jmineau.github.io/pystiltctl/)
[![codecov](https://codecov.io/gh/jmineau/pystiltctl/branch/main/graph/badge.svg)](https://codecov.io/gh/jmineau/pystiltctl)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Pyrefly](https://img.shields.io/badge/types-pyrefly-blue.svg)](https://pyrefly.org)

> **pystiltctl has retired (2026-11).** Running a project on Kubernetes is now part of
> [runtraj](https://github.com/jmineau/runtraj), which runs PYSTILT's particles, and the rest
> went to [PYSTILT](https://github.com/jmineau/PYSTILT). This repository is archived and gets
> no more changes.

What pystiltctl did, and where it is now:

| pystiltctl | Now |
|---|---|
| `submit` (an indexed Job), `status` | `stilt submit` / `runtraj submit` and `status`, with `backend: kubernetes` and an `execution: kubernetes:` block in `config.yaml` ([runtraj's guide](https://jmineau.github.io/runtraj/dev/guides/containers.html)) |
| `push`, `worker` | `kubernetes: inputs:` copies the inputs to a bucket at submit; each pod runs `runtraj run <bucket URL> --task I/N` |
| `schedule`, `tick` (hourly, for `sites.csv`) | `stilt schedule` and `stilt tick` |
| the fetch Job before a Job | `kubernetes: fetch: true`: each pod gets the met in turn before it runs (runtraj after 0.1.0a3) |
| `tick --render` (footprints for a web map) | PYSTILT's map stage, `stilt.footprint.MapStage` |
| the image `ghcr.io/jmineau/pystiltctl` | PYSTILT's `Dockerfile`, built with your own HYSPLIT ([PYSTILT's guide](https://jmineau.github.io/PYSTILT/dev/guides/containers.html)) |
| the cluster and kind pages | [Setting Up Kubernetes](https://jmineau.github.io/runtraj/dev/guides/kubernetes_setup.html) in runtraj's docs |

The layout followed Ben Fasoli's [stiltctl](https://github.com/uataq/stiltctl), which ran
STILT-R on Kubernetes. pystiltctl never had a release.

## Use of AI coding agents

This project is developed with the help of AI coding agents, directed and
reviewed by the maintainer, who owns the design and the science.

## License

MIT; see [LICENSE](LICENSE).
