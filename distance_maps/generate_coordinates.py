"""Generate a north-to-south, west-to-east regular grid over Pune."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

LAT_MIN, LAT_MAX = 18.42, 18.62
LON_MIN, LON_MAX = 73.75, 73.97
GRID_N = 9


def generate_coordinates():
    Path("img").mkdir(exist_ok=True)
    latitudes = np.linspace(LAT_MAX, LAT_MIN, GRID_N)
    longitudes = np.linspace(LON_MIN, LON_MAX, GRID_N)
    # Row-major order: each row runs west -> east, rows run north -> south.
    coordinates = np.array([(lat, lon) for lat in latitudes for lon in longitudes])
    np.save("coordinates.npy", coordinates)
    np.savetxt("coordinates.csv", coordinates, delimiter=",", header="latitude,longitude", comments="")

    plt.figure(figsize=(7, 7))
    plt.scatter(coordinates[:, 1], coordinates[:, 0], s=18)
    plt.title("Pune travel-time sampling grid")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig("img/original_coordinates.png", dpi=160)
    plt.close()
    print(f"Saved {len(coordinates)} coordinates to coordinates.npy")
    return coordinates


if __name__ == "__main__":
    generate_coordinates()
