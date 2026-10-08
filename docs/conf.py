"""Sphinx configuration."""

import datetime
import sys
from importlib.metadata import version as _version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "_ext"))

# -- Project -------------------------------------------------------------------

project = "pystiltctl"
author = "James Mineau"
copyright = f"{datetime.date.today().year}, {author}"
release = _version("pystiltctl")  # from git tags, via setuptools-scm
version = release
# Builds from main (and local builds) are "dev"; release builds are their version.
version_match = "dev" if (".dev" in release or "+" in release) else release

# -- General -------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
    "api_pages",  # _ext/api_pages.py: class pages with member tables
]
templates_path = ["_templates"]
exclude_patterns = ["_build"]

# -- HTML ----------------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_title = f"{project} {version_match}"  # not the full dev version
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme_options = {
    "github_url": "https://github.com/jmineau/pystiltctl",
    "show_toc_level": 2,
    "navbar_align": "left",
    "navbar_end": ["version-switcher", "theme-switcher", "navbar-icon-links"],
    # The version dropdown. The Documentation workflow publishes dev/ (main),
    # one folder per release and stable/, and writes switcher.json listing them.
    "switcher": {
        "json_url": "https://jmineau.github.io/pystiltctl/switcher.json",
        "version_match": version_match,
    },
    "check_switcher": False,  # switcher.json exists only on the deployed site
    "show_version_warning_banner": True,  # point old versions at stable
}

# -- Extensions ----------------------------------------------------------------

# NumPy-style docstrings only.
napoleon_google_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_ivar = True  # what a class page's tables leave in "Attributes"

# A page per module, class, function, and class member, as in pandas'
# reference. The page templates are in _templates/autosummary/.
autodoc_default_options = {
    "member-order": "bysource",
}
autosummary_generate = True  # stubs go to docs/_autosummary/ (gitignored)

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}
