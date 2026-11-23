# MapLibre style 2D タイルレンダラ (Rust / WASM)

## 目的

MapLibre style (style-spec JSON) を**タイルベースの2D表現に限って**仕様準拠でレンダリングする最小のレンダラ。主ターゲットは `wasm32-unknown-unknown`(Cloudflare Workers / Deno / WASI / ブラウザ)。GPU・ネイティブバイナリなしで静止画(RGBA)を返せることが価値。

「完璧」の定義: **対象レイヤについて maplibre-gl-js の render-tests が許容差内で通ること**。ピクセル完全一致は目指さない(GPU vs tiny-skia の差)。

前提を忘れないこと: 正確さだけが目的なら MapLibre Native(ヘッドレス可)を使うほうが安い。本プロジェクトの価値は「GPU不要・軽量・WASM・サーバレス」。

## スコープ

対象:
- style-spec のパース(serde)と式(expression)評価
- ソース: vector(MVT), raster, raster-dem, geojson, image
- 投影: 描画は Web Mercator。カメラは center / zoom / bearing(2D 回転)。投影座標系への再投影(`set_projected_view`: PROJJSON + bbox + bearing(bbox の中心まわりの回転。出力画素と CRS 座標の対応を回すだけで、raster / DEM を描く Mercator のカメラにも同じ bearing を与える。パターンは CRS の格子に固定したまま地図と一緒に回る)。PROJ 文字列は受け付けない(CRS の表現は PROJJSON に一本化)。経緯度 / 横メルカトル / メルカトル・Pseudo Mercator / ランベルト正角円錐 / アルベルス正積円錐 / 正距円錐 / 極ステレオ / 斜軸ステレオ(sterea) / 斜軸メルカトル(Hotine A・B) / Krovak(北向き) / laea / 正距方位(aeqd、測地線は `geographiclib-rs`) / 正積円筒(cea) / 正距円筒(eqc) / カッシーニ(EPSG の級数。PROJ の厳密式と経度差の5乗でずれる) / 多円錐 / 正射 / Equal Earth / モルワイデ / ロビンソン(PROJ と同じ単精度の係数表) / サンソン(sinu) / Natural Earth / ヴィンケル第三 / ミラー / geos、EPSG の球面版は PROJ と同じく面積の等しい球(`+R_A`)、式は PROJ 9.8 と照合(`projection.rs` のテストの参照値)、楕円体・任意の長さ単位・本初子午線対応(経緯度 CRS はグリニッジ基準の度のみ)、BoundCRS / CompoundCRS は水平の CRS だけ読む、測地系変換なし、座標は常に東・北の順)は、ベクタ(fill / line / circle / heatmap / symbol / background)は頂点を投影して出力側に直接描く(タイル空間で線分・三角形を細分化、線幅などのピクセル長は投影の局所回転で画面空間に押し出し、タイルのクリップは出力画素ごとの Mercator 位置から決める、投影の反子午線をまたぐタイルは世界コピーで描き分ける。`*-pattern` は投影後の画面ピクセル上に、CRS 座標に固定した原点から整数ズームの Mercator と同じ縮尺(パターン 1px = 1 CSS px、タイルごとのズームに依らない)で一様に敷く。破線と line-pattern は線に沿った投影後の距離で敷き、位相はタイル境界との交点で揃えて交点間を整数周期に伸縮する(タイルごとに切られた線の始点から数えると境界で途切れるため))。raster / DEM だけ Mercator で描いた「絵」をメッシュで warp する。投影ビューのズーム(スタイル評価・ソースタイル選択)は CRS 単位/画素を EPSG:3857 の赤道と同じ式で読む(OGC スケール分母。位置で決めるとタイルごとにズームが変わり境界に継ぎ目が出る)。極付近などで raster / DEM の Mercator の絵が出力の16倍の面積を超えるときは、ズームではなく絵の解像度(pixel ratio)と raster / DEM のタイルのズームだけを下げる
- レイヤ: background / fill / line / circle / raster / symbol / hillshade / color-relief / heatmap
- sprite(fill-pattern, line-pattern, icon)、glyph(SDF PBF)

非対象(手を出さない): fill-extrusion / 3D / pitch / terrain / globe・GPU 的な非mercator投影(maplibre の globe 等) / sky / light、実行時のスタイル・カメラ変更(setPaintProperty / setStyle / setFeatureState 等。render テストの `operations` は wait 系以外を含めば skip)、ネットワーク取得・キャッシュ・PMTiles 読み出し(ホストの責務)。

