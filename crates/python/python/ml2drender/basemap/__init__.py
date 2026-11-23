"""Basemaps for matplotlib rendered from MapLibre styles, with contextily's ``add_basemap`` parameters.

contextily draws raster XYZ tiles under data plotted with matplotlib (GeoPandas). This module renders a MapLibre
style (vector tiles, labels, sprites) with ml2drender instead, for the extent of the axes, at their resolution,
directly in their CRS::

    import ml2drender.basemap as mlb

    ax = gdf.plot()
    mlb.add_basemap(ax, crs=gdf.crs, source="OpenFreeMap.Positron")

It needs matplotlib, and pyproj for ``crs``. They are not dependencies of ml2drender: this module imports without
them, and ``add_basemap`` raises an ``ImportError`` with the ``pip install`` command for the missing ones. Sources that
are not MapLibre styles (raster tiles and files) are left to contextily.
"""

from ._plotting import add_attribution, add_basemap
from ._render import set_cache_dir
from ._source import STYLES

__all__ = ["STYLES", "add_attribution", "add_basemap", "set_cache_dir"]
