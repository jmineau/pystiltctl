"""Drawing footprints for a web map."""

import json
import threading
from datetime import UTC, datetime, timedelta, timezone
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


def _dateline_footprint():
    """
    A footprint whose grid crosses the antimeridian, as PYSTILT represents it (#28).

    ``xmin=170, xmax=190, xres=5`` makes PYSTILT wrap and sort the grid's
    longitudes into this array (``footprint/gridding.py``).
    """
    lon = np.array([-177.5, -172.5, 172.5, 177.5])
    lat = np.array([10.0, 12.0])
    return xr.DataArray(
        np.ones((1, lat.size, lon.size)),
        dims=("time", "lat", "lon"),
        coords={"time": [0], "lat": lat, "lon": lon},
        attrs={"units": "ppm (umol m-2 s-1)-1"},
    )


def test_bounds_raises_for_a_grid_that_crosses_the_antimeridian():
    """``lon.min()``/``lon.max()`` would stretch this 20-degree grid over 360 degrees."""
    with pytest.raises(ValueError, match="antimeridian"):
        render.bounds(_dateline_footprint())


def test_image_raises_for_a_grid_that_crosses_the_antimeridian():
    with pytest.raises(ValueError, match="antimeridian"):
        render.image(_dateline_footprint())


def test_image_puts_west_at_the_left_whatever_order_lon_comes_in():
    """A footprint whose longitudes run east to west is drawn as the same map."""
    foot = footprint([[1e-2, 1e-9, 1e-9]] * 3)  # the strong column is the westmost
    mirrored = foot.isel(lon=slice(None, None, -1))
    assert foot["lon"].values[0] < foot["lon"].values[-1]
    assert mirrored["lon"].values[0] > mirrored["lon"].values[-1]
    assert (render.image(mirrored) == render.image(foot)).all()
    alpha = render.image(foot)[0, :, 3]
    assert alpha[0] > 0 and alpha[2] == 0  # drawn at the west, not the east
    assert render.bounds(mirrored) == render.bounds(foot)


def test_bounds_refuses_a_grid_of_one_row_or_column():
    for values in ([[1.0]], [[1.0, 1.0]], [[1.0], [1.0]]):
        with pytest.raises(ValueError, match="cell size"):
            render.bounds(footprint(values))
        with pytest.raises(ValueError, match="cell size"):
            render.image(footprint(values))


def test_bounds_raises_for_two_columns_across_the_antimeridian():
    foot = footprint(np.ones((2, 2)), lon0=-179.5, res=359.0)
    assert list(foot["lon"].values) == [-179.5, 179.5]
    with pytest.raises(ValueError, match="antimeridian"):
        render.bounds(foot)
    assert render.bounds(footprint(np.ones((2, 2)), lon0=170.0, res=5.0))[1][1] == 177.5


def test_image_puts_north_at_the_top_and_hides_small_values():
    # South row strong, north row below the scale.
    foot = footprint([[1e-2, 1e-2], [1e-9, 1e-9]])
    rgba = render.image(foot)
    assert rgba.shape == (2, 2, 4)
    assert rgba.dtype == np.uint8
    assert (rgba[0, :, 3] == 0).all()  # north: transparent
    assert (rgba[1, :, 3] > 0).all()  # south: drawn


def test_image_is_summed_over_time_on_a_fixed_scale():
    weak = render.image(footprint(np.full((2, 2), 1e-4)))
    strong = render.image(footprint(np.full((2, 2), 1e-2)))
    assert weak[0, 0, 3] < strong[0, 0, 3]
    top = render.image(footprint(np.full((2, 2), 10.0)))
    assert top[0, 0, 3] == round(0.9 * 255)  # above VMAX: the top of the scale
    infinite = render.image(footprint(np.full((2, 2), np.inf)))
    assert (infinite == top).all()  # +inf is above the scale too
    assert (render.image(footprint(np.zeros((2, 2))))[..., 3] == 0).all()
    assert (render.image(footprint(np.full((2, 2), np.nan)))[..., 3] == 0).all()


def test_image_rows_follow_web_mercator():
    # Over 40-80 N, equal Mercator bands hold more rows of the southern grid:
    # the north of the image takes only the northmost grid rows, and the
    # image has more rows than the grid so that no grid row is left out.
    values = np.arange(1, 9)[:, None] * np.ones((8, 2)) * 1e-3
    rgba = render.image(footprint(values, lat0=40.0, res=5.0, hours=1))
    colors = [tuple(row[0]) for row in rgba]
    assert len(rgba) > 8
    assert colors[0] == colors[1]  # two image rows from the same grid row, in the north
    assert len(set(colors)) == 8


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
    assert index[0]["time"] == "2026-10-29T10:00:00Z"