## 設計制約(厳守)

- **純Rustのみ。C依存禁止**(zstd, freetype, harfbuzz, GDAL 等は不可)。依存クレートは採用前に `cargo build -p ml2drender --target wasm32-unknown-unknown` で確認する。
- **API の最小形は「style.json + XYZ → タイル画像」**: `Renderer::new(style, View::tile(z, x, y, tile_size))`。`View`(center/zoom/bearing/サイズ/pixel_ratio)は任意カメラ用の一般形で、render テストはこちらを使う。スタイルは生成時に固定(変更 API は作らない)。`set_view` でカメラだけ切り替えると、取得済みリソースとタイル単位のバケット(線のテッセレーション・塗りの earcut)を再利用する(ソースごとに LRU で `MAX_CACHED_TILES` まで)。再利用しないホストは `set_keep_buckets(false)`(TS 側は `ttlMs: 0`)で、線・塗りのバケットを最後に使うレイヤを描いた時点で捨てる(WASM のメモリは縮まないので、Workers の 128MB に収めるには描画中のピークを下げる)。ジオメトリやプロパティは要素ごとの小さな割り当てを避ける(MVT のプロパティはレイヤ共有のキー・値表へのタグ、グリフ範囲はテキストが使うコードポイントだけ保持)。1つのレンダラで描画を重ねて走らせるホストは、描画ごとに増加するチケットを `set_ticket` で渡し、`render_retaining(進行中で最古のチケット)` で描く(他の描画がまだ描いていないタイルを追い出さない。TS 側は描画の連番を使う)。
- **Sans-IO**: `core` は fetch / ファイル / async / スレッド / 時計に依存しない。`Renderer` が:
  - `needs() -> Vec<Resource>`: 現時点で必要な未取得リソース(tile / glyph range / sprite json / sprite png / TileJSON など)
  - `provide(url, Option<bytes>)`: 取得済みバイト列を渡す(`None` = 取得失敗/404)
  - `render() -> RGBA`: `needs()` が空のときのみ呼べる
- 必要リソースは**段階的に確定する**(タイル取得 → MVTデコードと式評価でテキスト確定 → glyph range 確定)。ホストは `needs()` が空になるまで取得と `provide()` を繰り返す。1往復で済む前提の設計にしない。
- `needs()` の段階: style → (TileJSON / GeoJSON / sprite json+png / image) → タイル → (欠落 raster の祖先 / グリフ範囲)。`provide` は現在のビューではなく `needs()` が返した URL の記録と照合する(`needs` と `provide` の間に `set_view` されても取りこぼさない)。`provide(None)` は上流の 404 と同じ意味(vector は空タイル、raster は祖先で代替、グリフ範囲は欠落)。
- 取得済みバイト列のキャッシュはホストの責務(TS 側は同一 URL の同時取得をまとめるだけ)。
- 単一スレッド。並列化しない。
- 採用済みクレート(すべて純 Rust・wasm32 確認済み): `serde_json`, `indexmap`, `unicode-normalization`, `earcut`(earcut.js 3.x の移植。MapLibre と同じ系統), `miniz_oxide`, `png`, `zune-jpeg`, `image-webp`, `jpeg-encoder`(JPEG 書き出し), `i_overlay`(MVT 書き出しのポリゴン結合のみ), `geographiclib-rs`(正距方位図法の測地線のみ。既定の feature は切る)。描画は自前のソフトウェア GL(tiny-skia は不採用: GL の AA・合成規則と一致させるため)。
- テキストは SDF グリフを自前でしきい値処理して合成(halo / text-opacity もこの層)。MapLibre は HarfBuzz を使わず PBF グリフのメトリクスで自前レイアウトしているので、`shaping` / `placement` のアルゴリズムを再現する。RTL 等は後回し。

## 構成

