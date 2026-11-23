"""Rendering a box of a CRS to an RGBA array, from synchronous code."""

import asyncio
import atexit
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from numbers import Real
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, TypeVar

import httpx

from ml2drender import ProjectedBox, Renderer

from ._source import Labels, preprocess_style

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

Loader = Callable[[str], Awaitable[bytes | None]]
Zoom = float | Literal["auto"]
Timeout = float | tuple[float, float] | None

USER_AGENT = "ml2drender (+https://github.com/Kanahiro/ml2drender)"
_EARTH_RADIUS = 6378137.0
# EPSG:3857, for the default CRS without pyproj: what ml2drender reads of its PROJJSON.
_WEB_MERCATOR = {
    "type": "ProjectedCRS",
    "base_crs": {"datum": {"ellipsoid": {"semi_major_axis": _EARTH_RADIUS, "inverse_flattening": 298.257223563}}},
    "conversion": {"method": {"name": "Popular Visualisation Pseudo Mercator"}},
}

T = TypeVar("T")


class Crs(NamedTuple):
    """The PROJJSON of a CRS, and its units in a turn of Web Mercator's equator (how ml2drender reads zooms)."""

    projjson: dict[str, Any]
    units_per_turn: float
    geographic: bool = False


def crs_of(crs: Any) -> Crs:
    """The :class:`Crs` of ``crs``; None is EPSG:3857 (without pyproj)."""
    turn = 2 * math.pi * _EARTH_RADIUS
    if crs is None:
        return Crs(_WEB_MERCATOR, turn)
    import pyproj

    crs = pyproj.CRS.from_user_input(crs)
    if crs.is_geographic:
        return Crs(crs.to_json_dict(), 360.0, geographic=True)
    return Crs(crs.to_json_dict(), turn / crs.axis_info[0].unit_conversion_factor)


Bbox = tuple[float, float, float, float]


def on_screen(crs: Crs, bbox: Bbox, stretch: float) -> tuple[Crs, Bbox]:
    """``crs`` and ``bbox`` (min x, min y, max x, max y) of it to draw axes that show a unit of y ``stretch`` times as
    long as one of x.

    Axes of geographic CRSs show latitudes stretched (GeoPandas' by 1 / cos of the middle latitude), so an image in
    degrees would be too, labels and all. Those get the projection the axes show, with square pixels on screen: the
    equidistant cylindrical x = R λ, y = stretch R φ (λ, φ in radians), true to scale on the parallel of cosine
    1 / stretch of a sphere of radius stretch R. Other CRSs and stretches are drawn as they are.
    """
    if not crs.geographic or not stretch > 1:
        return crs, bbox
    projjson = {
        "type": "ProjectedCRS",
        "base_crs": {"datum": {"ellipsoid": {"radius": stretch * _EARTH_RADIUS}}},
        "conversion": {
            "method": {"name": "Equidistant Cylindrical"},
            "parameters": [
                {"name": "Latitude of 1st standard parallel", "value": math.degrees(math.acos(1 / stretch))},
            ],
        },
    }
    meters = math.radians(_EARTH_RADIUS)  # in a degree
    left, bottom, right, top = bbox
    box = (left * meters, bottom * meters * stretch, right * meters, top * meters * stretch)
    return Crs(projjson, 2 * math.pi * _EARTH_RADIUS), box


def check_zoom(zoom: Zoom, zoom_adjust: float | None) -> float | None:
    """``zoom`` plus ``zoom_adjust``, or None for ``"auto"``."""
    if isinstance(zoom, str) and zoom == "auto":
        return None
    if isinstance(zoom, bool) or not isinstance(zoom, Real):
        raise TypeError(f"zoom must be a number or 'auto', not {zoom!r}")
    return float(zoom) + (zoom_adjust or 0)


