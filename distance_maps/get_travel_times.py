"""Fetch the complete driving-duration matrix using one OSRM Table request."""
import os
import time

import numpy as np
import requests

COORDINATES_FILE = "coordinates.npy"
OUTPUT_FILE = "travel_times.npy"
OSRM_URL = os.environ.get("OSRM_URL", "http://localhost:5001")
PROFILE = os.environ.get("OSRM_PROFILE", "driving")
TIMEOUT_SECONDS = int(os.environ.get("OSRM_TIMEOUT", "120"))
MAX_RETRIES = 3


def fetch_duration_matrix(coordinates):
    if coordinates.ndim != 2 or coordinates.shape[1] != 2:
        raise ValueError("Coordinates must have shape (n, 2) as latitude, longitude.")
    if len(coordinates) > 100:
        raise ValueError("This script currently supports up to 100 points per OSRM Table request.")

    # OSRM expects longitude,latitude pairs, not latitude,longitude.
    coordinate_string = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coordinates)
    url = f"{OSRM_URL.rstrip('/')}/table/v1/{PROFILE}/{coordinate_string}"
    params = {"annotations": "duration", "sources": "all", "destinations": "all"}

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "Ok":
                raise RuntimeError(f"OSRM returned {payload.get('code')}: {payload.get('message', 'no message')}")
            matrix = np.asarray(payload["durations"], dtype=float)
            expected = (len(coordinates), len(coordinates))
            if matrix.shape != expected:
                raise ValueError(f"OSRM returned matrix shape {matrix.shape}; expected {expected}.")
            matrix[matrix < 0] = np.nan
            np.fill_diagonal(matrix, 0.0)
            return matrix
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"OSRM request failed (attempt {attempt}/{MAX_RETRIES}): {exc}. Retrying in {wait}s.")
                time.sleep(wait)
    raise RuntimeError(f"Could not retrieve OSRM duration matrix from {url}") from last_error


def main():
    if not os.path.exists(COORDINATES_FILE):
        raise FileNotFoundError(f"{COORDINATES_FILE} not found. Run distance_maps/generate_coordinates.py first.")
    coordinates = np.load(COORDINATES_FILE)
    print(f"Requesting {len(coordinates)} x {len(coordinates)} driving-duration matrix from {OSRM_URL}...")
    durations = fetch_duration_matrix(coordinates)
    unreachable = int(np.isnan(durations).sum())
    np.save(OUTPUT_FILE, durations)
    print(f"Saved {OUTPUT_FILE}; unreachable directed pairs: {unreachable}")
    if unreachable:
        print("Unreachable pairs are retained as NaN and handled explicitly by the MDS stage.")


if __name__ == "__main__":
    main()
