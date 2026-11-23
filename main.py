"""Run the complete Pune travel-time cartogram pipeline from the repo root."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGES = [
    "distance_maps/get_map.py",
    "distance_maps/generate_coordinates.py",
    "distance_maps/get_travel_times.py",
    "distance_maps/mds.py",
    "distance_maps/distort_map.py",
]


def main():
    for stage in STAGES:
        print(f"\n===== Running {stage} =====", flush=True)
        subprocess.run([sys.executable, str(ROOT / stage)], cwd=ROOT, check=True)
    print("\nPipeline complete. Open pune_travel_time_map.html in a browser.")


if __name__ == "__main__":
    main()