def test_render_skips_what_it_drew_before(site):
    render.render("/project", site, ["r1a"])
    assert render.render("/project", site, ["r1a"]) == []


def test_render_needs_a_variant_when_there_are_several(site, monkeypatch):
    monkeypatch.setattr(FakeProject, "variants", {"hrrr": None, "nam": None})
    with pytest.raises(ValueError, match="2 variants"):
        render.render("/project", site, ["r1a"])


class _Unreachable:
    """Wraps a real filesystem but raises *error* from ``info``, as a broken bucket would."""

    def __init__(self, fs, error: Exception):
        self.fs = fs
        self.error = error

    def __getattr__(self, name):
        return getattr(self.fs, name)

    def info(self, path, **kwargs):
        raise self.error


class _EndpointConnectionError(Exception):
    """Stands in for a botocore error s3fs does not translate into an OSError."""


def _break(monkeypatch, site, error: Exception):
    """Make render's ``url_to_fs`` for *site* return a filesystem whose ``info`` raises."""
    fs, root = fsspec.core.url_to_fs(site)
    monkeypatch.setattr(
        render.fsspec.core,
        "url_to_fs",
        lambda url: (_Unreachable(fs, error), root),
    )
    return fs, root


def test_render_raises_oserror_on_a_bucket_error_fsspec_does_not_translate(
    site, monkeypatch
):
    """A bucket error checking a day's index must not look like there is none (#31)."""
    _break(
        monkeypatch, site, _EndpointConnectionError("could not connect to the endpoint")
    )
    with pytest.raises(OSError, match="could not connect") as excinfo:
        render.render("/project", site, ["r1a"])
    assert not isinstance(excinfo.value, FileNotFoundError)


def test_render_lets_an_oserror_from_the_bucket_through_unchanged(site, monkeypatch):
    """A connectivity error fsspec already raises as OSError is not swallowed either."""
    _break(monkeypatch, site, ConnectionError("refused"))
    with pytest.raises(OSError, match="refused") as excinfo:
        render.render("/project", site, ["r1a"])
    assert not isinstance(excinfo.value, FileNotFoundError)


def test_render_does_not_drop_other_receptors_entries_on_a_bucket_error(
    site, monkeypatch
):
    """A bucket error reading the index must not be treated as an empty index (#31)."""
    fs, root = fsspec.core.url_to_fs(site)
    day = f"{root}/2026-10-29"
    fs.makedirs(day, exist_ok=True)
    recorded = [{"receptor": "r1z", "time": "2026-10-29T09:00:00Z"}]
    fs.pipe_file(f"{day}/index.json", json.dumps(recorded).encode())
    _break(monkeypatch, site, TimeoutError("timed out reaching the bucket"))
    with pytest.raises(OSError, match="timed out"):
        render.render("/project", site, ["r1a"])
    # the earlier receptor's entry is untouched: render stopped before it could
    # mistake the bucket error for an empty index and overwrite the file.
    assert json.loads(fs.cat_file(f"{day}/index.json")) == recorded


def test_time_is_written_in_utc_with_its_zone():
    """A browser reads a time with no zone as its own local time."""
    assert render.utc(datetime(2026, 10, 29, 10)) == "2026-10-29T10:00:00Z"
    denver = datetime(2026, 10, 29, 4, tzinfo=timezone(timedelta(hours=-6)))
    assert render.utc(denver) == "2026-10-29T10:00:00Z"
    assert render.utc(datetime(2026, 10, 29, 10, tzinfo=UTC)) == "2026-10-29T10:00:00Z"


class Hours(FakeProject):
    """A project whose receptor ``r<H>`` is hour H of one day; ``bad`` ones cannot be read."""

    bad: set[str] = set()

    def simulation(self, receptor_id, variant):
        receptor = SimpleNamespace(
            id=receptor_id,
            time=datetime(2026, 10, 29, int(receptor_id[1:])),
            longitude=-111.85,
            latitude=40.77,
            altitude=35.0,
        )
        if receptor_id in self.bad:

            class Corrupt(SimpleNamespace):
                @property
                def footprint(self):
                    raise OSError("corrupt footprint file")

            return Corrupt(receptor=receptor)
        return SimpleNamespace(
            receptor=receptor, footprint=footprint(np.ones((2, 2)) * 1e-3)
        )


