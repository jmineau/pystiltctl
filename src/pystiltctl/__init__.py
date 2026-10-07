"""
Run PYSTILT projects at scale outside Slurm.

PYSTILT runs a project's receptors on one machine or as a Slurm job array,
and each task of an array is a command line, ``stilt run --task I/N``.
pystiltctl runs those command lines elsewhere (Kubernetes Jobs, a queue,
autoscaled workers) and keeps the project and its output directory in sync.
It calls PYSTILT; PYSTILT knows nothing of it.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("pystiltctl")  # set by setuptools-scm from git tags
except PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0+unknown"

# A library leaves logging configuration to the application.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["__version__"]
