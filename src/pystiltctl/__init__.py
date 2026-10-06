"""
Run PYSTILT projects at scale outside Slurm.

PYSTILT runs a project's receptors on one machine or as a Slurm job array,
and each task of an array is a command line, ``stilt run --task I/N``.
pystiltctl runs those command lines elsewhere (Kubernetes Jobs, a queue,
autoscaled workers) and keeps the project and its output directory in sync.
It calls PYSTILT; PYSTILT knows nothing of it.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pystiltctl")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["__version__"]