def projected_box(bbox: Bbox, size: int, pixel_ratio: float) -> ProjectedBox:
    """``bbox`` (min x, min y, max x, max y), ``size`` CSS pixels on its long side."""
    if not 1 <= size < 2**32 or not pixel_ratio > 0:
        raise ValueError(f"cannot draw {bbox} {size} CSS pixels wide at pixel ratio {pixel_ratio}")
    return ProjectedBox(bbox, size, pixel_ratio)


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    """Run ``coroutine`` to completion; in a thread of its own when an event loop runs here (Jupyter).

    Interrupting the wait (KeyboardInterrupt) cancels it there.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    loop = asyncio.new_event_loop()
    task = loop.create_task(coroutine)

    def complete() -> T:
        # The loop is closed by its thread, once stopped: an interrupt of the wait below may come before.
        try:
            return loop.run_until_complete(task)
        finally:
            loop.close()

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(complete)
        try:
            return future.result()
        except KeyboardInterrupt:
            # The pool waits for the thread to stop at its next await (unless the render ended and closed the loop).
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)
            raise


def attribution_text(values: Iterable[object]) -> str:
    """The attributions among ``values`` (HTML or text), as plain text joined by `` | ``.

    As MapLibre's attribution control, those repeated or part of another are left out.
    """
    texts: list[str] = []
    for value in values:
        if isinstance(value, str):
            text = " ".join(unescape(re.sub(r"<[^>]*>", "", value)).split())
            if text and text not in texts:
                texts.append(text)
    return " | ".join(text for text in texts if not any(text in other for other in texts if other != text))


def _client(headers: dict[str, str] | None, timeout: Timeout) -> httpx.AsyncClient:
    merged = httpx.Headers({"User-Agent": USER_AGENT})
    merged.update(headers or {})  # case-insensitive: a user-agent of the caller replaces ours
    # None: no time limit, as in contextily.
    limit = httpx.Timeout(timeout[1], connect=timeout[0]) if isinstance(timeout, tuple) else timeout
    return httpx.AsyncClient(headers=merged, follow_redirects=True, timeout=limit)


# Where downloads are kept: a temporary directory made on first use and removed at exit, unless set_cache_dir.
_cache_dir: Path | None = None


def set_cache_dir(path: str | os.PathLike[str]) -> None:
    """Keep the resources ``add_basemap`` downloads in ``path`` (made if missing), as contextily's ``set_cache_dir``.

    By default they are kept in a temporary directory removed when Python exits. A directory set here is kept, so a
    later session that sets it again reuses them; delete it to download them again.
    """
    global _cache_dir
    _cache_dir = Path(path)


def _cache_file(url: str, headers: dict[str, str] | None) -> Path:
    """The file keeping the response to ``url`` requested with ``headers``."""
    global _cache_dir
    if _cache_dir is None:
        _cache_dir = Path(tempfile.mkdtemp(prefix="ml2drender-"))
        atexit.register(shutil.rmtree, _cache_dir, ignore_errors=True)
    key = json.dumps([url, sorted((headers or {}).items())])
    return _cache_dir / hashlib.sha256(key.encode()).hexdigest()


# Failed requests (network errors, timeouts, HTTP 429 and 5xx) are tried again this many times, after 1 s, 2 s, ...
_RETRIES = 2
_BACKOFF = 1.0


async def _fetch(client: httpx.AsyncClient, url: str, file: Path | None) -> bytes | None:
    """The response to ``url``, kept in ``file`` (None: not cached), or None for a missing resource (not kept): HTTP
    404, or 400, with which WMTS servers answer tiles out of their range (swisstopo's, outside Switzerland)."""
    if file is not None:
        with contextlib.suppress(FileNotFoundError):
            return file.read_bytes()
    for attempt in range(_RETRIES + 1):
        try:
            response = await client.get(url)
        except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError):
            if attempt == _RETRIES:
                raise
        else:
            if attempt == _RETRIES or response.status_code != 429 and response.status_code < 500:
                break
        await asyncio.sleep(_BACKOFF * 2**attempt)
    if response.status_code in (400, 404):
        return None
    response.raise_for_status()
    if file is None:
        return response.content
    file.parent.mkdir(parents=True, exist_ok=True)
    partial = file.with_name(f"{file.name}.{uuid.uuid4().hex}")  # no half-written file if interrupted
    partial.write_bytes(response.content)
    partial.replace(file)
    return response.content


async def _render(
    style: dict[str, Any] | str,
    crs: Crs,
    box: ProjectedBox,
    labels: Labels,
    load: Loader | None,
    headers: dict[str, str] | None,
    timeout: Timeout,
) -> tuple[bytes, str]:
    async with contextlib.AsyncExitStack() as stack:
        # The style and its TileJSON are not cached: they name the current tiles, whose URLs may change with the data
        # (OpenFreeMap's carry its date), so a kept copy could point to tiles removed since.
        current: set[str] = set()
        if load is None:
            client = await stack.enter_async_context(_client(headers, timeout))

            async def fetch(url: str) -> bytes | None:
                return await _fetch(client, url, None if url in current else _cache_file(url, headers))

            load = fetch
        spec = style
        if isinstance(spec, str):
            current.add(spec)
            data = await load(spec)
            if data is None:
                raise ValueError(f"style not found: {spec}")
            spec = json.loads(data)
        spec = preprocess_style(spec, labels)
        sources = {name: source for name, source in spec.get("sources", {}).items() if isinstance(source, dict)}
        current.update(source["url"] for source in sources.values() if isinstance(source.get("url"), str))
        # As MapLibre's attribution control: the sources of the layers drawn (not hidden by their visibility); and the
        # style's own, as some carry one (OpenHistoricalMap's).
        used = {
            layer.get("source")
            for layer in spec.get("layers", [])
            if isinstance(layer, dict) and layer.get("layout", {}).get("visibility") != "none"
        }
        drawn = [source for name, source in sources.items() if name in used]
        attributions: list[object] = [spec.get("attribution"), *(source.get("attribution") for source in drawn)]
        tilejsons = {source["url"] for source in drawn if isinstance(source.get("url"), str)}
        get = load

        async def tracked(url: str) -> bytes | None:
            data = await get(url)
            if data is not None and url in tilejsons:
                with contextlib.suppress(ValueError, AttributeError):
                    attributions.append(json.loads(data).get("attribution"))
            return data

        rgba = await Renderer(spec, tracked).projected(crs.projjson).render(box)
    return rgba, attribution_text(attributions)


def render(
    style: dict[str, Any] | str,
    crs: Crs,
    box: ProjectedBox,
    labels: Labels = True,
    load: Loader | None = None,
    headers: dict[str, str] | None = None,
    timeout: Timeout = None,
) -> tuple["NDArray[np.uint8]", str]:
    """The (height, width, 4) RGBA image of ``box`` of ``crs`` and the attribution of the style's sources."""
    import numpy as np

    rgba, attribution = run(_render(style, crs, box, labels, load, headers, timeout))
    width, height = box.pixels
    return np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4).copy(), attribution