- `crates/core/` — crate `ml2drender`。純粋ロジック(sans-IO)。
  - `expression/` — style-spec 式の移植(パース・型検査・評価)、`function.rs` は実行時の legacy function。
  - `style/` — レイヤ・プロパティのコンパイル、フィルタ。`spec.json` は v8.json の必要部分(`scripts/trim-spec.ts` で生成)。
  - `mvt.rs`(MVT デコード・エンコード、overzoom 分割)、`geojson.rs`(geojson-vt 移植)、`supercluster.rs`(GeoJSON クラスタ)、`tile.rs`(Transform・タイル被覆・URL)、`projection.rs`(投影式と PROJJSON の解釈。内部では PROJJSON を PROJ 文字列相当のパラメータに落として読む)、`reproject.rs`(raster / DEM の warp。頂点から描くレイヤは `Transform::reprojected` 経由で出力座標へ)、`image.rs`(画像デコード・GL 風テクスチャ)、`sprite.rs`(sprite 読み込み)。
  - `render/` — `raster.rs` がソフトウェア GL(三角形・GL ライン・合成・マスク)、`x4.rs` が WASM SIMD で三角形の行を4画素ずつ処理する経路(円・線(破線・パターン・グラデーションなし)・シンボル・単色の塗り)、`mod.rs` / `line.rs` / `raster_layer.rs` / `pattern.rs` が各レイヤのバケット生成とシェーダ再現。
  - `symbol/` — `glyphs.rs`(PBF グリフ)、`shaping.rs`、`layout.rs`(バケット・quad・衝突ボックス)、`placement.rs`(衝突判定)、`projection.rs`(line ラベル)、`draw.rs`(SDF/アイコンのシェーダ)。
  - 方針: MapLibre の **バケット生成とシェーダの計算を移植**し(ジオメトリ・式・量子化)、GL と同じ規則でラスタライズする(tiny-skia は使わない)。GPU パイプラインの構造は真似せず、出力が変わらない範囲で独自に組み替えてよい(深度バッファの代わりに画素ごとの最上位不透明レイヤを記録、varying の平面補間、バケットの描画間再利用など)。pitch 依存の計算は pitch 0 に簡約する。上流の量子化・桁あふれ等の癖も再現する(例: extrude の Int8 桁あふれ、GL の top-left 規則を y-down バッファでは「左辺+下辺」に読み替え、`linesofar` の量子化、overzoom 分割 `zoomLevelsToOverscale`=4(バケットは vt-pbf で再エンコードする前の浮動小数座標を `loadGeometry` と同じく 8192 で丸めて読む)、衝突ボックスの Int16 切り捨て、layout 同一レイヤのバケット共有)。
