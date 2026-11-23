# ml2drender

**A 2D map renderer for MapLibre styles, written in Rust. It needs no GPU.** It compiles to WebAssembly and runs in browsers, Cloudflare Workers, Deno and Node.js. Node-API and Python bindings are also included.

[Live demo](https://kanahiro.github.io/ml2drender/) · [API reference](https://kanahiro.github.io/ml2drender/api/) · [Quick start](#quick-start) · [Projected CRSs](#projected-coordinate-reference-systems) · [Node.js addon](https://github.com/Kanahiro/ml2drender/tree/main/packages/ml2drender-node#readme) · [Python](https://github.com/Kanahiro/ml2drender/tree/main/crates/python#readme) ([Colab notebook](https://colab.research.google.com/github/Kanahiro/ml2drender/blob/main/examples/python/quickstart.ipynb))

- **No GPU, no native dependencies.** It is a software rasterizer in pure Rust. It runs on `wasm32-unknown-unknown` and links no C libraries.
- **Faithful to MapLibre.** Bucket building, shader math and quantization are ported from MapLibre GL JS. It passes 98.3% of the applicable upstream render tests at GL JS's own tolerances ([conformance](#conformance)).
- **Any projected CRS.** It draws maps directly in a CRS given as PROJJSON: UTM, national grids (including US State Plane in feet), Albers, LAEA, polar and oblique stereographic, world maps (Equal Earth, Robinson, Mollweide, Winkel Tripel…), orthographic and geostationary views and more.
- **MapLibre GL JS in any CRS.** Every source of a style (vector tiles, GeoJSON, raster, DEM) can be reprojected and served on a CRS's tile grid. MapLibre GL JS can then draw the original style in that CRS, with interaction and feature queries.
- **Made for serverless.** The core does no network or file I/O; the host fetches everything.

> [!NOTE]
> ml2drender draws flat 2D maps. 3D features (fill-extrusion, terrain, globe, pitch) are out of scope; layers of types it does not draw (fill-extrusion) are skipped, so rewrite them in the style beforehand if you want them drawn (for example as fills). Styles are fixed when a renderer is created: there is no API for changing a style at runtime.

## Quick start

```ts
import { MapRenderer } from '@ml2drender/wasm';

const style = await (await fetch('https://demotiles.maplibre.org/style.json')).json();
const renderer = await MapRenderer.init(style);

const image = await renderer.renderView({ center: [139.767, 35.681], zoom: 5, width: 512, height: 512 });
const png = image.toPng(); // or image.toWebp(), image.toJpeg(80); raw RGBA in image.data
```

The renderer works out which tiles, TileJSON, sprites and glyphs it needs, and fetches them. In browsers (with Vite or similar), the bundled WASM loads automatically. Elsewhere, pass the compiled module as the `wasm` option: `await WebAssembly.compile(readFileSync(new URL(import.meta.resolve('@ml2drender/wasm/renderer.wasm'))))` in Node.js and Deno, or `import wasm from '@ml2drender/wasm/renderer.wasm'` in Cloudflare Workers (Wrangler).

### Installation

```sh
npm install @ml2drender/wasm   # WebAssembly: browsers, Node.js, Deno, Workers
npm install @ml2drender/node   # native Node.js addon
pip install ml2drender         # Python
```

| Package | Runs in | API |
|---|---|---|
| [`@ml2drender/wasm`](#javascript-api) | browsers, Node.js, Deno, Cloudflare Workers | Everything below: fetching, Web Mercator and projected views, reprojected sources for GL JS. |
| [`@ml2drender/node`](https://github.com/Kanahiro/ml2drender/tree/main/packages/ml2drender-node#readme) | Node.js (Linux, macOS, Windows) | HTTP fetching by default, with an optional `loadResource(url)` callback. Web Mercator cameras; boxes, tiles and cameras of a projected CRS. Renders run in parallel off the JS thread. |
| [`ml2drender` on PyPI](https://github.com/Kanahiro/ml2drender/tree/main/crates/python#readme) | CPython 3.11+ (Linux, macOS, Windows) | Async HTTP fetching with HTTPX by default, with an optional async `load(url)` callback. Web Mercator cameras; boxes, tiles and cameras of a projected CRS. Basemaps for matplotlib and GeoPandas with contextily's `add_basemap` parameters ([`ml2drender.basemap`](https://github.com/Kanahiro/ml2drender/tree/main/crates/python#basemaps-for-matplotlib)). |

All three draw with the same Rust core. To build from source, see [Development](#development).

## Usage

### Web Mercator images

```ts
const renderer = await MapRenderer.init(style);

await renderer.renderView({ center: [lng, lat], zoom: 12, bearing: 30, width: 800, height: 600 }); // a camera
await renderer.renderTile({ z: 12, x: 3638, y: 1612 });                                            // an XYZ tile
await renderer.render({ bbox: [139.5, 35.5, 140, 36], size: 1024 });                               // a lng/lat box
```

In a browser, the pixels of a `RenderedImage` go straight onto a canvas:

```ts
canvas.getContext('2d')!.putImageData(new ImageData(image.data, image.width, image.height), 0, 0);
```

### Images in a projected CRS

```ts
const utm = renderer.projected({
  name: 'EPSG:32654',
  projjson: utm54n, // PROJJSON of EPSG:32654
  tileMatrixSet: { origin: [-2_000_000, 6_000_000], extentAtZoom0: 4_000_000 },
});

await utm.renderView({ center: [139.767, 35.681], zoom: 6, bearing: 30, width: 800, height: 600 }); // a camera
await utm.render({ bbox: [300_000, 3_900_000, 500_000, 4_100_000], size: 512 });                     // a box in CRS units
await utm.renderTile({ z: 3, x: 4, y: 2 });                                                         // a tile of the CRS's grid
```

`renderView` takes `center` as lng/lat and a zoom of the `tileMatrixSet` grid. It draws the same area as MapLibre GL JS does with that `center`, `zoom` and `bearing` once the CRS is registered with `addProjection`.

### MapLibre GL JS in any CRS

ml2drender returns each source reprojected onto the CRS's tile grid, and MapLibre GL JS draws the original style from those tiles. The CRS is registered with GL JS 6.15's experimental `addProjection`. GL JS draws the map with WebGL, so it stays interactive and features can be queried.

```ts
// Server (e.g. Workers): serve reprojected tiles of each source
const crs = { name: 'EPSG:32654', projjson: utm54n, tileMatrixSet };
const tile = await renderer.projected(crs).getTile({ source, z, x, y }); // MVT, PNG or terrarium PNG

// Browser: register the CRS with GL JS and draw the rewritten style
import { addProjection, Map } from 'maplibre-gl';
import proj4 from 'proj4';

addProjection({ name: crs.name, projection: proj4(crs.projjson), tileMatrixSet: crs.tileMatrixSet });
const glStyle = renderer.projected(crs).toStyle({ tiles: (source) => `https://tiles.example.com/${source}/{z}/{x}/{y}` });
new Map({ container: 'map', style: glStyle });
```

`addProjection` takes any `forward` / `inverse` pair between lng/lat and CRS coordinates, such as a [proj4js](https://github.com/proj4js/proj4js) converter, which reads PROJJSON. ml2drender draws with its own formulas; for the CRSs of the demo the two agree within a few micrometres. Two things to know:

- proj4js does not read the Geostationary Satellite method from PROJJSON. Give it the PROJ string (`+proj=geos ...`) instead, as the [demo](https://github.com/Kanahiro/ml2drender/blob/main/examples/browser/src/proj.ts) does.
- If GL JS also draws GeoJSON sources itself, its workers need the same converter. Set it with `setProjectionHandler`, as the [demo](https://github.com/Kanahiro/ml2drender/blob/main/examples/browser/src/gl-projection-handler.ts) does.

## JavaScript API

The `@ml2drender/wasm` package (WebAssembly). This section is an overview. Every type and option is documented in the [API reference](https://kanahiro.github.io/ml2drender/api/).

### MapRenderer.init(style, options?)

Takes a style object (the official `StyleSpecification` type) and loads the WASM. The style is copied when `init` is called. Resource URLs reach `loadResource` exactly as the style writes them, so make `glyphs` and TileJSON `url`s absolute.

| Option | Default | Description |
|---|---|---|
| `loadResource` | HTTP `fetch` | `(url) => Promise<Uint8Array \| null>`. Fetches tiles, TileJSON, sprites and glyphs. Return `null` (HTTP 404) for a missing resource and rendering goes on without it; any other failure should reject. Concurrency limits, retries and caching belong here. |
| `wasm` | bundled WASM | A compiled `WebAssembly.Module`. Required in Node.js and Cloudflare Workers. |
| `cache.ttlMs` | `300_000` | How long the renderer inside WASM (decoded tiles, geometry, glyphs) is reused across renders. `0` creates and frees one per render, for the least memory; `Infinity` never expires. |

Concurrent requests for the same URL are fetched once. Fetched bytes are not cached: rely on the browser's HTTP cache, or cache inside `loadResource` (for example with the Cache API on Workers).

Renderers with `cache.ttlMs: 0` share one WASM instance per module, and WASM memory never shrinks. Overlapping renders each hold their decoded data while they wait for fetches, so peak memory grows with the number of renders in flight. Where memory is tight, limit that number yourself:

```ts
import pLimit from 'p-limit';

const limit = pLimit(2); // one limit for every renderer of the module
const image = await limit(() => renderer.renderTile({ z, x, y }));
```

### Rendering methods

Each one fetches what it needs and resolves to a `RenderedImage` of all the style's layers. One renderer can mix views, methods and CRSs, even concurrently, and they share fetched resources.

| Method | Area drawn |
|---|---|
| `renderView({ center, zoom, bearing?, width, height })` | A Web Mercator camera. Zoom levels are GL JS's: at zoom 0 the world is 512 CSS px wide. |
| `renderTile({ z, x, y, tileSize? })` | An XYZ tile of the Web Mercator grid. `tileSize` defaults to 256. |
| `render({ bbox, size })` | A lng/lat box `[west, south, east, north]`, fitted so that its long side is `size` px. `west > east` crosses the antimeridian. |
| `projected(crs).renderView({ center, zoom, bearing?, width, height })` | A camera in the CRS, with the same arguments as the Mercator `renderView`. `center` is lng/lat. `bearing` is the angle clockwise from the CRS's y axis of the direction that is up, so at 0 the y axis is up. Zoom levels are those of GL JS with the CRS registered by `addProjection`: at zoom 0, tile 0/0/0 of the grid is 512 CSS px wide. A `center` outside the CRS's domain throws a `RangeError`. |
| `projected(crs).render({ bbox, size })` | A box `[minX, minY, maxX, maxY]` in CRS units (degrees for longlat), y axis up, fitted so that its long side is `size` px. For a rotated view, use `renderView`. |
| `projected(crs).renderTile({ z, x, y, tileSize? })` | A tile of the CRS's grid. |

All of them also accept two options:

- `pixelRatio` (default 1): the output has CSS px × `pixelRatio` pixels.
- `margin` (default 0): extra CSS px drawn on every side and then cropped. This reduces symbols that go missing because their anchor is just outside a tile.

A `RenderedImage` has:

- `width` and `height`.
- `data`: row-major RGBA pixels in a `Uint8ClampedArray`.
- `toPng()` and `toWebp()`: a lossless PNG or WebP, encoded inside WASM. WebP is about 30% smaller than PNG.
- `toJpeg(quality)`: a JPEG encoded inside WASM. `quality` is 1–100, chroma is subsampled 4:2:0, and transparent pixels become black.

### renderer.projected(crs)

Returns a `ProjectedRenderer` that draws in `crs`. The original renderer is unchanged. `crs` has the same shape as a CRS registered with MapLibre GL JS's `addProjection`:

- `projjson`: the CRS as PROJJSON. `render` of a box needs nothing else.
- `tileMatrixSet`: required by `renderTile`, `getTile` and `renderView`. A CRS has no tile grid of its own, so tiles and zoom levels mean nothing without one. Either form works:
  - `{ origin: [minX, maxY], extentAtZoom0 }`, MapLibre GL JS's grid (`addProjection`). Tile 0/0/0 is a square of side `extentAtZoom0` whose top-left corner is `origin`. Each zoom level splits every tile into four. x grows to the east and y to the south.
  - An [OGC 2D TileMatrixSet](https://docs.ogc.org/is/17-083r4/17-083r4.html) (2.0, JSON), such as `WorldCRS84Quad` or a national grid with its own scales. `z` is an index of its `tileMatrices`, and `x`/`y` range over each level's `matrixWidth`/`matrixHeight`. `pointOfOrigin` is in the CRS's axis order (from `orderedAxes` when it says Lat/Lon or N/E, else from the PROJJSON), and `cornerOfOrigin: "bottomLeft"` counts rows up. Tiles that are not square render `tileSize` wide. `variableMatrixWidths` are not supported. GL JS's `addProjection` takes only the first form, so tiles from `getTile` that GL JS reads with `toStyle`'s style must be cut on that grid (`getTile` itself works with either form).

  Which to use: the first when the tiles are shown by MapLibre GL JS (pass the `tileMatrixSet` given to `addProjection`), with no limit on zoom levels. An OGC set for a published grid (a TileMatrixSet registry, an OGC API - Tiles server, a national grid), or for one the first cannot describe: scales not halved at each level, tiles not square or not 2^z × 2^z, a fixed list of levels. A GL JS grid is also the OGC set whose level z has 512 × 512 px tiles, `cellSize` = `extentAtZoom0 / 512 / 2^z` and 2^z × 2^z tiles from `origin`.

- `name`: the CRS's name in GL JS, required by `toStyle` only.

Projected renders pick a zoom level from CRS units per CSS px, read on EPSG:3857's scale at the equator (the idea behind OGC scale denominators; for longlat, 360° is one trip around the world). That zoom drives style evaluation and source tile selection. It does not depend on position, so tiles of one grid zoom line up at their edges. At high latitudes the zoom is higher than a Mercator map of the same ground resolution would use. It is not the grid zoom given to `renderView`: the two agree only when `extentAtZoom0` (or the width of a tile of an OGC set's first level) is the width of EPSG:3857's world (about 40,075 km, or 360° for longlat).

Besides `renderView`, `render` and `renderTile`, it has two methods for MapLibre GL JS:

#### getTile({ source, z, x, y })

Returns the data of one style source, reprojected into a tile of the grid, as a `Promise<Uint8Array>` that GL JS reads directly. It is data, not a picture: style layers and filters are not applied. Source data is fetched at the zoom the tile would have if drawn at 512 CSS px.

| Source | Result |
|---|---|
| vector / geojson | An MVT tile with extent 4096 and a buffer of 128 units. All layers, features and properties are kept. A GeoJSON source's layer is named after the source. |
| raster / image | A 512 px PNG, drawn as a raster layer with default paint. Image sources become tiles too. |
| raster-dem | A 512 px PNG of each pixel's elevation, bilinearly interpolated from the source DEM and encoded as `terrarium`. |

How vector data is reprojected:

- Polygons cut at source tile edges are merged with their fragments that have the same properties, and only then projected. Source tile boundaries leave no seams. IDs are kept only when all fragments share one.
- Lines stay split at source tile boundaries.
- Anything outside the projection's domain is removed (for example the far side of the globe in a geostationary view). Lines stop at the edge of the domain, and polygons are closed along it.

#### toStyle({ tiles })

Returns the style given to `init`, rewritten so that GL JS can draw it in CRS `crs.name` from `getTile`'s tiles.

- `tiles(sourceId)` returns the URL template of each source's tiles (`{z}/{x}/{y}` of the CRS's grid).
- vector and geojson sources become vector sources. raster and image sources become raster sources. raster-dem sources become terrarium raster-dem sources. All use 512 px tiles.
- Layers of GeoJSON sources get a `source-layer`.
- Other layers, fill-extrusion included, are kept. Layers of video sources are removed.

GL JS evaluates the style at its own zoom on the projected grid, and hillshade slopes come from the tile's zoom. Zoom-dependent styling may therefore look slightly different from ml2drender's own renders.

## Projected coordinate reference systems

CRSs are given as PROJJSON (`GeographicCRS` or `ProjectedCRS`, or a `BoundCRS` or `CompoundCRS` of one, whose datum shift and vertical part are ignored). PROJ strings and WKT are not accepted. You can get PROJJSON from `projinfo -o PROJJSON -q EPSG:32654`, for example.

- **Supported methods:** longlat, Transverse Mercator (UTM, national grids), Mercator (including Web Mercator), Lambert Conformal Conic, Albers Equal Area, Equidistant Conic, Polar and Oblique Stereographic, Hotine Oblique Mercator (variants A and B), Krovak (North Orientated), Lambert Azimuthal Equal Area, Azimuthal Equidistant, Lambert Cylindrical Equal Area, Equidistant Cylindrical, Cassini-Soldner, American Polyconic, Orthographic, Equal Earth, Mollweide, Robinson, Sinusoidal, Natural Earth, Winkel Tripel, Miller Cylindrical and Geostationary Satellite (geos), including EPSG's spherical variants. Any ellipsoid, linear unit (e.g. US survey feet) and prime meridian works. A geographic CRS must use degrees from Greenwich. Results match PROJ 9.8 within a micrometre, except Cassini-Soldner, which follows EPSG's series (within 0.2 mm 1° from the central meridian).
- **TODO, not supported yet:**
  - Gnomonic and Modified Azimuthal Equidistant (EPSG 9832).
  - South- and west-orientated axes: Transverse Mercator (South Orientated) (EPSG 9808), Lambert Conic Conformal (West Orientated) (EPSG 9826) and Krovak (EPSG 9819, southing/westing). Krovak (North Orientated) works.
  - Other EPSG variants: Mercator (variant C) (EPSG 1044), Polar Stereographic (variant C) (EPSG 9830), Lambert Conic Conformal (2SP Belgium) (EPSG 9803), (2SP Michigan) (EPSG 1051) and Near-Conformal (EPSG 9817), Krovak Modified (EPSG 1042, 1043), Hyperbolic Cassini-Soldner (EPSG 9833), Transverse Mercator Zoned Grid System (EPSG 9824).
  - National and local methods: New Zealand Map Grid (EPSG 9811), Laborde Oblique Mercator (EPSG 9813), Guam Projection (EPSG 9831) and Vertical Perspective (EPSG 9838).
  - Interrupted projections such as Interrupted Goode Homolosine.
  - Other PROJ methods not listed above, such as Bonne, Van der Grinten, Eckert, Hammer and Aitoff.
  - Geographic CRSs with a prime meridian other than Greenwich or angles in units other than degrees.
  - Exact Cassini-Soldner (as PROJ 9) in place of EPSG's series.
- **No datum transformations.** Style coordinates (WGS84) are projected as if they were lng/lat on the CRS's ellipsoid.
- **Axis order:** always easting first, then northing (longitude, latitude), whatever the CRS's declared axis order.
- **How layers are drawn:** background, fill, line, circle, heatmap and symbol layers are drawn from projected vertices directly into the output. Pixel lengths such as line widths, circle radii and text sizes stay constant on screen. raster, hillshade and color-relief layers are drawn in Web Mercator and warped onto a mesh. Areas outside the projection's domain are transparent.

## Other bindings

The core is a plain Rust crate with no I/O, so it can be wrapped for other hosts. Each host runs the same loop: ask the renderer what it `needs`, fetch it, `provide` the bytes, repeat until nothing is needed, then `render`.

- **Node.js (Node-API):** [`@ml2drender/node`](https://github.com/Kanahiro/ml2drender/tree/main/packages/ml2drender-node#readme), from [`crates/napi`](https://github.com/Kanahiro/ml2drender/tree/main/crates/napi).
- **Python:** [`ml2drender` on PyPI](https://github.com/Kanahiro/ml2drender/tree/main/crates/python#readme), from [`crates/python`](https://github.com/Kanahiro/ml2drender/tree/main/crates/python) (PyO3, abi3).
- **Rust:** the `ml2drender` crate in [`crates/core`](https://github.com/Kanahiro/ml2drender/tree/main/crates/core) (not on crates.io). `Renderer::new(style, View::tile(z, x, y, 256))`, then `needs()` / `provide()` / `render()`.

## Conformance

The [MapLibre GL JS render tests](https://github.com/maplibre/maplibre-gl-js/tree/main/test/integration/render) are run with GL JS's own comparison: pixelmatch, with each test's tolerances.

| Suite | Result |
|---|---|
| Render tests (1008 applicable) | 991 pass / 17 fail (98.3%) |
| Expression tests (Style Spec, 583) | 581 pass / 2 fail (99.7%) |

The 727 tests out of scope cover 3D, pitch, non-Mercator GL projections and runtime style changes. Main known gaps:

- Bidirectional text and Arabic shaping. Glyphs drawn with browser fonts (TinySDF) are not available either.
- History-dependent placement (`prevAnchor`, CrossTileSymbolIndex). Single still images cannot reproduce it.
- Locale-sensitive collation, and `number-format` in locales other than en-US.
- Sub-pixel rounding of the GPU the reference images were drawn with (SwiftShader snaps vertices to 1/16 px). It moves a pixel row or texel in a few tests.

## Architecture

```text
 host (JS / Node-API / Python)              core::Renderer (sans-IO Rust)
 ─────────────────────────────              ─────────────────────────────
 style + view ─────────────────────────────▶ style · expressions
                ◀───────────── needs(): URLs   sources: MVT · GeoJSON · images · sprites
 fetch                                         symbols: shaping · placement
 provide(url, bytes) ──────────────────────▶   projection · reprojection
                ◀──────────── render(): RGBA   software GL rasterizer
                ◀─────── export_tile(): MVT/PNG
```

- [`crates/core`](https://github.com/Kanahiro/ml2drender/tree/main/crates/core) is a Rust library with no network, files, async, threads or clocks (sans-IO). It reports the URLs it needs through `needs()` and works on the bytes the host passes to `provide()`. What it needs becomes known step by step: tiles first, then the text they contain, then the glyph ranges for that text.
- [`crates/wasm`](https://github.com/Kanahiro/ml2drender/tree/main/crates/wasm) exposes the core through a plain C ABI, without wasm-bindgen.
- [`packages/ml2drender`](https://github.com/Kanahiro/ml2drender/blob/main/packages/ml2drender/src/index.ts) hides WASM memory handling and the fetch loop behind the TypeScript API.

The renderer does not imitate the GPU pipeline. It ports MapLibre's bucket building, shader math and quantization, and rasterizes with the same rules as GL.

## Development

```sh
pnpm install
pnpm build:wasm        # WASM → packages/ml2drender/wasm/
pnpm demo              # starts the demo (examples/browser)
pnpm test              # Rust unit tests
pnpm typecheck         # TypeScript type checks, including the API type tests
pnpm lint              # rustfmt, clippy and Ruff
pnpm fixtures          # fetches the upstream test data (first time only, about 144 MB)
pnpm test:render       # render tests → target/test-report/render/
pnpm test:expression   # expression tests → target/test-report/expression/
pnpm build:native      # Node-API addon → packages/ml2drender-node/native/
pnpm test:node         # Node-API addon tests
pnpm test:python       # Python binding tests (uses uv)
pnpm build:docs        # API reference (TypeDoc) → target/docs/
pnpm bench base=<dir> head=.   # compares WASM performance between two checkouts
```

```text
crates/core/               rendering engine (sans-IO)
crates/wasm/               C ABI for WebAssembly
crates/napi/               Node-API addon
crates/python/             Python extension
crates/cli/                test runner and native checks
packages/ml2drender/       JavaScript API (WebAssembly)
packages/ml2drender-node/  Node-API addon package
examples/browser/          demo (Vite + MapLibre GL JS)
```

You need Node.js (version in `.node-version`), pnpm (version in `packageManager`), Rust via rustup (version in `rust-toolchain.toml`), and uv for Python. CI (`.github/workflows/build.yml`) builds and tests every package on Linux, macOS and Windows; `publish.yml` publishes to npm and PyPI.

Design constraints and conventions are described in [AGENTS.md](https://github.com/Kanahiro/ml2drender/blob/main/AGENTS.md) (in Japanese). Issues and pull requests are welcome. A change to rendering should keep `pnpm test:render` results unchanged, or explain why they change.

ml2drender was written largely with AI coding agents, using the MapLibre GL JS implementation as the reference.

## License

[BSD-3-Clause](https://github.com/Kanahiro/ml2drender/blob/main/LICENSE). Much of the code is ported from MapLibre GL JS and the MapLibre Style Spec (BSD-3-Clause), and from @maplibre/geojson-vt, supercluster, kdbush, polylabel and vt-pbf (MIT / ISC). Their copyright notices and licenses are reproduced in [LICENSE](https://github.com/Kanahiro/ml2drender/blob/main/LICENSE). The test data fetched by `pnpm fixtures` is under its own licenses.
