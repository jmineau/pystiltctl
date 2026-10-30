"""Drawing footprints for a web map."""

import json
from datetime import datetime
from types import SimpleNamespace

import fsspec
import numpy as np
import pytest
import xarray as xr

from pystiltctl import render


def footprint(values, lat0=40.0, lon0=-112.0, res=0.5, hours=2):
    """A footprint on a regular grid, the same in each hour, from rows south to north."""
    values = np.asarray(values, dtype=float)
    lat = lat0 + res * np.arange(values.shape[0])
    lon = lon0 + res * np.arange(values.shape[1])
    data = np.repeat(values[None] / hours, hours, axis=0)
    coords = {"time": np.arange(hours), "lat": lat, "lon": lon}
    return xr.DataArray(
        data,
        dims=("time", "lat", "lon"),
        coords=coords,
        attrs={"units": "ppm (umol m-2 s-1)-1"},
    )


def test_bounds_are_the_outer_cell_edges():
    foot = footprint(np.ones((3, 2)))
    assert render.bounds(foot) == [[39.75, -112.25], [41.25, -111.25]]


def test_image_puts_north_at_the_top_and_hides_small_values():
    # South row strong, north row below the scale.
    foot = footprint([[1e-2, 1e-2], [1e-9, 1e-9]])
    rgba = render.image(foot)
    assert rgba.shape == (2, 2, 4)
    assert rgba.dtype == np.uint8
    assert (rgba[0, :, 3] == 0).all()  # north: transparent
    assert (rgba[1, :, 3] > 0).all()  # south: drawn


def test_image_is_summed_over_time_on_a_fixed_scale():
    weak, strong = render.image(footprint([[1e-4]])), render.image(footprint([[1e-2]]))
    assert weak[0, 0, 3] < strong[0, 0, 3]
    top = render.image(footprint([[10.0]]))
    assert top[0, 0, 3] == round(0.9 * 255)  # above VMAX: the top of the scale


def test_image_rows_follow_web_mercator():
    # Over 40-80 N, equal Mercator bands hold more rows of the southern grid:
    # the north quarter of the image takes only the northmost grid row.
    values = np.arange(1, 9)[:, None] * np.ones((8, 1)) * 1e-3
    rgba = render.image(footprint(values, lat0=40.0, res=5.0, hours=1))
    colors = [tuple(row[0]) for row in rgba]
    assert colors[0] == colors[1]  # two image rows from the same grid row, in the north
    assert len(set(colors)) < 8


def test_png_is_a_png():
    assert render.png(footprint(np.ones((2, 2)) * 1e-3)).startswith(b"\x89PNG")


class FakeProject:
    """Stands in for stilt.Project: two receptors, one with a footprint."""

    variants = {"hrrr": None}

    def __init__(self, directory, output=None):
        self.output = output

    def simulation(self, receptor_id, variant):
        hour = 10 if receptor_id.endswith("a") else 11
        receptor = SimpleNamespace(
            id=receptor_id,
            time=datetime(2026, 10, 29, hour),
            longitude=-111.85,
            latitude=40.77,
            altitude=35.0,
        )
        if receptor_id.endswith("a"):
            return SimpleNamespace(
                receptor=receptor, footprint=footprint(np.ones((2, 2)) * 1e-3)
            )

        class NotRun(SimpleNamespace):
            @property
            def footprint(self):
                raise FileNotFoundError("not run")

        return NotRun(receptor=receptor)


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(render.stilt, "Project", FakeProject)
    return "memory://site-" + datetime.now().strftime("%H%M%S%f") + "/footprints"


def test_render_draws_finished_footprints_and_indexes_the_day(site):
    assert render.render("/project", site, ["r1a", "r2b"]) == ["r1a"]
    fs, root = fsspec.core.url_to_fs(site)
    day = f"{root}/2026-10-29"
    assert fs.cat_file(f"{day}/r1a.png").startswith(b"\x89PNG")
    index = json.loads(fs.cat_file(f"{day}/index.json"))
    assert [i["receptor"] for i in index] == ["r1a"]
    assert index[0]["bounds"] == [[39.75, -112.25], [40.75, -111.25]]
    assert index[0]["time"] == "2026-10-29T10:00:00"


def test_render_skips_what_it_drew_before(site):
    render.render("/project", site, ["r1a"])
    assert render.render("/project", site, ["r1a"]) == []


def test_render_needs_a_variant_when_there_are_several(site, monkeypatch):
    monkeypatch.setattr(FakeProject, "variants", {"hrrr": None, "nam": None})
    with pytest.raises(ValueError, match="2 variants"):
        render.render("/project", site, ["r1a"])
