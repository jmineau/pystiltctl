# AGENTS.md

Orientation for contributors and coding agents working on pystiltctl.

> **Keep this file current.** When the layout, the commands, or the
> boundary below change, update the matching section in the same change.
> Personal or machine-specific notes belong in an untracked `*.local.md`
> file (gitignored), and local plans in `dev/` (excluded through
> `.git/info/exclude`).

## What it is

pystiltctl runs [PYSTILT](https://github.com/jmineau/PYSTILT) projects at
scale outside Slurm: on Kubernetes, behind a queue, on autoscaled
workers, with results on an object store. Its layout follows Ben Fasoli's
[stiltctl](https://github.com/uataq/stiltctl), which ran STILT-R on
Kubernetes; it is a new codebase, not a fork, and it runs PYSTILT.

## The boundary

PYSTILT stays a library and a command line with no queue, no database,
and no orchestration (decided in PYSTILT#67, #87, and #150). Everything
that schedules work lives here. The line between them:

| pystiltctl owns | PYSTILT owns |
|---|---|
| queues, Kubernetes Jobs, scaling | the transport run, footprints, and the output layout |
| splitting work into tasks and starting them | what a task runs: `stilt run <project> --task I/N` (or `--receptors FILE`) |
| syncing a project and its outputs to and from a bucket | reading and writing an output directory, local or `s3://`/`gs://` |
| retries and alerts, from a task's exit code | the exit code: 0 all complete, 1 some failed, 2 stopped before finishing |
| dashboards and reports | `stilt status`, `stilt output ls`, `project.status()` |

Rules that keep the line:

- **Call PYSTILT through its public surface only:** the `stilt` command
  line, or `stilt.Project` and the names in `stilt.__all__`. Never import
  a private name (`stilt._*`, a module's `_function`). If pystiltctl needs
  something PYSTILT does not expose, open a PYSTILT issue rather than
  reaching in.
- **A task is a command line.** Whatever starts it (a Kubernetes indexed
  Job, a queue consumer), the work is `stilt run --task I/N` in a
  container that has the project, its meteorology, and its output
  directory. Completion is decided by PYSTILT, from the files in the
  output directory; pystiltctl never keeps its own record of what is done.
- **No PYSTILT logic here.** Nothing in this package computes particles,
  footprints, hashes, or completion. If it seems to need to, the logic
  belongs in PYSTILT.

## Layout

```
src/pystiltctl/      the package (CLI and drivers as they are built)
tests/               pytest
dev/                 local plans, excluded from git (dev/TASKS.md)
```

stiltctl's other parts (a `Dockerfile`, `helm/` charts, `terraform/`,
`scripts/`) come here as each is built, in the same places.

## Development

- Python 3.11+, managed with [uv](https://docs.astral.sh/uv/):
  `uv sync --group dev`, `uv run pytest`, `uv run ruff check`,
  `uv run pyright`.
- The conventions are PYSTILT's: ruff (`E, F, UP, B, SIM, I, D213`),
  NumPy-style docstrings with the summary on the line after the opening
  quotes, pyright in basic mode, Conventional Commits, and PYSTILT's voice
  for docs and help text (short sentences, the reader's words).
- Keep changes small and code easy to read and debug; most users are
  scientists who will read the source when something looks wrong.
