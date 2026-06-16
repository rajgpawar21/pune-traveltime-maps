import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.manifold import MDS
from scipy.spatial import procrustes
import os

os.makedirs("img", exist_ok=True)

travel_times = np.load("travel_times.npy")
n = travel_times.shape[0]
print(f"Loaded travel_times.npy — {n}x{n} matrix")

real_max = travel_times[travel_times < 9999].max()
travel_times[travel_times == 9999] = real_max

symmetric = (travel_times + travel_times.T) / 2

print("Running MDS...")
mds = MDS(
    n_components=2,
    dissimilarity='precomputed',
    random_state=42,
    max_iter=3000,
    n_init=4
)
new_coordinates = mds.fit_transform(symmetric)

# ---- Procrustes alignment ----
# Load original geographic coordinates and use them as the
# reference orientation so MDS output aligns with the real map
original_coordinates = np.load("coordinates.npy")

# Convert lat/lon to a pixel-like space (flip lat so north=up)
ref = np.zeros((n, 2))
ref[:, 0] = original_coordinates[:, 1]   # lon → x
ref[:, 1] = -original_coordinates[:, 0]  # lat → y (flip so north is up)

# Procrustes rotates/reflects/scales MDS to best match the reference
_, new_aligned, _ = procrustes(ref, new_coordinates)

# Clamp aligned coordinates to stay strictly within bounds
# This prevents edge pixels from mapping outside the image
from sklearn.preprocessing import MinMaxScaler
scaler = MinMaxScaler(feature_range=(0.1, 0.9))
new_aligned = scaler.fit_transform(new_aligned)

np.save("new_coordinates.npy", new_aligned)
print("Saved new_coordinates.npy (Procrustes aligned)")

# Plot original
lons_orig = [c[1] for c in original_coordinates]
lats_orig = [c[0] for c in original_coordinates]
plt.figure(figsize=(6, 6))
plt.scatter(lons_orig, lats_orig, c='steelblue', s=20)
plt.title("Original coordinates (geographic)")
plt.xlabel("Longitude")
plt.ylabel("Latitude")
plt.tight_layout()
plt.savefig("img/original_coordinates.png")
plt.close()

# Plot aligned MDS output
colors = plt.cm.viridis(np.linspace(0, 1, n))
plt.figure(figsize=(6, 6))
plt.scatter(new_aligned[:, 0], new_aligned[:, 1], c=colors, s=20)
plt.title("Travel-time distorted coordinates (Pune, aligned)")
plt.xlabel("MDS dimension 1")
plt.ylabel("MDS dimension 2")
plt.tight_layout()
plt.savefig("img/new_coordinates.png")
plt.close()
print("Saved coordinate plots")