@pytest.fixture
def hours(site, monkeypatch):
    monkeypatch.setattr(render.stilt, "Project", Hours)
    monkeypatch.setattr(Hours, "bad", set())
    return site


def indexed(site):
    """The receptors in the day's index.json, or None when there is no index."""
    fs, root = fsspec.core.url_to_fs(site)
    path = f"{root}/2026-10-29/index.json"
    return (
        [i["receptor"] for i in json.loads(fs.cat_file(path))]
        if fs.exists(path)
        else None
    )


def test_render_indexes_every_footprint_after_an_error_part_way(hours, monkeypatch):
    """The third footprint of a tick cannot be read: the first two are already drawn."""
    ids = ["r10", "r11", "r12", "r13"]
    monkeypatch.setattr(Hours, "bad", {"r12"})
    with pytest.raises(OSError, match="corrupt"):
        render.render("/project", hours, ids)
    monkeypatch.setattr(Hours, "bad", set())
    render.render("/project", hours, ids)
    assert indexed(hours) == ids


def test_render_indexes_every_footprint_after_a_stop_before_the_index(
    hours, monkeypatch
):
    """A stop signal between a footprint's files and its index entry loses nothing."""
    ids = ["r10", "r11", "r12"]
    fs_for = fsspec.core.url_to_fs
    stop = {"now": True}

    class StopsAtTheIndex:
        def __init__(self, fs):
            self.fs = fs

        def __getattr__(self, name):
            return getattr(self.fs, name)

        def pipe_file(self, path, data, **kwargs):
            if stop["now"] and path.endswith("index.json"):
                raise KeyboardInterrupt("SIGTERM")
            return self.fs.pipe_file(path, data, **kwargs)

    monkeypatch.setattr(
        render.fsspec.core,
        "url_to_fs",
        lambda url: (StopsAtTheIndex(fs_for(url)[0]), fs_for(url)[1]),
    )
    with pytest.raises(KeyboardInterrupt):
        render.render("/project", hours, ids[:2])
    stop["now"] = False
    assert render.render("/project", hours, ids) == ids
    assert indexed(hours) == ids


def test_render_indexes_a_footprint_drawn_before_the_index_was_kept(hours):
    """A day whose files have no index entry (an older run) is drawn and indexed again."""
    fs, root = fsspec.core.url_to_fs(hours)
    day = f"{root}/2026-10-29"
    fs.makedirs(day)
    fs.pipe_file(f"{day}/r10.json", b"{}")
    assert render.render("/project", hours, ["r10"]) == ["r10"]
    assert indexed(hours) == ["r10"]


def test_render_keeps_the_days_earlier_entries_in_time_order(hours):
    render.render("/project", hours, ["r12"])
    render.render("/project", hours, ["r10", "r12", "r11"])
    assert indexed(hours) == ["r10", "r11", "r12"]


class ManyReceptors:
    """Stands in for ``stilt.Project``: as many same-day receptors as asked for."""

    variants = {"hrrr": None}

    def __init__(self, directory, output=None):
        self.output = output

    def simulation(self, receptor_id, variant):
        receptor = SimpleNamespace(
            id=receptor_id,
            time=datetime(2026, 10, 29) + timedelta(seconds=int(receptor_id)),
            longitude=-111.85,
            latitude=40.77,
            altitude=35.0,
        )
        return SimpleNamespace(
            receptor=receptor, footprint=footprint(np.ones((2, 2)) * 1e-3)
        )


def test_render_index_is_atomic_on_a_local_folder(tmp_path, monkeypatch):
    """
    A reader must never see ``index.json`` half written, on a local folder (#29).

    ``index.json`` is rewritten after every footprint, so one call that
    draws many receptors of the same day rewrites it many times while a
    reader polls it, as a web map would.
    """
    monkeypatch.setattr(render.stilt, "Project", ManyReceptors)
    url = str(tmp_path / "footprints")
    path = tmp_path / "footprints" / "2026-10-29" / "index.json"
    ids = [str(n) for n in range(40)]

    stop = threading.Event()
    bad_reads = []

    def read_forever():
        while not stop.is_set():
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                continue
            try:
                json.loads(data)
            except ValueError:
                bad_reads.append(data[:80])

    reader = threading.Thread(target=read_forever)
    reader.start()
    try:
        render.render("/project", url, ids)
    finally:
        stop.set()
        reader.join()
    assert bad_reads == []


