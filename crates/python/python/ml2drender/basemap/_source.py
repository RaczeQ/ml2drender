"""MapLibre styles for ``source``."""

import calendar
import contextlib
import datetime
import difflib
import importlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

Source = str | os.PathLike[str] | dict[str, Any]
Labels = bool | Literal["only"]


def _imagery(url: str, attribution: str, max_zoom: int) -> dict[str, Any]:
    """A style of one layer: the 256-pixel raster tiles at ``url``, up to ``max_zoom``."""
    source = {"type": "raster", "tiles": [url], "tileSize": 256, "maxzoom": max_zoom, "attribution": attribution}
    layer = {"id": "imagery", "type": "raster", "source": "imagery"}
    return {"version": 8, "sources": {"imagery": source}, "layers": [layer]}


# Mapterhorn's terrain (terrarium-encoded, 0 m over the sea), shaded or coloured by elevation.
_DEM = {"dem": {"type": "raster-dem", "url": "https://tiles.mapterhorn.com/tilejson.json"}}
_HILLSHADE = {"id": "hillshade", "type": "hillshade", "source": "dem"}
# fmt: off
_RELIEF = {
    "id": "relief",
    "type": "color-relief",
    "source": "dem",
    "paint": {
        "color-relief-color": [
            "interpolate", ["linear"], ["elevation"],
            -1, "#93b874", 0, "#c6dbef", 1, "#93b874", 200, "#c5d48d", 500, "#e6d6a0", 1000, "#d4b47e",
            2000, "#b4906f", 3000, "#9c8a80", 4500, "#e9e5e2", 6000, "#ffffff",
        ]
    },
}
# fmt: on
_WMTS = "https://tiles.maps.eox.at/wmts/1.0.0/{}_3857/default/g/{{z}}/{{y}}/{{x}}.jpg"
_EOX = "Sentinel-2 cloudless by EOX IT Services GmbH (contains modified Copernicus Sentinel data {})"
_IGN = (
    "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
    "&STYLE=normal&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
)

