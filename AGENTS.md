# AGENTS.md

Orientation for contributors and coding agents working on pystiltctl.
Contribution mechanics are in [CONTRIBUTING.md](CONTRIBUTING.md).

> **Keep this file current.** If you change the module layout, the build/test
> commands, or learn a new invariant or gotcha, update the matching section in
> the same change. A section that no longer matches the code is worse than no
> section: fix it or delete it.
>
> Personal or machine-specific notes (local paths, cluster setup) belong in an
> untracked file, not here. Anything matching `*.local.md` is gitignored for
> this (e.g. `CLAUDE.local.md`); add your tool's local files to `.gitignore` if
> they are not covered. Some agents stop reading `AGENTS.md` once a local
> instruction file exists, so import or reference it from yours.

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

- `src/pystiltctl/`: the package (`py.typed` ships, so annotations are public API).
- `tests/`: pytest suite.
- `docs/`: Sphinx site (PyData theme); `docs/api.rst` drives the autosummary API pages.
- `dev/`: local plans (`dev/TASKS.md`), excluded from git through `.git/info/exclude`.

stiltctl's other parts (a `Dockerfile`, `helm/` charts, `terraform/`,
`scripts/`) come here as each is built, in the same places.

pystiltctl is an application, not a library: it is installed into a Linux
container image and run from there. So CI tests Linux only, and a release
will publish a container image (and Helm chart) once the `Dockerfile` exists;
it is not published to PyPI.

## Commands

The environment is uv-managed (`uv sync`); every task is a `just` recipe, and CI
runs the same recipes.

| Task | Command |
|---|---|
| Set up / refresh `.venv` | `uv sync` |
| Everything CI checks | `just quality-check` |
| Tests (no network/slow) | `just test` (parallel; extra args go to pytest) |
| Lint / fix and format | `just lint` / `just format` |
| Type check | `just type-check` (pyrefly) |
| Docs (warnings are errors) | `just build-docs` |
| Live docs preview | `just docs-serve` (port 8000) |
| All pre-commit hooks | `just pre-commit` |
| Build and check dists | `just dist` |
| Draft changelog entries | `just changelog` |

After editing dependencies in `pyproject.toml`, run `uv lock` and commit
`uv.lock` in the same commit.

## Conventions

- Python 3.11+; prefer stdlib features over backports.
- Ruff lint rules `E, F, UP, B, SIM, I, D213, NPY, RUF100`; line length is the
  formatter's business. Suppress a rule inline with a reason (`# noqa: B008 - why`).
- NumPy-style docstrings, with the summary on the second line (D213):

  ```python
  def f(x):
      """
      Summarize in one line.

      Parameters
      ----------
      x : float
          What x is.
      """
  ```

- Public functions, classes and modules have docstrings (`just docstr` enforces 95%).
- pyrefly must pass with no errors; suppress with `# pyrefly: ignore[<code>]` on the
  line above, with a reason.
- The library never configures logging; modules use `logging.getLogger(__name__)`.
- Docs and help text use PYSTILT's voice: short sentences, the reader's words.
- Keep changes small and code easy to read and debug; most users are scientists
  who will read the source when something looks wrong.

## Tests

- `network`: needs live network access; `slow`: expensive. Both are skipped by
  `just test` and in CI; run them with `uv run pytest -m network` or `-m slow`.
- Markers are strict, and warnings are errors: register new markers, and ignore a
  specific third-party warning, in `[tool.pytest]` in `pyproject.toml`.
- `just test` runs in parallel (pytest-xdist); tests must not depend on order or
  shared state. Debug with a serial `uv run pytest tests/test_x.py -x`.
- Tests must not depend on machine-specific paths or data.

## Commits, changelog, releases

- Commit messages follow Conventional Commits (`fix(io): ...`, `docs: ...`), with
  `!` for breaking changes (`feat!: ...`).
- User-visible changes go under `## [Unreleased]` in `CHANGELOG.md`, which follows
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). `just changelog`
  drafts entries from commit messages (`cliff.toml`); the entries are edited by hand.
- Docs are versioned on GitHub Pages (`dev/`, one folder per release, `stable/`);
  `.github/scripts/docs_versions.py` maintains the gh-pages branch. Never edit
  gh-pages by hand.
- The version comes from git tags (setuptools-scm); there is no version string in
  the source. Pushing a `vX.Y.Z` tag publishes it. **Do not cut a release, create
  a tag, or push unless the maintainer asks.**

## Template

Tooling files come from [jmineau/python-template](https://github.com/jmineau/python-template)
via copier (`.copier-answers.yml`). Never edit `.copier-answers.yml` by hand;
`copier update` pulls in template changes.
