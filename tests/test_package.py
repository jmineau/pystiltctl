"""PYSTILT, which pystiltctl drives, imports."""

import importlib


def test_pystilt_imports_as_stilt():
    """The pystilt distribution provides the `stilt` package."""
    assert importlib.import_module("stilt")
