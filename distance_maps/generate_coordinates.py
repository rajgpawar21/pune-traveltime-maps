import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Pune bounding box
LAT_MIN, LAT_MAX = 18.42, 18.62
LON_MIN, LON_MAX = 73.75, 73.97
GRID_N = 9    # was 8 — more points = better edge coverage

lats = np.linspace(LAT_MAX, LAT_MIN, GRID_N)   # top to bottom
lons = np.linspace(LON_MIN, LON_MAX, GRID_N)   # left to right

coordinates = []
for lat in lats:
    for lon in lons:
        coordinates.append((lat, lon))

np.save("coordinates.npy", coordinates)
print(f"Generated {len(coordinates)} coordinates")

# Plot to verify they cover Pune correctly
lats_plot = [c[0] for c in coordinates]
lons_plot = [c[1] for c in coordinates]
plt.figure(figsize=(6,6))
plt.scatter(lons_plot, lats_plot, s=10)
plt.title("Coordinate grid over Pune")
plt.xlabel("Longitude")
plt.ylabel("Latitude")
plt.tight_layout()
plt.savefig("img/original_coordinates.png")
print("Saved img/original_coordinates.png")