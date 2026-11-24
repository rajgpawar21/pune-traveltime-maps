# Travel-time maps & micro-market signals (India T1/T2)

**[Open the interactive map](https://rajgpawar21.github.io/pune-traveltime-maps/web/)**

Reshape a city by **how long it takes to get around**, track how that changes over time, and
use the result to find *micro-markets*: active neighbourhoods that are also quickly reachable
from other active neighbourhoods.

```
python -m traveltime.build --city pune          # ~30 s per snapshot, no Docker needed
cd web && python -m http.server 8000            # open http://localhost:8000
```

## What changed from v1 (raster warp)

| v1 problem | Cause | Fix |
|---|---|---|
| Text rotated, sheared, resized, edges smeared | A *picture* of OSM tiles was warped with a triangulation of ~89 points; `BORDER_REPLICATE` smeared the margins | Nothing raster is warped. Roads, water and parks are **vector** geometry warped vertex-by-vertex by a smooth field; labels are placed afterwards in screen space, always horizontal, constant size, collision-checked |
| No way to compare with the normal map | Separate layers | **Stretch slider**: 0 % = geographic, 100 % = travel-time, continuous in between; optional ghost of the geographic roads |
| Fixed Leaflet image overlay | Overlay can't be re-projected | Own canvas viewer with wheel / pinch / drag / double-click zoom, level-of-detail by zoom |
| One snapshot | — | N snapshots in a shared frame (see below), **time slider**, delta layers |
| Per-run min-max rescaling | `MinMaxScaler` on every run | **Fixed scale**: map distance = km at a reference speed, identical across snapshots, so differences are real |
| Warp could fold | No check | Warp field is a thin-plate spline, **checked for negative Jacobian** and smoothed until fold-free |

## Pipeline

`osm_scan` (one pass over the PBF: roads, rail/metro, water, parks, places, POIs, buildings)
→ `router` (free-flow graph router, or OSRM, or your own matrix) → `embed` (1/d-weighted SMACOF
MDS in fixed units, aligned to the baseline frame, TPS field) → `metrics` (activity, gravity
accessibility, micro-market score, clusters) → `web/data/<city>/` + `outputs/<city>_micromarkets.csv`.

### Time series

Add snapshots to `config/cities.yaml`; every snapshot uses the **same grid and the same scale**.

```yaml
snapshots:
  - {id: osm-2018, label: "OSM 2018", pbf: data/snapshots/pune-2018.osm.pbf}
  - {id: osm-2022, label: "OSM 2022", pbf: data/snapshots/pune-2022.osm.pbf}
  - {id: osm-2026, label: "OSM 2026", pbf: data/pune-latest.osm.pbf}
  - {id: am-peak,  label: "Weekday 09:00", matrix: data/pune-0900.npy}   # n×n minutes, same grid
```

* **Infrastructure history**: re-route older OSM snapshots (Geofabrik history/internal extracts,
  Overpass `[date:…]`, ohsome). Metro lines, flyovers and ring roads show up as connectivity change.
* **Congestion**: supply peak/off-peak matrices from a traffic API (TomTom, HERE, Google, Mapbox). See "Concept notes".
* **Scenarios**: `speed_scale: {primary: 1.4}` for what-ifs (flagged in the UI). `config/examples_whatif.yaml` is a demo of this, **not real history**.

### Colour theory of the change layer

Borrowed from [night-time-lights](https://github.com/rajgpawar21/night-time-lights): a temporal RGB
composite where **R = earliest, G = middle, B = latest** snapshot. White = persistently strong,
blue/cyan = new, red = fading, black = nothing (transparent). Differences are amplified ×5 around each
cell's own mean because change is small next to the spread across the city. The inflation layer
deliberately uses a different palette (teal ↔ orange) so the two meanings never mix.

### Micro-market score

`M = sqrt( pct(activity) × pct(accessibility to *other* active cells) )`, where
accessibility = `log1p Σ_j activity_j · 2^(−t_ij / half_life)` (gravity model, 10-min half-life) and
percentiles are taken against the **first snapshot**, so growth can exceed the baseline. Clusters are
contiguous 8-neighbour blocks of cells with `M ≥ threshold`. The CSV ranks clusters by Δscore when
there are ≥ 2 snapshots (otherwise by score).

## Concept notes (read before trusting a number)

1. **Which way "inflated" points depends on the data.** With free-flow speeds the dense, well-connected
   core is *compressed* (everything is close) and the periphery inflates. "Busy neighbourhoods inflate"
   is a *congestion* effect and only appears with peak-hour traffic matrices. Activity and inflation are
   therefore kept as separate layers; the micro-market score uses activity × connectivity, which is
   robust to either.
2. **Fidelity is regional, not street-level.** Pune: map error vs travel time is 8 % beyond 8 km (plain
   geography: 24 %), 15 % at 3–8 km (18 %), but **31 % under 3 km (21 %)**: river crossings, one-ways and barriers can't
   be embedded in 2-D. Read neighbourhoods, not blocks. Per-snapshot numbers are in `meta.json`.
3. **OSM activity trends are biased.** POI/building counts grow as mapping effort grows. Before
   believing a rising micro-market, cross-check with independent growth signals (VIIRS night lights,
   Google Open Buildings temporal, registrations) and **back-test against price data**: does Δscore at
   t₀ predict appreciation to t₁? Until it does, treat the ranking as hypotheses, not a signal.
4. The built-in router is free-flow with a 4 s junction delay, no turn costs, no traffic. Use OSRM
   (`--router osrm`, chunked tables) if you want a specific engine's behaviour.

## Scaling to all T1/T2 cities

Add one block per city to `config/cities.yaml` (`bbox`, `tier`, snapshots). Cities are independent, so
run them in parallel (a CI matrix over `--city`). Cut per-city PBFs from state extracts with
`osmium extract -b W,S,E,N`. ~500 cells and one snapshot take ~30 s; 0.5 km grids for dense cores are ~4× cells.
`web/data/index.json` feeds the city selector automatically.

## Tests

`python -m pytest tests` covers fixed-scale MDS, fold-free fields, one-way routing, speed scenarios,
accessibility, percentile comparability and clustering.

## Legacy

`distance_maps/`, `main.py`, `PCoA.R`, `site/` and `worker/` are the v1 raster pipeline and its
GitHub-Actions generator UI, left untouched (deps in `requirements-legacy.txt`).

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).