- `crates/cli/` — crate `ml2drender-cli`、バイナリ `ml2drender`。テストランナー兼ネイティブ検証用。
- `crates/wasm/` — crate `ml2drender-wasm`。wasm-bindgen なしの素の C-ABI export(`renderer_new/set_view/set_projected_view/set_ticket/set_keep_buckets/set_export_source/needs/provide/render/export_mvt/free`, `projection_new/forward/free`(投影ビューの center を CRS 座標にするための内部用。結果は `xy_ptr`), `tile_matrix`(格子の JSON のタイル z/x/y の bbox・縦横比・ズーム 0 の CSS px あたりの CRS 単位。結果は `xy_ptr`), `encode_png/encode_webp/encode_jpeg`, `alloc/dealloc`, RGBA / PNG・エラー等の結果は `out_ptr/out_len` の共有バッファ)。
- `crates/napi/` — crate `ml2drender-napi`。Node-API バインディング(`Renderer`(生成はスタイルだけで、`setView` か `setProjectedView`(PROJJSON 文字列 + bbox + bearing)・`setProjectedTile`・`setProjectedCamera`(PROJJSON 文字列 + 格子の JSON 文字列 + タイル / カメラ)で描く範囲を決める。`needs`/`provide`/`render` は libuv のスレッドプールで実行し JS スレッドを塞がない)、`encodePng/encodeWebp/encodeJpeg`)。`pnpm build:native` → `packages/ml2drender-node/native/ml2drender.<platform>-<arch>.node`(`native` プロファイル: panic は JS のエラーに巻き戻す。Linux は `GLIBC=2.34` で cargo-zigbuild によりリンク先 glibc を固定)。取得ループは持たない(利用側が `needs`→fetch→`provide` ループを書く)。
- `packages/ml2drender-node/` — npm パッケージ `@ml2drender/node`。Node-API アドオンのローダと型(`src/index.ts`)と README。入口は wasm 版と同じく `Renderer` の生成だけ: `new Renderer(style, { loadResource })` → `renderer.renderView(view)`(Mercator のカメラ)・`renderer.projected(crs)` の `render(box)`・`renderTile(tile)`・`renderView(view)`(任意 CRS。wasm 版と同じ形で、`crs` は `projjson` と `tileMatrixSet` を読む(`name` は読まない)。格子のタイルとカメラ → bbox の計算は core の `ProjectedView::tile` / `camera`。`getTile` / `toStyle` は wasm 版だけ)が標準 `fetch`(HTTP 404 は欠落、その他はエラー)で `needs`→取得→`provide` を回して RGBA を返す。`loadResource` で取得を差し替え可能。スタイルは `StyleSpecification`(オブジェクト)で受け、ラッパーが JSON 文字列にしてアドオンへ渡す。レンダラは取得済みリソースを保持して次の view で再利用し、同じレンダラの描画は順番に実行する(アドオンのレンダラは view を1つしか持たず、呼び出しを重ねられない)。アドオンの sans-IO の手順(`needs` / `provide` / `setView` / `setProjectedView` / `setTicket` / `setKeepBuckets`)は公開しない。ローダはリポジトリ内のビルド(`native/`)があればそれを、なければプラットフォーム別パッケージ `@ml2drender/node-<platform>-<arch>[-gnu]` を読む(napi-rs と同じ分割。`scripts/npm-node.ts` が対象プラットフォームの一覧を持ち、公開時に optionalDependencies とプラットフォーム別パッケージを生成する)。
- `crates/python/` — crate `ml2drender-python`。PyO3 abi3(Python 3.11+)、maturin の mixed Rust/Python 構成。入口は `Renderer(style, load=None)` の生成だけ(スタイルは `dict`)。`python/ml2drender/__init__.py` の `await renderer.render_view(view)`(Mercator の `View`)・`renderer.projected(projjson, tile_matrix_set=None)` の `await render(box)`(`ProjectedBox`: bbox + 長辺の画素数。bearing は持たない(bbox は CRS の y 軸が上の範囲。回転は `render_view`))・`render_tile(z, x, y, ...)`・`render_view(view)`(任意 CRS。`projjson` は PROJJSON の `dict`、格子は `TileMatrixSet(origin, extent_at_zoom0)` か OGC 2D TileMatrixSet の `dict`(どちらも JSON 文字列にして core の `TileMatrixSet::parse` へ)。Node 版の `projected(crs)` と同じ範囲)は HTTPX の `AsyncClient` を描画中に再利用し、段階ごとに必要なリソースを asyncio で並行取得する(HTTP 404 は欠落、その他はエラー)。非同期 `load` で取得を差し替え可能。失敗・キャンセル時は残る取得をキャンセルして完了を待つ。同じレンダラの描画は `asyncio.Lock` で順番に実行し、取得済みリソースを次の view で再利用する。Rust の `_native.Renderer` は sans-IO の同期 API(生成はスタイルの JSON 文字列だけで、`set_view` / `set_projected_view` / `set_projected_tile` / `set_projected_camera` で描く範囲を決める。デコード・描画は呼び出し元のスレッドで実行)で、公開 API ではない。Python 環境は uv: `pnpm build:python` は `uvx maturin build`、`pnpm test:python` は `uv run --project crates/python`。PyPI 用の README と型情報(`python/ml2drender/_native.pyi`、Python のインライン型、`py.typed`)を置く。`python/ml2drender/basemap/` は contextily の `add_basemap` と同じ引数で MapLibre スタイルを描く matplotlib 用モジュール(軸の範囲を軸の CRS の `ProjectedBox` として一度に描く。`zoom` は contextily と同じ 256px タイルのズームで、スタイルはその −1 で評価)。軸の大きさは描くときのもの(図の layout engine と aspect を適用してから読む)。OpenHistoricalMap のタイルを使うスタイルは OHM のサイトと同じく今日の地図を描く(地物を描くレイヤのフィルタに `start_decdate` / `end_decdate` で今日存在する地物だけを残す条件を、そのフィルタと同じ構文(legacy / 式。混ぜると式として読まれる)で足す。値の無い地物は残す。日付の指定は持たない。`labels` とサービスごとのスタイルの変更は `_source.preprocess_style` にまとめ、サービスはソースの URL で見分ける)。経緯度の軸は GeoPandas が緯度方向に 1/cos(中央の緯度) 伸ばして表示するので、画像も(ラベルごと)伸びないよう、表示どおりの正距円筒図法(球、標準緯線 arccos(1/伸び))で描く。描くのは MapLibre スタイルだけで、contextily のコード・ラスタ処理は持ち込まない(タイルプロバイダ・タイル URL・ラスタファイルは `UserWarning` 付きで `contextily.add_basemap` に渡し、contextily が無ければ `ImportError`)。取得したリソースは contextily と同じくディスクにキャッシュする(既定は終了時に消す一時ディレクトリ、`set_cache_dir` で永続化。スタイルと TileJSON は毎回取得する: タイルの URL がデータの版を含むことがあり、古い版は消える)。`timeout=None` は contextily と同じく無制限。失敗した取得(ネットワークエラー・タイムアウト・HTTP 429・5xx)は間隔を倍にしながら2回まで再試行する。名前付きスタイル `STYLES` は API キー不要のもの(スタイル JSON の URL。地形(Mapterhorn の raster-dem)・空中写真などスタイル JSON の無いものはスタイルの dict)。同じ軸に重ねて描いた basemap の帰属表示は1つにまとめる。`ml2drender` の公開 API だけを使い、別パッケージに切り出せる形にする。画像は既定で図の 1 インチあたり 192 画素、図の dpi がそれより高ければその dpi(既定の dpi では軸の2倍で、HiDPI と2倍の dpi での保存向け。`pixel_ratio` で変える)。matplotlib・pyproj・contextily はパッケージの依存に入れない(モジュールはそれらなしで import でき、`add_basemap` などが呼ばれた時点で足りないものを `pip install ...` 付きの `ImportError` で示す。geopandas の `explore` と同じ)。テストが使う matplotlib・pyproj は `test` グループに入れ、`pnpm test:python`(`uv run` が入れる `dev` グループが `test` と `lint`(ruff)を含む)と CI の wheel テスト(`uv pip install --group`)で使う。lint は `lint` グループだけを入れる。API を変えたら型情報と README も直す。
- 非対応レイヤ — core は非対応タイプのレイヤ(fill-extrusion など)を読み飛ばす。fill に置き換える等はスタイルを渡す側の責務(ラッパーは置き換えない)。
- `packages/ml2drender/` — npm パッケージ `@ml2drender/wasm`。TypeScript の公開 API と型テスト。`src/index.ts` が WASM のロード・メモリ操作・リソース取得を隠す。描画は Mercator 前提の `renderView`(center / zoom / bearing)・`renderTile`(XYZ タイル)・`render`(経緯度 bbox と長辺の画素数。縦横比とズームは bbox から決める。投影法を意識せずに使える口)と、`renderer.projected(crs)`(crs = GL JS の `addProjection` と同じ `name` / `tileMatrixSet` + PROJJSON。必須は PROJJSON だけで、`tileMatrixSet` は格子を使う `renderTile` / `renderView` / `getTile`、`name` は `toStyle` だけが要求する。`tileMatrixSet` は GL JS の `{ origin, extentAtZoom0 }` か OGC 2D TileMatrixSet で、タイルの bbox とカメラの縮尺は wasm の `tile_matrix` 経由で core が計算する)の `render`(CRS 座標の bbox と長辺の画素数。bearing は持たない(bbox は CRS の y 軸が上の範囲。回転は `renderView`))・`renderTile`(格子の z/x/y)・`renderView`(引数は Mercator の `renderView` と同じ `View`。経緯度の center と格子のズームを TS 側で bearing 0 の bbox に直し、bearing は core が bbox の中心まわりに回す)に分け、どれも RGBA の `RenderedImage` を返し、`image.toPng()` / `toWebp()` / `toJpeg(quality)` で WASM 内エンコードする(形式が増えても `toXxx()` を足すだけ)。`projected(crs).getTile`(格子の z/x/y + source。bbox は TS 側で計算、512px 固定)は描画ではなく、スタイルの 1 ソースを GL JS がそのまま読める形で再投影して返す(core は `set_export_source` → `needs`/`provide` → `export_tile`)。vector / GeoJSON は MVT(線・点は元タイルごとに切る。ポリゴンは元タイル内に収まるものはそのまま、緩衝帯だけのものは捨て、タイル縁で切られた断片はレイヤ・属性ごとに `i_overlay` で結合してつながった部分ごとの地物にしてから投影し、元タイル境界の継ぎ目を出さない(OpenMapTiles の海などは ID がタイルごとに違うので ID では束ねない)。定義域外(geos の裏側)は縁を二分探索し、ポリゴンは d3-geo の clip と同じく縁に沿って時計回りにつなぎ直す。リングは外周→その穴の順に並べる(読み手は最初のリングの向きを外周とみなす)。並べるのは出力範囲で切る前(切った後は外周と穴が切り口の辺を共有し、内外判定が曖昧になる)。スタイルで絞らず全レイヤ・全属性。一度書き出した vector ソースのタイルは全レイヤを保持する)。raster / image は既定 paint の raster レイヤで描いた Mercator の絵を warp した PNG、raster-dem は出力画素ごとの Mercator 位置で backfill 済み DEM の標高を双線形補間し terrarium で符号化した PNG。`projected(crs).toStyle({ tiles })` は init に渡したスタイルのソースを GL JS 用に差し替えて返す。投影式(forward / inverse)は公開しない(GL JS の `addProjection` に渡す変換は proj4js で足りるので、README とデモにコード例だけ置く)。`init(..., { wasm })` でコンパイル済みモジュールを渡せる。`ttlMs: 0` のレンダラは描画ごとに WASM 側のレンダラを解放し、モジュールごとに1つの WASM インスタンスを共有する(解放したメモリを次の描画が再利用する。trap したインスタンスは捨てて次から作り直す)。リソースを保持するレンダラは専用インスタンス(GC で丸ごと回収)。
- `examples/browser/` — pnpm パッケージ `demo-browser`(Vite + TypeScript、maplibre-gl は npm 依存)。ml2drender の出力だけを GL JS で表示する。地図ペインの切り替えで、ml2drender が描いたラスタタイルと、GL JS 自身の描画(メインスレッドの `ttlMs: 0` のレンダラの `toStyle` で、全ソースを `ml2drender-source://` プロトコル経由の Worker の `getTile` に差し替える。投影なしでは EPSG:3857 の PROJJSON と GL JS の Mercator 格子を使う。ホバーで地物属性を表示)を選ぶ。左の Style JSON・PROJJSON・GeoJSON の入力が正(UI は英語)。各欄は見出しで開閉し、閉じているときはプルダウン(`src/styles.ts`、`src/projjson-presets.json`、GeoJSON は `main.ts` 内)だけ、開くと直接編集できる。入力は 500ms のデバウンスで自動反映し、Style / PROJJSON が変わったときだけタイルを描き直す(反映は直列化)。PROJJSON が空なら Web Mercator を再投影なしで描く。PROJJSON は proj4js の変換(`src/proj.ts`。proj4js が PROJJSON から読めない geos だけ PROJ 文字列に直す。ml2drender と同じく BoundCRS / CompoundCRS は水平の CRS を読み、測地系変換はしない(proj4js は既知の測地系を WGS84 から自動で変換してしまうので止める)。ml2drender の投影式とはデモのプリセットで概ね数 µm 以内で一致(proj4js の式が PROJ と違うロビンソンは約 1.6 m、EPSG:2056 の Hotine は約 7 cm)。プリセットは proj4js が読めて ml2drender と一致する CRS に限る(proj4js が読めない・式が違う natearth / wintri / 楕円体の eqc・ortho / `+R_A` の球面版 / 本初子午線がパリのものは入れない))と、bbox(適用範囲)を投影した正方形のタイル格子で GL JS の `addProjection` に登録し(`src/projections.ts`)、各タイルを `projected(crs).renderTile`(空なら `renderTile`)で描く。投影の整合確認に GeoJSON 欄(既定は Natural Earth の都市)だけは GL JS 自身が描く(点は円、線・面も描く。ホバーで属性表示。変更は `setData` のみ)。GL JS は GeoJSON を Worker でタイル化するため、`src/gl-projection-handler.ts` を `importScriptInWorkers` で読み込み、`setProjectionHandler` に同じ `src/proj.ts` の変換を渡す(URL 末尾の `#.mjs` は GL JS にモジュールとして直接 import させるため、`vite.config.ts` の `worker.format: 'es'` はそのモジュールを ES 形式で出すため)。位置は URL ハッシュに残る。`needs`→fetch→`provide` ループのホスト実装例を兼ねる。JS API は `@ml2drender/wasm` を `workspace:*` で参照。WASM は `pnpm build:wasm` で `packages/ml2drender/wasm/` にコピーし、API 内で `?url` import。社内標準のフロントエンド既定は SvelteKit だが、1画面の検証用ツールなので素の Vite にしている。
- `examples/python/quickstart.ipynb` — Python 版の紹介用 Notebook(Colab で開く。README のバッジから)。PyPI の公開版を `%pip install` して demotiles を描き、`bearing` と投影(PROJJSON は pyproj から)を見せる。CI では実行しない。API を変えたら合わせて直す。
- `examples/python/basemap.ipynb` — `ml2drender.basemap`(`import ml2drender.basemap as mlb`)の例の Notebook(Colab でも開ける)。描いた地図を出力として保存する(ローカルで上から実行して保存。CI では実行しない)。API や `STYLES` を変えたら合わせて直す。
- API リファレンス — `packages/ml2drender/typedoc.json` の TypeDoc で生成(`pnpm build:docs` → `target/docs/`、トップページは README)。Pages(`.github/workflows/pages.yml`)でデモの `/api/` に置く。公開 API の型・メンバーには TSDoc を書く(`npx typedoc --validation.notDocumented` で漏れを確認)。README は英語(OSS 公開用)、リポジトリ内ファイルへのリンクは TypeDoc でも辿れるよう GitHub の絶対 URL にする。
- CI — `.github/workflows/build.yml`(`workflow_call`)が Rust のテスト(`cargo test` と上流 fixture の render / expression テスト。失敗時はレポートを artifact に残す。サマリー(known-failures との差分を含む)はジョブサマリーにし、`ci.yml` の `test-report` ジョブが PR にコメントする。PR コメントは `scripts/pr-comment.sh` でワークフローごとに1つを上書き更新)、WASM 版の Node.js / Deno / Vite での確認と公開形の tarball の動作確認、Node-API アドオンと Python wheel の Linux(x64/arm64)・macOS(x64/arm64)・Windows(x64)でのビルドとテストを行い、公開物を artifact に残す。`ci.yml`(PR・main)と `publish.yml`(`workflow_dispatch`。npm の 3 種と PyPI に、リポジトリ内の共通バージョンで公開。trusted publishing + provenance)が呼ぶ。バージョンは `packages/ml2drender`・`packages/ml2drender-node` の package.json と `crates/python/Cargo.toml` で揃える。
- `crates/cli/tests/exclusions.json` — 対象外 render テストの除外ルール(理由付き)。pitch/roll/非mercator/operations はランナー内にハードコード。
- `crates/cli/tests/known-failures.json` — 現時点で通らない render / expression テストの ID。ランナーはここにない失敗があれば exit 1(CI はこれで退行を検知する)。通るようになった ID は警告が出るので一覧から消す。
- `fixtures/` — 上流テスト(gitignore)。`scripts/fetch-fixtures.sh` が固定コミットから取得し LICENSE を同梱する。上流のテストがネットワークから読むリソース(demotiles のグリフ)は `render/remote/<host>/` に置き、ランナーが `https://<host>/...` をそこへ読み替える。

