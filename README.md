# pystiltctl

[![Tests](https://github.com/jmineau/pystiltctl/actions/workflows/tests.yml/badge.svg)](https://github.com/jmineau/pystiltctl/actions/workflows/tests.yml)
[![Code Quality](https://github.com/jmineau/pystiltctl/actions/workflows/quality.yml/badge.svg)](https://github.com/jmineau/pystiltctl/actions/workflows/quality.yml)
[![Documentation](https://github.com/jmineau/pystiltctl/actions/workflows/docs.yml/badge.svg)](https://jmineau.github.io/pystiltctl/)
[![codecov](https://codecov.io/gh/jmineau/pystiltctl/branch/main/graph/badge.svg)](https://codecov.io/gh/jmineau/pystiltctl)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Run [PYSTILT](https://github.com/jmineau/PYSTILT) projects at scale outside Slurm: Kubernetes
Jobs, queues, autoscaled workers, and outputs on an object store.

PYSTILT runs a project's receptors on one machine or as a Slurm job array, and each task of an
array is one command line, `stilt run <project> --task I/N`. pystiltctl starts those command
lines elsewhere and keeps the project and its outputs in sync. It calls PYSTILT; PYSTILT knows
nothing of it. [AGENTS.md](AGENTS.md) draws the line between the two.

The layout follows Ben Fasoli's [stiltctl](https://github.com/uataq/stiltctl), which ran STILT-R
on Kubernetes. This is a new codebase that runs PYSTILT.

**Status:** started. Nothing runs yet.

## Installation

From GitHub:

```bash
pip install git+https://github.com/jmineau/pystiltctl
```

pystiltctl requires Python 3.11 or newer.

## Documentation

<https://jmineau.github.io/pystiltctl/>

## Contributing

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```bash
uv sync                    # .venv with the package and the dev tools
uv run pre-commit install
just quality-check         # lint, type check, docstrings, tests
```

## License

MIT; see [LICENSE](LICENSE).
