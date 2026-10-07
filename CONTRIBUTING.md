# Contributing to pystiltctl

Thank you for considering a contribution. Bug reports and feature requests go
in [the issue tracker](https://github.com/jmineau/pystiltctl/issues);
code changes come as pull requests.

Coding agents: read [AGENTS.md](AGENTS.md) as well.

## Setup

You need [uv](https://docs.astral.sh/uv/) and git. [just](https://just.systems/)
comes with the dev tools.

```bash
git clone https://github.com/jmineau/pystiltctl.git
cd pystiltctl
uv sync                    # .venv with the package (editable) and the dev tools
uv run pre-commit install  # run the hooks on every commit
```

Without uv: `python -m venv .venv`, activate it, then
`pip install --group dev -e .` (needs pip 25.1 or newer).

## Checks

Every check has a `just` recipe, and CI runs the same recipes. Run them with
`uv run just <recipe>`, or plain `just` if it is on your PATH (`just --list`
shows them all).

| Recipe | What it does |
|---|---|
| `just quality-check` | `lint`, `type-check`, `docstr` and `test`: what CI checks |
| `just test` | pytest in parallel, skipping tests marked `network` or `slow` (extra args go to pytest; plain `uv run pytest` runs serially, for debugging) |
| `just format` | fix lint and format the code with ruff |
| `just build-docs` | build the HTML docs into `docs/_build/`; warnings are errors |
| `just docs-serve` | preview the docs at <http://127.0.0.1:8000>, rebuilt on every save (on a remote machine, forward the port, e.g. VS Code's Ports panel) |
| `just changelog` | draft CHANGELOG entries from the commits since the last release |
| `just pre-commit` | run every hook on every file |

After changing dependencies in `pyproject.toml`, run `just lock` (the
`uv-lock` hook also does this on commit) and commit `uv.lock` with the change.

## Making a change

1. Branch from `main`.
2. Make the change, with tests and NumPy-style docstrings.
3. Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for
   anything a user would notice.
4. Commit with a [Conventional Commits](https://www.conventionalcommits.org/)
   message, e.g. `fix(io): read files with a BOM` or `feat!: ...` for a
   breaking change.
5. Open a pull request. Link the issue it addresses ("Fixes #12").

## Releasing

The version comes from git tags, through setuptools-scm, so there is no
version string to bump.

1. Run `just changelog` to draft the entries from your commit messages, edit
   them into `CHANGELOG.md` under `## [Unreleased]`, then rename that heading
   to `## [X.Y.Z] - YYYY-MM-DD` and start a new empty `## [Unreleased]` above
   it. Commit (`chore(release): X.Y.Z`) and push to `main`.
2. Run `just release X.Y.Z`. It checks that the tree is clean, that `main` is in
   sync with GitHub, and that the version is newer than every existing tag, then
   pushes the tag `vX.Y.Z`.
3. The Publish workflow builds the tag and creates a GitHub
   Release from the CHANGELOG section. Zenodo archives the release and mints a
   DOI. The Documentation workflow publishes the docs as `X.Y.Z/` (and, for a
   final release, `stable/`) in the version dropdown.

Pre-releases (`0.3.0a1`, `0.3.0rc1`) work the same way. They appear in the
docs dropdown until the final release replaces them (see `DOCS_PRERELEASES` in
`.github/workflows/docs.yml` to change that).

## Dependency updates

Dependabot opens one pull request a month per kind of pin: GitHub Actions,
pre-commit hooks, and `uv.lock` (the dev tools; it never raises the minimum
versions in `pyproject.toml`). Merge it when CI passes.

## Template

The tooling (CI workflows, pre-commit, justfile, packaging configuration) comes
from [jmineau/python-template](https://github.com/jmineau/python-template).
`.copier-answers.yml` records the template version; `copier update` pulls in
later template changes. Improvements that would help every package are best
made in the template.