## コマンド

全体を pnpm で管理する(`package.json` の scripts が cargo を包む)。Rust は `rust-toolchain.toml`(1.100、clippy・rustfmt・wasm32 ターゲット)で固定し、`.cargo/config.toml` で公開7日未満のクレートを解決しない。rustfmt は `rustfmt.toml`(max_width 120)、clippy の許可は `Cargo.toml` の `[workspace.lints.clippy]`。Python は uv(`crates/python/.python-version`、`exclude-newer = "7 days"`)と Ruff。GitHub Actions の action は最新版のコミット SHA で固定し、行末にバージョンを書く。Node は `.node-version`(26, Active LTS)、pnpm は `packageManager` で固定。`pnpm-workspace.yaml` で `minimumReleaseAge: 10080`(公開7日未満の版は入れない)。JS は TypeScript のみ(`.js` を新規に作らない)。TypeScript は全パッケージで 6.0 に揃える(TS 7 は TypeDoc が使う JS API を持たないため)。

```sh
pnpm install
pnpm fixtures                      # 初回のみ(~144MB)
pnpm test                          # cargo test
pnpm test:node                     # Node-API アドオン(pnpm build:native 後)
pnpm test:python                   # Python バインディング(uv)
pnpm build:native                  # Node-API アドオン
pnpm build:docs                    # API リファレンス(TypeDoc)→ target/docs/
pnpm test:render [filter]          # → target/test-report/render/{summary.md,results.json,<id>/diff.png}
pnpm test:expression [filter]      # → target/test-report/expression/{summary.md,failures.json}
pnpm bench base=<checkout> head=. # WASM の性能比較(fixture 使用、各 checkout で pnpm build:wasm 済み)。PR では .github/workflows/bench.yml が base と比較しコメント
pnpm demo                          # wasm ビルド + Vite dev server(examples/browser)
pnpm typecheck                     # tsc --noEmit(全パッケージ)
pnpm lint                          # cargo fmt --check・clippy -D warnings(ネイティブと wasm32)・ruff check / format --check
./target/release/ml2drender compare a.png b.png [threshold]
```

