# Pune Travel-Time Maps

An interactive travel-time cartogram of Pune, India. Instead of treating every kilometre as equal, the project uses driving durations from OpenStreetMap's routing engine (OSRM) to reposition a regular grid of locations. The output is an interactive Leaflet map with the original raster, distorted cartogram, and sampling-point layers.

Adapted from [carstonhernke/traveltime-maps](https://github.com/carstonhernke/traveltime-maps).

## What the pipeline does

1. **Download the basemap** — `distance_maps/get_map.py` downloads a 9×9 mosaic of OpenStreetMap tiles around Pune (18.53744°N, 73.86652°E, zoom 14). It records the exact Web Mercator geographic bounds in `map_bounds.json`.
2. **Create the sampling grid** — `distance_maps/generate_coordinates.py` creates 81 points in a 9×9 north-to-south, west-to-east grid across Pune.
3. **Calculate driving durations** — `distance_maps/get_travel_times.py` calls the OSRM Table service once to request the full 81×81 duration matrix. Unreachable pairs remain missing values, not arbitrary sentinel durations.
4. **Embed the network** — `distance_maps/mds.py` symmetrises reciprocal travel durations for classical metric MDS, applies a documented penalty when both directions are unreachable, and aligns the resulting two-dimensional embedding to geographic orientation.
5. **Warp and publish** — `distance_maps/distort_map.py` performs inverse image mapping for OpenCV's `remap` and writes an interactive Leaflet HTML map.

The directional OSRM matrix is retained in `travel_times.npy`. MDS requires symmetric dissimilarities, so reciprocal durations are averaged when both directions exist. If only one direction exists, that observed duration is used. If neither direction exists, the pair receives a penalty above the largest observed duration. These choices are modelling assumptions, not claims that road travel is truly symmetric.

## Interactive output

After a successful run, open `pune_travel_time_map.html` in a browser. Keep the generated `img/` directory beside the HTML file.

- Switch between the OpenStreetMap basemap, original raster, and travel-time cartogram.
- Toggle the 81 sampling points and inspect their coordinates.
- Adjust the cartogram overlay opacity.
- Zoom and pan using Leaflet.

The HTML references Leaflet from unpkg and OpenStreetMap tiles, so an internet connection is needed to view the interactive map. The generated PNGs are retained as supporting outputs, not the final interface.

## Requirements

- Python 3.10 or later
- Docker Desktop (for a local OSRM routing server)
- Internet access to download OSM tiles and view the Leaflet map

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Prepare the OSRM routing server

Download a Pune-area OSM extract in PBF format from [BBBike Extracts](https://extract.bbbike.org/) and save it as `data/pune-latest.osm.pbf`. Then run these commands from the repository root:

```bash
mkdir -p data
docker run -t -v "$(pwd)/data:/data" osrm/osrm-backend osrm-extract \
  -p /opt/car.lua /data/pune-latest.osm.pbf
docker run -t -v "$(pwd)/data:/data" osrm/osrm-backend osrm-partition \
  /data/pune-latest.osrm
docker run -t -v "$(pwd)/data:/data" osrm/osrm-backend osrm-customize \
  /data/pune-latest.osrm
docker run -t -i -p 5001:5000 -v "$(pwd)/data:/data" osrm/osrm-backend \
  osrm-routed --algorithm mld /data/pune-latest.osrm
```

The container listens on port 5000 internally and is exposed on **localhost:5001**. The Python pipeline uses `http://localhost:5001` by default. If your server uses a different address, set `OSRM_URL`, for example:

```bash
export OSRM_URL=http://localhost:5001
```

## Run the full pipeline

With the OSRM container running, open a second terminal at the repository root:

```bash
source .venv/bin/activate
python main.py
```

You can also run each stage independently:

```bash
python distance_maps/get_map.py
python distance_maps/generate_coordinates.py
python distance_maps/get_travel_times.py
python distance_maps/mds.py
python distance_maps/distort_map.py
```

## Main outputs

| File | Purpose |
|---|---|
| `map.png` | Stitched OSM source raster |
| `map_bounds.json` | Exact geographic bounds of the raster |
| `coordinates.npy` / `coordinates.csv` | 81 sampling locations |
| `travel_times.npy` | Directed OSRM duration matrix in seconds |
| `new_coordinates.npy` | MDS cartogram control points |
| `img/original_map_pune.png` | Original map image |
| `img/distorted_map_pune.png` | Travel-time-distorted image |
| `pune_travel_time_map.html` | Interactive map (open this file) |

Generated data and images are local outputs and should not be committed unless intentionally needed for a reproducible release.

## Important assumptions and limitations

- The 81-point grid is a coarse spatial sample, not a routing model for every address in Pune.
- OSRM driving times depend on the downloaded OSM extract, routing profile, road network completeness, and any traffic assumptions in the routing data. This pipeline does not model live traffic.
- A symmetric MDS map cannot preserve one-way travel-time asymmetry. Reciprocal durations are averaged as described above.
- Missing route pairs are penalised for embedding; inspect the reported unreachable-pair count before interpreting the map.
- MDS is a low-dimensional approximation. It does not guarantee that every pairwise travel duration is preserved exactly.
- The HTML overlay uses the exact bounds of the source OSM raster. The cartogram is a visual transformation of that raster, not a navigable street map in the transformed coordinate system. Use the original OSM layer for geographic navigation.
- OpenStreetMap tile usage is subject to the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/). The script uses a descriptive User-Agent and downloads only a small mosaic.

## Attribution

© [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors. Routing is provided locally by [OSRM](https://project-osrm.org/). This project is an educational visualisation and is not affiliated with either project.

## Legacy file

`PCoA.R` is retained only as a historical reference. The current pipeline runs MDS in Python and does not require R or manually exchanging CSV files.
