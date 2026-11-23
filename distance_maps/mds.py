"""Convert OSRM travel times into a 2D cartogram using metric MDS."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import procrustes
from sklearn.manifold import MDS
from sklearn.preprocessing import MinMaxScaler


def make_symmetric_dissimilarity(durations):
    """Average reciprocal durations; retain a one-way duration if only one exists."""
    matrix = np.asarray(durations, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("Travel-time matrix must be square.")
    finite = np.isfinite(matrix) & (matrix >= 0)
    values = matrix[finite]
    if values.size == 0:
        raise ValueError("The travel-time matrix contains no reachable routes.")
    largest = float(values.max())
    penalty = max(largest * 1.5, largest + 60.0)

    n = matrix.shape[0]
    symmetric = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = matrix[i, j], matrix[j, i]
            a_ok, b_ok = np.isfinite(a), np.isfinite(b)
            if a_ok and b_ok:
                value = (a + b) / 2.0
            elif a_ok:
                value = a
            elif b_ok:
                value = b
            else:
                value = penalty
            symmetric[i, j] = symmetric[j, i] = value
    return symmetric, penalty


def main():
    Path("img").mkdir(exist_ok=True)
    durations = np.load("travel_times.npy")
    coordinates = np.load("coordinates.npy")
    if len(coordinates) != durations.shape[0]:
        raise ValueError("coordinates.npy and travel_times.npy have inconsistent sizes.")

    dissimilarity, unreachable_penalty = make_symmetric_dissimilarity(durations)
    print(f"Running MDS for {len(coordinates)} locations; missing-pair penalty={unreachable_penalty:.1f}s")

    model = MDS(
        n_components=2,
        metric=True,
        dissimilarity="precomputed",
        random_state=42,
        n_init=4,
        max_iter=3000,
        eps=1e-6,
    )
    embedded = model.fit_transform(dissimilarity)

    # Orient the solution to match the geographic grid (east right, north up).
    reference = np.column_stack((coordinates[:, 1], -coordinates[:, 0]))
    _, aligned, _ = procrustes(reference, embedded)
    aligned = MinMaxScaler(feature_range=(0.08, 0.92)).fit_transform(aligned)
    np.save("new_coordinates.npy", aligned)

    plt.figure(figsize=(7, 7))
    plt.scatter(coordinates[:, 1], coordinates[:, 0], s=18)
    plt.title("Original Pune sampling locations")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.tight_layout()
    plt.savefig("img/original_coordinates.png", dpi=160)
    plt.close()

    plt.figure(figsize=(7, 7))
    plt.scatter(aligned[:, 0], aligned[:, 1], c=np.arange(len(aligned)), cmap="viridis", s=24)
    plt.title("Travel-time cartogram control points")
    plt.xlabel("Cartogram x")
    plt.ylabel("Cartogram y")
    plt.tight_layout()
    plt.savefig("img/new_coordinates.png", dpi=160)
    plt.close()
    print(f"Saved new_coordinates.npy; MDS stress={getattr(model, 'stress_', float('nan')):.4f}")


if __name__ == "__main__":
    main()