比較は maplibre-gl-js と同じ: pixelmatch 8.0.0 の移植(`crates/cli/src/pixelmatch.rs`、JS版と300組で一致確認済み)、`threshold` 既定 0.1285、`allowed`(不一致画素率)既定 0.00025、テストごとの `metadata.test` 指定があればそれに従う。`expected*.png` が複数あれば最小差分を採用。expression は上流と同じく数値を有効数字6桁で比較。

## ルール

- **テスト特化のハードコード、特定ケースへの分岐は禁止。** 仕様に基づく実装のみ。通らないケースは通らないまま残す。
- 差が大きい失敗(アンチエイリアスやジョイン形状以外)は個別に原因を調査する。アンチエイリアスやジョイン形状は MapLibre(GPU)と差が出るので許容差で吸収する。
- **高速化・省メモリ化は render テストの判定を変えない。** `pnpm test:render` の全件の判定が変更前と一致することを確認する(`results.json` の `hash` は比較した RGBA の SipHash なので、変更前の結果と突き合わせれば出力のビット一致も確かめられる)。メモリは WASM の `memory.buffer.byteLength`(ピーク)で測り、密なタイル(都市部の z14 など)でも確かめる。出力が変わる近似は、差が 8bit の量子化程度に収まるものに限り、差分率の変化と `pnpm bench` の出力差を PR に書く。ただし DEM(hillshade / color-relief)は画素単位の差も許容せず、RGBA をビット一致させる(libm を近似に置き換えない)。計測は `pnpm bench`(中央値)で前後を比較し、効果が誤差の範囲の試作は採用しない。
- **メルカトル固有の仮定を増やさない。** 将来は他の投影も対応する予定なので、最適化の適用可否は地理座標・緯度・bearing 0 ではなく投影後の画面形状から判断する。
- タイル単位のキャッシュは、中身が依存するものをすべてキーに含める(例: symbol バケットは layout グループ・wrap・ピクセル比、線/塗りは overscaled zoom)。`set_view` で使い回したレンダラと新しいレンダラの出力一致は `reused_renderer_matches_fresh_renders` で検査する。

