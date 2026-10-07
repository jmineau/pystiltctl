"""Test package metadata."""

from importlib.metadata import version

import pystiltctl


def test_version():
    """`__version__` is the installed distribution's version."""
    assert isinstance(pystiltctl.__version__, str)
    assert pystiltctl.__version__ == version("pystiltctl")
