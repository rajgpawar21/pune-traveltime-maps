import requests
import math
import os
import shutil
from PIL import Image
from io import BytesIO

# Pune center
LAT_CENTER = 18.537438225978025
LON_CENTER = 73.86652421444191
ZOOM = 14
GRID_SIZE = 9
OUTPUT_FILE = "map.png"

def lat_lon_to_tile(lat, lon, zoom):
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(
        math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))
    ) / math.pi) / 2 * n)
    return x, y

def fetch_tile(x, y, zoom):
    url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
    # OSM requires a User-Agent header — identifies your app
    headers = {"User-Agent": "pune-traveltime-maps/1.0 student-project"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGB")

def get_map():
    os.makedirs("img", exist_ok=True)
    cx, cy = lat_lon_to_tile(LAT_CENTER, LON_CENTER, ZOOM)
    half = GRID_SIZE // 2

    tiles = []
    for row in range(cy - half, cy + half + 1):
        row_imgs = []
        for col in range(cx - half, cx + half + 1):
            print(f"  Fetching tile {col},{row}...")
            tile = fetch_tile(col, row, ZOOM)
            row_imgs.append(tile)
        tiles.append(row_imgs)

    tile_w, tile_h = tiles[0][0].size
    combined = Image.new("RGB", (tile_w * GRID_SIZE, tile_h * GRID_SIZE))
    for r, row in enumerate(tiles):
        for c, tile in enumerate(row):
            combined.paste(tile, (c * tile_w, r * tile_h))

    # Crop to square centered on Pune
    size = min(combined.size)
    left = (combined.width - size) // 2
    top  = (combined.height - size) // 2
    result = combined.crop((left, top, left + size, top + size))
    result.save(OUTPUT_FILE)
    print(f"Saved {OUTPUT_FILE}")
    return OUTPUT_FILE

if __name__ == "__main__":
    get_map()