#: Named MapLibre styles that load without an API key, for ``source="OpenFreeMap.Positron"`` and the like: the URL of
#: a style's JSON, or the style. Maps of the world, then of national mapping agencies (their country only), terrain
#: and imagery. ``Mapterhorn.Hillshade`` is transparent, to draw over another basemap. Check the terms of each
#: service before heavy use.
STYLES: dict[str, str | dict[str, Any]] = {
    "OpenFreeMap.Liberty": "https://tiles.openfreemap.org/styles/liberty",
    "OpenFreeMap.Bright": "https://tiles.openfreemap.org/styles/bright",
    "OpenFreeMap.Positron": "https://tiles.openfreemap.org/styles/positron",
    "OpenFreeMap.Dark": "https://tiles.openfreemap.org/styles/dark",
    "OpenFreeMap.Fiord": "https://tiles.openfreemap.org/styles/fiord",
    # VersaTiles' palettes, light and dark, also over Mapterhorn's hillshade ("VersaTiles.NaturalDarkTerrain").
    **{
        "VersaTiles." + name.title().replace("-", ""): f"https://tiles.versatiles.org/assets/styles/{name}/style.json"
        for palette in ["colorful", "natural", "muted", "gray", "toner"]
        for name in [palette, f"{palette}-dark", f"{palette}-terrain", f"{palette}-dark-terrain"]
    },
    "CartoDB.Positron": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    "CartoDB.DarkMatter": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    "CartoDB.Voyager": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
    # The same styles without their symbol layers, as labels=False draws them (CARTO has no style of labels only).
    "CartoDB.PositronNoLabels": "https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json",
    "CartoDB.DarkMatterNoLabels": "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json",
    "CartoDB.VoyagerNoLabels": "https://basemaps.cartocdn.com/gl/voyager-nolabels-gl-style/style.json",
    # Maptoolkit's, with relief and, for hiking and cycling, their routes.
    **{
        f"Maptoolkit.{name.title()}": f"https://styles.maptoolkit.org/{name}.json"
        for name in ["summer", "winter", "street", "light", "dark", "cycling", "hiking"]
    },
    # OpenHistoricalMap's, drawn as of today (see preprocess_style).
    **{
        f"OpenHistoricalMap.{name.title().replace('_', '')}": (
            f"https://unpkg.com/@openhistoricalmap/map-styles@latest/dist/{name}/{name}.json"
        )
        for name in ["historical", "japanese_scroll", "woodblock", "railway"]
    },
    "MapLibre.Demotiles": "https://demotiles.maplibre.org/style.json",
    "BasemapDE.Color": "https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/styles/bm_web_col.json",
    "BasemapDE.Gray": "https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/styles/bm_web_gry.json",
    "BasemapDE.Topo": "https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/styles/bm_web_top.json",
    "swisstopo.LightBaseMap": "https://vectortiles.geo.admin.ch/styles/ch.swisstopo.lightbasemap.vt/style.json",
    "swisstopo.BaseMap": "https://vectortiles.geo.admin.ch/styles/ch.swisstopo.basemap.vt/style.json",
    "swisstopo.BaseMapWinter": "https://vectortiles.geo.admin.ch/styles/ch.swisstopo.basemap-winter.vt/style.json",
    "swisstopo.ImageryBaseMap": "https://vectortiles.geo.admin.ch/styles/ch.swisstopo.imagerybasemap.vt/style.json",
    "IGN.Plan": "https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/standard.json",
    "IGN.PlanGris": "https://data.geopf.fr/annexes/ressources/vectorTiles/styles/PLAN.IGN/gris.json",
    "PDOK.Standaard": "https://api.pdok.nl/kadaster/brt-achtergrondkaart/ogc/v1/styles/standaard__webmercatorquad?f=mapbox",
    "PDOK.Darkmode": "https://api.pdok.nl/kadaster/brt-achtergrondkaart/ogc/v1/styles/darkmode__webmercatorquad?f=mapbox",
    "GSI.Standard": "https://gsi-cyberjapan.github.io/gsivectortile-mapbox-gl-js/std.json",
    "GSI.Pale": "https://gsi-cyberjapan.github.io/gsivectortile-mapbox-gl-js/pale.json",
    "Mapterhorn.Hillshade": {"version": 8, "sources": _DEM, "layers": [_HILLSHADE]},
    "Mapterhorn.Relief": {"version": 8, "sources": _DEM, "layers": [_RELIEF, _HILLSHADE]},
    "VersaTiles.Satellite": "https://tiles.versatiles.org/assets/styles/satellite/style.json",
    "EOX.Sentinel2Cloudless": _imagery(_WMTS.format("s2cloudless-2024"), _EOX.format(2024), 15),
    "EOX.Sentinel2Cloudless2016": _imagery(_WMTS.format("s2cloudless"), _EOX.format(2016), 15),
    "USGS.Imagery": _imagery(
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "USGS The National Map: Orthoimagery",
        16,
    ),
    "GSI.Photo": _imagery("https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", "GSI Japan", 18),
    "PDOK.Luchtfoto": _imagery(
        "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg",
        "Beeldmateriaal Nederland / PDOK (CC BY 4.0)",
        21,
    ),
    "IGN.Ortho": _imagery(_IGN, "IGN (Etalab 2.0)", 19),
}
DEFAULT_STYLE = "OpenFreeMap.Liberty"


def resolve(source: object) -> dict[str, Any] | str | None:
    """The MapLibre style ``source`` stands for (a dict, or the URL of its JSON), or None if it is not a style.

    A style is a dict with ``layers``, a name of :data:`STYLES`, a URL with a scheme (``https://``) and no ``{`` (that
    would be a template of tile URLs), or a local ``.json`` file. None is :data:`DEFAULT_STYLE`.
    """
    if source is None:
        return STYLES[DEFAULT_STYLE]
    if isinstance(source, dict):
        return source if "layers" in source else None
    if isinstance(source, str):
        if source in STYLES:
            return STYLES[source]
        if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://", source):
            return None if "{" in source else source
    if isinstance(source, str | os.PathLike) and Path(source).suffix.lower() == ".json":
        return json.loads(Path(source).read_text(encoding="utf-8"))
    return None


def misspelled(source: object) -> str | None:
    """The name of :data:`STYLES` that ``source`` looks like a misspelling of, unless it is a file or the name of a
    provider of xyzservices (contextily's)."""
    if not isinstance(source, str) or os.path.exists(source):
        return None
    with contextlib.suppress(ImportError, ValueError):
        importlib.import_module("xyzservices").providers.query_name(source)
        return None
    names = {name.lower(): name for name in STYLES}
    close = difflib.get_close_matches(source.lower(), names, n=1, cutoff=0.8)
    return names[close[0]] if close else None


