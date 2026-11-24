#!/usr/bin/env bash
# Usage: scripts/prepare_city.sh <city-key> <west> <south> <east> <north> [source.osm.pbf]
# Cuts a city PBF out of a larger extract and places it where config/cities.yaml expects it.
# Example (Bengaluru): scripts/prepare_city.sh bengaluru 77.40 12.76 77.86 13.18 southern-zone-latest.osm.pbf
#
# Getting a source extract (run on your machine, these hosts are not reachable from the cloud session):
#   Geofabrik : https://download.geofabrik.de/india/southern-zone-latest.osm.pbf  (~600 MB, covers Bengaluru)
#   or BBBike : https://extract.bbbike.org/  -> pick the bounding box, format "Protocolbuffer (PBF)"
set -euo pipefail
city=$1; w=$2; s=$3; e=$4; n=$5; src=${6:-}
out="data/${city}-latest.osm.pbf"
mkdir -p data
if [[ -n "$src" ]]; then
  command -v osmium >/dev/null || { echo "install osmium-tool (apt install osmium-tool / brew install osmium-tool)"; exit 1; }
  osmium extract -b "$w,$s,$e,$n" --strategy=smart "$src" -o "$out" --overwrite
else
  echo "Download a PBF covering $w,$s,$e,$n and save it as $out"; exit 1
fi
ls -lh "$out"
echo "Next: python -m traveltime.build --city $city"
