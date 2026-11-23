"""Basemaps and attributions on matplotlib axes."""

import importlib
import reprlib
import warnings
from typing import TYPE_CHECKING, Any

from ._render import Loader, Timeout, Zoom, attribution_text, check_zoom, crs_of, on_screen, projected_box, render
from ._source import Labels, Source, misspelled, resolve

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.text import Annotation

ATTRIBUTION_SIZE = 8
# CSS pixels in an inch: with the automatic zoom, a style's 12 px text is 9 points high on paper.
_CSS_PER_INCH = 96
# The image's pixels per inch of the figure, at least (two per CSS pixel, as on HiDPI screens): figures of the default
# dpi get twice their resolution, sharp on those screens and saved at twice their dpi; figures of a higher dpi, theirs.
_MIN_DPI = 2 * _CSS_PER_INCH


def add_basemap(
    ax: "Axes",
    zoom: Zoom = "auto",
    source: Source | None = None,
    headers: dict[str, str] | None = None,
    interpolation: str = "bilinear",
    attribution: str | bool | None = None,
    attribution_size: float = ATTRIBUTION_SIZE,
    reset_extent: bool = True,
    crs: Any = None,
    resampling: Any = None,
    zoom_adjust: float | None = None,
    timeout: Timeout = None,
    *,
    labels: Labels = True,
    pixel_ratio: float | None = None,
    load: Loader | None = None,
    **extra_imshow_args: Any,
) -> None:
    """Draw a basemap rendered from a MapLibre style under the data of ``ax``, with contextily's parameters.

    ``source`` is the style: a MapLibre style dict, the URL of a style JSON, a name of :data:`STYLES` (default
    ``"OpenFreeMap.Liberty"``) or a local ``.json`` file; a misspelled name raises ``ValueError``. Any other source (a
    tile provider or its name, a URL of tiles, a raster file) is contextily's: it is drawn by ``contextily.add_basemap``
    with a ``UserWarning``, or raises ``ImportError`` without contextily.

    The extent of ``ax`` is in ``crs``: EPSG:3857 (Spherical Mercator) by default, or anything
    ``pyproj.CRS.from_user_input`` reads (a GeoDataFrame's ``crs``, ``"EPSG:4326"``, a PROJJSON dict; requires
    pyproj). The style is drawn in that CRS directly (for the projections ml2drender supports), so ``resampling``,
    contextily's warping of raster tiles, is ignored (with a ``UserWarning``). Axes that stretch the latitudes of a
    geographic CRS (as GeoPandas does) get the equidistant cylindrical projection they show, so labels keep their shape.

    ``zoom`` is the level of detail as in contextily: the zoom of 256-pixel XYZ tiles of the same scale (the style
    is evaluated at MapLibre zoom ``zoom - 1``), plus ``zoom_adjust``; both may be fractional. ``"auto"`` draws the
    style at its size on a web page (96 CSS pixels to the inch of the figure). Either way the image covers the extent of
    ``ax`` exactly, with 192 pixels to the inch of the figure, or its dpi if higher: figures of the default dpi get
    twice their resolution, for HiDPI screens and saving at twice their dpi. ``pixel_ratio``, image pixels per CSS
    pixel, overrides that (with ``zoom="auto"``, ``dpi / 96`` matches a figure of ``dpi``). The size of ``ax`` is read
    as drawn (after the layout engine of the figure, such as ``layout="constrained"``, and its aspect), but a layout
    made later (``fig.tight_layout()``) stretches the image: lay the figure out first.

    ``labels=False`` drops the style's symbol layers (labels and icons); ``labels="only"`` keeps only them, as an
    overlay above the data (``zorder`` 9 unless given). Styles of OpenHistoricalMap's tiles are drawn as of today, as on
    its website.

    ``headers`` are sent with every request and ``timeout`` (seconds, a (connect, read) tuple, or None for no limit as
    in contextily) applies to each. Failed requests (network errors, timeouts, HTTP 429 and 5xx) are tried twice more,
    after 1 and 2 seconds. Downloads are cached on disk, by URL and ``headers`` (see :func:`set_cache_dir`), but for
    the style and its TileJSON, which name the current tiles. ``load``, an async ``load(url)`` returning bytes or None
    for a missing resource, replaces that fetching (it runs on a private event loop).

    The image is drawn with ``imshow`` with ``interpolation`` and ``extra_imshow_args``, keeping the aspect of ``ax``,
    and ``reset_extent`` keeps its limits. ``attribution`` is the text in the corner (by default or True, the
    attributions of the style and the sources it draws; False for none) in ``attribution_size`` points, added to the
    one of a basemap drawn before.
    """
    style = resolve(source)
    if style is None:
        if (name := misspelled(source)) is not None:
            message = f"{source!r} is neither a name of STYLES (did you mean {name!r}?) nor a source of contextily"
            raise ValueError(message)
        if labels is not True or pixel_ratio is not None or load is not None:
            raise TypeError(
                f"labels, pixel_ratio and load apply to MapLibre styles only, not to {reprlib.repr(source)}"
            )
        options = {
            "zoom": zoom,
            "headers": headers,
            "interpolation": interpolation,
            "attribution": attribution,
            "attribution_size": attribution_size,
            "reset_extent": reset_extent,
            "crs": crs,
            "resampling": resampling,
            "zoom_adjust": zoom_adjust,
            "timeout": timeout,
        }
        # None is contextily's default too: leave it out, for versions without headers or timeout.
        _contextily(ax, source, {key: value for key, value in options.items() if value is not None}, extra_imshow_args)
        return
    _require("add_basemap", ["matplotlib"] if crs is None else ["matplotlib", "pyproj"])
    if resampling is not None:
        warnings.warn("resampling is ignored: styles are drawn in the CRS of the axes, not warped", stacklevel=2)
    level = check_zoom(zoom, zoom_adjust)
    # The size and limits of ax as drawn: set by the layout engine of the figure (constrained, tight) and the aspect.
    figure = ax.figure.figure
    if figure.get_layout_engine() is not None:
        figure.draw_without_rendering()
    ax.apply_aspect()
    xmin, xmax, ymin, ymax = ax.axis()
    (left, right), (bottom, top) = sorted((xmin, xmax)), sorted((ymin, ymax))
    extent = (left, right, bottom, top)
    display = ax.get_window_extent()
    stretch = display.height * (right - left) / (display.width * (top - bottom)) if display.width else 1
    target, (left, bottom, right, top) = on_screen(crs_of(crs), (left, bottom, right, top), stretch)
    width, height = right - left, top - bottom
    if level is None:
        scale = _CSS_PER_INCH / ax.figure.dpi * max(display.width / width, display.height / height)
        scale *= 2 ** (zoom_adjust or 0)
    else:
        scale = 256 * 2**level / target.units_per_turn  # level: a zoom of 256-pixel XYZ tiles
    # The long side in whole CSS pixels (at least one): the scale drawn.
    size = max(1, round(max(width, height) * scale))
    if pixel_ratio is None:
        axes_ratio = max(display.width / width, display.height / height) * max(width, height) / size
        pixel_ratio = axes_ratio * max(1, _MIN_DPI / ax.figure.dpi)
    box = projected_box((left, bottom, right, top), size, pixel_ratio)
    image, text = render(style, target, box, labels, load, headers, timeout)
    if labels == "only":
        extra_imshow_args.setdefault("zorder", 9)
    extra_imshow_args.setdefault("aspect", ax.get_aspect())  # imshow's default would make it equal
    ax.imshow(image, extent=extent, interpolation=interpolation, **extra_imshow_args)
    if reset_extent:
        ax.axis((xmin, xmax, ymin, ymax))
    if attribution is None or attribution is True:
        attribution = text
    if attribution:
        from ._attribution import Attribution

        # Basemaps drawn over one another (a hillshade over a map) share one attribution.
        drawn = [artist for artist in ax.texts if isinstance(artist, Attribution)]
        if drawn:
            drawn[-1].set_text(attribution_text([drawn[-1].get_text(), str(attribution)]))
        else:
            add_attribution(ax, str(attribution), font_size=attribution_size)