def with_labels(style: dict[str, Any], labels: Labels) -> dict[str, Any]:
    """``style`` with its symbol layers (labels and icons) kept (True), removed (False), or alone (``"only"``)."""
    if labels is True:
        return style
    if labels is not False and labels != "only":
        raise ValueError(f"labels must be True, False or 'only', not {labels!r}")
    layers = [layer for layer in style.get("layers", []) if (layer.get("type") == "symbol") == (labels == "only")]
    return {**style, "layers": layers}


# The layers of features, which a filter applies to.
_FEATURES = {"fill", "line", "symbol", "circle", "heatmap", "fill-extrusion"}


def _syntax(layer_filter: object) -> str | None:
    """``"legacy"`` or ``"expression"``: the syntax ml2drender reads a layer filter in (as MapLibre, but for ``any``
    and ``all`` of both, read as an expression), or None for one that reads the same in both (``["has", key]``)."""
    if isinstance(layer_filter, bool):
        return None
    if not isinstance(layer_filter, list) or not layer_filter:
        return "legacy"
    op, args = layer_filter[0], layer_filter[1:]
    if op == "has":
        return "legacy" if not args or args[0] in ("$id", "$type") else None if len(args) == 1 else "expression"
    if op == "in":
        return (
            "expression" if len(args) >= 2 and (not isinstance(args[0], str) or isinstance(args[1], list)) else "legacy"
        )
    if op in ("!in", "!has", "none"):
        return "legacy"
    if op in ("==", "!=", ">", ">=", "<", "<="):
        return "expression" if len(args) != 2 or any(isinstance(arg, list) for arg in args) else "legacy"
    if op in ("any", "all"):
        syntaxes = {_syntax(child) for child in args}
        return "expression" if "expression" in syntaxes else "legacy" if "legacy" in syntaxes else None
    return "expression"


def _at_today(style: dict[str, Any]) -> dict[str, Any]:
    """``style`` with only the features that exist today, as OpenHistoricalMap's website draws its tiles (in which
    the features of all times overlap).

    A feature exists from its ``start_decdate`` (inclusive) to its ``end_decdate``, decimal years (1.0 is New Year's
    Day of 1 CE); without one, from or to any time. The condition joins a layer's own filter in its syntax: a filter
    mixing legacy and expression syntax would be read as an expression.
    """
    today = datetime.date.today()
    year = today.year + (today.timetuple().tm_yday - 1) / (366 if calendar.isleap(today.year) else 365)
    expression = [
        "all",
        ["any", ["!", ["has", "start_decdate"]], ["<=", ["get", "start_decdate"], year]],
        ["any", ["!", ["has", "end_decdate"]], [">", ["get", "end_decdate"], year]],
    ]
    legacy = [
        "all",
        ["any", ["!has", "start_decdate"], ["<=", "start_decdate", year]],
        ["any", ["!has", "end_decdate"], [">", "end_decdate", year]],
    ]

    def dated(layer: dict[str, Any]) -> dict[str, Any]:
        if layer.get("type") not in _FEATURES:
            return layer
        if "filter" not in layer:
            return {**layer, "filter": expression}
        exists = legacy if _syntax(layer["filter"]) == "legacy" else expression
        return {**layer, "filter": ["all", layer["filter"], exists]}

    return {**style, "layers": [dated(layer) for layer in style.get("layers", [])]}


# Changes to the styles of some services, by the URLs of their sources' tiles (or TileJSON): each applies to a style
# with a source of those, as the service's website draws it.
_SERVICES: list[tuple[re.Pattern[str], Callable[[dict[str, Any]], dict[str, Any]]]] = [
    (re.compile(r"https?://([^/]*\.)?openhistoricalmap\.org/"), _at_today),
]


def preprocess_style(style: dict[str, Any], labels: Labels) -> dict[str, Any]:
    """``style`` as ``add_basemap`` draws it: its symbol layers chosen by ``labels`` (see :func:`with_labels`), and
    changed as the website of a service draws its tiles (see :data:`_SERVICES`). ``style`` is left as it was."""
    style = with_labels(style, labels)
    sources = [source for source in style.get("sources", {}).values() if isinstance(source, dict)]
    urls = [url for source in sources for url in [source.get("url"), *source.get("tiles", [])] if isinstance(url, str)]
    for pattern, change in _SERVICES:
        if any(pattern.match(url) for url in urls):
            style = change(style)
    return style
