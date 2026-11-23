"""Download and stitch a square OSM tile mosaic around Pune.

The exact geographic bounds of the saved raster are written to map_bounds.json.
Those bounds are reused by the interactive Leaflet map so the image overlay
is geographically registered rather than positioned using guessed coordinates.
"""
import json
import math
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

LAT_CENTER = 18.537438225978025
LON_CENTER = 73.86652421444191
ZOOM = 14
GRID_SIZE = 9
OUTPUT_FILE = Path("map.png")
BOUNDS_FILE = Path("map_bounds.json")
USER_AGENT = "pune-traveltime-maps/1.0 (educational project)"


def lat_lon_to_tile(lat, lon, zoom):
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_pixel_to_lon(pixel_x, zoom):
    return pixel_x / (256.0 * (2 ** zoom)) * 360.0 - 180.0


def tile_pixel_to_lat(pixel_y, zoom):
    n = math.pi - 2.0 * math.pi * pixel_y / (256.0 * (2 ** zoom))
    return math.degrees(math.atan(math.sinh(n)))


def fetch_tile(session, x, y, zoom):
    url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")


def get_map():
    cx, cy = lat_lon_to_tile(LAT_CENTER, LON_CENTER, ZOOM)
    half = GRID_SIZE // 2
    tile_x0, tile_y0 = cx - half, cy - half
    mosaic = Image.new("RGB", (GRID_SIZE * 256, GRID_SIZE * 256))

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                x, y = tile_x0 + col, tile_y0 + row
                print(f"Fetching OSM tile z={ZOOM} x={x} y={y}")
                tile = fetch_tile(session, x, y, ZOOM)
                mosaic.paste(tile, (col * 256, row * 256))

    # Crop a square centred on the centre tile, retaining exact pixel bounds.
    width, height = mosaic.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    right, bottom = left + side, top + side
    mosaic.crop((left, top, right, bottom)).save(OUTPUT_FILE)

    global_left = tile_x0 * 256 + left
    global_top = tile_y0 * 256 + top
    global_right = global_left + side
    global_bottom = global_top + side
    bounds = {
        "north": tile_pixel_to_lat(global_top, ZOOM),
        "south": tile_pixel_to_lat(global_bottom, ZOOM),
        "west": tile_pixel_to_lon(global_left, ZOOM),
        "east": tile_pixel_to_lon(global_right, ZOOM),
        "zoom": ZOOM,
        "image_width": side,
        "image_height": side,
        "tile_grid_size": GRID_SIZE,
    }
    BOUNDS_FILE.write_text(json.dumps(bounds, indent=2), encoding="utf-8")
    print(f"Saved {OUTPUT_FILE} and {BOUNDS_FILE}")
    print("Map bounds:", bounds)
    return str(OUTPUT_FILE)


if __name__ == "__main__":
    get_map()