def test_choose_variant(site, monkeypatch):
    project = FakeProject("/project")
    assert render.choose_variant(project) == "hrrr"
    assert render.choose_variant(project, "nam") == "nam"
    monkeypatch.setattr(FakeProject, "variants", {"hrrr": None, "nam": None})
    with pytest.raises(ValueError, match="2 variants"):
        render.choose_variant(project)


def mercator(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def shown_rows(lat0, n, res):
    """
    Return the grid row (north first) each image row shows, and the image's rows.

    Each grid row has its own color. A row of the image is matched to the
    grid row it looks like, using the same footprint on a grid so thin that
    the rows are drawn one to one.
    """
    values = 10 ** (-4.95 + 3.9 * np.arange(n) / max(n - 1, 1))
    foot = footprint(values[:, None] * np.ones((1, 2)), lat0=lat0, res=res, hours=1)
    thin = footprint(values[:, None] * np.ones((1, 2)), lat0=0.0, res=1e-4, hours=1)
    reference = render.image(thin)[:, 0, :].astype(int)
    rgba = render.image(foot)
    got = [
        int(np.abs(reference - row[0].astype(int)).sum(axis=1).argmin()) for row in rgba
    ]
    return np.array(got), len(rgba)


def expected(lat0, n, res, rows):
    """For each of *rows* equal Mercator bands, the grid row (north first) it overlaps most."""
    lat = (lat0 + res * np.arange(n))[::-1]
    south, north = lat.min() - res / 2, lat.max() + res / 2
    bands = np.linspace(mercator(north), mercator(south), rows + 1)
    top, bottom = mercator(lat + res / 2), mercator(lat - res / 2)
    overlap = np.minimum(bands[:-1, None], top) - np.maximum(bands[1:, None], bottom)
    return overlap.argmax(axis=1)


GRIDS = {
    "the WBB grid": (40.3, 90, 0.01),
    "one degree at 41 N": (40.5, 10, 0.1),
    "20 to 60 N, 0.25 degree": (20.0, 161, 0.25),
    "20 to 60 N, 1 degree": (20.0, 41, 1.0),
    "70 to 85 N, 0.1 degree": (70.0, 151, 0.1),
    "equator to 80 N, 1 degree": (0.0, 81, 1.0),
    "60 to 20 S, 0.5 degree": (-60.0, 81, 0.5),
}


@pytest.mark.parametrize("grid", GRIDS.values(), ids=GRIDS.keys())
def test_image_rows_show_the_grid_row_a_web_map_would(grid):
    got, rows = shown_rows(*grid)
    assert (got == expected(*grid, rows)).all()


@pytest.mark.parametrize("grid", GRIDS.values(), ids=GRIDS.keys())
def test_image_shows_every_grid_row(grid):
    got, _ = shown_rows(*grid)
    assert len(set(got)) == grid[1]


def test_image_of_a_small_grid_has_a_row_per_grid_row():
    """The WBB grid has no row to rescue: its image is the size it always was."""
    assert render.image(footprint(np.ones((90, 100)), lat0=40.3, res=0.01)).shape == (
        90,
        100,
        4,
    )


def test_image_keeps_a_lone_strong_cell_on_a_wide_grid():
    values = np.full((161, 2), 1e-7)
    # Grid rows (south first) the plain, one-row-per-cell image would skip.
    plain = np.unique(expected(20.0, 161, 0.25, 161))
    skipped = sorted(set(range(161)) - set(plain))
    assert skipped
    values[161 - 1 - skipped[len(skipped) // 2], :] = 5e-2
    rgba = render.image(footprint(values, lat0=20.0, res=0.25, hours=1))
    assert (rgba[..., 3] > 0).any()


@pytest.mark.parametrize(
    "contents", [b"", b"[1, 2", b'{"a": 1}', b"[1]", b'[{"x": 1}]']
)
def test_render_names_an_index_that_is_not_one(site, contents):
    fs, root = fsspec.core.url_to_fs(site)
    path = f"{root}/2026-10-29/index.json"
    fs.pipe_file(path, contents)
    with pytest.raises(ValueError, match=r"2026-10-29/index\.json is not a footprint"):
        render.render("/project", site, ["r1a"])


def test_bounds_refuses_a_grid_past_web_mercators_edge():
    """A global grid has edges at +-90 degrees, where a web map has no row."""
    foot = footprint(np.ones((3, 3)), lat0=-60.0, res=60.0)
    with pytest.raises(ValueError, match="latitude"):
        render.bounds(foot)
    with pytest.raises(ValueError, match="latitude"):
        render.image(foot)
    assert render.bounds(footprint(np.ones((3, 3)), lat0=-60.0, res=40.0))
