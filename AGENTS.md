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

pystiltctl runs [PYSTILT](https://github.com/jmineau/PYSTILT) projects on
Kubernetes, as indexed Jobs, with inputs and results on a bucket. It does for
PYSTILT what Ben Fasoli's [stiltctl](https://github.com/uataq/stiltctl) did for
STILT-R; it is a new codebase, not a fork.

## The boundary

PYSTILT stays a library and a command line with no queue, no database and no
orchestration (PYSTILT#67, #87, #150). pystiltctl owns starting tasks, Jobs,
retries, bucket sync and reports; PYSTILT owns what a task runs (`stilt run
<project> --task I/N`), the output layout, completion, and the exit code (0 all
complete, 1 some failed, 2 bad command line, 3 stopped).

- **Call PYSTILT through its public surface only:** the `stilt` command line, or
  `stilt.Project` and the names in `stilt.__all__`; never a private name. If
  something is missing, open a PYSTILT issue rather than reaching in.
- **A task is a command line**, `stilt run --task I/N`, in a container with the
  project, its met and its output directory. PYSTILT decides completion from the
  output files; pystiltctl keeps no record of what is done.
- **No PYSTILT logic here**: nothing computes particles, footprints, hashes or
  completion.

## Layout

- `src/pystiltctl/`: the package (`py.typed` ships, so annotations are public API).
  - `cli.py`: the `pystiltctl` command (typer); each command calls one module.
  - `inputs.py`: `push`/`pull` copy the inputs to and from a bucket (fsspec), never the output.
  - `worker.py`: what a pod runs: pull, then `os.execv` into `python -m stilt run
    --task I/N`, so the stop signal and the exit code are `stilt`'s own.
  - `job.py`: the indexed Job and the CronJob, defaults from `execution`; retries are
    their `podFailurePolicy`, never a loop here.
  - `schedule.py`: `tick`, each hour's pod: `sites.csv` receptors via `add_receptors`,
    `receptors.csv` put back, then `stilt run --receptors` on the last day.
  - `status.py`: `stilt status --json` plus the Jobs labeled `pystiltctl/project`.
- `tests/`: pytest suite; `tests/kubernetes/`, the end-to-end test on kind (its README).
- `docs/`: Sphinx site (PyData theme). `docs/api.rst` lists the API by topic; each class gets a page with tables of its attributes and methods, and each member a page of its own (`docs/_templates/autosummary/`, `docs/_ext/api_pages.py`).
  Its examples run when the docs build (see [Examples in the docs](#examples-in-the-docs)).
- `dev/`: the maintainer's local notes, excluded from git through `.git/info/exclude`.
- `Dockerfile`: the image every pod runs (how to build it is in its header):
  `uv.lock` with the `s3` and `gcs` extras on `python:3.13-slim`, `tini` as
  process 1; PYSTILT's wheel carries a static `hycs_std`. `image.yml` (not a
  template file) smoke-tests it on every PR and pushes `ghcr.io/jmineau/pystiltctl`
  (`:main` from main; `:<version>`, `:latest` from a tag). pystiltctl is an
  application that runs in this Linux image: CI tests Linux only, and it is not on PyPI.

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
| Docs (runs every example; warnings are errors) | `just build-docs` |
| Live docs preview | `just docs-serve` (port 8000) |
| All pre-commit hooks | `just pre-commit` |
| Build and check dists | `just dist` |
| Draft changelog entries | `just changelog` |

After editing dependencies in `pyproject.toml`, run `uv lock` and commit
`uv.lock` in the same commit.

## Conventions

- Python 3.11+; prefer stdlib features over backports.
- Ruff lint rules `E, F, UP, B, SIM, I, D, D213, NPY, RUF100` (pydocstyle with
  the NumPy convention); line length is the formatter's business. Suppress a
  rule inline with a reason (`# noqa: B008 - why`).
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
- Each public attribute and property has its own page in the API reference, and that
  page is where its See Also and Examples go (as in pandas). Say what it is in its
  docstring, and add See Also or Examples where they help; one with no docstring
  shows an empty row.
- pyrefly must pass with no errors; suppress with `# pyrefly: ignore[<code>]` on the
  line above, with a reason.
- The library never configures logging; modules use `logging.getLogger(__name__)`.
- Docs and help text use PYSTILT's voice: short sentences, the reader's words.
- Keep changes small and code easy to read and debug; most users are scientists
  who will read the source when something looks wrong.

## Examples in the docs

Examples run when the docs build, so they show real output, and the build fails
when one breaks.

- **A figure in a docstring:** put a `.. plot::` directive in the Examples
  section. Its code runs (doctest `>>>` lines work), and the figure appears
  under it.

  ```rst
  Examples
  --------
  .. plot::

     >>> import matplotlib.pyplot as plt
     >>> _ = plt.plot([0, 1, 2], [0, 1, 4])
  ```

- **Code in an `.rst` page:** a `.. ipython:: python` block runs, and shows each
  line with its output, as in an IPython session. Changing a
  `.. code-block:: python` to `.. ipython:: python` is enough to make it run. For
  a figure, put `@savefig name.png` on the line above the plotting call.

  ```rst
  .. ipython:: python

     import matplotlib.pyplot as plt
     import pystiltctl

     pystiltctl.__version__

     @savefig squares.png width=5in
     plt.plot([0, 1, 2], [0, 1, 4]);
  ```

  A block that raises or warns fails the build; `:okexcept:` or `:okwarning:`
  under the directive allows one that is meant to. All `.rst` pages share one
  session, so each page imports and defines what it uses.
- **When they run:** `just build-docs` runs everything, as CI does;
  `just docs-serve` reruns a page's blocks whenever that page is rebuilt.
- **Notebooks, and code cells in Markdown pages,** are off in this project.
  `copier update --data docs_notebooks=true` turns them on; it adds myst-nb, and
  Jupyter with it, to the dev tools.
- **Data:** the docs build on GitHub Actions, so an example uses synthetic data,
  a small file in the repository, or a public download, never a machine-specific
  path. Seed random numbers so the figures don't change from build to build.
- **Optional dependencies:** if an example needs an extra, add
  `pystiltctl[extra]` to the `dev` group, so `uv sync` installs it
  here and in CI.

## Tests

- `network`: needs live network access; `slow`: expensive. Both are skipped by
  `just test` and in CI; run them with `uv run pytest -m network` or `-m slow`.
- Markers are strict, and warnings are errors: register new markers in
  `[tool.pytest]` in `pyproject.toml`. A test that triggers a warning on purpose
  asserts it (`with pytest.warns(UserWarning, match="..."):`); a third-party
  warning is ignored by message and module in `[tool.pytest]` `filterwarnings`.
- `just test` runs in parallel (pytest-xdist); tests must not depend on order or
  shared state. Debug with a serial `uv run pytest tests/test_x.py -x`.
- Tests must not depend on machine-specific paths or data.
- The pytest suite needs no cluster, no s3fs and no network: buckets are fsspec's
  `memory://` (fixture `bucket`), and `kubectl` and the worker's `os.execv`
  (fixture `execs`) are monkeypatched. `CONFIG` in `tests/conftest.py` is the
  smallest `config.yaml` PYSTILT opens; keep it valid.

## Commits, changelog, releases

- Commit messages follow Conventional Commits (`fix(io): ...`, `docs: ...`), with
  `!` for breaking changes (`feat!: ...`).
- User-visible changes go under `## [Unreleased]` in `CHANGELOG.md`, which follows
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). `just changelog`
  drafts entries from commit messages (`cliff.toml`); the entries are edited by hand.
- Docs are versioned on GitHub Pages, which serves the gh-pages branch (`dev/`,
  one folder per release, `stable/`); `.github/scripts/docs_versions.py`
  maintains the branch. Never edit gh-pages by hand.
- The version comes from git tags (setuptools-scm); there is no version string in
  the source. The maintainer cuts releases; pushing a `vX.Y.Z` tag publishes it.

## Template

Tooling files come from [jmineau/python-template](https://github.com/jmineau/python-template)
via copier (`.copier-answers.yml`). Never edit `.copier-answers.yml` by hand;
`copier update` pulls in template changes.