def _require(function: str, packages: list[str]) -> None:
    """Raise ``ImportError`` with the pip command for those of ``packages`` that ``function`` needs and are missing.

    They are not dependencies of ml2drender, as GeoPandas' ``explore`` asks for folium and others when called.
    """
    missing = []
    for package in packages:
        try:
            importlib.import_module(package)
        except ImportError:
            missing.append(package)
    if missing:
        message = f"{function}() requires {' and '.join(missing)}: pip install {' '.join(missing)}"
        raise ImportError(message, name=missing[0])


def _contextily(ax: "Axes", source: object, options: dict[str, Any], extra_imshow_args: dict[str, Any]) -> None:
    """Draw ``source``, which is not a MapLibre style, with ``contextily.add_basemap``."""
    try:
        contextily = importlib.import_module("contextily")
    except ImportError as error:
        message = (
            f"source {reprlib.repr(source)} is not a MapLibre style. For raster tiles and files, install contextily "
            "(pip install contextily) and use contextily.add_basemap"
        )
        raise ImportError(message, name="contextily") from error
    warnings.warn(
        f"source {reprlib.repr(source)} is not a MapLibre style: drawn by contextily.add_basemap", stacklevel=3
    )
    contextily.add_basemap(ax, source=source, **options, **extra_imshow_args)


def add_attribution(ax: "Axes", text: str, font_size: float = ATTRIBUTION_SIZE, **kwargs: Any) -> "Annotation":
    """Write ``text`` in the bottom right corner of ``ax`` on a translucent white box, as MapLibre's control does.

    Its words are wrapped to the width of ``ax`` whenever the figure is drawn, and what still does not fit (a word
    wider than the axes) is clipped. ``font_size`` is in points; ``kwargs`` go to matplotlib's ``Annotation``, over
    these defaults. The font is matplotlib's default, backed up by an installed font of Chinese, Japanese and Korean
    (Hiragino Sans, Yu Gothic, Noto Sans CJK JP, ...) for attributions in those. Returns the text.
    """
    _require("add_attribution", ["matplotlib"])
    from matplotlib import cbook
    from matplotlib.text import Text

    from ._attribution import PAD, Attribution, font_family

    # Under matplotlib's own names, which kwargs' aliases (fontsize for size, ha, ...) are turned into.
    options: dict[str, Any] = {
        "xycoords": "axes fraction",
        "xytext": (-PAD * font_size, PAD * font_size),
        "textcoords": "offset points",
        "fontsize": font_size,
        "fontfamily": font_family(),
        "horizontalalignment": "right",
        "verticalalignment": "bottom",
        "zorder": 10,
        "clip_on": True,
        "parse_math": False,
        "bbox": {"boxstyle": f"square,pad={PAD}", "facecolor": "white", "alpha": 0.7, "edgecolor": "none"},
    }
    artist = Attribution(text, (1, 0), **(options | cbook.normalize_kwargs(kwargs, Text)))
    ax.add_artist(artist)
    return artist