## WASM

- x86_64 のネイティブビルド(CLI・napi・python)は x86-64-v2(`.cargo/config.toml`。SSE4.1 の丸め命令で libm 呼び出しを避ける。出力は変わらない)。
- `pnpm build:wasm` で専用 `wasm` プロファイル(opt-level 3 / fat LTO / codegen-units 1 / panic abort / strip)と simd128 を使用。成果物は `target/wasm32-unknown-unknown/wasm/` から公開パッケージへコピーする。
- 明示的 SIMD(`core::arch::wasm32`)はスカラー版と演算の種類・順序を揃え、`wasm32` かつ `simd128` のときだけ使う。ネイティブ(テストランナー)はスカラー版しか通らないので、`x4.rs` 等を変更したら render テストのスタイルを変更前後の WASM で描いて RGBA の一致を確かめる。

## 既知の非対応(仕様上・設計上の理由で通らないもの)

- 双方向テキスト(bidi)・アラビア文字の字形整形。
- 複合文字スクリプトや取得できなかったグリフ範囲のブラウザフォント描画(TinySDF)。フォントを持たないため再現不可。
- 配置の履歴依存(`prevAnchor`)、CrossTileSymbolIndex、retained tiles。単発の静止画描画では再現しない。
- ロケール別照合(照合はルート照合のみ)、`number-format` は en-US のみ、`is-supported-script` は常に true、feature-state は常に空。
- composite 式は表示ズームで直接評価(上流はタイルズーム前後の値をシェーダで補間。symbol のサイズのみ上流どおり)。
