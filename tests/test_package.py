"""The package imports, and so does PYSTILT, which it drives."""

import importlib


def test_the_package_imports_and_has_a_version():
    import pystiltctl

    assert pystiltctl.__version__


def test_pystilt_imports_as_stilt():
    assert importlib.import_module("stilt")
