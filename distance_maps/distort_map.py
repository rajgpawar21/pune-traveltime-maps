"""Warp the Pune raster and publish an interactive Leaflet HTML map.

The interpolation is inverse-mapped: for each destination pixel in the warped
image, interpolate the corresponding source-image pixel. This is the mapping
direction required by cv2.remap().
"""
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import griddata


def lat_lon_to_image_pixel(lat, lon, bounds, width, height):
    # Web Mercator y is used because the source raster is an OSM tile mosaic.
    def mercator_y(latitude):
        latitude = np.clip(latitude, -85.05112878, 85.05112878)
        radians = np.radians(latitude)
        return np.log(np.tan(np.pi / 4.0 + radians / 2.0))

    x = (lon - bounds["west"]) / (bounds["east"] - bounds["west"]) * (width - 1)
    north_y = mercator_y(bounds["north"])
    south_y = mercator_y(bounds["south"])
    y = (north_y - mercator_y(lat)) / (north_y - south_y) * (height - 1)
    return x, y


def create_interactive_html(bounds, coordinates, output_path="pune_travel_time_map.html"):
    points = [
        {"lat": float(lat), "lon": float(lon), "label": f"Grid point {i + 1}"}
        for i, (lat, lon) in enumerate(coordinates)
    ]
    payload = json.dumps(points)
    south_west = [bounds["south"], bounds["west"]]
    north_east = [bounds["north"], bounds["east"]]
    center = [(bounds["south"] + bounds["north"]) / 2, (bounds["west"] + bounds["east"]) / 2]

    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pune Travel-Time Cartogram</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html, body, #map { height: 100%; margin: 0; }
#panel { position: absolute; z-index: 1000; top: 12px; left: 52px; max-width: 290px;
  background: white; padding: 12px 14px; border-radius: 8px; box-shadow: 0 1px 8px #5555;
  font: 14px/1.45 system-ui, sans-serif; }
#panel h1 { font-size: 16px; margin: 0 0 6px; }
#panel label { display: block; margin-top: 8px; }
#panel input { width: 100%; }
.leaflet-control-attribution { font-size: 11px; }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h1>Pune travel-time cartogram</h1>
  <div>Toggle the distorted raster and sampling points using the layer control.</div>
  <label for="opacity">Cartogram opacity: <span id="opacityValue">85%</span></label>
  <input id="opacity" type="range" min="0" max="100" value="85">
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const bounds = __BOUNDS__;
const points = __POINTS__;
const map = L.map('map');
const imageBounds = L.latLngBounds(bounds.southWest, bounds.northEast);
map.fitBounds(imageBounds);
const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
const original = L.imageOverlay('img/original_map_pune.png', imageBounds, {opacity: 0.95});
const cartogram = L.imageOverlay('img/distorted_map_pune.png', imageBounds, {opacity: 0.85}).addTo(map);
const pointLayer = L.layerGroup(points.map(p =>
  L.circleMarker([p.lat, p.lon], {radius: 4, color: '#b42318', weight: 1, fillOpacity: 0.85})
    .bindPopup(p.label + '<br>' + p.lat.toFixed(5) + ', ' + p.lon.toFixed(5))
));
pointLayer.addTo(map);
L.control.layers({'OpenStreetMap': osm}, {
  'Original raster': original,
  'Travel-time cartogram': cartogram,
  'Sampling points': pointLayer
}, {collapsed: false}).addTo(map);
document.getElementById('opacity').addEventListener('input', event => {
  const value = Number(event.target.value) / 100;
  cartogram.setOpacity(value);
  document.getElementById('opacityValue').textContent = event.target.value + '%';
});
</script>
</body>
</html>"""
    html = html.replace("__BOUNDS__", json.dumps({
        "southWest": south_west,
        "northEast": north_east,
    })).replace("__POINTS__", payload)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"Saved interactive map: {output_path}")


def main():
    source_path = Path("map.png")
    if not source_path.exists():
        raise FileNotFoundError("map.png not found. Run distance_maps/get_map.py first.")
    bounds_path = Path("map_bounds.json")
    if not bounds_path.exists():
        raise FileNotFoundError("map_bounds.json not found. Regenerate the map with distance_maps/get_map.py.")

    source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if source is None:
        raise RuntimeError(f"OpenCV could not read {source_path}")
    height, width = source.shape[:2]
    bounds = json.loads(bounds_path.read_text(encoding="utf-8"))
    coordinates = np.load("coordinates.npy")
    warped_points = np.load("new_coordinates.npy")
    if len(coordinates) != len(warped_points):
        raise ValueError("coordinates.npy and new_coordinates.npy have inconsistent sizes.")

    # Source control points are projected into the actual OSM raster extent.
    source_points = np.array([
        lat_lon_to_image_pixel(lat, lon, bounds, width, height)
        for lat, lon in coordinates
    ], dtype=np.float32)
    # MDS coordinates are normalized to [0, 1]; convert to destination pixels.
    destination_points = warped_points.astype(np.float32).copy()
    destination_points[:, 0] *= width - 1
    destination_points[:, 1] *= height - 1
    destination_points[:, 1] = (height - 1) - destination_points[:, 1]

    # Keep the raster frame stable at its edges while allowing the interior to move.
    edge_source = np.array([
        [0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1],
        [(width - 1) / 2, 0], [(width - 1) / 2, height - 1],
        [0, (height - 1) / 2], [width - 1, (height - 1) / 2],
    ], dtype=np.float32)
    edge_destination = edge_source.copy()
    source_control = np.vstack((source_points, edge_source))
    destination_control = np.vstack((destination_points, edge_destination))

    # cv2.remap needs destination -> source coordinates, hence the reversed
    # interpolation compared with a forward point transform.
    grid_x, grid_y = np.meshgrid(np.arange(width), np.arange(height))
    destination_grid = np.column_stack((grid_x.ravel(), grid_y.ravel()))
    source_grid = griddata(destination_control, source_control, destination_grid, method="linear")
    missing = ~np.isfinite(source_grid).all(axis=1)
    if missing.any():
        nearest = griddata(destination_control, source_control, destination_grid[missing], method="nearest")
        source_grid[missing] = nearest

    map_x = np.clip(source_grid[:, 0], 0, width - 1).reshape(height, width).astype(np.float32)
    map_y = np.clip(source_grid[:, 1], 0, height - 1).reshape(height, width).astype(np.float32)
    warped = cv2.remap(source, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REPLICATE)

    Path("img").mkdir(exist_ok=True)
    cv2.imwrite("img/original_map_pune.png", source)
    if not cv2.imwrite("img/distorted_map_pune.png", warped):
        raise RuntimeError("Could not write img/distorted_map_pune.png")
    create_interactive_html(bounds, coordinates)
    print("Saved img/original_map_pune.png and img/distorted_map_pune.png")


if __name__ == "__main__":
    main()
